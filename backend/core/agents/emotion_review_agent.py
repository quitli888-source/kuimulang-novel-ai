"""
情感评估Agent V4 - 评估情感共鸣、高潮设计和情绪曲线

V4改动：
- 错误处理不再默认通过，改为降级评分+标记P0
"""
from core.agents.base_agent import BaseAgent
from core.agents._helpers import sorted_part_nums  # P2-65
from core.llm_client import call_llm_json
from core.config import get_json_max_tokens  # R1-C: JSON 调用显式 max_tokens 统一来源
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("emotion_review", """你是一位情感分析专家，专门评估网文的情感冲击力和读者共鸣效果。

你的任务是从以下维度评估Part内容的情感效果：

## 评估维度

1. **情感共鸣强度**
   - 读者是否能代入角色的情感？
   - 是否有让人"心头一紧""鼻子一酸"的时刻？
   - 情感表达是否自然而非说教？

2. **情绪目标达成度**
   - 本Part的情绪目标是否达成？
   - 情感密度是否足够（避免平淡段落）？

3. **高潮设计**
   - 本Part是否有情感高潮点？
   - 高潮的铺垫是否充分？
   - 高潮的释放是否有力度？

4. **节奏与张力**
   - 情绪是否有起伏（避免平铺直叙）？
   - 张力是否在逐步积累？
   - 是否有适当的"松弛"让读者喘息？

5. **感官沉浸度**
   - 是否调动了读者的感官（视觉、听觉、触觉）？
   - 场景是否有画面感？
   - 读者是否"身临其境"？

## 评估标准

- 9-10分：令人拍案叫绝，情感冲击强烈
- 7-8分：效果好，能引发共鸣
- 5-6分：及格，但缺乏亮点
- 3-4分：平淡，读者可能跳读
- 1-2分：失败，情感断裂或虚假

## 输出格式

以JSON格式输出：
{
    "pass": true/false,
    "emotion_score": 1-10,
    "resonance_score": 1-10,
    "immersion_score": 1-10,
    "emotion_target_met": true/false,
    "emotion_curve": {
        "start": "起始情绪（如'紧张'）",
        "middle": "中段情绪（如'压抑'）",
        "end": "结尾情绪（如'震惊'）"
    },
    "highlights": ["本Part情感亮点1", "亮点2"],
    "weaknesses": ["情感薄弱点1", "薄弱点2"],
    "enhancement_suggestions": [
        {
            "location": "建议增强的位置",
            "current": "当前写法",
            "suggested": "建议改法",
            "expected_effect": "预期效果"
        }
    ],
    "verdict": "一句话情感评价"
}""")


class EmotionReviewAgent(BaseAgent):
    name = "情感评估Agent"
    description = "评估Part情感共鸣和情绪曲线"

    def execute(self, state, part_num: int, part_text: str) -> dict:
        self.log_start()

        outline = state.part_outline[part_num - 1] if state.part_outline else {}

        # R7-P0-2: 用 int keys 比较
        prev_context = ""
        if part_num > 1 and state.part_summaries:
            prev_context = "【前文情感脉络】\n"
            for p_num in sorted_part_nums(state):
                if p_num < part_num:
                    prev_context += f"Part {p_num}: {state.part_summaries[str(p_num)]}\n"

        user_prompt = f"""请评估Part {part_num}的情感效果。

## 本Part信息
标题：{outline.get('title', '')}
阶段：{outline.get('phase', '')}
情绪目标：{outline.get('emotion_target', '')}
核心事件：{outline.get('core_event', '')}

{prev_context}
## Part {part_num}正文
{part_text}

请以JSON格式输出评估结果。"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                # R1-C: 显式传 max_tokens（默认 4000 会被推理模型 reasoning 吃光）
                max_tokens=get_json_max_tokens(),
            agent=self.name, work_id=getattr(state, 'work_id', None))

            self.log_done(
                f"Part {part_num} 情感评分: {result.get('emotion_score', 0)}/10\n"
                f"共鸣度: {result.get('resonance_score', 0)}/10\n"
                f"沉浸度: {result.get('immersion_score', 0)}/10\n"
                f"判定: {result.get('verdict', '')}"
            )
            return result

        except Exception as e:
            self.log_error(str(e))
            return {
                "pass": False, "emotion_score": 3, "resonance_score": 3,
                "immersion_score": 3, "emotion_target_met": False,
                "emotion_curve": {"start": "未知", "middle": "未知", "end": "未知"},
                "highlights": [], "weaknesses": [f"情感评估Agent执行失败: {e}"],
                "enhancement_suggestions": [],
                "verdict": f"评估失败（已降级评分）: {e}",
            }
