"""
R5-P3-5.3: test_cost_persist.py —— 验证 cost_tracker 重启合并 + 双轨持久化。

核心断言：
  - record() 后 self.calls 有 N 条，summary 有 N 次调用
  - attach_work(work_id, work_cost_summary) 把 cost_summary["calls"] 反序列化叠加到 self.calls
  - 去重：重复 attach 不应叠加
  - get_summary() 返回 dict 含 'calls' 列表
  - 模拟重启：新 CostTracker 实例 attach_work 后 self.calls 等于历史数

既支持 pytest 也支持直接 python 跑。
"""
import sys
import tempfile
from pathlib import Path
from core.logger import get_logger
logger = get_logger('test_cost_persist')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

def test_record_increments_calls():
    """record() 3 次 → self.calls == 3 + summary['total_calls'] == 3"""
    from core.cost_tracker import CostTracker
    tracker = CostTracker()
    assert len(tracker.calls) == 0
    summary0 = tracker.get_summary()
    assert summary0['total_calls'] == 0
    assert summary0['calls'] == []
    assert 'calls' in summary0
    tracker.record('step-3.7-flash', 'part_writer', False, 100, 200, 300, 100)
    tracker.record('step-3.7-flash', 'plot_planner', False, 50, 150, 200, 80)
    tracker.record('step-3.7-flash', 'rolling_summary', False, 2000, 500, 2500, 1000)
    assert len(tracker.calls) == 3, f'record 3 次后应有 3 条，实际 {len(tracker.calls)}'
    summary3 = tracker.get_summary()
    assert summary3['total_calls'] == 3
    assert len(summary3['calls']) == 3
    logger.info('[test_record] PASS: record 3 次后 calls=3, summary.calls=3')

def test_attach_work_loads_history_from_summary():
    """R5-P0-3: attach_work(work_id, work_cost_summary) 把 cost_summary["calls"] 叠加到 self.calls

    模拟重启：CostTracker() 新实例 in-memory 空；
    attach_work(old_summary) → self.calls 应等于历史数
    """
    from core.cost_tracker import CostTracker
    old_tracker = CostTracker()
    with tempfile.TemporaryDirectory() as td:
        old_tracker.attach_work('work_history', Path(td))
        old_tracker.record('step-3.7-flash', 'part_writer', False, 100, 200, 300, 100)
        old_tracker.record('step-3.7-flash', 'plot_planner', False, 50, 150, 200, 80)
        old_tracker.record('step-3.7-flash', 'rolling_summary', False, 2000, 500, 2500, 1000)
        persisted = old_tracker.get_summary()
    assert len(persisted['calls']) == 3
    new_tracker = CostTracker()
    assert len(new_tracker.calls) == 0
    new_tracker.attach_work('work_history', persisted)
    assert len(new_tracker.calls) == 3, f'attach_work 应恢复 3 条历史，实际 {len(new_tracker.calls)}'
    logger.info('[test_attach_load] PASS: 新 tracker attach_work 后 calls=3')

def test_attach_work_dedup():
    """去重：相同 cost_summary 二次 attach 不应叠加"""
    from core.cost_tracker import CostTracker
    tracker = CostTracker()
    with tempfile.TemporaryDirectory() as td:
        tracker.attach_work('work_dedup', Path(td))
        tracker.record('step-3.7-flash', 'part_writer', False, 100, 200, 300, 100)
        tracker.record('step-3.7-flash', 'plot_planner', False, 50, 150, 200, 80)
        snapshot = tracker.get_summary()
    tracker.attach_work('work_dedup', snapshot)
    assert len(tracker.calls) == 2, f'重复 attach 应去重，实际 {len(tracker.calls)}'
    logger.info('[test_attach_dedup] PASS: 重复 attach 后仍 calls=2')

def test_restart_simulation_record_3_attach_3_then_record_1():
    """R5-P3-5.3 主断言：record 3 次 → attach_work → record 1 次 → calls == 4

    模拟重启合并：旧进程 3 次记录被新进程 attach_work 还原 + 新进程又记 1 次 → 总 4 条
    """
    from core.cost_tracker import CostTracker
    old_tracker = CostTracker()
    with tempfile.TemporaryDirectory() as td:
        old_tracker.attach_work('work_merge', Path(td))
        old_tracker.record('step-3.7-flash', 'part_writer', False, 100, 200, 300, 100)
        old_tracker.record('step-3.7-flash', 'plot_planner', False, 50, 150, 200, 80)
        old_tracker.record('step-3.7-flash', 'rolling_summary', False, 2000, 500, 2500, 1000)
        persisted = old_tracker.get_summary()
    assert len(persisted['calls']) == 3
    new_tracker = CostTracker()
    with tempfile.TemporaryDirectory() as td:
        new_tracker.attach_work('work_merge', persisted, Path(td))
        new_tracker.record('step-3.7-flash', 'milestone_summary', False, 3000, 2000, 5000, 1500)
    assert len(new_tracker.calls) == 4, f'重启合并 + 增量后应有 4 条，实际 {len(new_tracker.calls)}'
    summary = new_tracker.get_summary()
    assert summary['total_calls'] == 4
    assert len(summary['calls']) == 4
    logger.info(f'[test_restart_merge] PASS: record 3 + attach 3 + record 1 = calls={len(new_tracker.calls)}')

def test_get_summary_calls_field():
    """R5-P0-3: get_summary() 返回值必须含 'calls' 列表（用于 restart 合并 source of truth）"""
    from core.cost_tracker import CostTracker
    tracker = CostTracker()
    s0 = tracker.get_summary()
    assert 'calls' in s0, f"get_summary() 缺 'calls' 字段: keys={list(s0.keys())}"
    assert s0['calls'] == []
    tracker.record('step-3.7-flash', 'part_writer', False, 100, 200, 300, 100)
    s1 = tracker.get_summary()
    assert len(s1['calls']) == 1
    assert s1['calls'][0]['model'] == 'step-3.7-flash'
    logger.info("[test_get_summary_calls] PASS: get_summary() 返回值含 'calls' 字段")
if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_cost_persist.py —— R5-P3-5.3')
    logger.info('=' * 60)
    tests = [('record increments', test_record_increments_calls), ('attach loads history', test_attach_work_loads_history_from_summary), ('attach dedup', test_attach_work_dedup), ('restart merge', test_restart_simulation_record_3_attach_3_then_record_1), ('get_summary.calls field', test_get_summary_calls_field)]
    for name, fn in tests:
        logger.info(f'\n[{name}]')
        fn()
    logger.info('\n' + '=' * 60)
    logger.info('ALL PASS')
    logger.info('=' * 60)