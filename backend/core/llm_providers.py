"""
番茄小说AI创作系统 V5 - LLM供应商配置
支持 MiniMax / DeepSeek / OpenAI / SiliconFlow / Step-3.7-Flash

P2-100: 单一来源 (single source of truth) —— PROVIDER_ENV_KEYS + PROVIDER_DISPLAY_META
合并派生 PRESET_PROVIDERS，避免两端重复维护导致 api_key_name / base_url drift。
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


# ==================== 预设供应商 ====================
@dataclass
class LLMProvider:
    """单个LLM供应商配置"""
    id: str           # "minimax" / "deepseek" / "openai" / "siliconflow" / "step"
    name: str         # 显示名
    api_key_name: str # .env中的变量名
    base_url: str     # 默认API地址
    model: str        # 默认模型
    json_model: str   # 默认JSON模型
    api_key: str = "" # 用户填入的key（运行时填充，不存盘）
    enabled: bool = True


# =============================================
# 单一来源：env key 名 + 默认 base_url / model / json_model
# + 单独的 PROVIDER_DISPLAY_META 仅放显示名（与配置无关）
# =============================================
# P2-100: 这是 SOLE SOURCE OF TRUTH；PRESET_PROVIDERS 由本表派生。
PROVIDER_ENV_KEYS: dict = {
    # provider_id -> {"api_key": str, "model": str, "json_model": str, "base_url": str, "default_model": str, "default_json_model": str, "default_base_url": str}
    "step": {
        "api_key": "STEP_API_KEY",
        "model": "STEP_MODEL",
        "json_model": "STEP_JSON_MODEL",
        "base_url": "STEP_BASE_URL",
        "default_model": "step-5-preview",
        "default_json_model": "step-5-preview",
        "default_base_url": "https://api.stepfun.com/step_plan/v1",
    },
    "minimax": {
        "api_key": "MINIMAX_API_KEY",
        "model": "MINIMAX_MODEL",
        "json_model": "MINIMAX_JSON_MODEL",
        "base_url": "MINIMAX_BASE_URL",
        "default_model": "MiniMax-Text-01",
        "default_json_model": "abab6.5s-chat",
        "default_base_url": "https://api.minimax.chat/v1",
    },
    "minimax_m3": {
        "api_key": "MINIMAX_M3_API_KEY",
        "model": "MINIMAX_M3_MODEL",
        "json_model": "MINIMAX_M3_JSON_MODEL",
        "base_url": "MINIMAX_M3_BASE_URL",
        "default_model": "MiniMax-M3",
        "default_json_model": "abab6.5s-chat",
        "default_base_url": "https://api.minimax.cn/v1",
    },
    "deepseek": {
        "api_key": "DEEPSEEK_API_KEY",
        "model": "DEEPSEEK_MODEL",
        "json_model": "DEEPSEEK_JSON_MODEL",
        "base_url": "DEEPSEEK_BASE_URL",
        "default_model": "deepseek-chat",
        "default_json_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com/v1",
    },
    "openai": {
        "api_key": "OPENAI_API_KEY",
        "model": "OPENAI_MODEL",
        "json_model": "OPENAI_JSON_MODEL",
        "base_url": "OPENAI_BASE_URL",
        "default_model": "gpt-4o-mini",
        "default_json_model": "gpt-4o-mini",
        "default_base_url": "https://api.openai.com/v1",
    },
    "siliconflow": {
        "api_key": "SILICONFLOW_API_KEY",
        "model": "SILICONFLOW_MODEL",
        "json_model": "SILICONFLOW_JSON_MODEL",
        "base_url": "SILICONFLOW_BASE_URL",
        "default_model": "Qwen/Qwen2.5-72B-Instruct",
        "default_json_model": "Qwen/Qwen2.5-72B-Instruct",
        "default_base_url": "https://api.siliconflow.cn/v1",
    },
    "custom": {
        "api_key": "CUSTOM_LLM_API_KEY",
        "model": "CUSTOM_LLM_MODEL",
        "json_model": "CUSTOM_LLM_JSON_MODEL",
        "base_url": "CUSTOM_LLM_BASE_URL",
        "default_model": "",
        "default_json_model": "",
        "default_base_url": "",
    },
}

# 仅放显示名（与配置无关，独立于 PROVIDER_ENV_KEYS）
PROVIDER_DISPLAY_META: dict = {
    "step": "Step-3.7-Flash",
    "minimax": "MiniMax（推荐）",
    "minimax_m3": "MiniMax-M3（MiniMax.cn 新版）",
    "deepseek": "DeepSeek",
    "openai": "OpenAI（GPT系列）",
    "siliconflow": "SiliconFlow（硅基流动）",
    "custom": "自定义LLM",
}


def _build_preset_providers() -> list:
    """从 PROVIDER_ENV_KEYS + PROVIDER_DISPLAY_META 派生 PRESET_PROVIDERS。"""
    out = []
    for pid, keys in PROVIDER_ENV_KEYS.items():
        out.append(LLMProvider(
            id=pid,
            name=PROVIDER_DISPLAY_META.get(pid, pid),
            api_key_name=keys["api_key"],
            base_url=keys["default_base_url"],
            model=keys["default_model"],
            json_model=keys["default_json_model"],
        ))
    return out


# P2-100: PRESET_PROVIDERS 现在是派生量 —— 加新 provider 只需在 PROVIDER_ENV_KEYS
#         + PROVIDER_DISPLAY_META 各加一行，无需再维护两份硬编码。
PRESET_PROVIDERS = _build_preset_providers()


def get_provider_from_id(provider_id: str) -> Optional[LLMProvider]:
    for p in PRESET_PROVIDERS:
        if p.id == provider_id:
            return p
    return None


def load_provider_api_key(provider: LLMProvider, read_env_fn) -> LLMProvider:
    """从.env加载供应商的API Key / base_url / model / json_model

    所有预设 provider 的 base_url / model / json_model 均可被 .env 中对应的
    *_BASE_URL / *_MODEL / *_JSON_MODEL 覆盖（此前仅 custom 生效，预设 provider 的
    env 模型覆盖被静默忽略，.env 里切模型无任何效果）。
    """
    provider.api_key = read_env_fn(provider.api_key_name, "")
    provider.base_url = read_env_fn(PROVIDER_ENV_KEYS[provider.id]["base_url"], provider.base_url)
    provider.model = read_env_fn(PROVIDER_ENV_KEYS[provider.id]["model"], provider.model)
    provider.json_model = read_env_fn(PROVIDER_ENV_KEYS[provider.id]["json_model"], provider.json_model)
    return provider


# ==================== 激活的LLM配置 ====================
@dataclass
class ActiveLLMConfig:
    """
    当前激活的LLM配置
    支持单个主供应商（所有Agent共用）或每个Agent独立配置
    """
    # 当前选中的供应商ID
    active_provider_id: str = "step"

    # 每个Agent是否使用独立供应商（默认False，所有Agent共用active_provider_id）
    per_agent_enabled: bool = False

    # 当 per_agent_enabled=True 时，每个Agent的供应商ID
    agent_providers: dict = field(default_factory=lambda: {
        "plot_planner": "step",
        "part_writer": "step",
        "style_optimizer": "step",
        "emotion_review": "step",
        "logic_review": "step",
        "consistency_review": "step",
    })

    # 全局温度（当per_agent_enabled=False时所有Agent使用此温度）
    global_temperature: float = 0.7

    # 每个Agent独立温度（当per_agent_enabled=True时使用）
    # R5-P1-2.3: part_writer 0.8 → 0.65（更稳的推理模型风格）
    # R5-P0-2: 新增 rolling_summary / milestone_summary 摘要 Agent 温度（与 plot_planner 对齐 0.3）
    agent_temperatures: dict = field(default_factory=lambda: {
        "plot_planner": 0.3,
        "part_writer": 0.65,
        "style_optimizer": 0.5,
        "emotion_review": 0.3,
        "logic_review": 0.3,
        "consistency_review": 0.3,
        "rolling_summary": 0.3,
        "milestone_summary": 0.3,
    })

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ActiveLLMConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def get_env_key(provider_id: str, field: str) -> str:
    """R19-P1-10: 统一查询 —— 获取 provider 的某个 env key 名。
    provider_id 不存在或 field 不在 PROVIDER_ENV_KEYS 中 → 返回空字符串（向后兼容）。
    """
    keys = PROVIDER_ENV_KEYS.get(provider_id, {})
    return keys.get(field, "")
