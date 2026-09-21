"""
R1-J: P0 定向修复回路 —— Phase 4 评审检出 P0 后系统内自愈。
R4-2: 姓名漂移定点修复（确定性字符串归一 + 4 条安全闸 + 计数复检 + 回退）。
R4-5: 修复回路四段式（分流 / 变坏回退 / 条件性第二跳 / 失败汇总与告警）。
R5-3: 配对推导第三来源（issue 引文 span + 位置型替换指令闸）—— 修不修不再
取决于评审 issue 的 character 字段写法（R4-2 来源 (b) 的盲区）。

背景：评审发现 P0 后此前仅记录进 review_report，全管线无重写路径，
G4（logic+consistency P0 总数 = 0）只能靠"预防全对"，容错为零。

设计（Reviewer 批准缩窄版 + Round 4 收紧 + Round 5 加固）：
- 分诊（R4-2）：名称类 P0 → 定点修复（零 LLM 的字符串替换 + 确定性复检）；
  结构/logic 类 → 全文重写。定点修复的 (错误名, 正确名) 配对只认显式来源
  （名册已晋升别名候选 / consistency issue 字段字面包含 / issue 引文 span），
  歧义即放弃；**禁止编辑距离/相似度猜配对**（Round 1 拒绝理由负面清单）
- 引文 span 来源（R5-3）必须过**位置型替换指令闸**：span 前后 6 个非引号字符内
  出现替换指令词（改为/简写为/统一为…），否则拒绝 —— 防"大长老林渊→林万重"
  式坏配对（把称谓并进名字）；span 含功能词（的了之是在被将与其为和或也）
  视为短语而非名字
- 定点修复 4 条安全闸全部满足才替换：配对来源显式 / len(错误名)>=2 /
  错误名与注册名·已登记别名·退场名互不为子串（双向）/ 错误名不在退场名单
- 替换后重审 Logic + Consistency（Emotion 不参与）；P0 归零才保留，
  否则回退保留原文（现有兜底语义不变）
- 重写最多 2 轮（R4-5 hard cap）：第二轮仅在第一轮严格改善时条件触发，
  brief 必须附第一轮新引入的问题清单；重写导致劣化（residual 不降 / 出现
  首检没有的新 P0 类别）→ 立即回退原文并标注 revision_degraded
- 修订痕迹写入 s.data['revision_log']（定点修复条目带
  type='name_spotfix' + wrong_name/right_name/pair_source + trigger，只增不改）
- 不静默丢内容；重写走与 Phase 3 相同的 _save_chunk_progress 落盘路径
"""
import asyncio
import os
import re
import time

from api.sse import EventType
from core.config import get_json_max_tokens
from core.established_facts import EstablishedFacts, earliest_departure_parts
from core.llm_client import call_llm
from core.logger import get_logger
from core.name_registry import promoted_candidates, render_name_roster
from core.prompt_loader import load_prompt
from core.text_utils import truncate
from services.name_audit import (
    anchor_span, append_audit_log, classify_departed_occurrences,
    record_name_pairs,
)

logger = get_logger('consistency_repair')

# R4-5: 重写硬顶 2 轮（第二轮仅条件触发；对照 chinese-novelist-skill 3 轮 /
# self-refine 4 次，2 轮在 20 Part × 多小时约束下更稳）
MAX_REWRITE_ROUNDS = 2

# consistency issue 的 character 字段可能是"林万重/林渊"这类复合写法，
# 按常见分隔符切分出候选错误名 token（只做字面切分，不做任何模糊匹配）
_CHAR_FIELD_SPLIT_RE = re.compile(r'[/、,，;；|\s]+')

# R5-3: 配对推导第三来源 —— issue 引文 span（QianBi"引证验真"模式）
_QUOTED_SPAN_RE = re.compile(r'[「」『』“”‘’"\']([^「」『』“”‘’"\']{2,12})[「」『』“”‘’"\']')
# 位置型替换指令词：span 前后紧邻出现才认（防"大长老林渊→林万重"坏配对）
_DIRECTIVE_RE = re.compile(r'改为|改写为|统一为|应为|修正为|写成|写作|简写为|讹为|误作|笔误|混淆为|错写成')
_DIRECTIVE_WINDOW = 6        # 指令词须在 span 前后 6 个非引号字符内
_DIRECTIVE_MARGIN = 2        # 窗口余量：防 3 字指令词跨 6 字边界被截断
# 含功能词的 span 视为短语而非名字（大长老林渊 / 您的传讯玉 这类直接排除）
_SPAN_STOP_CHARS = '的了之是在被将与其为和或也'
_QUOTE_CHARS = '「」『』“”‘’"\''

# R6-5（S3）: 定点编辑（SEARCH/REPLACE 锚块）—— prompts/targeted_edit.txt
# 文件优先 + 内嵌 fallback（R5-4 纪律）。编辑面从 4000 字缩到 ≤90 字，
# 直接攻击"全量重写引入新问题"（reval：8 个触发修复的 Part 6 个劣化）。
TARGETED_EDIT_SYSTEM = load_prompt("targeted_edit", """你是长篇小说修订专家。上一轮评审检出了本 Part 的 P0 级问题（见下方修订指令），请用 SEARCH/REPLACE 编辑块逐条修正——只改动问题所在的最短片段，其余一字不动。

## 编辑协议（必须严格遵守）

对每个需要修正的问题，输出一个编辑块（每个块前用一行注明针对的问题编号）：

【问题 1】
<<<<<<< SEARCH
<与原文逐字相同的片段，不少于 15 字，在全文恰好出现 1 次>
=======
<替换片段，长度不得超过原文片段的 ±30%>
>>>>>>> REPLACE

## 强约束

1. 只输出最多 3 个编辑块；不得输出编辑块之外的任何内容
2. SEARCH 必须从原文逐字复制（客户端会做逐字校验，改写或幻觉的片段会被弃用）
3. 不得改动编辑块之外的任何一字；不得改变剧情走向
4. 不得引入角色名册之外的任何姓名
5. 替换片段长度不得超过 SEARCH 的 ±30%

## 退场角色修正规范（本 Part 有已退场角色以非法形态出现时适用）

已退场角色只允许以合法形态存在：碑林/碑影模仿其形貌或声音、回忆、影像、他人提及、残留之念/执念残像。
修正方向：把实体行动/直接引语归因/参战改写为"碑林以他的形貌/声音……"式归因
（例："林渊冷笑一声，抬手压下" → "碑林模仿着他的声线冷笑，借他的形貌抬手压下"）；
不得删除该角色在剧情中的功能位（对抗关系、情绪功能、事件结果保留），不得改变剧情走向，
不得引入角色名册之外的任何姓名。
""")

# R6-5（S3）: 编辑协议常量（确定性硬闸，零相似度——守 Round 1 负面清单）
_EDIT_MAX_BLOCKS = 3              # 最多编辑块数（与 P0 触发上限一致）
_EDIT_MIN_SEARCH_LEN = 15         # SEARCH 最短长度
_EDIT_LEN_TOLERANCE = 0.30        # 替换片段长度 ±30%
_EDIT_TOTAL_LEN_TOLERANCE = 0.10  # 全文长度变化 ≤10%（保 G2/G3 密度）
_EDIT_ANCHOR_MIN = 10             # anchor 引文最短长度（定位信号下限）
# anchor 引文抽取：description/location 的引号 span（R5-4 三步工序强制 ≤40 字
# 引文 / R6-1 logic 明细 description 的 原文：“anchor” 段）
_ANCHOR_QUOTE_RE = re.compile('[「」『』“”‘’"\']([^「」『』“”‘’"\']{10,60})[「」『』“”‘’"\']')
# R8-P0-1（S1）: issue 字面片段第三来源 —— 中文数量短语（≥4 字）或 description
# 中 ≥6 字连续非标点片段；要求在正文逐字出现且恰好 1 次（零相似度），命中
# 名册名/退场名则跳过（服务 Part 6 时间线 P0 等无引文场景）
_ISSUE_QUANTITY_RE = re.compile('[一二三四五六七八九十百千万零两0-9]+[年月日个]')
_ISSUE_RUN_RE = re.compile('[^\\s，。；：、？！“”‘’"\'（）()【】—…·]{6,}')
# R8-P0-1（S1）: departed 类 P0 判定维度（02_review §1.1.5）
_DEPARTED_DIMENSIONS = ('角色状态/身份', '角色状态', '状态连续性')
# 编辑块解析（严格正则切分；无法解析 → 调用方落回全量重写）
_EDIT_BLOCK_RE = re.compile(
    r'<<<<<<< SEARCH[^\n]*\n([\s\S]*?)\n=======[^\n]*\n([\s\S]*?)\n>>>>>>> REPLACE')


class _RevisionStateProxy:
    """R1-J: 包一层 state，把修订指令追加到 PartWriterAgent 看到的上下文末尾。

    不修改 PartWriterAgent 本身（其 prompt/schema 不在本轮批准边界内），
    其余属性全部委托给内层 TempStoryState。
    """

    def __init__(self, inner, brief: str):
        self._inner = inner
        self._brief = brief

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def get_part_context(self, part_num):
        base = self._inner.get_part_context(part_num) or ''
        if self._brief:
            base += (
                '\n\n## ⚠ 修订指令（上一轮评审检出 P0 问题，本次重写必须逐条修正，'
                '其余设定/剧情推进不变）\n' + self._brief + '\n'
            )
        return base


