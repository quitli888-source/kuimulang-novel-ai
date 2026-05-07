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
from functools import lru_cache
from openai import OpenAI
from core.config import (
    get_llm_config_for_agent,
    get_llm_config,  # 向后兼容
)
from core.error_handler import LLMError, NetworkError, SystemError

# ---- 全局客户端 ----
_client = None        # 正文写作客户端
_json_client = None   # JSON任务客户端（可能与上面相同）


def get_client() -> OpenAI:
    """获取默认正文写作 LLM 客户端（全局模式）"""
    global _client
    if _client is None:
        cfg = get_llm_config()
        _client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    return _client


def get_json_client() -> OpenAI:
    """获取默认 JSON 任务专用客户端（全局模式）"""
    global _json_client
    if _json_client is None:
        cfg = get_llm_config()
        _json_client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    return _json_client


# ---- temperature 安全修正 ----
def _safe_temperature(temp: float, model: str) -> float:
    """
    部分国内模型（如 minimax）temperature 不能为 0，最低 0.01。
    deepseek / gpt 系列不受此限制。
    """
    minimax_prefixes = ("minimax", "abab", "m2", "MiniMax")
    if any(model.lower().startswith(p.lower()) for p in minimax_prefixes):
        return max(temp, 0.01)
    return temp


# ---- response_format 支持检测 ----
# 已知不支持 json_object 的模型前缀（小写）
_NO_JSON_FORMAT_MODELS = (
    "minimax", "abab", "m2", "mini-max",
    "deepseek",       # deepseek 官方 API 不稳定支持，通过中转走 gpt-4o-mini 不需要这里
    "qwen",
    "glm",
    "ernie",
)

# 运行时缓存：模型名 -> 是否支持
_response_format_cache: dict[str, bool] = {}


def _supports_response_format(model: str) -> bool:
    """判断模型是否支持 response_format=json_object"""
    # 用户手动覆盖（通过环境变量）
    _json_format_flag = os.environ.get("JSON_MODEL_SUPPORTS_RESPONSE_FORMAT", "")
    if _json_format_flag == "1":
        return True
    if _json_format_flag == "0":
        return False

    # 缓存命中
    if model in _response_format_cache:
        return _response_format_cache[model]

    # 已知不支持列表
    m_lower = model.lower()
    for prefix in _NO_JSON_FORMAT_MODELS:
        if m_lower.startswith(prefix):
            _response_format_cache[model] = False
            return False

    # 默认认为支持（gpt / claude 等）
    _response_format_cache[model] = True
    return True


# ---- 思维链过滤（MiniMax M2.7 等推理模型会输出 <think>...</think>）----
def _strip_think_tags(text: str) -> str:
    """
    移除模型输出中的 <think>...</think> 推理链内容。
    MiniMax M2.7 / deepseek-r1 等思维链模型会在输出中包含思考过程，
    正文和 JSON 任务都不需要这部分内容。
    """
    # 移除 <think>...</think> 块（可能多行）
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    return cleaned.strip()


