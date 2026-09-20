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
from typing import Optional
from openai import OpenAI
from core.config import get_llm_config_for_agent, get_llm_config
from core.error_handler import LLMError, NetworkError, SystemError
from core.logger import get_logger
# R2-8: 删除死导入 strip_padding_chars（全文件未使用；truncate 保留）
from core.text_utils import truncate
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

def _is_client_error(e: Exception) -> bool:
    """R4-P1-x: 识别 4xx 客户端错误（不重试）—— 401 鉴权 / 400 参数 / 404 模型不存在。
    既认 openai SDK 异常类型，也兜底认 status_code 属性与报文关键词（兼容代理包装的异常）。"""
    try:
        from openai import AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError, UnprocessableEntityError
        if isinstance(e, (AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError, UnprocessableEntityError)):
            return True
    except ImportError:
        pass
    status = getattr(e, 'status_code', None)
    if isinstance(status, int) and 400 <= status < 500:
        return True
    msg = str(e).lower()
    return 'invalid api key' in msg or 'authentication' in msg or 'model not found' in msg or 'does not exist' in msg and 'model' in msg
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

    P2-99: 优先匹配 ```json ... ``` 块；只有没 ```json 时才回退到任意 ``` 块，
           避免模型输出 ```python ... ``` 包含 ```json 字面量时被非贪婪匹配错选。
    """
    raw = raw.strip()
    # 1) 优先匹配 ```json ... ``` 显式 JSON 块
    json_match = re.search('```json\\s*\\n?([\\s\\S]*?)\\n?```', raw, re.IGNORECASE)
    if json_match:
        raw = json_match.group(1).strip()
    else:
        # 2) 回退到任意 ``` ... ``` 块（最后一个，避免 ```python 示例被错选）
        md_matches = list(re.finditer('```([a-zA-Z]*)\\s*\\n?([\\s\\S]*?)\\n?```', raw))
        if md_matches:
            raw = md_matches[-1].group(2).strip()
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

def call_llm(system_prompt: str, user_prompt: str, temperature: float=0.7, max_tokens: int=4000, agent: str='default', stream: bool=False, stream_callback: callable=None, work_id: Optional[str]=None, expected_min_len: Optional[int]=None) -> str:
    """调用 LLM 并返回文本结果，自动重试3次。agent参数决定使用哪个供应商的配置。

    P1-87: 新增 work_id 参数 —— 透传给 cost_tracker，使 per-work 成本统计准确。
    R2-3: 新增 expected_min_len 参数 —— 调用方告知"本次输出至少应有多少字"。
      仅当 finish_reason == 'length' 且 content 短于该值（推理模型 reasoning
      吃光预算的无歧义签名）时生效：warning 日志区分空返/短返/普通截断三态，
      并在既有 3 次 attempt 循环内以 max_tokens *= 2 升级重试（与 call_llm_json
      的翻倍阶梯同构）。默认 None = 现有调用方零行为变化；不会把"模型自然写短"
      误判为重试（无 length 签名不触发）。
    """
    client, model_name = _get_client_for_agent(agent)
    temp = _safe_temperature(temperature, model_name)
    messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
    current_max = max_tokens  # R2-3: 短返升级重试时翻倍，不影响调用方传入值
    for attempt in range(3):
        try:
            call_start = time.time()
            logger.info(f'    [LLM] 第{attempt + 1}次调用开始: model={model_name}, max_tokens={current_max}, agent={agent}, stream={stream}, work_id={work_id}')
            logger.info(f'    [LLM] messages长度: {len(messages)} 条, 系统提示长度: {len(system_prompt)} 字符')
            if stream and stream_callback:
                response = client.chat.completions.create(model=model_name, messages=messages, temperature=temp, max_tokens=current_max, stream=True)
                content = ''
                last_chunk = None
                for chunk in response:
                    # R4-P1-x: 部分 provider 的流末尾发 choices=[] 的 usage-only chunk，
                    # 直接 chunk.choices[0] 会 IndexError → 整个请求白重试 3 次（token 已扣）。
                    if not chunk.choices:
                        last_chunk = chunk
                        continue
                    if chunk.choices[0].delta.content:
                        chunk_content = chunk.choices[0].delta.content
                        content += chunk_content
                        stream_callback(chunk_content)
                    last_chunk = chunk
                content = content.strip()
            else:
                response = client.chat.completions.create(model=model_name, messages=messages, temperature=temp, max_tokens=current_max)
                if not response.choices:
                    raise ValueError(f'LLM 返回空 choices: model={model_name}')
                message = response.choices[0].message
                # R4-P1-x: 推理模型（deepseek-r1 / MiniMax-M3 代理）可能把全部预算花在
                # reasoning 上，content 为 None —— None.strip() 会 AttributeError 触发无谓重试。
                content = (message.content or '')
                finish_reason = getattr(response.choices[0], 'finish_reason', None)
                if finish_reason == 'length':
                    # R4-P1-x: 此前完全不看 finish_reason —— max_tokens 截断的残篇会被当作
                    # 完整输出返回，Part 中间夹半句话。至少留下可观测痕迹。
                    # R2-3: 区分"空返/短返/普通截断"三态；短返（调用方传了
                    # expected_min_len 且 content 不足）在 attempt 循环内翻倍重试
                    # —— Round 1 实证 431 字短返曾静默覆盖 5212 字原文。
                    if not content:
                        logger.warning(f'    [LLM] 警告: finish_reason=length 且 content 为空（max_tokens={current_max}，模型可能把预算全部花在 reasoning 上）'
                                       + (f'，期望最少 {expected_min_len} 字' if expected_min_len else ''))
                    elif expected_min_len and len(content) < expected_min_len:
                        logger.warning(f'    [LLM] 警告: finish_reason=length 且输出异常短（{len(content)} 字 < 期望 {expected_min_len} 字，max_tokens={current_max}）')
                    else:
                        logger.info(f'    [LLM] 警告: finish_reason=length，输出可能被 max_tokens={current_max} 截断')
                    if expected_min_len and len(content) < expected_min_len and attempt < 2:
                        current_max *= 2
                        logger.info(f'    [LLM] 短返升级重试: max_tokens {current_max // 2} -> {current_max}')
                        continue
                # P0-44: 删除 last_chunk = None —— stream=False 路径下 usage 取自 response，
                # last_chunk 仅 stream=True 路径才有意义，赋值后再读 = dead branch
            content = _strip_think_tags(content)
            call_duration = (time.time() - call_start) * 1000
            logger.info(f'    [LLM] API调用成功，耗时: {call_duration:.2f}ms')
            content_preview = truncate(content, n=100, suffix="...")
            logger.info(f'    [LLM] 返回内容长度: {len(content)} 字符, 预览: {content_preview}')
            try:
                from core.cost_tracker import get_tracker, estimate_tokens_from_text
                tracker = get_tracker(work_id=work_id)  # P1-87: 透传 work_id 到 per-work tracker
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
            # R4-P1-x: 4xx 客户端错误（鉴权失败/参数错误/模型不存在）重试永远不会成功，
            # 此前一律重试 3 次 + 线性 sleep，纯浪费配额与用户时间。
            if _is_client_error(e):
                raise LLMError(f'LLM调用失败（客户端错误，不重试）: {e}')
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

