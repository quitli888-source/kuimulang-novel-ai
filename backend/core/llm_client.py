"""
番茄小说AI创作系统 V5 - LLM调用基类
V5改动：
- 支持多供应商（MiniMax / DeepSeek / OpenAI / SiliconFlow）
- 每个Agent可独立选择供应商
- 从 backend.core.config 读取多供应商配置
V5.1改动：集成错误处理系统
"""
import json
import re
import time
import os
from openai import OpenAI
from core.config import get_llm_config_for_agent, get_llm_config
from core.error_handler import LLMError, NetworkError, SystemError
from core.logger import get_logger
logger = get_logger('llm_client')

def _safe_temperature(temp: float, model: str) -> float:
    """
    部分国内模型（如 minimax）temperature 不能为 0，最低 0.01。
    deepseek / gpt 系列不受此限制。
    """
    minimax_prefixes = ('minimax', 'abab', 'm2', 'MiniMax')
    if any((model.lower().startswith(p.lower()) for p in minimax_prefixes)):
        return max(temp, 0.01)
    return temp
_NO_JSON_FORMAT_MODELS = ('minimax', 'abab', 'm2', 'mini-max', 'deepseek', 'qwen', 'glm', 'ernie', 'step')
_response_format_cache: dict[str, bool] = {}

def _supports_response_format(model: str) -> bool:
    """判断模型是否支持 response_format=json_object"""
    _json_format_flag = os.environ.get('JSON_MODEL_SUPPORTS_RESPONSE_FORMAT', '')
    if _json_format_flag == '1':
        return True
    if _json_format_flag == '0':
        return False
    if model in _response_format_cache:
        return _response_format_cache[model]
    m_lower = model.lower()
    for prefix in _NO_JSON_FORMAT_MODELS:
        if m_lower.startswith(prefix):
            _response_format_cache[model] = False
            return False
    _response_format_cache[model] = True
    return True

def _strip_think_tags(text: str) -> str:
    """
    移除模型输出中的 <think>...</think> 推理链内容。
    MiniMax M2.7 / deepseek-r1 等思维链模型会在输出中包含思考过程，
    正文和 JSON 任务都不需要这部分内容。
    """
    cleaned = re.sub('<think>[\\s\\S]*?</think>', '', text, flags=re.IGNORECASE)
    return cleaned.strip()

def _extract_json(raw: str) -> str:
    """
    从 LLM 返回文本中提取 JSON 字符串。
    处理以下情况：
    1. ```json ... ``` 或 ``` ... ``` markdown 包裹
    2. 前后有解释文字
    3. 模型在 JSON 前加了"以下是..."之类引导语
    4. 末尾有多余的逗号（宽松修复）
    """
    raw = raw.strip()
    md_match = re.search('```(?:json)?\\s*\\n?([\\s\\S]*?)\\n?```', raw)
    if md_match:
        raw = md_match.group(1).strip()
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and (end >= start):
        raw = raw[start:end + 1]
    raw = re.sub(',\\s*([}\\]])', '\\1', raw)
    return raw

