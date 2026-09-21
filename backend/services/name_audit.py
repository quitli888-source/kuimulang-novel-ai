"""
R5-1: 违禁名词典 + 全稿确定性终审的数据层（vale/prh 模式，零 LLM，纯字符串运算）。

数据键（work JSON 新增 2 个顶层键，只增不改既有结构，R1-J 纪律）：
  name_drift_dict  {wrong_name: entry} —— 跨 Part 持久化的违禁词典（resume 不丢）
  name_audit_log   [{part, wrong, right, count_before, count_after, action,
                     trigger, pair_source, timestamp}] —— 终审处置留痕

词典四路沉淀：alias_candidate（名册已晋升候选）/ issue_quote·issue_character
（各检查点 derive_name_pairs 输出，R5-2 接线当场沉淀）/ name_spotfix
（revision_log 历史条目恢复，resume/历史 run 可用）/ final_audit（终审 B
触发的针对性重审产出）。

blocking 分层（02_review §2.1 裁定）：只有 blocking 残留进 G4；advisory
（单次无证据候选、闸未过、无指令佐证的 issue 配对）只告警不进 G4。

R6-6（S6）: 退场角色复现探测器（scan_departed_reappearance）—— 复用
derive_departed_characters 账本（R1-E 同源）对 final_draft 的 advisory 复扫，
findings 永不进 residual_blocking、永不自动改文本、永不进 G4（对齐 R5-1
探测器 B 的既定裁定范式）。

fail-open 纪律：任何解析异常（脏数据/非 dict/反序列化失败）返回空结果并
logger.info，绝不阻断管线；全程纯字符串运算。
"""
import re
import time

from core.established_facts import derive_departed_characters
from core.logger import get_logger

logger = get_logger('name_audit')

DRIFT_DICT_KEY = 'name_drift_dict'
AUDIT_LOG_KEY = 'name_audit_log'


