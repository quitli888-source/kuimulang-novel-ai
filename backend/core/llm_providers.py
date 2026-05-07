"""
番茄小说AI创作系统 V5 - LLM供应商配置
支持 MiniMax / DeepSeek / OpenAI / SiliconFlow
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


# ==================== 预设供应商 ====================
@dataclass
class LLMProvider:
    """单个LLM供应商配置"""
    id: str           # "minimax" / "deepseek" / "openai" / "siliconflow"
    name: str         # 显示名
    api_key_name: str # .env中的变量名
    base_url: str     # 默认API地址
    model: str        # 默认模型
    json_model: str   # 默认JSON模型
    api_key: str = "" # 用户填入的key（运行时填充，不存盘）
    enabled: bool = True


# 预设供应商列表（供前端下拉框使用）
PRESET_PROVIDERS = [
    LLMProvider(
        id="minimax",
        name="MiniMax（推荐）",
        api_key_name="MINIMAX_API_KEY",
        base_url="https://api.minimax.chat/v1",
        model="MiniMax-Text-01",
        json_model="abab6.5s-chat",
    ),
    LLMProvider(
        id="deepseek",
        name="DeepSeek",
        api_key_name="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        json_model="deepseek-chat",
    ),
    LLMProvider(
        id="openai",
        name="OpenAI（GPT系列）",
        api_key_name="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        json_model="gpt-4o-mini",
    ),
    LLMProvider(
        id="siliconflow",
        name="SiliconFlow（硅基流动）",
        api_key_name="SILICONFLOW_API_KEY",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen2.5-72B-Instruct",
        json_model="Qwen/Qwen2.5-72B-Instruct",
    ),
    LLMProvider(
        id="custom",
        name="自定义LLM",
        api_key_name="CUSTOM_LLM_API_KEY",
        base_url="",
        model="",
        json_model="",
    ),
]


def get_provider_from_id(provider_id: str) -> Optional[LLMProvider]:
    for p in PRESET_PROVIDERS:
        if p.id == provider_id:
            return p
    return None


def load_provider_api_key(provider: LLMProvider, read_env_fn) -> LLMProvider:
    """从.env加载供应商的API Key"""
    provider.api_key = read_env_fn(provider.api_key_name, "")
    
    # 对于自定义LLM，还需要加载base_url、model和json_model
    if provider.id == "custom":
        provider.base_url = read_env_fn("CUSTOM_LLM_BASE_URL", "")
        provider.model = read_env_fn("CUSTOM_LLM_MODEL", "")
        provider.json_model = read_env_fn("CUSTOM_LLM_JSON_MODEL", "")
    
    return provider


# ==================== 激活的LLM配置 ====================
@dataclass
class ActiveLLMConfig:
    """
    当前激活的LLM配置
    支持单个主供应商（所有Agent共用）或每个Agent独立配置
    """
    # 当前选中的供应商ID
    active_provider_id: str = "minimax"

    # 每个Agent是否使用独立供应商（默认False，所有Agent共用active_provider_id）
    per_agent_enabled: bool = False

    # 当 per_agent_enabled=True 时，每个Agent的供应商ID
    agent_providers: dict = field(default_factory=lambda: {
        "plot_planner": "minimax",
        "part_writer": "minimax",
        "style_optimizer": "minimax",
        "emotion_review": "minimax",
        "logic_review": "minimax",
        "consistency_review": "minimax",
    })

    # 全局温度（当per_agent_enabled=False时所有Agent使用此温度）
    global_temperature: float = 0.7

    # 每个Agent独立温度（当per_agent_enabled=True时使用）
    agent_temperatures: dict = field(default_factory=lambda: {
        "plot_planner": 0.3,
        "part_writer": 0.8,
        "style_optimizer": 0.5,
        "emotion_review": 0.3,
        "logic_review": 0.3,
        "consistency_review": 0.3,
    })

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ActiveLLMConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
