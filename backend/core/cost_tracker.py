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
import time
from pathlib import Path
from typing import Optional


class CostTracker:
    """全局LLM调用成本追踪器"""

    # 估算单价（每百万token，人民币）
    MODEL_PRICING = {
        "step-3.7-flash": {
            "input": 0.15,
            "output": 0.6,
            "_note": "Step-3.7-Flash 市场参考价（按官方公开口径估算），待官方定价更新",
        },
        "deepseek-v3.2": {"input": 1.0, "output": 2.0},
        "MiniMax-Text-01": {"input": 1.0, "output": 4.0},
        "MiniMax-M3": {"input": 0.15, "output": 0.6},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "default": {"input": 1.0, "output": 2.0},
    }

    DEFAULT_COST_THRESHOLD_RMB = 50.0

    # R17: 节流写盘参数
    FLUSH_EVERY_N_RECORDS = 20       # 每 20 条真实（estimated=False）调用触发 flush
    FLUSH_EVERY_N_SECONDS = 30.0     # 或每 30 秒兜底
    PERSIST_ESTIMATED = False        # 估算记录不入盘（仅 in-memory 显示）

    def __init__(self):
        self.calls = []
        self._start_time = time.time()
        self._persist_work_id: Optional[str] = None
        self._persist_path: Optional[Path] = None
        self._persisted_summary: dict = {}
        # R17: 节流计数器
        self._last_flush_ts: float = time.time()
        self._last_flush_count: int = 0
        self._dirty: bool = False  # 标记有未落盘的真实调用

    def attach_work(
        self,
        work_id: str,
        work_cost_summary: Optional[dict] = None,
        persist_dir: Optional[Path] = None,
    ) -> None:
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
            self._persist_path = target_dir / f"{work_id}.cost.json"
        except Exception as e:
            print(f"[cost_tracker] attach_work 路径设置失败: {e}")
            self._persist_path = None

        # R5-P0-3: 从 work_cost_summary 叠加历史 calls（重启合并 / 双轨持久化合并）
        if isinstance(work_cost_summary, dict):
            calls = work_cost_summary.get("calls")
            if isinstance(calls, list) and calls:
                existing_keys = {
                    (
                        c.get("timestamp"),
                        c.get("model"),
                        c.get("agent"),
                        c.get("prompt_tokens"),
                        c.get("completion_tokens"),
                    )
                    for c in self.calls
                }
                added = 0
                for c in calls:
                    if not isinstance(c, dict):
                        continue
                    sig = (
                        c.get("timestamp"),
                        c.get("model"),
                        c.get("agent"),
                        c.get("prompt_tokens"),
                        c.get("completion_tokens"),
                    )
                    if sig in existing_keys:
                        continue
                    self.calls.append(c)
                    existing_keys.add(sig)
                    added += 1
                if added:
                    print(f"[cost_tracker] attach_work 从 work_cost_summary 恢复 {added} 条历史")
                # R17: attach 时若已有新调用进来过，把 _last_flush_count 校准到当前数量，避免立刻 flush
                self._last_flush_count = len([
                    c for c in self.calls if not c.get("estimated")
                ])
            self._persisted_summary = {
                k: v for k, v in work_cost_summary.items() if k != "calls"
            }

    def record(self, model: str, agent: str, is_json: bool,
               prompt_tokens: int, completion_tokens: int,
               total_tokens: int, duration_ms: float,
               estimated: bool = False):
        """记录一次LLM调用。R7-P0-3: estimated=True 表示 token 数是基于文本长度估算（非 provider 上报）。
        R17-P0-2: 估算记录不入盘（避免污染持久化历史）；真实记录节流写盘（每 20 条或 30s）。
        """
        self.calls.append({
            "timestamp": round(time.time() - self._start_time, 1),
            "model": model,
            "agent": agent,
            "is_json": is_json,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_ms": round(duration_ms, 0),
            "estimated": bool(estimated),
        })

        # R17: 估算记录不入盘（落盘数据是"真实 provider 上报"的 source of truth）
        if estimated and not self.PERSIST_ESTIMATED:
            return

        # R17: 节流 — 每 N 条真实记录或每 T 秒兜底触发 flush
        self._dirty = True
        real_added = self.calls.count(None) if False else (  # 简化计数
            len([c for c in self.calls if not c.get("estimated")])
        )
        if (real_added - self._last_flush_count >= self.FLUSH_EVERY_N_RECORDS
                or time.time() - self._last_flush_ts >= self.FLUSH_EVERY_N_SECONDS):
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """R4-P1-7: 把当前 self.calls 序列化为 JSON 写到 _persist_path。
        R17: 加 fcntl.flock 文件锁防多 worker 冲突；估算记录不写入磁盘。
        """
        if not self._persist_path or not self._dirty:
            return
        try:
            # R17: 过滤掉估算记录（避免污染持久化历史）
            persist_calls = [c for c in self.calls if not c.get("estimated")] if not self.PERSIST_ESTIMATED else list(self.calls)

            # R17: fcntl.flock 文件锁（Linux/macOS）；Windows 退化为普通写入
            payload = json.dumps(persist_calls, ensure_ascii=False, indent=2)
            try:
                import fcntl
                with open(self._persist_path, "w", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        f.write(payload)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (ImportError, AttributeError):
                # Windows：直接写
                self._persist_path.write_text(payload, encoding="utf-8")

            self._last_flush_ts = time.time()
            self._last_flush_count = len(persist_calls)
            self._dirty = False
        except Exception as e:
            # 写盘失败静默：成本是辅助数据，不应阻塞创作主流程
            print(f"[cost_tracker] _flush_to_disk 失败（不影响主流程）: {e}")

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
            return {
                "total_calls": 0, "total_tokens": 0,
                "prompt_tokens": 0, "completion_tokens": 0,
                "estimated_cost_rmb": 0.0,
                "estimated_cost": 0.0,  # R8-P0-3 alias
                "total_duration_ms": 0,
                "calls": [],
            }

        total_prompt = sum(c["prompt_tokens"] for c in self.calls)
        total_completion = sum(c["completion_tokens"] for c in self.calls)
        total = total_prompt + total_completion
        total_duration = sum(c["duration_ms"] for c in self.calls)

        # 按模型分别估算成本
        model_costs = {}
        for call in self.calls:
            model = call["model"]
            pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING["default"])
            cost = (call["prompt_tokens"] * pricing["input"] +
                    call["completion_tokens"] * pricing["output"]) / 1_000_000
            model_costs[model] = model_costs.get(model, 0.0) + cost

        total_cost = sum(model_costs.values())
        cost_rounded = round(total_cost, 4)

        return {
            "total_calls": len(self.calls),
            "total_tokens": total,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "estimated_cost_rmb": cost_rounded,
            "estimated_cost": cost_rounded,  # R8-P0-3 alias
            "total_duration_ms": round(total_duration, 0),
            "model_breakdown": {m: round(c, 4) for m, c in model_costs.items()},
            "calls": list(self.calls),
        }

    def get_per_part_summary(self, part_num: int) -> dict:
        """获取某个Part的成本"""
        # Part调用都带有 "Part N" 在agent字段中
        part_calls = [c for c in self.calls if f"Part {part_num}" in c.get("agent", "")]
        if not part_calls:
            return {
                "total_tokens": 0, "calls": 0,
                "estimated_cost_rmb": 0.0,
                "estimated_cost": 0.0,  # R8-P0-3 alias
            }

        total_prompt = sum(c["prompt_tokens"] for c in part_calls)
        total_completion = sum(c["completion_tokens"] for c in part_calls)

        # 使用第一个调用的模型来估算（假设同一Part用同一模型）
        model = part_calls[0]["model"]
        pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING["default"])
        cost = (total_prompt * pricing["input"] + total_completion * pricing["output"]) / 1_000_000
        cost_rounded = round(cost, 4)

        return {
            "total_tokens": total_prompt + total_completion,
            "calls": len(part_calls),
            "estimated_cost_rmb": cost_rounded,
            "estimated_cost": cost_rounded,  # R8-P0-3 alias
        }

    def should_prompt_for_cost(self, work_id: Optional[str] = None, threshold: Optional[float] = None) -> bool:
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
            # R8-P0-3: 优先 estimated_cost_rmb，回退 estimated_cost 兼容
            cost = summary.get("estimated_cost_rmb", summary.get("estimated_cost", 0.0))
            return float(cost) >= float(threshold)
        except Exception:
            return False

    def reset(self):
        """重置追踪器"""
        self.calls = []
        self._start_time = time.time()