def call_llm_json(system_prompt: str, user_prompt: str, temperature: float=0.3, max_tokens: int=4000, agent: str='default', work_id: Optional[str]=None) -> dict:
    """
    调用 LLM 并返回 JSON 结果。
    - 根据 agent 参数选择对应供应商的 JSON 模型
    - 自动检测并降级 response_format（不支持的模型靠 prompt 引导）
    - 多级 JSON 修复，兼容各种输出风格
    - 失败时最多重试3次

    P1-87: 新增 work_id 参数 —— 透传给 cost_tracker，使 per-work 成本统计准确。
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
    last_raw = ''
    call_start = time.time()
    current_max_tokens = max_tokens
    for attempt in range(3):
        try:
            kwargs = {'model': model, 'messages': [{'role': 'system', 'content': enhanced_system}, {'role': 'user', 'content': full_user_prompt}], 'temperature': temp, 'max_tokens': current_max_tokens}
            if use_response_format:
                kwargs['response_format'] = {'type': 'json_object'}
            resp = client.chat.completions.create(**kwargs)
            call_duration = (time.time() - call_start) * 1000
            if not resp.choices:
                raise ValueError('LLM 返回空 choices')
            raw = (resp.choices[0].message.content or '').strip()
            last_raw = raw  # R4-P1-x: 供最终失败时附 raw_text 给上层营救逻辑
            finish_reason = getattr(resp.choices[0], 'finish_reason', None)
            if not raw and finish_reason == 'length':
                # R1-C: 与 call_llm:224-227 对齐的可观测日志 —— 推理模型把全部预算
                # 花在 reasoning 上时 content 为空，此前 JSON 路径无痕迹，只能看到
                # "解析失败"，无法区分"没输出"与"预算被推理吃光"。
                logger.warning(
                    f'    [JSON] 警告: finish_reason=length 且 content 为空'
                    f'（model={model}, attempt={attempt + 1}, max_tokens={current_max_tokens}）'
                )
            raw = _strip_think_tags(raw)
            try:
                from core.cost_tracker import get_tracker
                tracker = get_tracker(work_id=work_id)  # P1-87: 透传 work_id
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
                # R1-C: ×1.5 阶梯对推理模型不够（1800→2700→4050 仍可能被 reasoning
                # 吃光，冒烟实证 3 连败）；直接翻倍并保底 6000。
                current_max_tokens = max(6000, int(current_max_tokens * 2))
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
            # R4-P1-x: 4xx 客户端错误不重试（同 call_llm 约定）
            if _is_client_error(e):
                raise LLMError(f'LLM调用失败（客户端错误，不重试）: {e}')
            last_error = e
            logger.info(f'  [LLM-JSON] 第{attempt + 1}次调用失败: {e}')
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    # R4-P1-x: 异常附 raw_text —— LogicReviewAgent 的 regex 营救路径读 e.raw_text，
    # 此前 SystemError 不带该属性，营救逻辑从未生效（永远返回降级评分）。
    def _raise_final(err):
        if isinstance(err, ValueError):
            exc = SystemError(f'JSON解析失败: {err}')
        elif 'network' in str(err).lower() or 'connection' in str(err).lower():
            exc = NetworkError(f'网络连接失败: {err}')
        elif 'rate_limit' in str(err).lower() or 'quota' in str(err).lower():
            exc = SystemError(f'速率限制或配额不足: {err}')
        else:
            exc = LLMError(f'LLM调用失败: {err}')
        try:
            exc.raw_text = last_raw or ''
        except Exception:
            pass
        raise exc
    _raise_final(last_error)