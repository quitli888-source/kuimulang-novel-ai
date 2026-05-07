"""
番茄小说AI创作系统 V4 - Token成本追踪器

追踪每次LLM调用的token消耗，统计总成本。
"""
import time


class CostTracker:
    """全局LLM调用成本追踪器"""

    # 估算单价（每百万token，人民币）
    # 可根据实际使用的模型调整
    MODEL_PRICING = {
        "deepseek-v3.2": {"input": 1.0, "output": 2.0},       # DeepSeek V3
        "MiniMax-Text-01": {"input": 1.0, "output": 4.0},     # MiniMax
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},       # GPT-4o-mini
        "gpt-4o": {"input": 2.5, "output": 10.0},            # GPT-4o
        "default": {"input": 1.0, "output": 2.0},
    }

    def __init__(self):
        self.calls = []  # [{timestamp, model, agent, is_json, prompt_tokens, completion_tokens, total_tokens, duration_ms}]
        self._start_time = time.time()

    def record(self, model: str, agent: str, is_json: bool,
               prompt_tokens: int, completion_tokens: int,
               total_tokens: int, duration_ms: float):
        """记录一次LLM调用"""
        self.calls.append({
            "timestamp": round(time.time() - self._start_time, 1),
            "model": model,
            "agent": agent,
            "is_json": is_json,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_ms": round(duration_ms, 0),
        })

    def get_summary(self) -> dict:
        """获取统计摘要"""
        if not self.calls:
            return {
                "total_calls": 0, "total_tokens": 0,
                "prompt_tokens": 0, "completion_tokens": 0,
                "estimated_cost_rmb": 0.0, "total_duration_ms": 0,
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

        return {
            "total_calls": len(self.calls),
            "total_tokens": total,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "estimated_cost_rmb": round(total_cost, 4),
            "total_duration_ms": round(total_duration, 0),
            "model_breakdown": {m: round(c, 4) for m, c in model_costs.items()},
        }

    def get_per_part_summary(self, part_num: int) -> dict:
        """获取某个Part的成本"""
        # Part调用都带有 "Part N" 在agent字段中
        part_calls = [c for c in self.calls if f"Part {part_num}" in c.get("agent", "")]
        if not part_calls:
            return {"total_tokens": 0, "calls": 0, "estimated_cost_rmb": 0.0}

        total_prompt = sum(c["prompt_tokens"] for c in part_calls)
        total_completion = sum(c["completion_tokens"] for c in part_calls)

        # 使用第一个调用的模型来估算（假设同一Part用同一模型）
        model = part_calls[0]["model"]
        pricing = self.MODEL_PRICING.get(model, self.MODEL_PRICING["default"])
        cost = (total_prompt * pricing["input"] + total_completion * pricing["output"]) / 1_000_000

        return {
            "total_tokens": total_prompt + total_completion,
            "calls": len(part_calls),
            "estimated_cost_rmb": round(cost, 4),
        }

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