def load_drift_dict(data: dict) -> dict:
    """读取违禁词典（容忍脏数据：data 非 dict / 词典非 dict / 条目非 dict）。"""
    raw = data.get(DRIFT_DICT_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {w: e for w, e in raw.items()
            if isinstance(w, str) and w and isinstance(e, dict)}


def is_blocking(entry: dict) -> bool:
    """blocking 分层公式（02_review §2.1，逐字实现）。

    blocking = applied_verified 或（directive_confirmed 且 gates_passed）或
    （source=='alias_candidate' 且带 evidence）；其余为 advisory。
    """
    if not isinstance(entry, dict):
        return False
    return bool(entry.get('applied_verified')
                or (entry.get('directive_confirmed') and entry.get('gates_passed'))
                or (entry.get('source') == 'alias_candidate' and entry.get('evidence')))


def record_name_pairs(data: dict, pairs: list, part_num: int, trigger: str,
                      applied_verified: bool = False,
                      gates_passed: bool = False) -> list:
    """沉淀/更新违禁词典条目（只升不降），返回受影响条目；调用方负责 s._save()。

    - parts_seen 去重追加；occurrences 每次调用累加（记录事件计数）；
    - directive_confirmed / gates_passed / applied_verified 只升不降；
    - right/source/evidence 首次出现的信息不被后写覆盖；
    - directive_confirmed 由来源推定：issue_quote 配对按定义已过位置型指令闸。

    Args:
        data: work JSON dict（原地写 DRIFT_DICT_KEY）
        pairs: derive_name_pairs 产物（[{wrong, right, source, evidence}]）
        part_num: 观察到的 Part 编号
        trigger: 沉淀触点（first_pass / re_review / final_audit / name_spotfix）
        applied_verified: 该配对是否曾被"重审通过"的定点修复真正应用过
        gates_passed: 该配对是否已过 4 条安全闸
    """
    if not isinstance(data, dict):
        return []
    drift = load_drift_dict(data)
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    affected: list = []
    for p in pairs or []:
        if not isinstance(p, dict):
            continue
        wrong = (p.get('wrong') or '').strip()
        right = (p.get('right') or '').strip()
        if not wrong or not right or wrong == right:
            continue
        source = (p.get('source') or '').strip()
        evidence = (p.get('evidence') or '').strip()[:30]
        entry = drift.get(wrong)
        if not isinstance(entry, dict):
            entry = {
                'wrong': wrong, 'right': right, 'source': source,
                'directive_confirmed': source == 'issue_quote',
                'gates_passed': bool(gates_passed),
                'applied_verified': bool(applied_verified),
                'evidence': evidence,
                'parts_seen': [], 'first_seen_part': part_num,
                'last_seen_part': part_num, 'occurrences': 0,
                'timestamp': now,
            }
            drift[wrong] = entry
        else:
            if right and not entry.get('right'):
                entry['right'] = right
            if source and not entry.get('source'):
                entry['source'] = source
            entry['directive_confirmed'] = (bool(entry.get('directive_confirmed'))
                                            or source == 'issue_quote')
            entry['gates_passed'] = bool(entry.get('gates_passed')) or bool(gates_passed)
            entry['applied_verified'] = (bool(entry.get('applied_verified'))
                                         or bool(applied_verified))
            if evidence and not entry.get('evidence'):
                entry['evidence'] = evidence
        if part_num not in entry['parts_seen']:
            entry['parts_seen'].append(part_num)
        entry['occurrences'] = int(entry.get('occurrences') or 0) + 1
        entry['last_seen_part'] = part_num
        entry['timestamp'] = now
        affected.append(entry)
    data[DRIFT_DICT_KEY] = drift
    return affected


def append_audit_log(data: dict, entry: dict) -> None:
    """name_audit_log 追加一条处置记录（容忍脏数据；落盘由调用方 _save 负责）。"""
    if not isinstance(data, dict) or not isinstance(entry, dict):
        return
    try:
        log = list(data.get(AUDIT_LOG_KEY) or [])
    except Exception:
        log = []
    log.append(entry)
    data[AUDIT_LOG_KEY] = log


# ----------------- 双探测器（纯函数，零 LLM，毫秒级） -----------------

def _part_sort_key(key):
    """final_draft 按键排序（数字键升序；非数字键垫后）。"""
    try:
        return (0, int(key), '')
    except (TypeError, ValueError):
        return (1, 0, str(key))


def _iter_facts(facts_raw):
    """把 dict / EstablishedFacts / list[Fact] / list[dict] 统一成可迭代事实。"""
    if facts_raw is None:
        return []
    facts = getattr(facts_raw, 'facts', None)
    if facts is None and isinstance(facts_raw, dict):
        facts = facts_raw.get('facts')
    if not isinstance(facts, (list, tuple)):
        return []
    return [f for f in facts if f is not None]


def _fact_field(fact, name: str):
    if isinstance(fact, dict):
        return fact.get(name)
    return getattr(fact, name, None)


def active_canonicals(facts_raw, registry: dict, part_num=None) -> set:
    """从 established_facts 取 subject∈canonicals 且未 superseded 的规范名集合。

    part_num 给定时只取 part_num <= 该值 的 fact —— "valid 区间覆盖该 Part"
    的近似（Fact 无有效区间字段，02_review §1.2 裁定口径；角色后期才在场时
    不会反向污染早期 Part）。容忍 dict/Fact/EstablishedFacts/None（fail-open）。
    """
    canonicals = ({n for n in registry if isinstance(n, str) and n}
                  if isinstance(registry, dict) else set())
    if not canonicals:
        return set()
    try:
        limit = int(part_num) if part_num is not None else None
    except (TypeError, ValueError):
        limit = None
    active: set = set()
    for f in _iter_facts(facts_raw):
        subject = _fact_field(f, 'subject')
        if not isinstance(subject, str) or subject not in canonicals:
            continue  # 非角色 subject（林家/玉佩 类）天然排除
        if _fact_field(f, 'superseded_by'):
            continue
        try:
            fp = int(_fact_field(f, 'part_num') or 0)
        except (TypeError, ValueError):
            fp = 0
        if limit is not None and fp > limit:
            continue
        active.add(subject)
    return active


def scan_forbidden(part_text: str, drift_dict: dict) -> list:
    """探测器 A（vale Terms 模式）：违禁对扫描。

    Returns:
        [{wrong, right, count, blocking, source, evidence}]（count =
        part_text.count(wrong)；词典条目非 dict 跳过）。
    """
    if not isinstance(part_text, str) or not part_text:
        return []
    findings: list = []
    for wrong, entry in (drift_dict or {}).items():
        if not isinstance(wrong, str) or not wrong or not isinstance(entry, dict):
            continue
        count = part_text.count(wrong)
        if count <= 0:
            continue
        findings.append({'wrong': wrong, 'right': entry.get('right', ''),
                         'count': count, 'blocking': is_blocking(entry),
                         'source': entry.get('source', ''),
                         'evidence': entry.get('evidence', '')})
    return findings


def canonical_absent(part_text: str, canonicals) -> list:
    """探测器 B 原料：规范名在（active）facts 有位但正文 0 次 → finding。

    实测校准（02_review §1.2）：Part 1/Part 2 都会命中（Part 1 为假阳性——
    角色以描写形式在场从未被点名），故 B 永久只告警、不进 G4、不改文本。
    """
    if not isinstance(part_text, str) or not part_text:
        return []
    out: list = []
    for name in (canonicals or []):
        if not isinstance(name, str) or not name:
            continue
        if part_text.count(name) == 0:
            out.append({'canonical': name, 'kind': 'canonical_absent'})
    return out


def audit_name_drift(final_draft: dict, drift_dict: dict, facts_raw, registry: dict) -> dict:
    """终审双探测器纯扫描（零 LLM）：A 违禁对 + B 规范名在位。

    跳过 '[Part ' 开头的失败占位与非 str 值（G1 占位不是交付文本）。
    任何脏数据（facts 非 dict / registry 为 list / final_draft 含占位）不抛
    异常，fail-open 返回空/跳过并 logger.info。

    Returns:
        {'scanned', 'findings', 'fixed': [], 'residual_blocking': [],
         'residual_advisory': [], 'canonical_absent': []}
        （fixed 由调用方的定点修复动作回填；残留分类含 A/B 全部 finding）
    """
    result = {'scanned': 0, 'findings': [], 'fixed': [],
              'residual_blocking': [], 'residual_advisory': [], 'canonical_absent': []}
    if not isinstance(final_draft, dict):
        logger.info('[name_audit] final_draft 非 dict，终审扫描跳过（fail-open）')
        return result
    drift = drift_dict if isinstance(drift_dict, dict) else {}
    if not isinstance(registry, dict):
        logger.info('[name_audit] registry 非 dict，探测器 B 跳过（fail-open）')
        registry = {}
    for key in sorted(final_draft.keys(), key=_part_sort_key):
        text = final_draft.get(key)
        if not isinstance(text, str) or not text.strip() or text.startswith('[Part '):
            continue
        try:
            part_num = int(key)
        except (TypeError, ValueError):
            continue
        result['scanned'] += 1
        for f in scan_forbidden(text, drift):
            rec = dict(f, part=part_num, kind='forbidden_name')
            result['findings'].append(rec)
            (result['residual_blocking'] if f['blocking']
             else result['residual_advisory']).append(rec)
        for f in canonical_absent(text, active_canonicals(facts_raw, registry, part_num)):
            rec = dict(f, part=part_num)
            result['findings'].append(rec)
            result['canonical_absent'].append(rec)
    return result


# ----------------- R6-6（S6）: 退场角色复现探测器（advisory-only） -----------------

# derive_departed_characters 账本值格式："PartN 死亡: 事实描述"（既有格式）
_DEPARTED_PART_RE = re.compile(r'^Part(\d+)\s')


def _parse_departed_part(record) -> int | None:
    """从退场账本值 "PartN 死亡: ..." 前缀解析退场 Part（解析失败返回 None）。"""
    if not isinstance(record, str):
        return None
    m = _DEPARTED_PART_RE.match(record.strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def scan_departed_reappearance(final_draft: dict, facts_raw, character_names) -> list:
    """R6-6（S6）: 退场角色复现确定性探测器（advisory-only，零 LLM，毫秒级）。

    对 derive_departed_characters(facts_raw, character_names) 账本中每个角色，
    扫 final_draft 中 part > 退场 Part 的正文 canonical 名出现处；count >= 2
    才报（阈值缓释回忆/他人提及的合法形式）；samples = ±20 字上下文 ×2。

    与 R1-E 预检的边界：R1-E 扫的是 Phase 3 修复前 parts；本探测器扫的是
    终审时点的 final_draft（修复+风格优化后的交付文本），findings 与 name
    audit 同批落 name_audit_log 供 Round 7 统计"确定检出 vs LLM 判定"一致率。

    Args:
        final_draft: work JSON 的 final_draft（{part: text}，容忍脏数据）
        facts_raw: established_facts（dict / EstablishedFacts / list[Fact] / None）
        character_names: Phase 2 正式角色名可迭代对象（防常见词误报）

    Returns:
        [{part, character, count, samples, kind: 'departed_reappearance'}, ...]
        （findings 永不进 residual_blocking、永不自动改文本、永不进 G4）
    """
    if not isinstance(final_draft, dict):
        logger.info('[name_audit] final_draft 非 dict，退场复现扫描跳过（fail-open）')
        return []
    # derive_departed_characters 需要 EstablishedFacts 实例或其 .facts 列表
    # （属性访问）—— work JSON 的 dict 形态先经 from_dict 归一（容忍脏数据）
    facts_obj = facts_raw
    if isinstance(facts_raw, dict):
        try:
            from core.established_facts import EstablishedFacts
            facts_obj = EstablishedFacts()
            facts_obj.from_dict(facts_raw)
        except Exception as e:
            logger.info(f'[name_audit] established_facts 反序列化失败（fail-open）: {e}')
            return []
    try:
        departed = derive_departed_characters(facts_obj, character_names)
    except Exception as e:
        logger.info(f'[name_audit] derive_departed_characters 失败（fail-open）: {e}')
        return []
    if not isinstance(departed, dict) or not departed:
        return []
    findings: list = []
    for character, record in departed.items():
        if not isinstance(character, str) or not character:
            continue
        dep_part = _parse_departed_part(record)
        if dep_part is None:
            continue
        for key in sorted(final_draft.keys(), key=_part_sort_key):
            try:
                part_num = int(key)
            except (TypeError, ValueError):
                continue
            if part_num <= dep_part:
                continue  # 退场 Part 本身（含死亡场景）不算复现
            text = final_draft.get(key)
            if not isinstance(text, str) or not text.strip() or text.startswith('[Part '):
                continue
            count = text.count(character)
            if count < 2:
                continue  # 阈值缓释：1 次出现视为回忆/他人提及的合法形式
            samples: list = []
            idx = text.find(character)
            while idx != -1 and len(samples) < 2:
                lo = max(0, idx - 20)
                hi = min(len(text), idx + len(character) + 20)
                samples.append(text[lo:hi].replace('\n', ' '))
                idx = text.find(character, idx + 1)
            findings.append({'part': part_num, 'character': character,
                             'count': count, 'samples': samples,
                             'kind': 'departed_reappearance'})
            logger.info(f'[name_audit] 退场复现命中: Part {part_num} "{character}" '
                        f'×{count}（退场记录: {record}）——advisory 只告警不阻断')
    return findings


def recover_drift_dict_from_revision_log(data: dict) -> dict:
    """从 revision_log 的 name_spotfix 条目恢复违禁词典（resume/历史 run）。

    来源 name_spotfix：applied_verified 按该条目的 revision_passed 置位；
    gates_passed=True（历史上的定点修复按定义过了 4 条安全闸）。
    只增不改（record_name_pairs 只升不降语义）。
    """
    log = data.get('revision_log') if isinstance(data, dict) else None
    if not isinstance(log, list):
        return load_drift_dict(data)
    for entry in log:
        if not isinstance(entry, dict) or entry.get('type') != 'name_spotfix':
            continue
        try:
            part_num = int(entry.get('part'))
        except (TypeError, ValueError):
            part_num = 0
        pairs = []
        for wrong, right in zip(str(entry.get('wrong_name') or '').split('|'),
                                str(entry.get('right_name') or '').split('|')):
            wrong, right = wrong.strip(), right.strip()
            if wrong and right and wrong != right:
                pairs.append({'wrong': wrong, 'right': right,
                              'source': 'name_spotfix', 'evidence': ''})
        if pairs:
            record_name_pairs(data, pairs, part_num, 'name_spotfix',
                              applied_verified=bool(entry.get('revision_passed')),
                              gates_passed=True)
    return load_drift_dict(data)


def review_reports_name(review: dict, wrong: str) -> bool:
    """终审验证用：重审结果是否仍报**同一错误名**的名称类 P0。

    修复未生效/判错 → True（调用方回退该 Part 的 final_draft）。
    报其他名字的问题不阻断本配对（按 02_review §2.1 终审动作 1 的验证分工）。
    """
    for issue in (review or {}).get('issues') or []:
        if not isinstance(issue, dict) or issue.get('level') != 'P0':
            continue
        text = (f"{issue.get('description') or ''}{issue.get('location') or ''}"
                f"{issue.get('verdict') or ''}")
        if wrong and wrong in text:
            return True
    return False
