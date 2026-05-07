"""
逻辑校验Agent V4 - 检查情节逻辑、因果链、前文一致性

V4改动：
- 错误处理不再默认通过，改为降级评分+标记P0
"""
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm_json
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("logic_review", """你是一位严格的文学编辑，专精于网文的逻辑一致性审查。

你的任务是从以下维度审查Part内容，找出所有逻辑问题：

## 审查维度

1. **因果链检查**
   - 事件A是否合理导致事件B？
   - 角色的行动是否有充分动机？
   - 是否存在"因为剧情需要"的强行转折？

2. **前文一致性**
   - 角色的位置/状态是否与前文结尾一致？
   - 角色是否知道了他不该知道的信息？
   - 时间线是否连续（不能突然跳到"第二天"而没有交代）？
   - 已发生的事件是否被正确引用（不能遗忘或矛盾）？

3. **人物一致性**
   - 角色言行是否符合已建立的人设？
   - 角色的情绪变化是否有合理过渡？

4. **设定一致性**
   - 是否违反了已建立的世界观规则？
   - 设定细节是否前后矛盾？

5. **伏笔检查**
   - 应该埋设的伏笔是否已自然融入？
   - 应该揭晓的伏笔是否得到了合理解释？

## 问题分级

- **P0（致命）**：与前文直接矛盾或逻辑断裂，读者会困惑
- **P1（严重）**：缺乏因果关系或动机不足，影响阅读体验
- **P2（轻微）**：可优化的细节问题

## 严格评分标准

- 9-10分：逻辑完美，无任何问题
- 7-8分：有小问题但不影响阅读（合格）
- 5-6分：有明显逻辑瑕疵（需要返工）
- 3-4分：逻辑混乱（必须重写）
- 1-2分：完全不合格

## 输出格式

以JSON格式输出：
{
    "pass": true/false,
    "overall_score": 1-10,
    "issues": [
        {
            "level": "P0/P1/P2",
            "dimension": "因果链/前文一致性/人物一致性/设定一致性/伏笔",
            "location": "问题位置",
            "description": "问题描述",
            "suggestion": "修正建议"
        }
    ],
    "continuity_check": {
        "character_states": "角色状态是否与前文一致",
        "timeline": "时间线是否连续",
        "established_facts": "已建立事实是否被正确引用"
    },
    "strengths": ["本章优点"],
    "verdict": "一句话判断"
}""")


class LogicReviewAgent(BaseAgent):
    name = "逻辑校验Agent"
    description = "检查情节逻辑和前文一致性"

    def execute(self, state, part_num: int, part_text: str) -> dict:
        self.log_start()

        outline = state.part_outline[part_num - 1] if state.part_outline else {}
        characters_info = ""
        if state.characters:
            characters_info = "\n".join(
                f"- {c['name']}({c['role']}): {c['core_trait']}, 动机:{c['motivation']}, 秘密:{c['secret']}"
                for c in state.characters
            )

        # 构建前文信息
        prev_context = ""
        if part_num > 1 and state.part_summaries:
            prev_context = "【前文摘要】\n"
            for p_num in sorted(state.part_summaries.keys()):
                if p_num < part_num:
                    prev_context += f"Part {p_num}: {state.part_summaries[p_num]}\n"

        if part_num > 1 and part_num - 1 in state.parts:
            prev_tail = state.parts[part_num - 1][-500:]
            prev_context += f"\n【前一部分（Part {part_num-1}）结尾】\n{prev_tail}"

        has_prev = bool(prev_context)
        prev_note = "（注意：这是第一部分，没有前文，不需要检查前文一致性）" if not has_prev else ""

        user_prompt = f"""请审查Part {part_num}的逻辑一致性。

## Part规划
核心事件：{outline.get('core_event', '')}
情绪目标：{outline.get('emotion_target', '')}
与前文因果关系：{outline.get('causality', '')}

## 角色档案
{characters_info}

## 世界观
{state.world_setting}

## 伏笔任务
埋设：{outline.get('foreshadow_plant', [])}
揭晓：{outline.get('foreshadow_reveal', [])}

{prev_context}

## Part {part_num}正文
{part_text}

请以JSON格式输出审查结果。{prev_note}重点关注与前文的一致性（如果有前文的话）。"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                agent=self.name,
            )

            p0_count = len([i for i in result.get("issues", []) if i.get("level") == "P0"])
            p1_count = len([i for i in result.get("issues", []) if i.get("level") == "P1"])

            self.log_done(
                f"Part {part_num} 逻辑评分: {result.get('overall_score', 0)}/10\n"
                f"问题: P0x{p0_count} P1x{p1_count} P2x{len(result.get('issues', [])) - p0_count - p1_count}\n"
                f"判定: {result.get('verdict', '')}"
            )
            return result

        except Exception as e:
            self.log_error(str(e))
            return {
                "pass": False, "overall_score": 3,
                "issues": [{"level": "P0", "dimension": "自动审查", "location": "全局",
                            "description": f"逻辑审查Agent执行失败: {e}", "suggestion": "需人工核查此Part的逻辑一致性"}],
                "strengths": [], "verdict": f"审查失败（已降级评分）: {e}",
                "continuity_check": {"character_states": "未检查", "timeline": "未检查", "established_facts": "未检查"},
            }
