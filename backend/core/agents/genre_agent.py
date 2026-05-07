"""
题材判断Agent V3 - 判断故事所属题材并给出市场定位建议

V3改动：
- Prompt外部化到 prompts/genre.txt
"""
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm_json
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("genre", """你是一位番茄小说平台的内容运营专家，深谙平台算法推荐机制和用户偏好。

你的任务是根据故事核心要素，判断最合适的题材分类，并提供市场定位建议。

番茄小说标签系统：
- 一级分类：男生小说、女生小说
- 二级分类：悬疑、都市、玄幻、言情、科幻、历史等
- 三级分类：无限流、甜宠、重生、末世、逆袭、穿越等
- 四级分类：快节奏、打脸、反转、高智商等风格标签

平台用户特征：
- 下沉市场用户占比51.43%
- 日活超1000万，日均阅读80分钟
- 男性偏好：玄幻、都市、战神、逆袭
- 女性偏好：言情、穿书、甜宠、重生
- 2万字短篇：快节奏、强冲突、高情感密度

你必须以JSON格式输出：
{
    "genre_primary": "主题材（如'悬疑'、'言情'、'科幻'、'历史'）",
    "genre_secondary": "子类型（如'无限流'、'甜宠'、'末世'、'穿越'）",
    "target_gender": "目标受众性别（'male'、'female'、'both'）",
    "tags": {
        "level1": "一级标签",
        "level2": "二级标签",
        "level3": "三级标签",
        "level4": ["四级标签1", "四级标签2", "四级标签3"]
    },
    "market_analysis": {
        "popularity": "该题材在番茄的受欢迎程度（高/中/低）",
        "competition": "竞争激烈程度（高/中/低）",
        "opportunity": "市场机会描述",
        "similar_hits": ["类似爆款作品1", "类似爆款作品2"]
    },
    "writing_strategy": {
        "opening_hook": "开篇建议（如何在前500字抓住读者）",
        "pace_advice": "节奏建议",
        "emotion_points": ["必须设置的情感爆发点1", "情感爆发点2", "情感爆发点3"],
        "ending_advice": "结尾建议"
    },
    "word_distribution": {
        "opening": "开篇字数建议（如'3000字'）",
        "development": "发展段字数建议",
        "climax": "高潮段字数建议",
        "ending": "结尾字数建议"
    }
}

注意：
- word_distribution的总和必须等于{TARGET_WORD_COUNT}字
- emotion_points必须至少3个
- tags的level4必须至少3个标签
- writing_strategy要具体可执行""")


class GenreAgent(BaseAgent):
    name = "题材判断Agent"
    description = "判断故事题材并给出市场定位"

    def execute(self, state) -> dict:
        self.log_start()

        elements = state.core_elements

        user_prompt = f"""请根据以下故事核心要素，判断题材分类和市场定位：

【主角】{elements.get('protagonist', {}).get('identity', '未知')}
【核心冲突】{elements.get('conflict', {}).get('core_conflict', '未知')}
【主题】{elements.get('theme', '未知')}
【情感基调】{elements.get('emotion_tone', '未知')}
【世界观关键词】{elements.get('world_keywords', [])}
【钩子点】{elements.get('hook_points', [])}
【创意扩展方向】{elements.get('suggested_expansion', [])}

请以JSON格式输出。"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                agent=self.name,
            )

            self.log_done(
                f"题材: {result['genre_primary']} > {result['genre_secondary']}\n"
                f"目标受众: {'男性' if result['target_gender'] == 'male' else '女性' if result['target_gender'] == 'female' else '通用'}\n"
                f"市场热度: {result['market_analysis']['popularity']}\n"
                f"标签: {result['tags']['level3']} + {', '.join(result['tags']['level4'])}"
            )
            return result

        except Exception as e:
            self.log_error(str(e))
            return {
                "genre_primary": "都市",
                "genre_secondary": "情感",
                "target_gender": "both",
                "tags": {"level1": "女生小说", "level2": "言情", "level3": "甜宠", "level4": ["快节奏", "反转"]},
                "market_analysis": {"popularity": "中", "competition": "中", "opportunity": str(e), "similar_hits": []},
                "writing_strategy": {"opening_hook": "", "pace_advice": "", "emotion_points": [], "ending_advice": ""},
                "word_distribution": {"opening": "3000字", "development": "14000字", "climax": "2000字", "ending": "1000字"},
            }