def count_p0(logic_result: dict, consistency_result: dict) -> int:
    """聚合 Logic + Consistency 的 P0 计数。

    降级结果（agent 调用失败）不计入 —— 重写治不了基础设施故障，
    且降级结果的 P0 是"未检查"标记而非真实矛盾。
    """
    lr = logic_result or {}
    cr = consistency_result or {}
    if lr.get('_fallback') or str(cr.get('verdict', '')).startswith('检查失败'):
        return 0
    logic_p0 = 0
    try:
        logic_p0 = int(lr.get('p0_count', 0) or 0)
    except (TypeError, ValueError):
        logic_p0 = 0
    cons_issues = [i for i in (cr.get('issues') or [])
                   if isinstance(i, dict) and i.get('level') == 'P0']
    return logic_p0 + len(cons_issues)


# ----------------- R4-2: 名称类分诊与配对推导（纯函数，可单测） -----------------

def _p0_issues(result: dict) -> list:
    return [i for i in ((result or {}).get('issues') or [])
            if isinstance(i, dict) and i.get('level') == 'P0']


def is_name_issue(issue: dict) -> bool:
    """名称类 P0 判定：dimension=='名称一致性'。"""
    return isinstance(issue, dict) and (issue.get('dimension') or '').strip() == '名称一致性'


def has_name_issue(consistency_result: dict, registry: dict) -> bool:
    """R4-2 分诊：是否存在名称类 P0。

    名称类 = consistency P0 issue 的 dimension=='名称一致性'，或其
    description/location/verdict 含名册已晋升的别名对（variant 字面出现）。
    R5-3: verdict 纳入文本扫描面（复审判定文本常只在此给出两种写法）。
    """
    if not isinstance(registry, dict) or not registry:
        return False
    for issue in _p0_issues(consistency_result):
        if is_name_issue(issue):
            return True
        text = (f"{issue.get('description') or ''} {issue.get('location') or ''} "
                f"{issue.get('verdict') or ''}")
        for info in registry.values():
            if not isinstance(info, dict):
                continue
            for cand in promoted_candidates(info):
                if (cand.get('variant') or '') and cand['variant'] in text:
                    return True
    return False


def _window_has_directive(text: str, start: int, end: int, forward: bool) -> bool:
    """R5-3: 位置型替换指令判定 —— 从 span 边界出发，跳过引号字符，在前后各
    _DIRECTIVE_WINDOW 个非引号字符（+余量）内命中 _DIRECTIVE_RE。

    真案例：…被大量简写为'井中意识'… → 向后命中"简写为"；
            应将所有'林渊'改为'林万重' → 向前命中"改为"。
    坏案例：…突然出现另一位'大长老林渊'… → 前后 6 字无指令词 → 拒绝。
    """
    chars: list = []
    i = end if forward else start - 1
    step = 1 if forward else -1
    count = 0
    limit = _DIRECTIVE_WINDOW + _DIRECTIVE_MARGIN
    while 0 <= i < len(text) and count < limit:
        ch = text[i]
        if ch not in _QUOTE_CHARS:
            chars.append(ch)
            count += 1
        i += step
    window = ''.join(chars) if forward else ''.join(reversed(chars))
    return bool(_DIRECTIVE_RE.search(window))


def _directive_quoted_spans(field_text: str) -> list:
    """R5-3: 抽取带位置型替换指令佐证的引号 span（纯函数，可单测）。

    候选 span 须满足：2-12 字 / 不含 _SPAN_STOP_CHARS 功能词 / 同一字段内某次
    出现的前后窗口命中替换指令词（指令词与 span 不得跨字段拼接判定）。

    Returns:
        [(span, evidence 切片), ...]（按出现顺序、同 span 去重；evidence 含
        指令词原文，≤30 字）。
    """
    if not field_text:
        return []
    out: list = []
    seen: set = set()
    for m in _QUOTED_SPAN_RE.finditer(field_text):
        span = m.group(1)
        if not (2 <= len(span) <= 12):
            continue
        if any(ch in _SPAN_STOP_CHARS for ch in span):
            continue  # 含功能词 → 短语而非名字
        if span in seen:
            continue
        if not (_window_has_directive(field_text, m.start(), m.end(), True)
                or _window_has_directive(field_text, m.start(), m.end(), False)):
            continue  # 无位置型替换指令佐证 → 拒绝（坏配对防线）
        seen.add(span)
        lo = max(0, m.start() - 15)
        hi = min(len(field_text), m.end() + 15)
        out.append((span, field_text[lo:hi].strip()))
    return out


def derive_name_pairs(part_text: str, consistency_result: dict, registry: dict) -> list:
    """R4-2/R5-3: 推导显式 (错误名, 正确名) 配对（确定性，零相似度）。

    来源三类（按 (a) → (b) → (c) 顺序执行，seen 去重先到先得）：
      (a) registry alias_candidates 中已按晋升规则生效的条目
          （同一 variant ≥2 次或带非空 evidence）；
      (b) consistency P0 issue 的 character 字段 token + description/location
          中**同时字面包含**该 token（不在 registry）与某个 registry canonical 名；
      (c) R5-3: issue 引文 span（description/suggestion/verdict）—— span 不在
          registry、正文 count>0、且带位置型替换指令佐证（_DIRECTIVE_RE 紧邻），
          与 description 中唯一字面出现的 canonical 配对。修不修不再取决于
          character 字段写法（(b) 的盲区：character 只写规范名时配对为空）。

    歧义即放弃：description 含多个 canonical 候选、不含任何 canonical
    名、或错误名本身在 registry 中 → 不产配对（调用方落回重写）。
    只保留在正文中实际出现过的错误名（count > 0）。

    Returns:
        [{"wrong", "right", "source", "evidence"}, ...]（source ∈
        {'alias_candidate', 'issue_character', 'issue_quote'}）
    """
    if not isinstance(registry, dict) or not registry:
        return []
    part_text = part_text or ''
    canonicals = [n for n in registry if n]
    pairs: list = []
    seen: set = set()

    def _add(wrong, right, source, evidence=''):
        wrong = (wrong or '').strip()
        right = (right or '').strip()
        if not wrong or not right or wrong == right:
            return
        if right not in registry or wrong in registry:
            return  # 正确名必须在名册、错误名必须不在名册
        if part_text.count(wrong) <= 0:
            return  # 正文没有这个错误名 —— 替换无意义
        key = (wrong, right)
        if key in seen:
            return
        seen.add(key)
        pairs.append({'wrong': wrong, 'right': right, 'source': source,
                      'evidence': (evidence or '').strip()[:30]})

    # (a) 名册已晋升的别名候选
    for name, info in registry.items():
        if not isinstance(info, dict):
            continue
        for cand in promoted_candidates(info):
            _add(cand.get('variant'), name, 'alias_candidate', cand.get('evidence', ''))

    # (b) consistency issue 的 character 字段 + description/location 字面包含
    for issue in _p0_issues(consistency_result):
        desc = (issue.get('description') or '').strip()
        loc = (issue.get('location') or '').strip()
        text = f'{desc} {loc}'
        if not desc:
            continue
        # 歧义判定只看 description：含多个 canonical 候选即放弃
        # （location 常顺带提及他人，如"……同时扑向林尘"，不作为歧义依据）
        present = [n for n in canonicals if n in desc]
        if len(present) != 1:
            continue  # 歧义（0 个或 ≥2 个 canonical）即放弃
        canonical = present[0]
        char_field = (issue.get('character') or '').strip()
        for token in _CHAR_FIELD_SPLIT_RE.split(char_field):
            token = token.strip()
            if token and token not in registry and token in text and token != canonical:
                _add(token, canonical, 'issue_character', desc)

    # (c) R5-3: issue 引文 span + 位置型替换指令闸（desc/sugg/verdict 各自独立
    # 判定指令位置，不跨字段拼接；canonical 歧义口径同 (b)：只看 description）
    for issue in _p0_issues(consistency_result):
        desc = (issue.get('description') or '').strip()
        if not desc:
            continue
        present = [n for n in canonicals if n in desc]
        if len(present) != 1:
            continue  # 歧义（0 个或 ≥2 个 canonical）即放弃
        canonical = present[0]
        for field_text in (desc, (issue.get('suggestion') or '').strip(),
                           (issue.get('verdict') or '').strip()):
            for span, evidence in _directive_quoted_spans(field_text):
                if span != canonical and span not in registry:
                    _add(span, canonical, 'issue_quote', evidence)
    return pairs


def apply_safety_gates(pairs: list, registry: dict, departed_names=None) -> list:
    """R4-2: 4 条安全闸（全部满足才保留配对；任一不满足 → 丢弃该配对）。

    (a) 配对来源显式（derive_name_pairs 已保证：alias_candidate / issue_character）
    (b) len(错误名) >= 2
    (c) 错误名与任何注册名 / 已登记别名 / 退场名互不为子串（双向检查）
    (d) 错误名不在 departed 名单
    """
    reg_names = [n for n in (registry or {}) if n]
    aliases: list = []
    for info in (registry or {}).values():
        if isinstance(info, dict):
            aliases.extend(a.strip() for a in (info.get('aliases') or [])
                           if isinstance(a, str) and a.strip())
    departed = [n for n in (departed_names or []) if n]
    check_names = reg_names + aliases + departed
    valid: list = []
    for p in pairs or []:
        wrong = (p.get('wrong') or '').strip()
        if len(wrong) < 2:  # 闸 (b)
            logger.info(f'[ConsistencyRepairer] 安全闸(b): 错误名 "{wrong}" 长度 <2，放弃该配对')
            continue
        clash = next((n for n in check_names if wrong in n or n in wrong), None)
        if clash:  # 闸 (c)
            logger.info(f'[ConsistencyRepairer] 安全闸(c): 错误名 "{wrong}" 与 "{clash}" 互为子串，放弃该配对')
            continue
        if wrong in departed:  # 闸 (d)
            logger.info(f'[ConsistencyRepairer] 安全闸(d): 错误名 "{wrong}" 在退场名单，放弃该配对')
            continue
        valid.append(p)
    return valid


