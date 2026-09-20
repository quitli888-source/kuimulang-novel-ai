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
import re
import time

from api.sse import EventType
from core.established_facts import EstablishedFacts
from core.logger import get_logger
from core.name_registry import promoted_candidates, render_name_roster
from core.text_utils import truncate

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
        if has_name_issue(consistency_result, registry):
            pairs = derive_name_pairs(part_text, consistency_result, registry)
            if pairs:
                note = await self._spotfix_names(
                    part_num, part_text, pairs, registry, p0,
                    logic_result, consistency_result, state_mock)
                if note is not None:
                    # R4-3: 真实首检数随 note 上行（修复通过时 entry 的结果已被
                    # 重审值替换，聚合器需要它计算 first_pass_p0 预算护栏）
                    note.setdefault('first_pass_p0', p0)
                    return note
                logger.info(f'[ConsistencyRepairer] Part {part_num} 定点修复不可用，落回全文重写')
            else:
                logger.info(f'[ConsistencyRepairer] Part {part_num} 名称类 P0 但配对推导为空（歧义即放弃），走全文重写')

        note = await self._rewrite_repair(
            part_num, part_text, p0, logic_result, consistency_result, state_mock, registry)
        note.setdefault('first_pass_p0', p0)
        return note

    # ----------------- R4-2: 姓名漂移定点修复 -----------------

    async def _spotfix_names(self, part_num: int, part_text: str, pairs: list,
                             registry: dict, p0_before: int,
                             logic_result: dict, consistency_result: dict,
                             state_mock) -> dict | None:
        """姓名定点修复。返回 note dict；返回 None 表示放弃定点修复（落回重写）。"""
        s = self.service
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

        spot_meta = {
            'type': 'name_spotfix',
            'wrong_name': '|'.join(p['wrong'] for p in gated),
            'right_name': '|'.join(p['right'] for p in gated),
            'pair_source': '|'.join(sorted({p['source'] for p in gated})),
        }
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
            self._append_revision_log(part_num, p0_before, note, extra=spot_meta)
            await s.emitter.emit(EventType.LOG, {
                'message': (f'✅ Part {part_num} 姓名定点修复完成'
                            f'（{spot_meta["wrong_name"]}→{spot_meta["right_name"]}，'
                            f'重审 P0 归零，零重写零内容损失）'),
                'work_id': s.work_id}, work_id=s.work_id)
            return note

        # 仍不过：回退保留原文（现有兜底语义不变），留痕供全量跑后审计
        s._save_chunk_progress(part_num, part_text, truncate(part_text, n=200, suffix='...'))
        note = {'revision_attempted': True, 'revision_passed': False,
                'revision_spotfixed': True, 'residual_p0': residual_p0}
        self._append_revision_log(part_num, p0_before, note, extra=spot_meta)
        await s.emitter.emit(EventType.LOG, {
            'message': (f'↩️ Part {part_num} 姓名定点修复后仍有 {residual_p0} 个 P0，'
                        f'回退保留原文（{spot_meta["wrong_name"]}→{spot_meta["right_name"]}）'),
            'work_id': s.work_id}, work_id=s.work_id)
        return note

    # ----------------- R4-5: 全文重写四段式 -----------------

    async def _rewrite_repair(self, part_num: int, part_text: str, p0_before: int,
                              logic_result: dict, consistency_result: dict,
                              state_mock, registry: dict) -> dict:
        """四段式：分流（调用方已完成）→ 变坏回退 → 条件性第二跳 → 失败汇总。"""
        s = self.service
        introduced: list = []
        round_logic, round_cons = logic_result, consistency_result
        current_p0_before = p0_before
        # 名称类指令（配对存在但闸未过 / 无名称类 issue 时为空）
        name_pairs = derive_name_pairs(part_text, consistency_result, registry)

        for round_no in range(1, MAX_REWRITE_ROUNDS + 1):
            brief = self._build_revision_brief(
                part_num, round_logic, round_cons,
                name_pairs=name_pairs, introduced_problems=introduced,
                part_text=part_text)
            new_text, rewrite_error = await self._rewrite_once(part_num, brief)

            # 重写产物不可用（空/过短/异常）—— 保留原文，仅留痕
            min_acceptable = max(500, len(part_text) // 3)
            if not new_text or len(new_text) < min_acceptable:
                note = {'revision_attempted': True, 'revision_passed': False,
                        'revision_error': rewrite_error or 'rewrite_empty_or_too_short'}
                self._append_revision_log(part_num, p0_before, note)
                await s.emitter.emit(EventType.LOG, {
                    'message': f'↩️ Part {part_num} 重写产物不可用，保留原文',
                    'work_id': s.work_id}, work_id=s.work_id)
                return note

            # 重审 Logic + Consistency（Emotion 不参与，不影响其评审结果）
            new_logic = await self._re_review(self.logic_agent, 'logic', part_num, new_text, state_mock)
            new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, new_text, state_mock)
            residual_p0 = count_p0(new_logic, new_cons)

            # R4-5 变坏回退显式化：residual 没变好或出现首检没有的新 P0 类别
            if is_revision_degraded(current_p0_before, residual_p0,
                                    round_cons, new_cons, round_logic, new_logic):
                s._save_chunk_progress(part_num, part_text, truncate(part_text, n=200, suffix='...'))
                note = {'revision_attempted': True, 'revision_passed': False,
                        'residual_p0': residual_p0, 'revision_degraded': True}
                self._append_revision_log(part_num, p0_before, note)
                await s.emitter.emit(EventType.LOG, {
                    'message': (f'↩️ Part {part_num} 重写导致劣化'
                                f'（{current_p0_before}→{residual_p0} 或引入新问题类别），'
                                f'已回退原文'),
                    'work_id': s.work_id}, work_id=s.work_id)
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
                self._append_revision_log(part_num, p0_before, note)
                await s.emitter.emit(EventType.LOG, {
                    'message': f'✅ Part {part_num} 重写修复完成（重审 P0 归零，{len(new_text)} 字）',
                    'work_id': s.work_id}, work_id=s.work_id)
                return note

            # 未归零：仅当严格改善且未达硬顶时允许第二轮（brief 附新引入问题清单）
            if residual_p0 < current_p0_before and round_no < MAX_REWRITE_ROUNDS:
                introduced = newly_introduced_problems(round_logic, round_cons, new_logic, new_cons)
                round_logic, round_cons = new_logic, new_cons
                current_p0_before = residual_p0
                await s.emitter.emit(EventType.LOG, {
                    'message': (f'🔁 Part {part_num} 第 {round_no} 轮重写严格改善'
                                f'（残留 {residual_p0} 个 P0），进入第 {round_no + 1} 轮'
                                f'（附第一轮新引入问题清单 {len(introduced)} 条）'),
                    'work_id': s.work_id}, work_id=s.work_id)
                continue

            # 未改善或已达 2 轮硬顶：保留原文（恢复落盘），仅留痕
            s._save_chunk_progress(part_num, part_text, truncate(part_text, n=200, suffix='...'))
            note = {'revision_attempted': True, 'revision_passed': False, 'residual_p0': residual_p0}
            self._append_revision_log(part_num, p0_before, note)
            await s.emitter.emit(EventType.LOG, {
                'message': f'↩️ Part {part_num} 重写后仍有 {residual_p0} 个 P0，保留原文',
                'work_id': s.work_id}, work_id=s.work_id)
            return note

        # 理论不可达（循环内每个分支都 return）；防御性保留原文
        s._save_chunk_progress(part_num, part_text, truncate(part_text, n=200, suffix='...'))
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
                              part_text: str = '') -> str:
        """用 issues + 相关 established_facts + 角色名册生成 revision brief。

        R4-2 增强：名册段置尾（权威名源）；名称类指令具体到
        "错误写法'X'（本 Part 出现 N 次，例：<quote 锚点>）→ 正确写法'Y'；
        只改名字，其余一字不动"；logic V5 短协议无明细时明确要求对照名册与
        前文事实清单逐项自查人名/物品名/角色状态。
        R4-5 增强：第二轮 brief 附第一轮新引入的问题清单。
        """
        l_p0 = [i for i in ((logic_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0']
        c_p0 = [i for i in ((consistency_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0']
        lines = []
        lr = logic_result or {}
        if lr.get('p0_count') and not l_p0:
            lines.append(f"- 逻辑审查判定 P0 共 {lr.get('p0_count')} 处；结论: {(lr.get('verdict') or '')[:120]}")
            lines.append('- 逻辑审查未给出问题明细：请对照下方角色名册与前文已确立事实清单，'
                         '逐项自查人名/物品名/角色状态/时间线，逐一修正')
        for i in l_p0 + c_p0:
            desc = (i.get('description') or '').strip()
            sugg = (i.get('suggestion') or '').strip()
            loc = (i.get('location') or f'Part {part_num}').strip()
            if not desc:
                continue
            lines.append(f"- [{loc}] {desc}" + (f' → 修正建议: {sugg}' if sugg else ''))
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
                             extra: dict = None) -> None:
        """修订痕迹落盘（s.data['revision_log']，resume 可查）。

        R4-2: extra 携带定点修复专属字段（type='name_spotfix' +
        wrong_name/right_name/pair_source）—— 只增不改既有字段。
        """
        entry = {
            'part': part_num,
            'p0_before': p0_before,
            'revision_attempted': bool(note.get('revision_attempted')),
            'revision_passed': bool(note.get('revision_passed')),
            'residual_p0': note.get('residual_p0'),
            'revision_error': note.get('revision_error'),
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