# ---- JSON 提取与修复 ----
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

    # 1. 优先处理 markdown 代码块
    md_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", raw)
    if md_match:
        raw = md_match.group(1).strip()

    # 2. 找到第一个 { 和最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end >= start:
        raw = raw[start:end + 1]

    # 3. 移除行尾和属性末尾多余的逗号（常见于国内模型）
    # 例: "key": "value",\n} -> "key": "value"\n}
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

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
    # 第一次：直接尝试
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 第二次：提取后尝试
    cleaned = _extract_json(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第三次：单引号转双引号（某些模型会输出 Python dict 风格）
    try:
        fixed = cleaned.replace("'", '"')
        # 修复 Python True/False/None -> JSON true/false/null
        fixed = re.sub(r'\bTrue\b', 'true', fixed)
        fixed = re.sub(r'\bFalse\b', 'false', fixed)
        fixed = re.sub(r'\bNone\b', 'null', fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 第四次：尝试截断到最后一个完整的顶层 key-value
    # （应对模型输出被 max_tokens 截断的情况）
    try:
        # 找到所有顶层 } 的位置，从末尾倒推
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
        pass

    raise ValueError(f"JSON修复失败，原始内容（前500字符）: {raw[:500]}")


# ---- 核心调用函数 ----
_client = None
_json_client = None
# 缓存：agent_name -> (client, model_name)
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
    return client, cfg.model


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


def call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 4000,
    agent: str = "default",
    stream: bool = False,
    stream_callback: callable = None,
) -> str:
    """调用 LLM 并返回文本结果，自动重试3次。agent参数决定使用哪个供应商的配置。"""
    client, model_name = _get_client_for_agent(agent)
    temp = _safe_temperature(temperature, model_name)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(3):
        try:
            call_start = time.time()
            print(f"    [LLM] 第{attempt + 1}次调用开始: model={model_name}, max_tokens={max_tokens}, agent={agent}, stream={stream}")
            print(f"    [LLM] messages长度: {len(messages)} 条, 系统提示长度: {len(system_prompt)} 字符")
            
            if stream and stream_callback:
                # 流式调用
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tokens,
                    stream=True,
                )
                
                content = ""
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        chunk_content = chunk.choices[0].delta.content
                        content += chunk_content
                        stream_callback(chunk_content)
                
                content = content.strip()
            else:
                # 非流式调用
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content.strip()
            
            content = _strip_think_tags(content)
            call_duration = (time.time() - call_start) * 1000
            print(f"    [LLM] API调用成功，耗时: {call_duration:.2f}ms")
            
            content_preview = content[:100] + "..." if len(content) > 100 else content
            print(f"    [LLM] 返回内容长度: {len(content)} 字符, 预览: {content_preview}")

            # 记录Token消耗
            try:
                from core.cost_tracker import get_tracker
                tracker = get_tracker()
                usage = response.usage if not stream else None
                if usage:
                    print(f"    [LLM] Token使用: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
                    tracker.record(
                        model=model_name, agent=agent, is_json=False,
                        prompt_tokens=usage.prompt_tokens or 0,
                        completion_tokens=usage.completion_tokens or 0,
                        total_tokens=usage.total_tokens or 0,
                        duration_ms=call_duration,
                    )
                else:
                    print(f"    [LLM] 警告: usage为None，无法记录Token消耗")
            except Exception as usage_err:
                print(f"    [LLM] 成本追踪失败: {usage_err}")
                pass  # 成本追踪失败不影响主流程

            return content
        except Exception as e:
            print(f"  [LLM] 第{attempt + 1}次调用失败: {e}")
            if attempt < 2:
                wait_time = 3 * (attempt + 1)
                print(f"  [LLM] 等待{wait_time}秒后重试...")
                time.sleep(wait_time)
            else:
                print(f"  [LLM] 3次调用全部失败，抛出异常")
                # 抛出标准化的LLM错误
                if "network" in str(e).lower() or "connection" in str(e).lower():
                    raise NetworkError(f"网络连接失败: {e}")
                elif "rate_limit" in str(e).lower() or "quota" in str(e).lower():
                    raise SystemError(f"速率限制或配额不足: {e}")
                else:
                    raise LLMError(f"LLM调用失败: {e}")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    agent: str = "default",
) -> dict:
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

    # 系统提示增强：明确 JSON 输出要求
    enhanced_system = system_prompt
    if not use_response_format:
        # 对不支持 response_format 的模型，在 system prompt 末尾强化 JSON 约束
        json_constraint = (
            "\n\n【重要输出格式要求】\n"
            "你必须且只能输出一个合法的 JSON 对象（以 { 开始，以 } 结束）。\n"
            "绝对不要在 JSON 前后添加任何解释文字、markdown 代码块标记或其他内容。\n"
            "输出的第一个字符必须是 {，最后一个字符必须是 }。"
        )
        enhanced_system = system_prompt + json_constraint

    json_reminder = "\n\n请直接输出 JSON 对象，第一个字符是 {，最后一个字符是 }，不要任何其他内容。"
    full_user_prompt = user_prompt + json_reminder

    last_error = None
    call_start = time.time()
    current_max_tokens = max_tokens
    for attempt in range(3):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": enhanced_system},
                    {"role": "user", "content": full_user_prompt},
                ],
                "temperature": temp,
                "max_tokens": current_max_tokens,
            }

            # 如果模型支持 response_format，加上（可大幅减少输出噪声）
            if use_response_format:
                kwargs["response_format"] = {"type": "json_object"}

            resp = client.chat.completions.create(**kwargs)
            call_duration = (time.time() - call_start) * 1000
            raw = resp.choices[0].message.content.strip()
            raw = _strip_think_tags(raw)  # 过滤 MiniMax/deepseek-r1 的思维链

            # 记录Token消耗
            try:
                from core.cost_tracker import get_tracker
                tracker = get_tracker()
                usage = resp.usage
                if usage:
                    tracker.record(
                        model=model, agent=agent, is_json=True,
                        prompt_tokens=usage.prompt_tokens or 0,
                        completion_tokens=usage.completion_tokens or 0,
                        total_tokens=usage.total_tokens or 0,
                        duration_ms=call_duration,
                    )
            except Exception:
                pass

            # 多级 JSON 修复
            result = _repair_json(raw)
            return result

        except ValueError as e:
            # JSON 解析失败 —— 可能是输出被截断，提高 max_tokens 重试
            last_error = e
            print(f"  [JSON] 第{attempt + 1}次解析失败: {e}")
            if attempt < 2:
                current_max_tokens = int(current_max_tokens * 1.5)  # 每次增加50%
                print(f"  [JSON] 提高max_tokens到{current_max_tokens}重试...")
                time.sleep(2)

        except Exception as e:
            err_str = str(e)
            # response_format 不支持时自动降级（运行时检测）
            if use_response_format and (
                "response_format" in err_str.lower()
                or "json_object" in err_str.lower()
                or "invalid" in err_str.lower()
                or "not supported" in err_str.lower()
            ):
                print(f"  [JSON] 模型 {model} 不支持 response_format，自动降级为 prompt 引导模式")
                _response_format_cache[model] = False
                use_response_format = False
                # 重试不计次数
                kwargs.pop("response_format", None)
                continue

            last_error = e
            print(f"  [LLM-JSON] 第{attempt + 1}次调用失败: {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))

    # 抛出标准化的LLM错误
    if isinstance(last_error, ValueError):
        raise SystemError(f"JSON解析失败: {last_error}")
    elif "network" in str(last_error).lower() or "connection" in str(last_error).lower():
        raise NetworkError(f"网络连接失败: {last_error}")
    elif "rate_limit" in str(last_error).lower() or "quota" in str(last_error).lower():
        raise SystemError(f"速率限制或配额不足: {last_error}")
    else:
        raise LLMError(f"LLM调用失败: {last_error}")