def _repair_json(raw: str) -> dict:
    """
    多级 JSON 修复策略：
    1. 直接解析
    2. _extract_json 后解析
    3. 尝试 json5 风格（单引号→双引号）
    4. 匹配最外层 { } 重新截取
    抛出 ValueError 表示完全失败
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.debug('llm_client: silent except (P2-19)', exc_info=True)
    cleaned = _extract_json(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.debug('llm_client: silent except (P2-19)', exc_info=True)
    try:
        fixed = cleaned.replace("'", '"')
        fixed = re.sub('\\bTrue\\b', 'true', fixed)
        fixed = re.sub('\\bFalse\\b', 'false', fixed)
        fixed = re.sub('\\bNone\\b', 'null', fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        logger.debug('llm_client: silent except (P2-19)', exc_info=True)
    try:
        brace_depth = 0
        last_valid_end = -1
        for i, ch in enumerate(cleaned):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    last_valid_end = i
                    break
        if last_valid_end != -1:
            return json.loads(cleaned[:last_valid_end + 1])
    except json.JSONDecodeError:
        logger.debug('llm_client: silent except (P2-19)', exc_info=True)
    raise ValueError(f'JSON修复失败，原始内容（前500字符）: {raw[:500]}')
_client = None
_json_client = None
_client_cache: dict = {}

def _get_client_for_agent(agent: str) -> tuple:
    """
    根据Agent获取对应的LLM客户端和模型名。
    支持每个Agent独立选择不同供应商。
    """
    if agent in _client_cache:
        return _client_cache[agent]
    cfg = get_llm_config_for_agent(agent)
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    _client_cache[agent] = (client, cfg.model)
    return (client, cfg.model)

def get_client() -> OpenAI:
    """获取默认LLM客户端"""
    global _client
    if _client is None:
        cfg = get_llm_config()
        _client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    return _client

def get_json_client() -> OpenAI:
    """获取默认JSON任务LLM客户端"""
    global _json_client
    if _json_client is None:
        cfg = get_llm_config()
        _json_client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    return _json_client

def reset_llm_clients() -> None:
    """R21-P2-22: 统一重置所有 LLM 客户端缓存 + 全局单例。
    供 config_api.py 在切换供应商/修改配置后调用，避免每个调用点都直接操作全局变量。
    """
    global _client, _json_client, _client_cache
    _client = None
    _json_client = None
    _client_cache.clear()

def call_llm(system_prompt: str, user_prompt: str, temperature: float=0.7, max_tokens: int=4000, agent: str='default', stream: bool=False, stream_callback: callable=None) -> str:
    """调用 LLM 并返回文本结果，自动重试3次。agent参数决定使用哪个供应商的配置。"""
    client, model_name = _get_client_for_agent(agent)
    temp = _safe_temperature(temperature, model_name)
    messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
    for attempt in range(3):
        try:
            call_start = time.time()
            logger.info(f'    [LLM] 第{attempt + 1}次调用开始: model={model_name}, max_tokens={max_tokens}, agent={agent}, stream={stream}')
            logger.info(f'    [LLM] messages长度: {len(messages)} 条, 系统提示长度: {len(system_prompt)} 字符')
            if stream and stream_callback:
                response = client.chat.completions.create(model=model_name, messages=messages, temperature=temp, max_tokens=max_tokens, stream=True)
                content = ''
                last_chunk = None
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        chunk_content = chunk.choices[0].delta.content
                        content += chunk_content
                        stream_callback(chunk_content)
                    last_chunk = chunk
                content = content.strip()
            else:
                response = client.chat.completions.create(model=model_name, messages=messages, temperature=temp, max_tokens=max_tokens)
                content = response.choices[0].message.content.strip()
                last_chunk = None
            content = _strip_think_tags(content)
            call_duration = (time.time() - call_start) * 1000
            logger.info(f'    [LLM] API调用成功，耗时: {call_duration:.2f}ms')
            content_preview = content[:100] + '...' if len(content) > 100 else content
            logger.info(f'    [LLM] 返回内容长度: {len(content)} 字符, 预览: {content_preview}')
            try:
                from core.cost_tracker import get_tracker, estimate_tokens_from_text
                tracker = get_tracker()
                usage = None
                if not stream:
                    usage = getattr(response, 'usage', None)
                elif last_chunk is not None and getattr(last_chunk, 'usage', None) is not None:
                    usage = last_chunk.usage
                if usage is not None:
                    logger.info(f'    [LLM] Token使用: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}')
                    tracker.record(model=model_name, agent=agent, is_json=False, prompt_tokens=usage.prompt_tokens or 0, completion_tokens=usage.completion_tokens or 0, total_tokens=usage.total_tokens or 0, duration_ms=call_duration)
                else:
                    try:
                        sys_prompt_text = system_prompt or ''
                        user_prompt_text = user_prompt or ''
                        completion_text = content or ''
                        prompt_chars = len(sys_prompt_text) + len(user_prompt_text)
                        prompt_tokens = estimate_tokens_from_text(sys_prompt_text + '\n' + user_prompt_text)
                        completion_tokens = estimate_tokens_from_text(completion_text)
                        total_tokens = prompt_tokens + completion_tokens
                        logger.info(f'    [LLM] R7-P0-3 兜底估算: prompt_chars={prompt_chars} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} total={total_tokens}')
                        tracker.record(model=model_name, agent=agent, is_json=False, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens, duration_ms=call_duration, estimated=True)
                    except Exception as est_err:
                        logger.info(f'    [LLM] R7-P0-3 兜底估算失败（不影响主流程）: {est_err}')
            except Exception as usage_err:
                logger.info(f'    [LLM] 成本追踪失败: {usage_err}')
                pass
            return content
        except Exception as e:
            logger.info(f'  [LLM] 第{attempt + 1}次调用失败: {e}')
            if attempt < 2:
                wait_time = 3 * (attempt + 1)
                logger.info(f'  [LLM] 等待{wait_time}秒后重试...')
                time.sleep(wait_time)
            else:
                logger.info(f'  [LLM] 3次调用全部失败，抛出异常')
                if 'network' in str(e).lower() or 'connection' in str(e).lower():
                    raise NetworkError(f'网络连接失败: {e}')
                elif 'rate_limit' in str(e).lower() or 'quota' in str(e).lower():
                    raise SystemError(f'速率限制或配额不足: {e}')
                else:
                    raise LLMError(f'LLM调用失败: {e}')

def call_llm_json(system_prompt: str, user_prompt: str, temperature: float=0.3, max_tokens: int=4000, agent: str='default') -> dict:
    """
    调用 LLM 并返回 JSON 结果。
    - 根据 agent 参数选择对应供应商的 JSON 模型
    - 自动检测并降级 response_format（不支持的模型靠 prompt 引导）
    - 多级 JSON 修复，兼容各种输出风格
    - 失败时最多重试3次
    """
    cfg = get_llm_config_for_agent(agent)
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    model = cfg.json_model
    temp = _safe_temperature(temperature, model)
    use_response_format = _supports_response_format(model)
    enhanced_system = system_prompt
    if not use_response_format:
        json_constraint = '\n\n【重要输出格式要求】\n你必须且只能输出一个合法的 JSON 对象（以 { 开始，以 } 结束）。\n绝对不要在 JSON 前后添加任何解释文字、markdown 代码块标记或其他内容。\n输出的第一个字符必须是 {，最后一个字符必须是 }。'
        enhanced_system = system_prompt + json_constraint
    json_reminder = '\n\n请直接输出 JSON 对象，第一个字符是 {，最后一个字符是 }，不要任何其他内容。'
    full_user_prompt = user_prompt + json_reminder
    last_error = None
    call_start = time.time()
    current_max_tokens = max_tokens
    for attempt in range(3):
        try:
            kwargs = {'model': model, 'messages': [{'role': 'system', 'content': enhanced_system}, {'role': 'user', 'content': full_user_prompt}], 'temperature': temp, 'max_tokens': current_max_tokens}
            if use_response_format:
                kwargs['response_format'] = {'type': 'json_object'}
            resp = client.chat.completions.create(**kwargs)
            call_duration = (time.time() - call_start) * 1000
            raw = resp.choices[0].message.content.strip()
            raw = _strip_think_tags(raw)
            try:
                from core.cost_tracker import get_tracker
                tracker = get_tracker()
                usage = resp.usage
                if usage:
                    tracker.record(model=model, agent=agent, is_json=True, prompt_tokens=usage.prompt_tokens or 0, completion_tokens=usage.completion_tokens or 0, total_tokens=usage.total_tokens or 0, duration_ms=call_duration)
            except Exception:
                logger.debug('llm_client: silent except (P2-19)', exc_info=True)
            result = _repair_json(raw)
            return result
        except ValueError as e:
            last_error = e
            logger.info(f'  [JSON] 第{attempt + 1}次解析失败: {e}')
            if attempt < 2:
                current_max_tokens = int(current_max_tokens * 1.5)
                logger.info(f'  [JSON] 提高max_tokens到{current_max_tokens}重试...')
                time.sleep(2)
        except Exception as e:
            err_str = str(e)
            if use_response_format and ('response_format' in err_str.lower() or 'json_object' in err_str.lower() or 'invalid' in err_str.lower() or ('not supported' in err_str.lower())):
                logger.info(f'  [JSON] 模型 {model} 不支持 response_format，自动降级为 prompt 引导模式')
                _response_format_cache[model] = False
                use_response_format = False
                kwargs.pop('response_format', None)
                continue
            last_error = e
            logger.info(f'  [LLM-JSON] 第{attempt + 1}次调用失败: {e}')
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    if isinstance(last_error, ValueError):
        raise SystemError(f'JSON解析失败: {last_error}')
    elif 'network' in str(last_error).lower() or 'connection' in str(last_error).lower():
        raise NetworkError(f'网络连接失败: {last_error}')
    elif 'rate_limit' in str(last_error).lower() or 'quota' in str(last_error).lower():
        raise SystemError(f'速率限制或配额不足: {last_error}')
    else:
        raise LLMError(f'LLM调用失败: {last_error}')