def apply_name_spotfix(part_text: str, pairs: list) -> tuple:
    """R4-2: 确定性替换 + 计数校验（替代 Scout 的长度相等假设——中文名不等长时
    计数恒等式仍成立）。

    Returns:
        (new_text, ok, reason)——ok=False 时 new_text == part_text（未改动）。
    """
    new_text = part_text or ''
    for p in pairs or []:
        wrong, right = p.get('wrong', ''), p.get('right', '')
        cur = new_text
        if cur.count(wrong) == 0:
            continue
        nxt = cur.replace(wrong, right)
        if nxt.count(wrong) != 0:
            return part_text, False, f'替换后 "{wrong}" 未清零'
        if nxt.count(right) != cur.count(right) + cur.count(wrong):
            return part_text, False, f'"{right}" 计数恒等式不成立'
        new_text = nxt
    return new_text, True, ''


def _issue_signature(issue: dict) -> tuple:
    """issue 去重签名（dimension + description 前 40 字 + character）。"""
    return ((issue.get('dimension') or '').strip(),
            (issue.get('description') or '').strip()[:40],
            (issue.get('character') or '').strip())


def _count_non_name_p0(logic_result: dict, consistency_result: dict) -> int:
    """R6-2（S2）: 非名称残留 P0 数 —— consistency 侧非 dimension=='名称一致性'
    的 P0 issue 数 + logic p0_count（logic 明细不喂名称分诊，全部计入非名称
    残留；残留中若仍有名称类 P0，本字段只数非名称部分，如实反映"本修复管不到
    的残留"）。"""
    n = 0
    try:
        n += int((logic_result or {}).get('p0_count') or 0)
    except (TypeError, ValueError):
        pass
    for i in ((consistency_result or {}).get('issues') or []):
        if isinstance(i, dict) and i.get('level') == 'P0' \
                and (i.get('dimension') or '').strip() != '名称一致性':
            n += 1
    return n


# ----------------- R6-5（S3）: 定点编辑（纯函数，可单测，零相似度） -----------------

def _issue_literal_candidates(issue: dict) -> list:
    """R8-P0-1（S1）: issue 字面片段候选（纯函数，零相似度）。

    中文数量短语（`三十七年` 类 ≥4 字，description/location 均可）或
    description 中 ≥6 字连续非标点片段；按出现顺序去重。候选须由调用方在
    正文中逐字校验唯一性——抽得出不等于用得了（保守，无静默错误）。
    """
    out: list = []
    seen: set = set()
    issue = issue if isinstance(issue, dict) else {}
    for field in ('description', 'location'):
        field_text = (issue.get(field) or '').strip()
        if not field_text:
            continue
        for m in _ISSUE_QUANTITY_RE.finditer(field_text):
            cand = m.group(0)
            if len(cand) >= 4 and cand not in seen:
                seen.add(cand)
                out.append(cand)
        if field == 'description':
            for m in _ISSUE_RUN_RE.finditer(field_text):
                cand = m.group(0)
                if cand not in seen:
                    seen.add(cand)
                    out.append(cand)
    return out


def _issue_anchor(issue: dict, part_text: str, skip_names=None) -> str:
    """R6-5（S3）+ R8-P0-1（S1）: 从 issue 抽取可在正文唯一定位的原文引文（纯字面，零相似度）。

    三个来源（按优先级）：
    1. description/location 的引号 span（R5-4 三步工序强制 ≤40 字引文 / R6-1
       logic 明细 description 的 原文：“anchor” 段）——取第一个在正文中恰好
       出现 1 次且长度 ≥_EDIT_ANCHOR_MIN 的 span；
    2. R8-1 issue 字面片段：数量短语/≥6 字连续片段在正文逐字出现且恰好 1 次，
       经 anchor_span 确定性扩窗+句子边界修剪为 ≥15 字唯一 span；候选命中
       名册名/退场名（skip_names）则跳过；
    找不到返回 ''（调用方放弃编辑、落回全量重写——保守，无静默错误）。
    """
    text = part_text or ''
    for field in ('description', 'location'):
        for m in _ANCHOR_QUOTE_RE.finditer((issue or {}).get(field) or ''):
            span = m.group(1)
            if len(span) >= _EDIT_ANCHOR_MIN and text.count(span) == 1:
                return span
    skip = [n for n in (skip_names or []) if n]
    for cand in _issue_literal_candidates(issue):
        idx = text.find(cand)
        if idx < 0 or text.count(cand) != 1:
            continue
        span = anchor_span(text, idx, idx + len(cand))
        if span and not any(n in span for n in skip):
            return span
    return ''


def _is_departed_issue(issue: dict, departed_names) -> bool:
    """R8-P0-1（S1）: departed 类 P0 判定 —— dimension ∈ 退场状态维度 且
    issue 的 character/description/location 命中退场账本角色名。"""
    if not isinstance(issue, dict):
        return False
    if (issue.get('dimension') or '').strip() not in _DEPARTED_DIMENSIONS:
        return False
    text = (f"{issue.get('character') or ''} {issue.get('description') or ''} "
            f"{issue.get('location') or ''}")
    return any(n and n in text for n in (departed_names or []))


def _departed_issue_anchor(issue: dict, base_text: str, departed_anchors: dict) -> str:
    """R8-P0-1（S1）: departed 类 issue 的 illegal span 补位。

    issue.character 或 description/location 命中账本角色名 → 取该角色第一个
    在正文仍唯一的 illegal span 作 anchor（span 逐字来自正文，消费时再过
    count==1 硬闸）。无命中返回 ''。
    """
    if not isinstance(issue, dict) or not departed_anchors:
        return ''
    text = (f"{issue.get('character') or ''} {issue.get('description') or ''} "
            f"{issue.get('location') or ''}")
    for name, spans in departed_anchors.items():
        if not name or name not in text:
            continue
        for span in spans or []:
            if span and base_text.count(span) == 1:
                return span
    return ''


def _edit_trigger_ok(base_text: str, base_logic: dict, base_cons: dict,
                     departed_anchors: dict = None, skip_names=None) -> tuple:
    """R6-5（S3）+ R8-P0-1（S1）: 编辑触发条件（全部满足才走编辑）—— P0 总数 ≤3，且每个 P0
    issue 都能取得唯一 anchor（consistency 引文 / R6-1 logic 明细 anchor /
    R8-1 issue 字面片段 / departed 类 illegal span）。

    Returns:
        (ok, anchors)——ok=False 时调用方落回全量重写。
    """
    p0_total = count_p0(base_logic, base_cons)
    if p0_total <= 0 or p0_total > _EDIT_MAX_BLOCKS:
        return False, []
    p0_issues = ([i for i in ((base_logic or {}).get('issues') or [])
                  if isinstance(i, dict) and i.get('level') == 'P0'
                  and not i.get('_detail_placeholder')]
                 + [i for i in ((base_cons or {}).get('issues') or [])
                    if isinstance(i, dict) and i.get('level') == 'P0'])
    if len(p0_issues) != p0_total:
        return False, []  # 明细与计数不一致（占位/无明细）→ 保守放弃
    anchors: list = []
    for issue in p0_issues:
        anchor = _issue_anchor(issue, base_text, skip_names=skip_names)
        if not anchor:
            anchor = _departed_issue_anchor(issue, base_text, departed_anchors or {})
        if not anchor:
            return False, []
        anchors.append(anchor)
    return True, anchors


def _edit_subset_trigger_ok(base_text: str, base_logic: dict, base_cons: dict,
                            departed_anchors: dict, skip_names=None) -> tuple:
    """R8-4（S4）: departed 类子集编辑旁路 —— P0 >3 时的严格有界开放。

    条件（全部满足）：(a) departed 类 P0 数 ≤_EDIT_MAX_BLOCKS 且每个都有
    illegal span 作 anchor；(b) 其余非 departed 类 P0 每个都有引文/literal
    anchor（保证后续可再编辑）。此时允许**子集编辑**：编辑块只针对 departed
    类 P0，brief 明示"其余 N 个 P0 不在本次编辑范围"。非 departed 类的
    P0>3 场景不开放此旁路（departed_anchors 为空即不开放）。

    Returns:
        (ok, anchors)——anchors 只含 departed 类 issue 的 anchor。
    """
    if not departed_anchors:
        return False, []
    p0_total = count_p0(base_logic, base_cons)
    if p0_total <= _EDIT_MAX_BLOCKS:
        return False, []  # 主路径已覆盖
    p0_issues = ([i for i in ((base_logic or {}).get('issues') or [])
                  if isinstance(i, dict) and i.get('level') == 'P0'
                  and not i.get('_detail_placeholder')]
                 + [i for i in ((base_cons or {}).get('issues') or [])
                    if isinstance(i, dict) and i.get('level') == 'P0'])
    if len(p0_issues) != p0_total:
        return False, []
    departed_names = list(departed_anchors)
    dep_issues = [i for i in p0_issues if _is_departed_issue(i, departed_names)]
    other_issues = [i for i in p0_issues if not _is_departed_issue(i, departed_names)]
    if not dep_issues or len(dep_issues) > _EDIT_MAX_BLOCKS:
        return False, []
    anchors: list = []
    for issue in dep_issues:
        anchor = _departed_issue_anchor(issue, base_text, departed_anchors)
        if not anchor:
            return False, []
        anchors.append(anchor)
    for issue in other_issues:
        if not _issue_anchor(issue, base_text, skip_names=skip_names):
            return False, []  # 其余 P0 也必须可定位（保证后续可再编辑）
    return True, anchors


