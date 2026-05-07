"""
文笔优化Agent V4.1 - 语言润色 + 文学性增强 + 字数约束

V4.1改动：
- 优先级重排：文学性增强 > 语言打磨 > 字数控制
- 提高temperature让润色更有文学性
- 保留更多文学表达，减少过度精简
"""
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm
from core.config import PART_WORD_MAX
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("style_optimizer", """你是一位文笔细腻、品味高级的文学编辑，擅长让文字从"能看"变成"好看"。

## 优化原则（按优先级排序）

### 第一优先级：文学性增强
你的核心任务是让文字更有质感、更有画面感、更有情绪感染力。
1. **加感官层次**：在关键场景植入视觉、听觉、嗅觉、触觉细节，让读者"身临其境"
   - 好："走廊里的灯管一闪一闪，电流声像苍蝇嗡嗡响" → 画面+声音+情绪
   - 差："走廊的灯光很昏暗"
2. **动作展现情绪**：把直白的情绪标签转化为具体的身体反应和微小动作
   - 好："她的指尖在桌面上划了一下，又缩回去" → 紧张、犹豫
   - 差："她感到紧张和犹豫"
3. **对话要有潜台词**：好的对话不是把话说完，而是留一半——角色嘴上说的和心里想的不一样
4. **环境即情绪**：让环境描写服务于情绪，不是单独存在。天晴天雨、明暗冷暖，都暗示角色内心
5. **结尾要有余韵**：不要说破。留一个画面、一个声音、一个未完成的动作，让读者自己去感受

### 第二优先级：语言打磨
1. **句式节奏**：长短句交替。紧张时短句密集，舒缓时放慢节奏。避免连续5句以上相同句式
2. **去陈词**：替换"心中一沉""不禁笑了""不由得""竟然""嘴角微微上扬"等网文陈词滥调，用更精确、更独特的表达
3. **精准动词**：一个精准的动词胜过三个形容词。能用"攥"就不用"紧紧地握"
4. **比喻要新鲜**：避免"像一把刀""如坠冰窟"等烂大街的比喻，寻找更独特的意象
5. **留白**：不是所有情绪都需要说破，有时候一个省略号、一句没说完的话，比长篇大论更有力量

### 第三优先级：字数控制（底线约束）
- 字数是底线，不是目标。在{max_words}字以内完成优化即可
- 如果原文超长，优先精简冗余（重复表达、无信息量的过渡句），而不是删减文学性内容
- 精简时确保每个保留的句子都有存在价值

### P0/P1问题强制修正
- 如果复核报告中有P0问题，必须完全修正，不留残余
- 如果复核报告中有P1问题，必须修正核心问题
- 修正时不改变情节走向，只修正逻辑矛盾和表达问题

## 严格约束

- 不要改变故事情节和核心内容
- 不要增加新的情节或事件
- 不要改变角色人设
- 输出字数必须 <= {max_words}字（硬约束）

## 输出格式

只输出优化后的正文，不要输出任何说明或标注。""")


class StyleOptimizerAgent(BaseAgent):
    name = "文笔优化Agent"
    description = "语言润色、字数控制、吸引力增强"

    def execute(self, state, part_num: int, part_text: str, review_results: dict = None, target_max: int = PART_WORD_MAX, target_min: int = 0) -> str:
        self.log_start()

        original_len = len(part_text)

        # 构建优化指令
        optimization_notes = ""

        if review_results:
            logic_review = review_results.get("logic", {})
            emotion_review = review_results.get("emotion", {})

            issues = []
            for issue in logic_review.get("issues", []):
                if issue.get("level") in ("P0", "P1"):
                    desc = issue.get('description', issue.get('location', '未描述'))
                    issues.append(f"[逻辑-{issue['level']}] {desc}\n  修正: {issue.get('suggestion', '')}")

            # 前文一致性检查
            continuity = logic_review.get("continuity_check", {})
            for key, val in continuity.items():
                if val and val not in ("未检查", "一致", ""):
                    issues.append(f"[一致性] {key}: {val}")

            for sug in emotion_review.get("enhancement_suggestions", []):
                issues.append(f"[情感增强] {sug.get('location', '')}: {sug.get('suggested', '')}")

            if issues:
                optimization_notes = "## 必须修正的问题\n" + "\n".join(issues) + "\n\n"

        outline = state.part_outline[part_num - 1] if state.part_outline else {}
        is_over = original_len > target_max

        # 根据是否超长选择不同的系统prompt侧重点
        if is_over:
            mode_instruction = f"""
## 超长警告
原文{original_len}字，超出上限{target_max}字{original_len - target_max}字。
你需要精简约{original_len - target_max}字，但不要大刀阔斧地删——只删冗余和废话，保留所有情节。
"""
        else:
            mode_instruction = ""

        # 目标字数：取实际长度和上限的较小值
        effective_max = max(original_len, target_max)

        user_prompt = f"""请优化Part {part_num}的语言表达。

## Part信息
阶段：{outline.get('phase', '')}
情绪目标：{outline.get('emotion_target', '')}
结尾钩子：{outline.get('end_hook', '')}

{optimization_notes}{mode_instruction}
## 字数约束
原文：{original_len}字
目标：保持与原文相近的字数（{effective_max}字以内）
{"可以适当精简" if is_over else "不要删减内容，只优化表达质量"}

## 原文
{part_text}

请输出优化后的正文。只输出正文，不要任何标注。"""

        try:
            # V4.1: 提高temperature让润色更有文学性
            temp = 0.5 if is_over else 0.7
            optimized = call_llm(
                system_prompt=SYSTEM_PROMPT.format(max_words=target_max),
                user_prompt=user_prompt,
                temperature=temp,
                max_tokens=effective_max + 500,
                agent=self.name,
            )

            optimized_len = len(optimized)

            # V4: 截断阈值从1.5→1.2，更严格控制字数
            if optimized_len > target_max * 1.2:
                # 但截断后不能低于最低字数（如果有传入target_min）
                safe_min = max(target_min, target_max * 0.7) if target_min > 0 else target_max * 0.7
                if optimized_len > safe_min:
                    print(f"  [截断] Part {part_num}: {optimized_len}字 -> 截断到{target_max}字")
                    truncated = optimized[:target_max]
                    last_break = max(truncated.rfind("\n\n"), truncated.rfind("\n"))
                    if last_break > target_max * 0.7:
                        optimized = truncated[:last_break].rstrip()
                        optimized_len = len(optimized)
                    else:
                        optimized = truncated.rstrip()
                        optimized_len = len(optimized)
                else:
                    print(f"  [警告] Part {part_num}: {optimized_len}字超长但截断会低于安全下限{safe_min}字，保留原文")

            diff_pct = (optimized_len - original_len) / original_len * 100 if original_len > 0 else 0

            self.log_done(
                f"Part {part_num}「{outline.get('title', '')}」\n"
                f"原文: {original_len}字 → 优化后: {optimized_len}字 ({diff_pct:+.1f}%)"
                f"{' [超长已截断]' if is_over else ''}"
            )
            return optimized

        except Exception as e:
            self.log_error(str(e))
            # 优化失败时直接返回原文（不截断）
            return part_text
