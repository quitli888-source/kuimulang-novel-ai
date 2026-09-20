"""
番茄小说AI创作系统 V4 - Token成本追踪器

追踪每次LLM调用的token消耗，统计总成本。
R3-P1-6: 增补 step-3.7-flash 定价（按 stepfun 公开市场参考价）；
         暴露 should_prompt_for_cost(work_id, threshold) 用于熔断 SSE 推 CONFIRM。
R4-P1-7: 增补 attach_work(work_id, persist_dir) —— 把 cost_tracker 绑定到作品，
         每次 record() 后把累计 calls 增量写回 work_id.cost.json；进程级单例
         跨重启清零可接受（落盘 history 是 source of truth）。
R5-P0-3: attach_work 新签名 (work_id, work_cost_summary) —— 反序列化 work JSON 中的
         cost_summary.calls 叠加到 self.calls（重启后 history 恢复）；
         _persist_path 改为 WORKS_DIR（与 works.py:{work_id}.json 同侧）。
         get_summary() 返回值增加 'calls' 字段（用于 restart 合并 source of truth）。
R17-P0-2: 节流写盘（每 N 条/T 秒 flush 一次）+ fcntl.flock 防多 worker 冲突；
         estimated=True 记录不写入磁盘（避免 600+ 估算污染持久化历史）。
"""
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional
from core.logger import get_logger
logger = get_logger('cost_tracker')

