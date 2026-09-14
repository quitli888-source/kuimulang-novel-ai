"""
番茄小说AI创作系统 V5 - 配置管理API
前端配置 → 自动同步写入 .env / data/llm_config.json
"""
import os
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import (
    get_app_config, DEFAULT_TEMPLATES, write_env, read_env, delete_env,
    LLMConfig, AgentConfigs, WritingTemplate, ENV_FILE, BASE_DIR,
    get_all_providers, load_llm_config, save_llm_config,
    get_llm_config_for_agent,
)
from core.llm_providers import ActiveLLMConfig

router = APIRouter()


# =============================================
# 供应商管理
# =============================================

@router.get("/providers")
def list_providers():
    """返回所有预设LLM供应商（带API Key填写状态和用户自定义模型配置）"""
    providers = get_all_providers()

    # 为每个供应商读取用户可能自定义的模型配置
    model_key_map = {
        "step": ("STEP_MODEL", "STEP_JSON_MODEL"),
        "minimax": ("MINIMAX_MODEL", "MINIMAX_JSON_MODEL"),
        "deepseek": ("DEEPSEEK_MODEL", "DEEPSEEK_JSON_MODEL"),
        "openai": ("OPENAI_MODEL", "OPENAI_JSON_MODEL"),
        "siliconflow": ("SILICONFLOW_MODEL", "SILICONFLOW_JSON_MODEL"),
        "custom": ("CUSTOM_LLM_MODEL", "CUSTOM_LLM_JSON_MODEL"),
    }
    
    for p in providers:
        if p["id"] in model_key_map:
            model_key, json_model_key = model_key_map[p["id"]]
            # 如果用户自定义了模型配置，使用用户的配置；否则使用默认值
            custom_model = read_env(model_key, "")
            custom_json_model = read_env(json_model_key, "")
            if custom_model:
                p["model"] = custom_model
                print(f"[GET] 供应商 {p['id']} 使用自定义模型: {custom_model}")
            if custom_json_model:
                p["json_model"] = custom_json_model
                print(f"[GET] 供应商 {p['id']} 使用自定义JSON模型: {custom_json_model}")
        
        # 对于自定义LLM，还需要读取Base URL
        if p["id"] == "custom":
            custom_base_url = read_env("CUSTOM_LLM_BASE_URL", "")
            if custom_base_url:
                p["base_url"] = custom_base_url
    
    return providers


class ProviderApiKeyUpdate(BaseModel):
    api_key: str


