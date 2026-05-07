"""
灵感解析Agent V3 - 从用户灵感中提取核心故事要素

V3改动：
- Prompt外部化到 prompts/inspiration.txt
"""
import json
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm_json
from core.prompt_loader import load_prompt


def _normalize_inspiration_fields(data: dict) -> dict:
    """将模型可能返回的异构字段名统一规范化为标准字段名"""
    # 尝试映射常见的字段变体
    protagonist_keys = ["protagonist", "主角", "hero", "character", "story_elements"]
    conflict_keys = ["conflict", "冲突", "central_conflict", "core_conflict"]
    theme_keys = ["theme", "主题", "potential_themes", "themes"]
    emotion_keys = ["emotion_tone", "emotional_core", "tone", "emotion"]
    keywords_keys = ["world_keywords", "keywords", "key_elements", "narrative_hooks"]
    hooks_keys = ["hook_points", "hooks", "hook", "key_questions"]
    expansion_keys = ["suggested_expansion", "expansion", "suggestions", "directions"]
    potential_keys = ["story_potential", "potential", "scores"]

    def find_val(d, keys, default):
        for k in keys:
            if k in d:
                return d[k]
        return default

    protagonist_raw = find_val(data, protagonist_keys, {})
    if isinstance(protagonist_raw, str):
        protagonist_raw = {"identity": protagonist_raw, "core_trait": "", "motivation": ""}
    elif isinstance(protagonist_raw, dict):
        protagonist_raw = {
            "identity": protagonist_raw.get("identity") or protagonist_raw.get("name") or protagonist_raw.get("description") or str(protagonist_raw),
            "core_trait": protagonist_raw.get("core_trait") or protagonist_raw.get("trait") or "",
            "motivation": protagonist_raw.get("motivation") or protagonist_raw.get("goal") or "",
        }

    conflict_raw = find_val(data, conflict_keys, {})
    if isinstance(conflict_raw, str):
        conflict_raw = {"core_conflict": conflict_raw, "internal_conflict": "", "external_conflict": ""}
    elif isinstance(conflict_raw, dict):
        conflict_raw = {
            "core_conflict": conflict_raw.get("core_conflict") or conflict_raw.get("main") or str(conflict_raw),
            "internal_conflict": conflict_raw.get("internal_conflict") or conflict_raw.get("internal") or "",
            "external_conflict": conflict_raw.get("external_conflict") or conflict_raw.get("external") or "",
        }

    theme_raw = find_val(data, theme_keys, "未知")
    if isinstance(theme_raw, list):
        theme_raw = theme_raw[0] if theme_raw else "未知"

    potential_raw = find_val(data, potential_keys, {})
    if not isinstance(potential_raw, dict):
        potential_raw = {}
    potential_raw = {
        "tension": int(potential_raw.get("tension", 7)),
        "emotion": int(potential_raw.get("emotion", 7)),
        "uniqueness": int(potential_raw.get("uniqueness", 7)),
        "market_fit": int(potential_raw.get("market_fit", 7)),
    }

    def to_list(v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [v]
        return []

    return {
        "protagonist": protagonist_raw,
        "conflict": conflict_raw,
        "theme": str(theme_raw),
        "emotion_tone": str(find_val(data, emotion_keys, "未知")),
        "world_keywords": to_list(find_val(data, keywords_keys, [])),
        "hook_points": to_list(find_val(data, hooks_keys, [])),
        "story_potential": potential_raw,
        "suggested_expansion": to_list(find_val(data, expansion_keys, [])),
    }

SYSTEM_PROMPT = load_prompt("inspiration", """你是一位专业的网文策划，请分析用户给出的故事灵感，用中文填写以下JSON模板。
必须严格使用下方的字段名，不允许修改字段名，不允许添加或删除字段。

{
    "protagonist": {
        "identity": "（主角是谁，一句话）",
        "core_trait": "（主角最突出的性格特质）",
        "motivation": "（主角的核心动机是什么）"
    },
    "conflict": {
        "core_conflict": "（故事的核心冲突，一句话）",
        "internal_conflict": "（主角内心的矛盾和挣扎）",
        "external_conflict": "（主角面对的外部对抗或困境）"
    },
    "theme": "（故事想表达的主题，一句话）",
    "emotion_tone": "（情感基调，如：悬疑紧张→温情治愈）",
    "world_keywords": ["世界观关键词1", "世界观关键词2", "世界观关键词3"],
    "hook_points": ["让读者放不下书的钩子1", "钩子2", "钩子3"],
    "story_potential": {
        "tension": 8,
        "emotion": 9,
        "uniqueness": 8,
        "market_fit": 9
    },
    "suggested_expansion": ["故事可发展的方向1", "方向2", "方向3"]
}""")


class InspirationAgent(BaseAgent):
    name = "灵感解析Agent"
    description = "从用户灵感中提取核心故事要素"

    def execute(self, state) -> dict:
        self.log_start()

        user_prompt = f"""请分析以下故事灵感，提取核心要素：

【用户灵感】
{state.inspiration}

请以JSON格式输出分析结果。"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.4,
                agent=self.name,
            )

            # 兼容字段名变体（模型有时会自行命名）
            result = _normalize_inspiration_fields(result)

            self.log_done(
                f"主角: {result['protagonist']['identity']}\n"
                f"核心冲突: {result['conflict']['core_conflict']}\n"
                f"主题: {result['theme']}\n"
                f"市场契合度: {result['story_potential']['market_fit']}/10"
            )
            return result

        except Exception as e:
            self.log_error(str(e))
            return {
                "error": str(e),
                "protagonist": {"identity": "解析失败", "core_trait": "", "motivation": ""},
                "conflict": {"core_conflict": "解析失败", "internal_conflict": "", "external_conflict": ""},
                "theme": "解析失败",
                "emotion_tone": "未知",
                "world_keywords": [],
                "hook_points": [],
                "story_potential": {"tension": 5, "emotion": 5, "uniqueness": 5, "market_fit": 5},
                "suggested_expansion": [],
            }