# 全局单例（向后兼容；同时支持 R20-P1-16 per-work 隔离）
_tracker = None
# R20-P1-16: per-work cost_tracker 隔离，避免多作品并发时 attach_work 覆盖 _persist_work_id
_trackers: dict[str, CostTracker] = {}


def get_tracker(work_id: Optional[str] = None) -> CostTracker:
    """R20-P1-16: 获取成本追踪器。
    - 传 work_id 时返回该 work 的 tracker（自动创建）
    - 不传 work_id 时返回全局共享 tracker（向后兼容，单作品场景可用）
    """
    global _tracker
    if work_id is not None:
        if work_id not in _trackers:
            _trackers[work_id] = CostTracker()
        return _trackers[work_id]
    # 不传 work_id：全局单例（向后兼容）
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker


def reset_tracker(work_id: Optional[str] = None):
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


# ---- R3-P1-6: 模块级便捷函数 ----
def should_prompt_for_cost(work_id: Optional[str] = None, threshold: Optional[float] = None) -> bool:
    """模块级便捷函数：调用对应 work 的 tracker 的 should_prompt_for_cost。"""
    return get_tracker(work_id=work_id).should_prompt_for_cost(work_id=work_id, threshold=threshold)


# ---- R7-P0-3: 流式调用 usage 兜底估算 ----
# 中文 1 字 ≈ 1.5 tokens（实测 BPE 平均），英文 1 字符 ≈ 0.77 token（BPE 平均）。
# 没有 tiktoken 时保守取 CJK 1.5 字/token + ASCII 1.3 字/token。
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
            # 基本汉字 + 扩展 A-F
            if (
                0x4E00 <= cp <= 0x9FFF
                or 0x3400 <= cp <= 0x4DBF
                or 0x20000 <= cp <= 0x2A6DF
                or 0x2A700 <= cp <= 0x2B73F
                or 0x2B740 <= cp <= 0x2B81F
                or 0x2B820 <= cp <= 0x2CEAF
            ):
                cjk_count += 1
            else:
                other_count += 1
        cjk_tokens = cjk_count / _CJK_RATIO
        other_tokens = other_count / _ASCII_RATIO
        return max(1, int(round(cjk_tokens + other_tokens)))
    except Exception:
        # 极端异常下退化为字符数 / 2
        return max(1, len(text) // 2)