class CostTracker:
    """全局LLM调用成本追踪器"""
    MODEL_PRICING = {'step-3.7-flash': {'input': 0.15, 'output': 0.6, '_note': 'Step-3.7-Flash 市场参考价（按官方公开口径估算），待官方定价更新'}, 'deepseek-v3.2': {'input': 1.0, 'output': 2.0}, 'MiniMax-Text-01': {'input': 1.0, 'output': 4.0}, 'MiniMax-M3': {'input': 0.15, 'output': 0.6}, 'gpt-4o-mini': {'input': 0.15, 'output': 0.6}, 'gpt-4o': {'input': 2.5, 'output': 10.0}, 'default': {'input': 1.0, 'output': 2.0}}
    DEFAULT_COST_THRESHOLD_RMB = 50.0
    FLUSH_EVERY_N_RECORDS = 20
    FLUSH_EVERY_N_SECONDS = 30.0
    PERSIST_ESTIMATED = False

    def __init__(self):
        self.calls = []
        self._start_time = time.time()
        self._persist_work_id: Optional[str] = None
        self._persist_path: Optional[Path] = None
        self._persisted_summary: dict = {}
        self._last_flush_ts: float = time.time()
        self._last_flush_count: int = 0
        self._real_count: int = 0  # P0-80: O(1) 计数，替代 record() 里 O(N) 的 list 遍历
        self._dirty: bool = False
        # R4-P2-x: 线程锁 —— record/_flush_to_disk/_trackers 此前无保护，
        # 当前 async 单线程下侥幸安全，接入线程池后并发 append/flush 会损坏 calls。
        self._lock = threading.RLock()

    def attach_work(self, work_id: str, work_cost_summary: Optional[dict]=None, persist_dir: Optional[Path]=None) -> None:
        """R5-P0-3: 把 cost_tracker 绑定到 work_id，同时从 work JSON 的 cost_summary
        反序列化历史 calls 叠加到 self.calls（重启恢复 + 跨进程合并）。

        Args:
            work_id: 作品 ID。
            work_cost_summary: 作品 JSON 中 data["cost_summary"] 字典，含 'calls' 列表
                （R4-P1-7 时 _save 注入；R5 起 get_summary() 也内嵌 'calls'）。
                传 None 时只挂路径、不合并历史（兼容旧调用）。
            persist_dir: 持久化目录，None 时取 WORKS_DIR（与 works.py 同侧）。

        行为：
            1. _persist_path → {persist_dir}/{work_id}.cost.json
            2. 若 work_cost_summary 含有 'calls' 列表 → 反序列化叠加到 self.calls
               （去重按 timestamp+model+agent，避免重复叠加）
            3. record() 后 _flush_to_disk() 自动把 self.calls 写盘（增量 source of truth）
        """
        self._persist_work_id = work_id
        try:
            if persist_dir is None:
                from core.config import WORKS_DIR
                target_dir = Path(WORKS_DIR)
            else:
                target_dir = Path(persist_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            self._persist_path = target_dir / f'{work_id}.cost.json'
        except Exception as e:
            logger.info(f'[cost_tracker] attach_work 路径设置失败: {e}')
            self._persist_path = None
        if isinstance(work_cost_summary, dict):
            calls = work_cost_summary.get('calls')
            if isinstance(calls, list) and calls:
                existing_keys = {(c.get('timestamp'), c.get('model'), c.get('agent'), c.get('prompt_tokens'), c.get('completion_tokens')) for c in self.calls}
                added = 0
                added_real = 0
                for c in calls:
                    if not isinstance(c, dict):
                        continue
                    sig = (c.get('timestamp'), c.get('model'), c.get('agent'), c.get('prompt_tokens'), c.get('completion_tokens'))
                    if sig in existing_keys:
                        continue
                    self.calls.append(c)
                    existing_keys.add(sig)
                    added += 1
                    if not c.get('estimated'):
                        added_real += 1
                if added:
                    logger.info(f'[cost_tracker] attach_work 从 work_cost_summary 恢复 {added} 条历史')
                # P0-80: O(1) 计数器；用增量而非每次重新遍历 self.calls
                self._real_count += added_real
                self._last_flush_count = self._real_count
            self._persisted_summary = {k: v for k, v in work_cost_summary.items() if k != 'calls'}

    def record(self, model: str, agent: str, is_json: bool, prompt_tokens: int, completion_tokens: int, total_tokens: int, duration_ms: float, estimated: bool=False):
        """记录一次LLM调用。R7-P0-3: estimated=True 表示 token 数是基于文本长度估算（非 provider 上报）。
        R17-P0-2: 估算记录不入盘（避免污染持久化历史）；真实记录节流写盘（每 20 条或 30s）。

        P0-80 修复：用 self._real_count 计数器取代每次 O(N) 的 len([...]) 扫描；
        旧实现每次 record 都会遍历 self.calls，100 Part × 30 calls/Part = 45 万次比较。
        """
        is_estimated = bool(estimated)
        with self._lock:
            self.calls.append({'timestamp': round(time.time() - self._start_time, 1), 'model': model, 'agent': agent, 'is_json': is_json, 'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'total_tokens': total_tokens, 'duration_ms': round(duration_ms, 0), 'estimated': is_estimated})
            if not is_estimated:
                self._real_count += 1
            if is_estimated and (not self.PERSIST_ESTIMATED):
                return
            self._dirty = True
            should_flush = self._real_count - self._last_flush_count >= self.FLUSH_EVERY_N_RECORDS or time.time() - self._last_flush_ts >= self.FLUSH_EVERY_N_SECONDS
        if should_flush:
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """R4-P1-7: 把当前 self.calls 序列化为 JSON 写到 _persist_path。
        R17: 加 fcntl.flock 文件锁防多 worker 冲突；估算记录不写入磁盘。
        R4-P2-x: 原子替换（tmp + os.replace）+ 实例锁保护并发 flush。
        """
        if not self._persist_path or not self._dirty:
            return
        with self._lock:
            try:
                persist_calls = [c for c in self.calls if not c.get('estimated')] if not self.PERSIST_ESTIMATED else list(self.calls)
                payload = json.dumps(persist_calls, ensure_ascii=False, indent=2)
                try:
                    import fcntl
                    # R4-P2-x: 先写临时文件再 os.replace —— 此前 open(path,'w') 在 flock 之前
                    # 就截断文件，锁等待期间的读者会看到空/半截 .cost.json。
                    tmp_path = self._persist_path.with_suffix('.json.tmp')
                    with open(tmp_path, 'w', encoding='utf-8') as f:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                        try:
                            f.write(payload)
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    os.replace(tmp_path, self._persist_path)
                except (ImportError, AttributeError):
                    tmp_path = self._persist_path.with_suffix('.json.tmp')
                    tmp_path.write_text(payload, encoding='utf-8')
                    os.replace(tmp_path, self._persist_path)
                self._last_flush_ts = time.time()
                self._last_flush_count = len(persist_calls)
                self._dirty = False
            except Exception as e:
                logger.info(f'[cost_tracker] _flush_to_disk 失败（不影响主流程）: {e}')

    def force_flush(self) -> None:
        """R17: 强制立即 flush（用于 Part 写完 / 任务结束 / 关键 checkpoint）"""
        self._flush_to_disk()

    def get_summary(self) -> dict:
        """获取统计摘要

        R5-P0-3: 返回值增加 'calls' 字段（self.calls 拷贝），
        供 _save() / works.py:get_work 在持久化层合并使用。
        R8-P0-3 (Bug G): 返回值同时含 'estimated_cost' 别名（旧下游脚本 r7_real_test.py
        / real_e2e_smoke.py 历史遗留使用 'estimated_cost' 读，会拿到默认 0）。
        """
        if not self.calls:
            return {'total_calls': 0, 'total_tokens': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'estimated_cost_rmb': 0.0, 'estimated_cost': 0.0, 'total_duration_ms': 0, 'calls': []}
        total_prompt = sum((c['prompt_tokens'] for c in self.calls))
        total_completion = sum((c['completion_tokens'] for c in self.calls))
        total = total_prompt + total_completion
        total_duration = sum((c['duration_ms'] for c in self.calls))
        model_costs = {}
        for call in self.calls:
            model = call['model']
            pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING['default'])
            cost = (call['prompt_tokens'] * pricing['input'] + call['completion_tokens'] * pricing['output']) / 1000000
            model_costs[model] = model_costs.get(model, 0.0) + cost
        total_cost = sum(model_costs.values())
        cost_rounded = round(total_cost, 4)
        return {'total_calls': len(self.calls), 'total_tokens': total, 'prompt_tokens': total_prompt, 'completion_tokens': total_completion, 'estimated_cost_rmb': cost_rounded, 'estimated_cost': cost_rounded, 'total_duration_ms': round(total_duration, 0), 'model_breakdown': {m: round(c, 4) for m, c in model_costs.items()}, 'calls': list(self.calls)}

    def get_per_part_summary(self, part_num: int) -> dict:
        """获取某个Part的成本

        R4-P1-x: agent 名精确匹配 Part 号——此前用 f'Part {part_num}' in agent 子串匹配，
        'Part 1' 会命中 'Part 10'~'Part 19'/'Part 1xx'，per-part 成本串号。
        匹配形如 'Part 10 ...' / 'Part 10_...' 且后续不是数字的调用。
        """
        part_calls = [c for c in self.calls if self._agent_matches_part(c.get('agent', ''), part_num)]
        if not part_calls:
            return {'total_tokens': 0, 'calls': 0, 'estimated_cost_rmb': 0.0, 'estimated_cost': 0.0}
        total_prompt = sum((c['prompt_tokens'] for c in part_calls))
        total_completion = sum((c['completion_tokens'] for c in part_calls))
        model = part_calls[0]['model']
        pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING['default'])
        cost = (total_prompt * pricing['input'] + total_completion * pricing['output']) / 1000000
        cost_rounded = round(cost, 4)
        return {'total_tokens': total_prompt + total_completion, 'calls': len(part_calls), 'estimated_cost_rmb': cost_rounded, 'estimated_cost': cost_rounded}

    @staticmethod
    def _agent_matches_part(agent: str, part_num: int) -> bool:
        """agent 名中的 Part 号精确匹配：'Part 3 writer' 命中 3，不命中 30/31。"""
        if not agent:
            return False
        for m in re.finditer(r'Part[ _](\d+)', agent):
            if int(m.group(1)) == part_num:
                return True
        return False

    def should_prompt_for_cost(self, work_id: Optional[str]=None, threshold: Optional[float]=None) -> bool:
        """R3-P1-6: 熔断判定 — 当前累计预估成本是否超过阈值。

        Args:
            work_id: 作品 ID（占位参数；当前 CostTracker 是进程级单例，
                     未来如需按 work_id 隔离可在此处过滤 self.calls）。
            threshold: 阈值（元），默认 DEFAULT_COST_THRESHOLD_RMB (¥50)。

        Returns:
            bool: 当前成本 >= 阈值返回 True，调用方应 SSE 推 CONFIRM 让用户决策。
        """
        if threshold is None:
            threshold = self.DEFAULT_COST_THRESHOLD_RMB
        try:
            summary = self.get_summary()
            cost = summary.get('estimated_cost_rmb', summary.get('estimated_cost', 0.0))
            return float(cost) >= float(threshold)
        except Exception:
            return False

    def reset(self):
        """重置追踪器"""
        self.calls = []
        self._start_time = time.time()
        self._real_count = 0  # P0-80: 同步清零
        self._last_flush_count = 0
        self._last_flush_ts = time.time()
_tracker = None
_trackers: dict[str, CostTracker] = {}

def get_tracker(work_id: Optional[str]=None) -> CostTracker:
    """R20-P1-16: 获取成本追踪器。
    - 传 work_id 时返回该 work 的 tracker（自动创建）
    - 不传 work_id 时返回全局共享 tracker（向后兼容，单作品场景可用）
    """
    global _tracker
    if work_id is not None:
        if work_id not in _trackers:
            _trackers[work_id] = CostTracker()
        return _trackers[work_id]
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker

def reset_tracker(work_id: Optional[str]=None):
    """R20-P1-16: 重置追踪器。
    - 传 work_id 时只清该 work
    - 不传时清全局 + 所有 per-work
    """
    global _tracker
    if work_id is not None:
        _trackers.pop(work_id, None)
        return
    _tracker = CostTracker()
    _trackers.clear()

def get_all_trackers() -> dict[str, CostTracker]:
    """R20-P1-16: 获取所有 per-work tracker（用于跨作品监控）。"""
    return dict(_trackers)

def should_prompt_for_cost(work_id: Optional[str]=None, threshold: Optional[float]=None) -> bool:
    """模块级便捷函数：调用对应 work 的 tracker 的 should_prompt_for_cost。"""
    return get_tracker(work_id=work_id).should_prompt_for_cost(work_id=work_id, threshold=threshold)
_CJK_RATIO = 1.5
_ASCII_RATIO = 1.3

def estimate_tokens_from_text(text: str) -> int:
    """R7-P0-3: 流式调用 usage 为 None 时用文本长度估算 token 数。

    规则：
      - 中文字符（CJK Unified Ideographs 等基本块）按 1 / 1.5 ≈ 0.67 token/字 折算
      - 其它字符（ASCII 拉丁 + 数字 + 符号）按 1 / 1.3 ≈ 0.77 token/字 折算
    返回估算的 token 数（int，至少 1）。
    """
    if not text:
        return 1
    try:
        cjk_count = 0
        other_count = 0
        for ch in text:
            cp = ord(ch)
            if 19968 <= cp <= 40959 or 13312 <= cp <= 19903 or 131072 <= cp <= 173791 or (173824 <= cp <= 177983) or (177984 <= cp <= 178207) or (178208 <= cp <= 183983):
                cjk_count += 1
            else:
                other_count += 1
        cjk_tokens = cjk_count / _CJK_RATIO
        other_tokens = other_count / _ASCII_RATIO
        return max(1, int(round(cjk_tokens + other_tokens)))
    except Exception:
        return max(1, len(text) // 2)