def _apply_targeted_edits(base_text: str, raw_output: str) -> tuple:
    """R6-5（S3）: 解析并应用 SEARCH/REPLACE 编辑块（纯函数，零相似度）。

    每块校验（任一失败 → 该块不应用并记 failure）：
      - base_text.count(search) == 1（唯一性硬闸，禁模糊匹配）
      - len(search) >= 15
      - abs(len(replace) - len(search)) <= 0.30 * len(search)（±30% 长度守卫）
      - 块间在原文中不重叠
    全文守卫：abs(len(new_text) - len(base_text)) <= 0.10 * len(base_text)
    （保 G2/G3 密度不被破坏，超限则整体回退编辑）。

    Returns:
        (new_text, applied_count, failures)——applied_count==0 时 new_text ==
        base_text（未改动；调用方落回全量重写）。
    """
    text = base_text or ''
    blocks = _EDIT_BLOCK_RE.findall(raw_output or '')
    if not blocks:
        return text, 0, ['no_valid_block']
    if len(blocks) > _EDIT_MAX_BLOCKS:
        logger.info(f'[ConsistencyRepairer] 编辑块 {len(blocks)} 个超过上限 '
                    f'{_EDIT_MAX_BLOCKS}，取前 {_EDIT_MAX_BLOCKS} 个')
        blocks = blocks[:_EDIT_MAX_BLOCKS]
    accepted: list = []   # (start, end, replace)
    failures: list = []
    for raw_search, raw_replace in blocks:
        search = raw_search.strip('\r\n')
        replace = raw_replace.strip('\r\n')
        if len(search) < _EDIT_MIN_SEARCH_LEN:
            failures.append(f'search_too_short({len(search)})')
            continue
        if text.count(search) != 1:
            failures.append('search_not_unique')
            continue
        if abs(len(replace) - len(search)) > _EDIT_LEN_TOLERANCE * len(search):
            failures.append('replace_len_out_of_tolerance')
            continue
        start = text.find(search)
        end = start + len(search)
        if any(start < e and b < end for b, e, _ in accepted):
            failures.append('blocks_overlap')
            continue
        accepted.append((start, end, replace))
    if not accepted:
        return text, 0, failures or ['all_blocks_failed']
    accepted.sort(key=lambda x: x[0])
    parts: list = []
    cursor = 0
    for start, end, replace in accepted:
        parts.append(text[cursor:start])
        parts.append(replace)
        cursor = end
    parts.append(text[cursor:])
    new_text = ''.join(parts)
    if abs(len(new_text) - len(text)) > _EDIT_TOTAL_LEN_TOLERANCE * len(text):
        return text, 0, failures + ['total_len_exceeded']
    return new_text, len(accepted), failures


def is_revision_degraded(p0_before: int, residual_p0: int,
                         first_cons: dict, new_cons: dict,
                         first_logic: dict, new_logic: dict) -> bool:
    """R4-5: 变坏回退判据 —— residual 没变好，或重审出现首检没有的新 P0。

    consistency 按 dimension/description 差集判定；logic V5 短协议无明细，
    用 p0_count 与 verdict 文本比对（p0_count 上升即变坏）。
    """
    if residual_p0 >= p0_before:
        return True
    first_sigs = {_issue_signature(i) for i in _p0_issues(first_cons)}
    if any(_issue_signature(i) not in first_sigs for i in _p0_issues(new_cons)):
        return True
    if int((new_logic or {}).get('p0_count') or 0) > int((first_logic or {}).get('p0_count') or 0):
        return True
    return False


def newly_introduced_problems(first_logic: dict, first_cons: dict,
                              new_logic: dict, new_cons: dict) -> list:
    """R4-5: 第一轮新引入的问题清单（首检 issues 与一审重审 issues 的差集 +
    logic verdict 变化）—— 第二轮 brief 必须附带，防重复犯错。"""
    first_sigs = {_issue_signature(i) for i in _p0_issues(first_cons)}
    problems = []
    for i in _p0_issues(new_cons):
        if _issue_signature(i) not in first_sigs:
            loc = (i.get('location') or '').strip()
            desc = (i.get('description') or '').strip()[:120]
            problems.append(f'[{loc}] {desc}' if loc else desc)
    fl, nl = first_logic or {}, new_logic or {}
    f_p0 = int(fl.get('p0_count') or 0)
    n_p0 = int(nl.get('p0_count') or 0)
    if n_p0 > f_p0:
        problems.append(f'逻辑 P0 数量上升: {f_p0} → {n_p0}；结论: {(nl.get("verdict") or "")[:120]}')
    elif (nl.get('verdict') or '') != (fl.get('verdict') or '') and n_p0 > 0:
        problems.append(f'逻辑结论变化: {(nl.get("verdict") or "")[:120]}')
    return problems


