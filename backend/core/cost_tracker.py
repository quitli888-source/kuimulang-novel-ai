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
"""
import json
import time
from pathlib import Path
from typing import Optional


class CostTracker:
    """全局LLM调用成本追踪器"""

    # 估算单价（每百万token，人民币）
    # 可根据实际使用的模型调整
    MODEL_PRICING = {
        "step-3.7-flash": {
            "input": 0.15,
            "output": 0.6,
            "_note": "Step-3.7-Flash 市场参考价（按官方公开口径估算），待官方定价更新",
        },                                                          # Step-3.7-Flash (默认模型)
        "deepseek-v3.2": {"input": 1.0, "output": 2.0},       # DeepSeek V3
        "MiniMax-Text-01": {"input": 1.0, "output": 4.0},     # MiniMax
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},       # GPT-4o-mini
        "gpt-4o": {"input": 2.5, "output": 10.0},            # GPT-4o
        "default": {"input": 1.0, "output": 2.0},
    }

    # R3-P1-6: 默认成本熔断阈值（元）。可在调用 should_prompt_for_cost 时覆盖。
    DEFAULT_COST_THRESHOLD_RMB = 50.0

    def __init__(self):
        self.calls = []  # [{timestamp, model, agent, is_json, prompt_tokens, completion_tokens, total_tokens, duration_ms}]
        self._start_time = time.time()
        # R4-P1-7: 当前绑定的 work_id + 持久化文件路径
        self._persist_work_id: Optional[str] = None
        self._persist_path: Optional[Path] = None
        # R5-P0-3: work JSON 中持久化的 cost_summary 聚合字段（不含 calls），
        # 用于 works.py:get_work 合并（重启瞬间 in-memory=0 时 history 不丢）
        self._persisted_summary: dict = {}

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
                # 去重：以 (timestamp, model, agent, prompt_tokens, completion_tokens)
                # 五元组作为签名，避免重复叠加同一调用
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
                        # R7-P0-3: estimated 字段不参与去重（保持向后兼容：旧 json 无此字段）
                        # 仅在 estimated=True 且字段已存在时区分（防止双 attach 重复叠加）
                    )
                    if sig in existing_keys:
                        continue
                    self.calls.append(c)
                    existing_keys.add(sig)
                    added += 1
                if added:
                    print(f"[cost_tracker] attach_work 从 work_cost_summary 恢复 {added} 条历史")
            # 同时把 work_cost_summary 里的 summary 字段视为"已持久化基线"，
            # 用于 works.py:get_work 时合并（避免重启瞬间 in-memory=0 覆盖 history）
            self._persisted_summary = {
                k: v for k, v in work_cost_summary.items() if k != "calls"
            }

    def record(self, model: str, agent: str, is_json: bool,
               prompt_tokens: int, completion_tokens: int,
               total_tokens: int, duration_ms: float,
               estimated: bool = False):
        """记录一次LLM调用。R7-P0-3: estimated=True 表示 token 数是基于文本长度估算（非 provider 上报）。"""
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
        # R4-P1-7: 增量落盘（best-effort，失败不影响主流程）
        self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """R4-P1-7: 把当前 self.calls 序列化为 JSON 写到 _persist_path。"""
        if not self._persist_path:
            return
        try:
            self._persist_path.write_text(
                json.dumps(self.calls, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            # 写盘失败静默：成本是辅助数据，不应阻塞创作主流程
            # TODO(R5): 多 worker 部署时加 fcntl.flock 文件锁
            pass

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


# 全局单例
_tracker = None


def get_tracker() -> CostTracker:
    """获取全局成本追踪器"""
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker


def reset_tracker():
    """重置全局成本追踪器"""
    global _tracker
    _tracker = CostTracker()


# ---- R3-P1-6: 模块级便捷函数 ----
def should_prompt_for_cost(work_id: Optional[str] = None, threshold: Optional[float] = None) -> bool:
    """模块级便捷函数：调用全局 tracker 的 should_prompt_for_cost。"""
    return get_tracker().should_prompt_for_cost(work_id=work_id, threshold=threshold)


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
