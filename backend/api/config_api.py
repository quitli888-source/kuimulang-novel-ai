"""
番茄小说AI创作系统 V5 - 配置管理API
前端配置 → 自动同步写入 .env / data/llm_config.json
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.config import get_app_config, DEFAULT_TEMPLATES, write_env, read_env, delete_env, get_all_providers, load_llm_config, save_llm_config, get_llm_config_for_agent
from core.llm_providers import ActiveLLMConfig
router = APIRouter()

@router.get('/providers')
def list_providers():
    """返回所有预设LLM供应商（带API Key填写状态和用户自定义模型配置）"""
    providers = get_all_providers()
    model_key_map = {'step': ('STEP_MODEL', 'STEP_JSON_MODEL'), 'minimax': ('MINIMAX_MODEL', 'MINIMAX_JSON_MODEL'), 'minimax_m3': ('MINIMAX_M3_MODEL', 'MINIMAX_M3_JSON_MODEL'), 'deepseek': ('DEEPSEEK_MODEL', 'DEEPSEEK_JSON_MODEL'), 'openai': ('OPENAI_MODEL', 'OPENAI_JSON_MODEL'), 'siliconflow': ('SILICONFLOW_MODEL', 'SILICONFLOW_JSON_MODEL'), 'custom': ('CUSTOM_LLM_MODEL', 'CUSTOM_LLM_JSON_MODEL')}
    for p in providers:
        if p['id'] in model_key_map:
            model_key, json_model_key = model_key_map[p['id']]
            custom_model = read_env(model_key, '')
            custom_json_model = read_env(json_model_key, '')
            if custom_model:
                p['model'] = custom_model
                logger.info(f"[GET] 供应商 {p['id']} 使用自定义模型: {custom_model}")
            if custom_json_model:
                p['json_model'] = custom_json_model
                logger.info(f"[GET] 供应商 {p['id']} 使用自定义JSON模型: {custom_json_model}")
        if p['id'] == 'custom':
            custom_base_url = read_env('CUSTOM_LLM_BASE_URL', '')
            if custom_base_url:
                p['base_url'] = custom_base_url
    return providers

class ProviderApiKeyUpdate(BaseModel):
    api_key: str

class ProviderConfigUpdate(BaseModel):
    api_key: str = ''
    base_url: str = ''
    model: str = ''
    json_model: str = ''

@router.put('/providers/{provider_id}')
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
    from core.llm_providers import PROVIDER_ENV_KEYS
    if provider_id not in PROVIDER_ENV_KEYS:
        raise HTTPException(400, f'未知供应商: {provider_id}')
    key_map = {pid: meta['api_key'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    if req.api_key:
        write_env(key_map[provider_id], req.api_key)
        logger.info(f'[PUT] 供应商 {provider_id} API Key已更新')
    model_key_map = {pid: meta['model'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    json_model_key_map = {pid: meta['json_model'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    if req.model and provider_id in model_key_map:
        write_env(model_key_map[provider_id], req.model)
        logger.info(f'[PUT] 供应商 {provider_id} 模型已更新为: {req.model}')
    if req.json_model and provider_id in json_model_key_map:
        write_env(json_model_key_map[provider_id], req.json_model)
        logger.info(f'[PUT] 供应商 {provider_id} JSON模型已更新为: {req.json_model}')
    if provider_id == 'custom' and req.base_url:
        write_env('CUSTOM_LLM_BASE_URL', req.base_url)
        logger.info(f'[PUT] 自定义LLM Base URL已更新为: {req.base_url}')
    from core.llm_client import reset_llm_clients
    reset_llm_clients()
    llm_client_module._json_client = None
    logger.info(f'[PUT] 供应商 {provider_id} 配置更新完成')
    return {'ok': True, 'provider_id': provider_id}

@router.get('/llm-config')
def get_llm_config_api():
    """返回当前激活的LLM供应商配置"""
    cfg = load_llm_config()
    return cfg.to_dict()

class LLMConfigUpdate(BaseModel):
    active_provider_id: str = 'minimax'
    per_agent_enabled: bool = False
    agent_providers: dict = {}
    global_temperature: float = 0.7
    agent_temperatures: dict = {}

@router.put('/llm-config')
def update_llm_config_api(req: LLMConfigUpdate):
    """
    更新LLM供应商激活配置
    支持：
    - 全局模式：所有Agent使用同一个供应商（设置 active_provider_id）
    - 分离模式：每个Agent独立选择供应商（设置 per_agent_enabled + agent_providers）
    """
    cfg = ActiveLLMConfig(active_provider_id=req.active_provider_id, per_agent_enabled=req.per_agent_enabled, agent_providers=req.agent_providers or {'plot_planner': req.active_provider_id, 'part_writer': req.active_provider_id, 'style_optimizer': req.active_provider_id, 'emotion_review': req.active_provider_id, 'logic_review': req.active_provider_id, 'consistency_review': req.active_provider_id}, global_temperature=req.global_temperature, agent_temperatures=req.agent_temperatures or {'plot_planner': 0.3, 'part_writer': 0.8, 'style_optimizer': 0.5, 'emotion_review': 0.3, 'logic_review': 0.3, 'consistency_review': 0.3})
    save_llm_config(cfg)
    from core.llm_client import reset_llm_clients
    reset_llm_clients()
    llm_client_module._json_client = None
    return {'ok': True}

@router.get('/llm')
def get_llm_config_legacy():
    """兼容：返回当前激活供应商的LLM配置"""
    cfg = get_llm_config_for_agent('part_writer')
    return {'api_key': cfg.api_key, 'base_url': cfg.base_url, 'model': cfg.model, 'json_model': cfg.json_model, 'has_api_key': bool(cfg.api_key)}

class LLMConfigUpdateLegacy(BaseModel):
    api_key: str = ''
    base_url: str = ''
    model: str = ''
    json_model: str = ''

@router.put('/llm')
def update_llm_config_legacy(req: LLMConfigUpdateLegacy):
    """兼容：写入当前激活供应商的API Key"""
    cfg = load_llm_config()
    provider_id = cfg.active_provider_id
    key_map = {pid: meta['api_key'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    model_key_map = {pid: meta['model'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    json_model_key_map = {pid: meta['json_model'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    base_url_key_map = {pid: meta['base_url'] for pid, meta in PROVIDER_ENV_KEYS.items()}
    if req.api_key and provider_id in key_map:
        write_env(key_map[provider_id], req.api_key)
    if req.base_url and provider_id in base_url_key_map:
        write_env(base_url_key_map[provider_id], req.base_url)
    if req.model and provider_id in model_key_map:
        write_env(model_key_map[provider_id], req.model)
    if req.json_model and provider_id in json_model_key_map:
        write_env(json_model_key_map[provider_id], req.json_model)
    from core.llm_client import reset_llm_clients
    reset_llm_clients()
    llm_client_module._json_client = None
    return {'ok': True}

@router.get('/agents')
def get_agent_configs():
    cfg = get_app_config()
    return cfg.agent_configs.to_dict()

@router.get('/templates')
def get_templates():
    return [{'name': t.name, 'target_words': t.target_words, 'part_count': t.part_count, 'part_word_min': t.part_word_min, 'part_word_max': t.part_word_max} for t in DEFAULT_TEMPLATES]

@router.get('/app')
def get_app_config_api():
    template_name = read_env('TEMPLATE_NAME', '短篇')
    logger.info(f'[GET] 当前模板名称: {template_name}')
    for t in DEFAULT_TEMPLATES:
        if t.name == template_name:
            logger.info(f'[GET] 找到模板: {t.name}, target_words={t.target_words}')
            if t.name == '自定义':
                custom_target_words = read_env('CUSTOM_TARGET_WORDS', '')
                custom_part_count = read_env('CUSTOM_PART_COUNT', '')
                if custom_target_words and custom_part_count:
                    target_words = int(custom_target_words)
                    part_count = int(custom_part_count)
                    return {'confirm_mode': True, 'template': {'name': t.name, 'target_words': target_words, 'part_count': part_count, 'part_word_min': max(1000, target_words // part_count // 2), 'part_word_max': min(10000, target_words // part_count * 2)}}
            return {'confirm_mode': True, 'template': {'name': t.name, 'target_words': t.target_words, 'part_count': t.part_count, 'part_word_min': t.part_word_min, 'part_word_max': t.part_word_max}}
    logger.info(f'[GET] 没有找到模板: {template_name}, 返回默认模板')
    return {'confirm_mode': True, 'template': {'name': DEFAULT_TEMPLATES[0].name, 'target_words': DEFAULT_TEMPLATES[0].target_words, 'part_count': DEFAULT_TEMPLATES[0].part_count, 'part_word_min': DEFAULT_TEMPLATES[0].part_word_min, 'part_word_max': DEFAULT_TEMPLATES[0].part_word_max}}

class AgentConfigUpdate(BaseModel):
    model: str = 'MiniMax-Text-01'
    temperature: float = 0.7
    max_tokens: int = 4000

class AppConfigUpdate(BaseModel):
    confirm_mode: bool = True
    template_name: str = '短篇'
    custom_target_words: int = 20000
    custom_part_count: int = 4

@router.put('/agents/{agent_name}')
def update_agent_config(agent_name: str, req: AgentConfigUpdate):
    valid_agents = ['plot_planner', 'part_writer', 'style_optimizer', 'emotion_review', 'logic_review', 'consistency_review']
    if agent_name not in valid_agents:
        raise HTTPException(400, f'未知Agent: {agent_name}')
    prefix = agent_name.upper()
    write_env(f'MODEL_{prefix}', req.model)
    write_env(f'TEMP_{prefix}', str(req.temperature))
    write_env(f'TOKENS_{prefix}', str(req.max_tokens))
    cfg = get_app_config()
    agent_cfg = getattr(cfg.agent_configs, agent_name)
    agent_cfg.model = req.model
    agent_cfg.temperature = req.temperature
    agent_cfg.max_tokens = req.max_tokens
    return {'ok': True}

@router.put('/app')
def update_app_config(req: AppConfigUpdate):
    try:
        logger.info(f'[PUT] 收到模板更新请求: {req.template_name}')
        write_env('TEMPLATE_NAME', req.template_name)
        logger.info(f'[PUT] 模板名称已保存到.env文件: {req.template_name}')
        cfg = get_app_config()
        logger.info(f'[PUT] 当前模板: {cfg.template.name}, target_words={cfg._target_words}, part_count={cfg._part_count}')
        cfg.confirm_mode = req.confirm_mode
        template_found = False
        for t in DEFAULT_TEMPLATES:
            logger.info(f'[PUT] 检查模板: {t.name}, target_words={t.target_words}')
            if t.name == req.template_name:
                logger.info(f'[PUT] 找到模板: {t.name}, target_words={t.target_words}')
                cfg.template = t
                if req.template_name == '自定义':
                    write_env('CUSTOM_TARGET_WORDS', str(req.custom_target_words))
                    write_env('CUSTOM_PART_COUNT', str(req.custom_part_count))
                    cfg._target_words = req.custom_target_words
                    cfg._part_count = req.custom_part_count
                    cfg._part_word_min = max(1000, req.custom_target_words // req.custom_part_count // 2)
                    cfg._part_word_max = min(10000, req.custom_target_words // req.custom_part_count * 2)
                else:
                    delete_env('CUSTOM_TARGET_WORDS')
                    delete_env('CUSTOM_PART_COUNT')
                    cfg._target_words = t.target_words
                    cfg._part_count = t.part_count
                    cfg._part_word_min = t.part_word_min
                    cfg._part_word_max = t.part_word_max
                template_found = True
                break
        logger.info(f'[PUT] 模板更新后: {cfg.template.name}, target_words={cfg._target_words}, part_count={cfg._part_count}')
        from core.config import _sync_from_app, reload_config
        _sync_from_app()
        reload_config()
        logger.info(f'[PUT] 模板更新成功: {req.template_name}, target_words={cfg._target_words}, part_count={cfg._part_count}')
        return {'ok': True}
    except Exception as e:
        logger.info(f'[PUT] Error in update_app_config: {e}')
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f'Internal Server Error: {str(e)}')
import os as _os
from core.sliding_window import WINDOW_CONFIG_FILE as _WCF
from core.logger import get_logger
logger = get_logger('config_api')

def load_window_config() -> dict:
    """从 data/window_config.json 读取滑动窗口配置。"""
    try:
        if _os.path.exists(_WCF):
            with open(_WCF, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_window_config(cfg: dict) -> None:
    """写入 data/window_config.json。"""
    _os.makedirs(_os.path.dirname(_WCF), exist_ok=True)
    with open(_WCF, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

class SlidingWindowConfigUpdate(BaseModel):
    window_size: int
    rolling_every: int
    milestone_every: int

@router.get('/sliding-window')
def get_sliding_window_config():
    """
    返回滑动窗口当前配置。
    从 .env / data/window_config.json / 类默认值三层 fallback 读取。
    """
    from core.sliding_window import SlidingWindow
    file_cfg = load_window_config()
    return {'window_size': int(file_cfg.get('window_size') or _os.environ.get('SLIDING_WINDOW_SIZE') or SlidingWindow.DEFAULT_WINDOW_SIZE), 'rolling_every': int(file_cfg.get('rolling_every') or _os.environ.get('SLIDING_ROLLING_EVERY') or SlidingWindow.DEFAULT_ROLLING_EVERY), 'milestone_every': int(file_cfg.get('milestone_every') or _os.environ.get('SLIDING_MILESTONE_EVERY') or SlidingWindow.DEFAULT_MILESTONE_EVERY), 'source': 'user_file' if file_cfg else 'env' if _os.environ.get('SLIDING_WINDOW_SIZE') else 'class_default', 'defaults': {'window_size': SlidingWindow.DEFAULT_WINDOW_SIZE, 'rolling_every': SlidingWindow.DEFAULT_ROLLING_EVERY, 'milestone_every': SlidingWindow.DEFAULT_MILESTONE_EVERY}}

@router.put('/sliding-window')
def update_sliding_window_config(req: SlidingWindowConfigUpdate):
    """
    UI 用户手动调整滑动窗口参数。
    写入 data/window_config.json，下次 SlidingWindow() 实例化时读取。
    范围校验：
      - window_size: 2 ~ 20
      - rolling_every: 2 ~ 10
      - milestone_every: 5 ~ 100
    """
    if not 2 <= req.window_size <= 20:
        raise HTTPException(400, f'window_size 必须在 2~20 之间，当前 {req.window_size}')
    if not 2 <= req.rolling_every <= 10:
        raise HTTPException(400, f'rolling_every 必须在 2~10 之间，当前 {req.rolling_every}')
    if not 5 <= req.milestone_every <= 100:
        raise HTTPException(400, f'milestone_every 必须在 5~100 之间，当前 {req.milestone_every}')
    new_cfg = {'window_size': int(req.window_size), 'rolling_every': int(req.rolling_every), 'milestone_every': int(req.milestone_every)}
    save_window_config(new_cfg)
    logger.info(f'[PUT] 滑动窗口配置已更新: {new_cfg}')
    return {'ok': True, 'config': new_cfg}

@router.post('/sliding-window/reset')
def reset_sliding_window_config():
    """重置为类默认值（删除 data/window_config.json）。"""
    try:
        if _os.path.exists(WINDOW_CONFIG_FILE):
            _os.remove(WINDOW_CONFIG_FILE)
        return {'ok': True, 'message': '已重置为类默认值'}
    except Exception as e:
        raise HTTPException(500, str(e))