class ConsistencyRepairer:
    """R1-J/R4-2/R4-5: 单 Part 的 P0 定向修复（名称类定点 + 最多 2 轮重写）。"""

    def __init__(self, service, logic_agent, consistency_agent):
        self.service = service
        self.logic_agent = logic_agent
        self.consistency_agent = consistency_agent
        # R5-2: 定点修复重审后的二次定点硬顶（每次 _spotfix_names 调用重置）
        self._spotfix_retry_used = False

    async def _emit_log(self, message: str) -> None:
        """R5-S6（P1-2）: emitter + logger 双写。

        verify 的 FakeEmitter.emit 是 pass —— 四段式回退/第二跳/完成消息只走
        emitter 时全量跑日志不可见（P1-2 实证）。双写后运行日志可实时观测，
        消息文本逐字保留（emoji 不变）。
        """
        await self.service.emitter.emit(EventType.LOG, {'message': message, 'work_id': self.service.work_id},
                                        work_id=self.service.work_id)
        logger.info(f'[ConsistencyRepairer] {message}')

    async def maybe_repair_part(self, part_num: int, part_text: str,
                                logic_result: dict, consistency_result: dict,
                                state_mock) -> dict:
        """p0_count > 0 时启动修复；返回 {} 表示未触发（行为与改前一致）。

        触发时返回标注 dict（revision_attempted/revision_passed，重审通过时
        附带替换用的 logic_result/consistency_result），由调用方合并进
        per_part_results 与 review_report。
        """
        s = self.service
        p0 = count_p0(logic_result, consistency_result)
        if p0 <= 0:
            return {}
        logger.info(f'[ConsistencyRepairer] Part {part_num} 检出 {p0} 个 P0，启动定向修复'
                    f'（名称类定点修复 / 其余全文重写，最多 {MAX_REWRITE_ROUNDS} 轮）')
        await s.emitter.emit(EventType.LOG, {
            'message': f'🔧 Part {part_num} 检出 {p0} 个 P0 问题，启动定向修复...',
            'work_id': s.work_id}, work_id=s.work_id)

        # R4-2 分诊：名称类 P0 → 定点修复（零 LLM 替换 + 确定性复检）
        registry = s.data.get('name_registry') or {}
        if not isinstance(registry, dict):
            registry = {}
        # R6-2/R6-5 链路 base（默认 None = 原始 part_text 与首检结果）：
        # spotfix → targeted edit → rewrite 三段串联，每段独立重审、独立判定
        base_text = None
        base_logic = None
        base_cons = None
        base_pairs = None
        base_p0 = p0
        if has_name_issue(consistency_result, registry):
            pairs = derive_name_pairs(part_text, consistency_result, registry)
            if pairs:
                # R5-2: 检查点产出当场沉淀进违禁词典（未过闸 → advisory；后续
                # 定点修复过闸后再记一次，record_name_pairs 只升不降）
                record_name_pairs(s.data, pairs, part_num, 'first_pass')
                note = await self._spotfix_names(
                    part_num, part_text, pairs, registry, p0,
                    logic_result, consistency_result, state_mock)
                if note is not None:
                    # R6-2（S2）: 残留 P0 不连坐、继续治 —— spotfix 已独立落盘
                    # （partial_applied），以修复稿为 base 进入定点编辑/重写；
                    # 劣化判据与二轮条件换成效审后基线（_base_logic/_base_cons/
                    # residual），first_pass_p0 恒定原始首检数（聚合预算护栏
                    # 不被修复过程改变）
                    if note.get('residual_p0', 0) > 0 and note.get('_base_text'):
                        base_text = note.pop('_base_text')
                        base_logic = note.pop('_base_logic')
                        base_cons = note.pop('_base_cons')
                        base_pairs = note.pop('_base_pairs', [])
                        base_p0 = note['residual_p0']
                        logger.info(f'[ConsistencyRepairer] Part {part_num} 定点修复后残留 '
                                    f'{note["residual_p0"]} 个 P0（非名称 '
                                    f'{note.get("residual_non_name_p0")} 个），以修复稿为 base '
                                    f'继续修复（定点编辑优先，失败落重写）')
                    else:
                        # 已通过（residual<=0）或无 base（旧形态）：维持现状
                        for _k in ('_base_text', '_base_logic', '_base_cons', '_base_pairs'):
                            note.pop(_k, None)
                        # R4-3: 真实首检数随 note 上行（修复通过时 entry 的结果
                        # 已被重审值替换，聚合器需要它计算 first_pass_p0 预算护栏）
                        note.setdefault('first_pass_p0', p0)
                        return note
                else:
                    logger.info(f'[ConsistencyRepairer] Part {part_num} 定点修复不可用，落回编辑/重写')
            else:
                logger.info(f'[ConsistencyRepairer] Part {part_num} 名称类 P0 但配对推导为空（歧义即放弃），走编辑/重写')

        # R6-5（S3）: 定点编辑前置尝试（SEARCH/REPLACE 锚块；每 Part ≤1 次，
        # 失败/劣化只回退编辑层，落回全量重写——总预算不涨）
        edit_note = await self._targeted_edit_repair(
            part_num, part_text, base_p0, logic_result, consistency_result,
            state_mock, registry, base_text=base_text, base_logic=base_logic,
            base_cons=base_cons, applied_name_pairs=base_pairs)
        if edit_note is not None and edit_note.get('revision_passed'):
            for _k in ('_base_text', '_base_logic', '_base_cons'):
                edit_note.pop(_k, None)
            edit_note.setdefault('first_pass_p0', p0)
            return edit_note
        if edit_note is not None:
            # 编辑未通过（劣化已回退 / 部分改善已保留）：带最新 base 继续
            base_text = edit_note.pop('_base_text', base_text)
            base_logic = edit_note.pop('_base_logic', base_logic)
            base_cons = edit_note.pop('_base_cons', base_cons)
            base_p0 = edit_note.get('residual_p0', base_p0)

        note = await self._rewrite_repair(
            part_num, part_text, base_p0, logic_result, consistency_result,
            state_mock, registry, base_text=base_text, base_logic=base_logic,
            base_cons=base_cons, applied_name_pairs=base_pairs)
        note.setdefault('first_pass_p0', p0)
        return note

    # ----------------- R4-2: 姓名漂移定点修复 -----------------

    async def _spotfix_names(self, part_num: int, part_text: str, pairs: list,
                             registry: dict, p0_before: int,
                             logic_result: dict, consistency_result: dict,
                             state_mock) -> dict | None:
        """姓名定点修复。返回 note dict；返回 None 表示放弃定点修复（落回重写）。

        R5-2: 重审后发现**新的**名称类 P0 且有界内再定点一次（硬顶 1 次，
        防震荡）—— 复检发现的名册外写法此前在结构上没有修复路径可接。
        """
        s = self.service
        # R5-2: 每次调用重置（每 Part 一次修复尝试最多 1 次额外定点）
        self._spotfix_retry_used = False
        departed_names = [n for n in (s.data.get('character_state_track') or {}) if n]
        gated = apply_safety_gates(pairs, registry, departed_names)
        if not gated:
            return None
        new_text, ok, reason = apply_name_spotfix(part_text, gated)
        if not ok:
            logger.info(f'[ConsistencyRepairer] Part {part_num} 定点替换校验失败（{reason}），放弃定点修复')
            return None

        # 重审 Logic + Consistency（Emotion 不参与，不影响其评审结果）
        new_logic = await self._re_review(self.logic_agent, 'logic', part_num, new_text, state_mock)
        new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, new_text, state_mock)
        residual_p0 = count_p0(new_logic, new_cons)

        # R5-2: 重审仍不过且发现新的名称类 P0 → 有界再定点一次（硬顶 1 次）。
        # 只走既有纯函数与既有 _re_review，零新 LLM 调用形态。
        if residual_p0 > 0 and not self._spotfix_retry_used:
            self._spotfix_retry_used = True
            if has_name_issue(new_cons, registry):
                logger.info(f'[ConsistencyRepairer] Part {part_num} 重审发现新的名称类 P0，'
                            f'尝试二次定点修复（有界 1 次）')
                pairs2 = derive_name_pairs(new_text, new_cons, registry)
                gated2 = apply_safety_gates(pairs2, registry, departed_names)
                if gated2:
                    fixed2, ok2, reason2 = apply_name_spotfix(new_text, gated2)
                    if ok2:
                        new_logic = await self._re_review(self.logic_agent, 'logic', part_num, fixed2, state_mock)
                        new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, fixed2, state_mock)
                        residual_p0 = count_p0(new_logic, new_cons)
                        new_text = fixed2  # 后续保留/回退分支都用修复后文本判定
                        gated = gated + gated2
                    else:
                        logger.info(f'[ConsistencyRepairer] Part {part_num} 二次定点校验失败（{reason2}），沿用首轮结果')

        spot_meta = {
            'type': 'name_spotfix',
            'wrong_name': '|'.join(p['wrong'] for p in gated),
            'right_name': '|'.join(p['right'] for p in gated),
            'pair_source': '|'.join(sorted({p['source'] for p in gated})),
        }
        trigger = 're_review' if self._spotfix_retry_used else 'first_pass'
        if residual_p0 <= 0:
            # 通过：落盘定点修复稿（与 Phase 3 相同的 checkpoint 路径）
            summary = truncate(new_text, n=200, suffix='...')
            s._save_chunk_progress(part_num, new_text, summary)
            part_key = str(part_num)
            state_mock.parts[part_key] = new_text
            state_mock.final_draft[part_key] = new_text
            note = {'revision_attempted': True, 'revision_passed': True,
                    'revision_spotfixed': True, 'residual_p0': 0,
                    'logic_result': new_logic, 'consistency_result': new_cons}
            # R5-2: 重审通过的定点修复 → 违禁词典沉淀（applied_verified=True）
            record_name_pairs(s.data, gated, part_num, trigger,
                              applied_verified=True, gates_passed=True)
            self._append_revision_log(part_num, p0_before, note, extra=spot_meta,
                                      trigger=trigger)
            await self._emit_log(
                f'✅ Part {part_num} 姓名定点修复完成'
                f'（{spot_meta["wrong_name"]}→{spot_meta["right_name"]}，'
                f'重审 P0 归零，零重写零内容损失）')
            return note

        # 仍不过：R6-2（S2）修复动作分层持久化 —— 名称修复本身安全（过 4 闸 +
        # 计数恒等式）→ 独立保留；残留的是"其他" P0，不是本修复的错
        # （guardrails OnFailAction: 每个 validator 独立处置，一个 FIX 成功的
        # 修复不因同批次其他校验仍失败而被回滚）。此前整篇回退原文，reval 实证
        # Part 5"尘儿→林尘"白修：确定性安全修复被两个它管不到的 logic P0 拖累。
        residual_non_name_p0 = _count_non_name_p0(new_logic, new_cons)
        summary = truncate(new_text, n=200, suffix='...')
        s._save_chunk_progress(part_num, new_text, summary)
        part_key = str(part_num)
        state_mock.parts[part_key] = new_text        # 快照同步：后续 Part 评审要看修复稿
        state_mock.final_draft[part_key] = new_text
        note = {'revision_attempted': True, 'revision_passed': False,
                'revision_spotfixed': True, 'partial_applied': True,
                'residual_p0': residual_p0,
                'residual_non_name_p0': residual_non_name_p0,
                # 基线仅供 maybe_repair_part 转交 _rewrite_repair 使用，以下划线
                # 开头，maybe_repair_part 必须在合并进 per_part_results 前 pop 掉
                # （不得进聚合口径）
                '_base_logic': new_logic, '_base_cons': new_cons,
                '_base_text': new_text,
                '_base_pairs': [dict(p) for p in gated]}
        # R5-2: 过闸但重审未整体通过 → 沉淀为 advisory（applied_verified 不置位；
        # recover_drift_dict_from_revision_log 按 revision_passed 恢复，保持 False）
        record_name_pairs(s.data, gated, part_num, trigger, gates_passed=True)
        # R6-2: 双留痕 —— revision_log（partial_applied + 非名称残留数，只增不改）
        # + name_audit_log（action='partial_applied'；trigger 不是 'final_audit'，
        # summarize_name_audit 的 latest-entry 口径只认 final_audit，不影响 G4）
        self._append_revision_log(
            part_num, p0_before, note,
            extra=dict(spot_meta, partial_applied=True,
                       residual_non_name_p0=residual_non_name_p0),
            trigger=trigger)
        append_audit_log(s.data, {
            'part': part_num,
            'wrong': '|'.join(p['wrong'] for p in gated),
            'right': '|'.join(p['right'] for p in gated),
            'count_before': '|'.join(str(part_text.count(p['wrong'])) for p in gated),
            'count_after': '|'.join(str(new_text.count(p['wrong'])) for p in gated),
            'action': 'partial_applied', 'trigger': trigger,
            'pair_source': '|'.join(sorted({p['source'] for p in gated})),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')})
        await self._emit_log(
            f'🔧 Part {part_num} 姓名定点修复已独立落盘'
            f'（{spot_meta["wrong_name"]}→{spot_meta["right_name"]}，残留 '
            f'{residual_non_name_p0} 个非名称 P0 转全文重写，修复不连坐）')
        return note

    # ----------------- R8-P0-1（S1）: 退场角色分类器接线（零 LLM，毫秒级） -----------------

    def _departed_ledger(self) -> dict:
        """退场账本（earliest_departure_parts：最早退场 Part，非 last-write-wins）。

        数据源 s.data 的 established_facts + characters 名（防常见词误报）；
        kill-switch KML_DEPARTED_CLASSIFY=0 → {}（分类链路整体关停，行为
        退化为 R6-5）。异常 fail-open 返回 {}（不影响主流程）。
        """
        if os.environ.get('KML_DEPARTED_CLASSIFY', '1') == '0':
            return {}
        s = self.service
        try:
            facts_raw = s.data.get('established_facts')
            char_names = [c.get('name', '') for c in (s.data.get('characters') or [])
                          if isinstance(c, dict) and c.get('name')]
            if not char_names:
                registry = s.data.get('name_registry') or {}
                char_names = [n for n in registry if isinstance(n, str)]
            return earliest_departure_parts(facts_raw, char_names) or {}
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] 退场账本派生失败（不影响主流程）: {e}')
            return {}

    def _departed_anchors(self, part_text: str, part_num: int) -> dict:
        """R8-P0-1（S1）: 本 Part 退场角色 illegal occurrence 的 span 表。

        分类在编辑发生前对 base_text 运行（终审时点注入无意义——R6-6 裁定同款）；
        每个 illegal occurrence 落 name_audit_log（action='departed_classified'，
        trigger='repair' 非 final_audit，不进 summarize latest-entry 口径、
        不影响 G4），供 Tester 统计"分类器 vs LLM 判定"一致率。

        Returns:
            {角色: [illegal span, ...]}（span 逐字来自正文、全文唯一、≥15 字）
        """
        ledger = self._departed_ledger()
        if not ledger or not isinstance(part_text, str) or not part_text:
            return {}
        s = self.service
        out: dict = {}
        for name, entry in ledger.items():
            try:
                items = classify_departed_occurrences(part_text, entry, part_num=part_num)
            except Exception as e:
                logger.info(f'[ConsistencyRepairer] 退场分类异常（{name}，跳过该角色）: {e}')
                continue
            illegal = [i for i in items if i['kind'] == 'illegal']
            for i in illegal:
                append_audit_log(s.data, {
                    'part': part_num, 'wrong': '', 'right': name,
                    'count_before': 1, 'count_after': 1,
                    'action': 'departed_classified', 'trigger': 'repair',
                    'pair_source': 'departed_classify', 'kind': 'illegal',
                    'reason': i['reason'], 'span': (i['span'] or '')[:40],
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')})
            if items:
                append_audit_log(s.data, {
                    'part': part_num, 'wrong': '', 'right': name,
                    'count_before': len(items), 'count_after': len(illegal),
                    'action': 'departed_classified', 'trigger': 'repair',
                    'pair_source': 'departed_classify_summary', 'kind': 'summary',
                    'reason': f'legal={len(items) - len(illegal)},illegal={len(illegal)}',
                    'dep_part': entry.get('dep_part'),
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')})
            # 只收非空 span（无合规 span 的 illegal occurrence 不产 anchor）
            spans = [i['span'] for i in illegal if i['span']]
            if spans:
                out[name] = spans
        return out

    def _departed_spec_block(self, departed_anchors: dict) -> str:
        """R8-P0-1（S1）: 退场角色修正规范段（02_review §1.1.7 模板逐字采用）。

        编辑路径 user prompt 在 brief 之后插入；同时呈现"首次死亡 Part N"与
        "名册标注 Part M 死亡"两个事实（§1.1.2 强制修正）。
        """
        if not departed_anchors:
            return ''
        ledger = self._departed_ledger()
        lines = ['\n## 退场角色修正规范（本 Part 适用）']
        for name in departed_anchors:
            entry = ledger.get(name) or {}
            lines.append(
                f'角色"{name}"已退场：首次死亡 Part {entry.get("dep_part", "?")}'
                f'（记录：{entry.get("earliest_record", "")}）；'
                f'名册标注 Part {entry.get("last_dep_part", "?")} 死亡——严禁出场。')
        lines.append('本 Part 中该角色只允许以合法形态存在：碑林/碑影模仿其形貌或声音、回忆、影像、'
                     '他人提及、残留之念/执念残像。')
        lines.append('修正方向：把实体行动/直接引语归因/参战改写为"碑林以他的形貌/声音……"式归因'
                     '（例："林渊冷笑一声，抬手压下" → "碑林模仿着他的声线冷笑，借他的形貌抬手压下"）；')
        lines.append('不得删除该角色在剧情中的功能位（对抗关系、情绪功能、事件结果保留），不得改变剧情走向，')
        lines.append('不得引入角色名册之外的任何姓名。')
        return '\n'.join(lines) + '\n'

    # ----------------- R6-5（S3）: SEARCH/REPLACE 定点编辑修复 -----------------

    async def _targeted_edit_repair(self, part_num: int, part_text: str, p0_before: int,
                                    logic_result: dict, consistency_result: dict,
                                    state_mock, registry: dict,
                                    base_text: str = None, base_logic: dict = None,
                                    base_cons: dict = None,
                                    applied_name_pairs: list = None) -> dict | None:
        """R6-5（S3）: SEARCH/REPLACE 定点编辑（全量重写的前置尝试，每 Part ≤1 次）。

        链路：spotfix（S2）→ 本方法（S3）→ _rewrite_repair（S2 签名），每段
        独立重审、独立判定；编辑失败只回退编辑层，不改变 MAX_REWRITE_ROUNDS=2
        总预算。

        触发条件（全部满足）：count_p0(base_logic, base_cons) ≤3 且每个 P0
        issue 都有唯一 anchor（_edit_trigger_ok，纯确定性）。

        Returns:
            None —— 未触发 / 无有效编辑块（调用方带原 base 落全量重写）；
            note（revision_passed=True）—— 编辑后重审 P0 归零（调用方返回）；
            note（revision_passed=False）—— 劣化已回退 base_text / 部分改善
              已保留，note 携带最新 _base_*（调用方 pop 后带最新 base 继续）。
        """
        s = self.service
        base_text = base_text if base_text is not None else (part_text or '')
        round_logic = base_logic if base_logic is not None else logic_result
        round_cons = base_cons if base_cons is not None else consistency_result
        # R8-P0-1（S1）: 退场角色 illegal span 表（编辑前对 base_text 分类，
        # 零 LLM；kill-switch 关停时 {} → 触发判定退化为 R6-5 逐字行为）
        departed_anchors = self._departed_anchors(base_text, part_num)
        registry_names = [n for n in (registry or {}) if isinstance(n, str)]
        departed_names = [n for n in (s.data.get('character_state_track') or {}) if n]
        skip_names = registry_names + departed_names
        ok, anchors = _edit_trigger_ok(base_text, round_logic, round_cons,
                                       departed_anchors, skip_names=skip_names)
        subset = False
        if not ok:
            # R8-4（S4）: departed 类子集编辑旁路（P0>3 但 departed 类 ≤3 且
            # 全带 illegal span、其余 P0 全带 anchor）——服务 Part 17 的
            # P0=4 死锁；非 departed 类 P0>3 不开放
            ok, anchors = _edit_subset_trigger_ok(base_text, round_logic, round_cons,
                                                  departed_anchors, skip_names=skip_names)
            subset = ok
        if not ok:
            logger.info(f'[ConsistencyRepairer] Part {part_num} 定点编辑触发条件不满足'
                        f'（P0 数 >{_EDIT_MAX_BLOCKS} 或缺唯一 anchor），落回全文重写')
            return None
        brief = self._build_revision_brief(
            part_num, round_logic, round_cons, part_text=base_text,
            applied_name_pairs=applied_name_pairs, numbered=True,
            departed_only=subset, departed_names=list(departed_anchors))
        user_prompt = (
            brief
            + self._departed_spec_block(departed_anchors)
            + f'\n\n## Part {part_num} 全文（编辑对象）\n{base_text}'
            + '\n\n请按编辑协议输出 SEARCH/REPLACE 编辑块'
              '（每个块前注明针对的问题编号；最多 3 个块）。')
        try:
            raw = await asyncio.to_thread(
                call_llm, TARGETED_EDIT_SYSTEM, user_prompt, 0.2,
                get_json_max_tokens(), 'targeted_edit', False, None,
                s.work_id, None)
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] Part {part_num} 定点编辑调用失败'
                        f'（落回全文重写）: {e}')
            return None
        new_text, applied, failures = _apply_targeted_edits(base_text, raw)
        edit_meta = {'type': 'targeted_edit', 'applied_blocks': applied,
                     'failed_blocks': len(failures), 'anchor_count': len(anchors)}
        if subset:
            # R8-4（S4）: 子集编辑模式标记（只编辑 departed 类 P0，纯观测）
            edit_meta['edit_subset'] = True
        if applied == 0:
            logger.info(f'[ConsistencyRepairer] Part {part_num} 编辑块全部校验失败'
                        f'（{len(failures)} 处：{failures[:3]}），落回全文重写')
            return None

        # 编辑后走既有四段式：双 agent 重审 → 劣化回退 / 保留
        new_logic = await self._re_review(self.logic_agent, 'logic', part_num, new_text, state_mock)
        new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, new_text, state_mock)
        residual_p0 = count_p0(new_logic, new_cons)

        if is_revision_degraded(p0_before, residual_p0, round_cons, new_cons,
                                round_logic, new_logic):
            # 回退编辑层：保留 base_text（原文/spotfix 稿从未离开）
            s._save_chunk_progress(part_num, base_text, truncate(base_text, n=200, suffix='...'))
            note = {'revision_attempted': True, 'revision_passed': False,
                    'revision_edited': True, 'residual_p0': residual_p0,
                    'revision_degraded': True,
                    '_base_logic': new_logic, '_base_cons': new_cons,
                    '_base_text': base_text}
            self._append_revision_log(part_num, p0_before, note, extra=edit_meta)
            await self._emit_log(
                f'↩️ Part {part_num} 定点编辑后劣化（{p0_before}→{residual_p0} 或引入'
                f'新问题类别），已回退编辑层（{applied} 块未保留）')
            return note

        if residual_p0 <= 0:
            # 通过：落盘编辑稿 + state_mock 快照同步
            s._save_chunk_progress(part_num, new_text, truncate(new_text, n=200, suffix='...'))
            part_key = str(part_num)
            state_mock.parts[part_key] = new_text
            state_mock.final_draft[part_key] = new_text
            note = {'revision_attempted': True, 'revision_passed': True,
                    'revision_edited': True,
                    'logic_result': new_logic, 'consistency_result': new_cons}
            self._append_revision_log(part_num, p0_before, note, extra=edit_meta)
            await self._emit_log(
                f'✅ Part {part_num} 定点编辑修复完成（{applied} 块，重审 P0 归零，'
                f'{len(new_text)} 字，块外文本逐字节未动）')
            return note

        # 未归零但未劣化（严格改善）：保留编辑稿，带最新 base 继续落重写
        s._save_chunk_progress(part_num, new_text, truncate(new_text, n=200, suffix='...'))
        part_key = str(part_num)
        state_mock.parts[part_key] = new_text
        state_mock.final_draft[part_key] = new_text
        note = {'revision_attempted': True, 'revision_passed': False,
                'revision_edited': True, 'residual_p0': residual_p0,
                '_base_logic': new_logic, '_base_cons': new_cons,
                '_base_text': new_text}
        self._append_revision_log(part_num, p0_before, note, extra=edit_meta)
        await self._emit_log(
            f'🔁 Part {part_num} 定点编辑严格改善（{p0_before}→{residual_p0}），'
            f'保留编辑稿并继续全文重写')
        return note

    # ----------------- R4-5: 全文重写四段式 -----------------

    async def _rewrite_repair(self, part_num: int, part_text: str, p0_before: int,
                              logic_result: dict, consistency_result: dict,
                              state_mock, registry: dict,
                              base_text: str = None, base_logic: dict = None,
                              base_cons: dict = None,
                              applied_name_pairs: list = None) -> dict:
        """四段式：分流（调用方已完成）→ 变坏回退 → 条件性第二跳 → 失败汇总。

        R6-2（S2）base 参数（与 S3 一次设计完，默认 None = 既有 15 项调用零改）：
        - base_text=None → 用 part_text；否则以 base_text 为重写的"当前文本"
          （spotfix 修复稿）—— brief 姓名指令段计数、产物下限 min_acceptable、
          落盘/回退分支的"原文"一律用 base_text（原文从未离开，回退代价小）；
        - base_logic/base_cons=None → 用传入的 logic_result/consistency_result；
          否则为效审后基线（spotfix 重审结果）—— 劣化判据与二轮条件比它；
        - p0_before 即劣化判据与二轮条件的基线（S2 链路传 spotfix 后残留数）；
        - applied_name_pairs：spotfix 已应用的配对（brief 增"姓名已按名册
          归一化"指令行，防重写把名字改回去）。
        """
        s = self.service
        base_text = base_text if base_text is not None else (part_text or '')
        round_logic = base_logic if base_logic is not None else logic_result
        round_cons = base_cons if base_cons is not None else consistency_result
        introduced: list = []
        current_p0_before = p0_before
        departed_names = [n for n in (s.data.get('character_state_track') or {}) if n]
        # 名称类指令（配对存在但闸未过 / 无名称类 issue 时为空）—— 对 base_text
        # 推导（已修复的名字 count=0 自然不产配对，由 applied_name_pairs 行兜底）
        name_pairs = derive_name_pairs(base_text, consistency_result, registry)

        for round_no in range(1, MAX_REWRITE_ROUNDS + 1):
            brief = self._build_revision_brief(
                part_num, round_logic, round_cons,
                name_pairs=name_pairs, introduced_problems=introduced,
                part_text=base_text, applied_name_pairs=applied_name_pairs)
            new_text, rewrite_error = await self._rewrite_once(part_num, brief)

            # 重写产物不可用（空/过短/异常）—— 保留原文（base_text），仅留痕
            min_acceptable = max(500, len(base_text) // 3)
            if not new_text or len(new_text) < min_acceptable:
                note = {'revision_attempted': True, 'revision_passed': False,
                        'revision_error': rewrite_error or 'rewrite_empty_or_too_short'}
                self._append_revision_log(part_num, p0_before, note)
                await self._emit_log(f'↩️ Part {part_num} 重写产物不可用，保留原文')
                return note

            # 重审 Logic + Consistency（Emotion 不参与，不影响其评审结果）
            new_logic = await self._re_review(self.logic_agent, 'logic', part_num, new_text, state_mock)
            new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, new_text, state_mock)
            residual_p0 = count_p0(new_logic, new_cons)

            # R5-2: 重审稿的姓名先修后判 —— 重写引入名字问题此前只能整篇回退
            # （P0-1 根因 2）。每轮最多 1 次（局部布尔量；MAX_REWRITE_ROUNDS=2
            # → 最多 2 次额外双审），修完原样进入既有劣化/保留/二跳判定。
            name_fix_used = False
            name_fix_pairs: list = []
            if has_name_issue(new_cons, registry) and not name_fix_used:
                name_fix_used = True
                rpairs = derive_name_pairs(new_text, new_cons, registry)
                rgated = apply_safety_gates(rpairs, registry, departed_names)
                if rgated:
                    rfixed, rok, rreason = apply_name_spotfix(new_text, rgated)
                    if rok:
                        new_logic = await self._re_review(self.logic_agent, 'logic', part_num, rfixed, state_mock)
                        new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, rfixed, state_mock)
                        residual_p0 = count_p0(new_logic, new_cons)
                        new_text = rfixed
                        name_fix_pairs = rgated
                        # 先按未验证沉淀；保留分支再置 applied_verified（只升不降）
                        record_name_pairs(s.data, rgated, part_num, 're_review',
                                          gates_passed=True)
                    else:
                        logger.info(f'[ConsistencyRepairer] Part {part_num} 重写稿姓名定点校验失败'
                                    f'（{rreason}），按重审原稿判定')
            trigger = 're_review' if name_fix_pairs else 'first_pass'

            # R4-5 变坏回退显式化：residual 没变好或出现首检没有的新 P0 类别
            if is_revision_degraded(current_p0_before, residual_p0,
                                    round_cons, new_cons, round_logic, new_logic):
                # R6-2: 回退的"原文"一律是 base_text（spotfix 稿，从未离开）
                s._save_chunk_progress(part_num, base_text, truncate(base_text, n=200, suffix='...'))
                note = {'revision_attempted': True, 'revision_passed': False,
                        'residual_p0': residual_p0, 'revision_degraded': True}
                self._append_revision_log(part_num, p0_before, note, trigger=trigger)
                await self._emit_log(
                    f'↩️ Part {part_num} 重写导致劣化'
                    f'（{current_p0_before}→{residual_p0} 或引入新问题类别），'
                    f'已回退原文')
                return note

            if residual_p0 <= 0:
                # 通过：落盘修订稿（与 Phase 3 相同的 checkpoint 路径）
                summary = truncate(new_text, n=200, suffix='...')
                s._save_chunk_progress(part_num, new_text, summary)
                part_key = str(part_num)
                state_mock.parts[part_key] = new_text
                state_mock.final_draft[part_key] = new_text
                note = {'revision_attempted': True, 'revision_passed': True,
                        'logic_result': new_logic, 'consistency_result': new_cons}
                if name_fix_pairs:
                    # 修复稿被保留 → 该配对确曾被"重审通过"的定点修复应用
                    note['revision_spotfixed'] = True
                    record_name_pairs(s.data, name_fix_pairs, part_num, 're_review',
                                      applied_verified=True, gates_passed=True)
                self._append_revision_log(part_num, p0_before, note, trigger=trigger)
                await self._emit_log(f'✅ Part {part_num} 重写修复完成（重审 P0 归零，{len(new_text)} 字）')
                return note

            # 未归零：仅当严格改善且未达硬顶时允许第二轮（brief 附新引入问题清单）
            if residual_p0 < current_p0_before and round_no < MAX_REWRITE_ROUNDS:
                introduced = newly_introduced_problems(round_logic, round_cons, new_logic, new_cons)
                round_logic, round_cons = new_logic, new_cons
                current_p0_before = residual_p0
                await self._emit_log(
                    f'🔁 Part {part_num} 第 {round_no} 轮重写严格改善'
                    f'（残留 {residual_p0} 个 P0），进入第 {round_no + 1} 轮'
                    f'（附第一轮新引入问题清单 {len(introduced)} 条）')
                continue

            # 未改善或已达 2 轮硬顶：保留原文（恢复落盘 base_text），仅留痕
            s._save_chunk_progress(part_num, base_text, truncate(base_text, n=200, suffix='...'))
            note = {'revision_attempted': True, 'revision_passed': False, 'residual_p0': residual_p0}
            self._append_revision_log(part_num, p0_before, note, trigger=trigger)
            await self._emit_log(f'↩️ Part {part_num} 重写后仍有 {residual_p0} 个 P0，保留原文')
            return note

        # 理论不可达（循环内每个分支都 return）；防御性保留原文（base_text）
        s._save_chunk_progress(part_num, base_text, truncate(base_text, n=200, suffix='...'))
        note = {'revision_attempted': True, 'revision_passed': False,
                'residual_p0': current_p0_before}
        self._append_revision_log(part_num, p0_before, note)
        return note

    async def _rewrite_once(self, part_num: int, brief: str) -> tuple:
        """调用 PartWriterAgent 带 brief 重写一次。返回 (new_text, error)。"""
        s = self.service
        try:
            from services.writing_service import TempStoryState
            from core.memory_manager import get_all_memory
            from core.agents.part_writer_agent import PartWriterAgent
            temp_state = TempStoryState(s.data, get_all_memory(), vector_store=s.vector_store)
            proxy = _RevisionStateProxy(temp_state, brief)
            writer = PartWriterAgent()
            writer.set_progress_callback(s.progress_callback)
            result = await asyncio.to_thread(writer.execute, proxy, part_num)
            if isinstance(result, dict) and result.get('success'):
                return (result.get('content', '') or ''), ''
            return '', 'rewrite_failed'
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] Part {part_num} 重写失败（保留原文）: {e}')
            return '', str(e)[:200]

    async def _re_review(self, agent, kind: str, part_num: int, part_text: str, state_mock) -> dict:
        try:
            return await asyncio.to_thread(agent.execute, state_mock, part_num, part_text)
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] 重审 {kind} Part {part_num} 失败: {e}')
            return self.service._review_failure(kind, part_num, e)

    def _build_revision_brief(self, part_num: int, logic_result: dict,
                              consistency_result: dict, name_pairs: list = None,
                              introduced_problems: list = None,
                              part_text: str = '',
                              applied_name_pairs: list = None,
                              numbered: bool = False,
                              departed_only: bool = False,
                              departed_names: list = None) -> str:
        """用 issues + 相关 established_facts + 角色名册生成 revision brief。

        R4-2 增强：名册段置尾（权威名源）；名称类指令具体到
        "错误写法'X'（本 Part 出现 N 次，例：<quote 锚点>）→ 正确写法'Y'；
        只改名字，其余一字不动"；logic V5 短协议无明细时明确要求对照名册与
        前文事实清单逐项自查人名/物品名/角色状态/时间线。
        R4-5 增强：第二轮 brief 附第一轮新引入的问题清单。
        R6-2（S2）增强：applied_name_pairs —— spotfix 已按名册归一化的配对，
        brief 增一行"姓名已按名册归一化（X→Y），重写时不得再引入名册外写法"
        （既有名册段兜底之上再加一层，防重写把已修的名字改回去）。
        R6-1（S1）：logic 明细（含 anchor 引文）非空时自动进 l_p0 明细行。
        R8-4（S4）departed_only：子集编辑模式——明细行只保留 departed 类
        P0，并明示"其余 N 个 P0 不在本次编辑范围，编辑后将继续修复"。
        """
        l_p0 = [i for i in ((logic_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0'
                and not i.get('_detail_placeholder')]  # R6-1: 占位项非可执行明细，不进 brief
        c_p0 = [i for i in ((consistency_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0']
        lines = []
        lr = logic_result or {}
        try:
            _l_p0_total = int(lr.get('p0_count') or 0)
        except (TypeError, ValueError):
            _l_p0_total = 0
        if departed_only:
            # R8-4（S4）: 子集编辑——只针对 departed 类 P0，其余明示不在本次范围
            _all_p0 = l_p0 + c_p0
            _dep = [i for i in _all_p0 if _is_departed_issue(i, departed_names or [])]
            _skip = len(_all_p0) - len(_dep)
            if _skip > 0:
                lines.append(f'- 本 Part 共检出 {len(_all_p0)} 个 P0，其中 {_skip} 个'
                             f'不在本次编辑范围（非退场角色类），编辑后将继续修复')
        # R6-1: 明细不全（真实明细数 < p0_count）时保留 generic 自查行 —— 明细
        # 只覆盖部分 P0，其余问题仍需模型对照名册与前文事实清单逐项自查
        if _l_p0_total and len(l_p0) < _l_p0_total:
            lines.append(f"- 逻辑审查判定 P0 共 {_l_p0_total} 处；结论: {(lr.get('verdict') or '')[:120]}")
            if not l_p0:
                lines.append('- 逻辑审查未给出问题明细：请对照下方角色名册与前文已确立事实清单，'
                             '逐项自查人名/物品名/角色状态/时间线，逐一修正')
            else:
                lines.append('- 逻辑审查明细不全：除上述明细外，请对照下方角色名册与前文已确立'
                             '事实清单，逐项自查其余人名/物品名/角色状态/时间线问题，逐一修正')
        if applied_name_pairs:
            applied_desc = '、'.join(
                f"'{p.get('wrong')}'→'{p.get('right')}'" for p in applied_name_pairs)
            lines.append(f'- 姓名已按名册归一化（{applied_desc}），'
                         f'重写时不得再引入名册外写法')
        issue_lines: list = []
        for i in l_p0 + c_p0:
            if departed_only and not _is_departed_issue(i, departed_names or []):
                continue  # R8-4（S4）: 子集编辑只列 departed 类明细
            desc = (i.get('description') or '').strip()
            sugg = (i.get('suggestion') or '').strip()
            loc = (i.get('location') or f'Part {part_num}').strip()
            if not desc:
                continue
            issue_lines.append(f"- [{loc}] {desc}" + (f' → 修正建议: {sugg}' if sugg else ''))
        if numbered:
            # R6-5（S3）: 编辑协议要求每个编辑块前注明问题编号
            lines.extend(f'{n}. {line[2:]}'
                         for n, line in enumerate(issue_lines, start=1))
        else:
            lines.extend(issue_lines)
        for p in (name_pairs or []):
            n = (part_text or '').count(p.get('wrong', ''))
            anchor = (p.get('evidence') or '').strip()
            if not anchor:
                idx = (part_text or '').find(p.get('wrong', ''))
                if idx >= 0:
                    anchor = (part_text or '')[max(0, idx - 10):idx + len(p['wrong']) + 10].replace('\n', ' ')
            lines.append(
                f"- 【姓名定点修正】错误写法'{p['wrong']}'（本 Part 出现 {n} 次"
                + (f'，例：{anchor}' if anchor else '')
                + f"）→ 正确写法'{p['right']}'；只改名字，其余一字不动")
        if introduced_problems:
            lines.append('- 【第一轮重写新引入的问题，本轮必须避免】')
            lines.extend(f'  - {x}' for x in introduced_problems)
        brief = '\n'.join(lines) if lines else '- 评审检出 P0 一致性问题，请对照前文事实清单全面自查。'
        # R4-2: 名册段（权威名源；与 facts 段并列）
        try:
            registry = self.service.data.get('name_registry') or {}
            if isinstance(registry, dict) and registry:
                departed = self.service.data.get('character_state_track') or {}
                roster = render_name_roster(
                    registry, departed if isinstance(departed, dict) else None)
                if roster:
                    brief += '\n\n' + roster
        except Exception as nr_err:
            logger.info(f'[ConsistencyRepairer] 名册段渲染失败（不影响 brief）: {nr_err}')
        facts_block = self._facts_block(part_num)
        if facts_block:
            brief += '\n\n' + facts_block
        return brief

    def _facts_block(self, part_num: int) -> str:
        """渲染 Part 1..N-1 的已确立事实（修订的权威基线）。"""
        facts = EstablishedFacts()
        raw = self.service.data.get('established_facts')
        if isinstance(raw, dict):
            try:
                facts.from_dict(raw)
            except Exception:
                return ''
        try:
            block = facts.render_for_prompt(before_part_num=part_num)
        except Exception:
            return ''
        if not block:
            return ''
        return '【前文已确立事实清单——重写内容不得与本表矛盾】\n' + block

    def _append_revision_log(self, part_num: int, p0_before: int, note: dict,
                             extra: dict = None, trigger: str = 'first_pass') -> None:
        """修订痕迹落盘（s.data['revision_log']，resume 可查）。

        R4-2: extra 携带定点修复专属字段（type='name_spotfix' +
        wrong_name/right_name/pair_source）—— 只增不改既有字段。
        R5-2: trigger 记录修复动作的检查点来源（first_pass 首检分诊 /
        re_review 复检二次定点 / final_audit 终审），全量跑后可统计各检查点
        贡献；旧条目补默认值 'first_pass' 保持形态稳定。
        """
        entry = {
            'part': part_num,
            'p0_before': p0_before,
            'revision_attempted': bool(note.get('revision_attempted')),
            'revision_passed': bool(note.get('revision_passed')),
            'residual_p0': note.get('residual_p0'),
            'revision_error': note.get('revision_error'),
            'trigger': trigger,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        # R4-5: 变坏回退事件显式落痕（仅劣化时出现，旧条目形态不变）
        if note.get('revision_degraded'):
            entry['revision_degraded'] = True
        if extra:
            for k, v in extra.items():
                if k not in entry:
                    entry[k] = v
        try:
            log = list(self.service.data.get('revision_log') or [])
            log.append(entry)
            self.service.data['revision_log'] = log
            self.service._save()
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] revision_log 落盘失败（不影响主流程）: {e}')
