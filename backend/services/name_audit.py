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

fail-open 纪律：任何解析异常（脏数据/非 dict/反序列化失败）返回空结果并
logger.info，绝不阻断管线；全程纯字符串运算。
"""
import time

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