class ProviderConfigUpdate(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    json_model: str = ""


@router.put("/providers/{provider_id}")
def update_provider_config(provider_id: str, req: ProviderConfigUpdate):
    """
    为指定供应商写入配置到 .env
    provider_id: minimax / deepseek / openai / siliconflow / custom
    
    支持修改：
    - API Key
    - 模型名称（所有供应商均可修改）
    - JSON模型名称（所有供应商均可修改）
    - Base URL（仅自定义LLM）
    """
    key_map = {
        "step": "STEP_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "custom": "CUSTOM_LLM_API_KEY",
    }
    if provider_id not in key_map:
        raise HTTPException(400, f"未知供应商: {provider_id}")

    # 写入API Key
    if req.api_key:
        write_env(key_map[provider_id], req.api_key)
        print(f"[PUT] 供应商 {provider_id} API Key已更新")

    # 对于所有供应商，都支持写入模型配置
    model_key_map = {
        "step": "STEP_MODEL",
        "minimax": "MINIMAX_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
        "openai": "OPENAI_MODEL",
        "siliconflow": "SILICONFLOW_MODEL",
        "custom": "CUSTOM_LLM_MODEL",
    }

    json_model_key_map = {
        "step": "STEP_JSON_MODEL",
        "minimax": "MINIMAX_JSON_MODEL",
        "deepseek": "DEEPSEEK_JSON_MODEL",
        "openai": "OPENAI_JSON_MODEL",
        "siliconflow": "SILICONFLOW_JSON_MODEL",
        "custom": "CUSTOM_LLM_JSON_MODEL",
    }
    
    if req.model and provider_id in model_key_map:
        write_env(model_key_map[provider_id], req.model)
        print(f"[PUT] 供应商 {provider_id} 模型已更新为: {req.model}")
    
    if req.json_model and provider_id in json_model_key_map:
        write_env(json_model_key_map[provider_id], req.json_model)
        print(f"[PUT] 供应商 {provider_id} JSON模型已更新为: {req.json_model}")
    
    # 对于自定义LLM，还需要写入Base URL
    if provider_id == "custom" and req.base_url:
        write_env("CUSTOM_LLM_BASE_URL", req.base_url)
        print(f"[PUT] 自定义LLM Base URL已更新为: {req.base_url}")
    
    # 清除 llm_client 的缓存
    from core import llm_client as llm_client_module
    llm_client_module._client_cache.clear()
    llm_client_module._client = None
    llm_client_module._json_client = None
    
    print(f"[PUT] 供应商 {provider_id} 配置更新完成")
    return {"ok": True, "provider_id": provider_id}


# =============================================
# LLM配置（多供应商）
# =============================================

@router.get("/llm-config")
def get_llm_config_api():
    """返回当前激活的LLM供应商配置"""
    cfg = load_llm_config()
    return cfg.to_dict()


class LLMConfigUpdate(BaseModel):
    active_provider_id: str = "minimax"
    per_agent_enabled: bool = False
    agent_providers: dict = {}
    global_temperature: float = 0.7
    agent_temperatures: dict = {}


@router.put("/llm-config")
def update_llm_config_api(req: LLMConfigUpdate):
    """
    更新LLM供应商激活配置
    支持：
    - 全局模式：所有Agent使用同一个供应商（设置 active_provider_id）
    - 分离模式：每个Agent独立选择供应商（设置 per_agent_enabled + agent_providers）
    """
    cfg = ActiveLLMConfig(
        active_provider_id=req.active_provider_id,
        per_agent_enabled=req.per_agent_enabled,
        agent_providers=req.agent_providers or {
            "plot_planner": req.active_provider_id,
            "part_writer": req.active_provider_id,
            "style_optimizer": req.active_provider_id,
            "emotion_review": req.active_provider_id,
            "logic_review": req.active_provider_id,
            "consistency_review": req.active_provider_id,
        },
        global_temperature=req.global_temperature,
        agent_temperatures=req.agent_temperatures or {
            "plot_planner": 0.3,
            "part_writer": 0.8,
            "style_optimizer": 0.5,
            "emotion_review": 0.3,
            "logic_review": 0.3,
            "consistency_review": 0.3,
        },
    )
    save_llm_config(cfg)
    # 清除 llm_client 的缓存（下次调用会重新读取配置）
    from core import llm_client as llm_client_module
    llm_client_module._client_cache.clear()
    llm_client_module._client = None
    llm_client_module._json_client = None
    return {"ok": True}


# =============================================
# 兼容旧接口（单供应商模式）
# =============================================

@router.get("/llm")
def get_llm_config_legacy():
    """兼容：返回当前激活供应商的LLM配置"""
    cfg = get_llm_config_for_agent("part_writer")
    return {
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "json_model": cfg.json_model,
        "has_api_key": bool(cfg.api_key),
    }


class LLMConfigUpdateLegacy(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    json_model: str = ""


@router.put("/llm")
def update_llm_config_legacy(req: LLMConfigUpdateLegacy):
    """兼容：写入当前激活供应商的API Key"""
    cfg = load_llm_config()
    provider_id = cfg.active_provider_id
    key_map = {
        "minimax": "MINIMAX_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
    }
    key_name = key_map.get(provider_id, "MINIMAX_API_KEY")

    if req.api_key:
        write_env(key_name, req.api_key)
    if req.base_url:
        write_env("OPENAI_BASE_URL", req.base_url)
    if req.model:
        write_env("OPENAI_MODEL", req.model)
    if req.json_model:
        write_env("OPENAI_JSON_MODEL", req.json_model)

    # 同步写入 step_* 模型键（保持与 step 供应商模型键一致）
    if provider_id == "step":
        if req.model:
            write_env("STEP_MODEL", req.model)
        if req.json_model:
            write_env("STEP_JSON_MODEL", req.json_model)
        if req.base_url:
            write_env("STEP_BASE_URL", req.base_url)

    from core import llm_client as llm_client_module
    llm_client_module._client_cache.clear()
    llm_client_module._client = None
    llm_client_module._json_client = None
    return {"ok": True}


# =============================================
# Agent配置
# =============================================

@router.get("/agents")
def get_agent_configs():
    cfg = get_app_config()
    return cfg.agent_configs.to_dict()


@router.get("/templates")
def get_templates():
    return [
        {"name": t.name, "target_words": t.target_words,
         "part_count": t.part_count, "part_word_min": t.part_word_min, "part_word_max": t.part_word_max}
        for t in DEFAULT_TEMPLATES
    ]


@router.get("/app")
def get_app_config_api():
    # 从.env文件中读取当前模板名称（持久化存储）
    template_name = read_env("TEMPLATE_NAME", "短篇")
    print(f"[GET] 当前模板名称: {template_name}")
    
    # 遍历所有模板，找到当前模板
    for t in DEFAULT_TEMPLATES:
        if t.name == template_name:
            print(f"[GET] 找到模板: {t.name}, target_words={t.target_words}")
            
            # 如果是自定义模板，从.env读取自定义配置
            if t.name == "自定义":
                custom_target_words = read_env("CUSTOM_TARGET_WORDS", "")
                custom_part_count = read_env("CUSTOM_PART_COUNT", "")
                if custom_target_words and custom_part_count:
                    target_words = int(custom_target_words)
                    part_count = int(custom_part_count)
                    return {
                        "confirm_mode": True,
                        "template": {
                            "name": t.name,
                            "target_words": target_words,
                            "part_count": part_count,
                            "part_word_min": max(1000, target_words // part_count // 2),
                            "part_word_max": min(10000, target_words // part_count * 2),
                        }
                    }
            
            return {
                "confirm_mode": True,
                "template": {
                    "name": t.name,
                    "target_words": t.target_words,
                    "part_count": t.part_count,
                    "part_word_min": t.part_word_min,
                    "part_word_max": t.part_word_max,
                }
            }
    # 如果没有找到模板，返回默认模板的配置
    print(f"[GET] 没有找到模板: {template_name}, 返回默认模板")
    return {
        "confirm_mode": True,
        "template": {
            "name": DEFAULT_TEMPLATES[0].name,
            "target_words": DEFAULT_TEMPLATES[0].target_words,
            "part_count": DEFAULT_TEMPLATES[0].part_count,
            "part_word_min": DEFAULT_TEMPLATES[0].part_word_min,
            "part_word_max": DEFAULT_TEMPLATES[0].part_word_max,
        }
    }




class AgentConfigUpdate(BaseModel):
    model: str = "MiniMax-Text-01"
    temperature: float = 0.7
    max_tokens: int = 4000


class AppConfigUpdate(BaseModel):
    confirm_mode: bool = True
    template_name: str = "短篇"
    custom_target_words: int = 20000
    custom_part_count: int = 4


@router.put("/agents/{agent_name}")
def update_agent_config(agent_name: str, req: AgentConfigUpdate):
    valid_agents = ["plot_planner", "part_writer", "style_optimizer",
                     "emotion_review", "logic_review", "consistency_review"]
    if agent_name not in valid_agents:
        raise HTTPException(400, f"未知Agent: {agent_name}")

    prefix = agent_name.upper()
    write_env(f"MODEL_{prefix}", req.model)
    write_env(f"TEMP_{prefix}", str(req.temperature))
    write_env(f"TOKENS_{prefix}", str(req.max_tokens))

    cfg = get_app_config()
    agent_cfg = getattr(cfg.agent_configs, agent_name)
    agent_cfg.model = req.model
    agent_cfg.temperature = req.temperature
    agent_cfg.max_tokens = req.max_tokens

    return {"ok": True}


@router.put("/app")
def update_app_config(req: AppConfigUpdate):
    try:
        print(f"[PUT] 收到模板更新请求: {req.template_name}")
        
        # 将模板名称持久化到.env文件
        write_env("TEMPLATE_NAME", req.template_name)
        print(f"[PUT] 模板名称已保存到.env文件: {req.template_name}")
        
        # 获取AppConfig实例
        cfg = get_app_config()
        print(f"[PUT] 当前模板: {cfg.template.name}, target_words={cfg._target_words}, part_count={cfg._part_count}")
        cfg.confirm_mode = req.confirm_mode
        
        # 找到并应用模板
        template_found = False
        for t in DEFAULT_TEMPLATES:
            print(f"[PUT] 检查模板: {t.name}, target_words={t.target_words}")
            if t.name == req.template_name:
                print(f"[PUT] 找到模板: {t.name}, target_words={t.target_words}")
                # 直接更新模板
                cfg.template = t
                if req.template_name == "自定义":
                    # 处理自定义模板
                    # 保存自定义模板配置到.env文件
                    write_env("CUSTOM_TARGET_WORDS", str(req.custom_target_words))
                    write_env("CUSTOM_PART_COUNT", str(req.custom_part_count))
                    # 直接更新内部状态
                    cfg._target_words = req.custom_target_words
                    cfg._part_count = req.custom_part_count
                    cfg._part_word_min = max(1000, req.custom_target_words // req.custom_part_count // 2)
                    cfg._part_word_max = min(10000, req.custom_target_words // req.custom_part_count * 2)
                else:
                    # 处理预设模板
                    # 清除自定义模板配置
                    delete_env("CUSTOM_TARGET_WORDS")
                    delete_env("CUSTOM_PART_COUNT")
                    # 直接更新内部状态
                    cfg._target_words = t.target_words
                    cfg._part_count = t.part_count
                    cfg._part_word_min = t.part_word_min
                    cfg._part_word_max = t.part_word_max
                template_found = True
                break
        
        print(f"[PUT] 模板更新后: {cfg.template.name}, target_words={cfg._target_words}, part_count={cfg._part_count}")
        
        # 重新同步V4兼容常量
        from core.config import _sync_from_app, reload_config
        _sync_from_app()
        reload_config()
        
        print(f"[PUT] 模板更新成功: {req.template_name}, target_words={cfg._target_words}, part_count={cfg._part_count}")

        return {"ok": True}
    except Exception as e:
        print(f"[PUT] Error in update_app_config: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Internal Server Error: {str(e)}")
