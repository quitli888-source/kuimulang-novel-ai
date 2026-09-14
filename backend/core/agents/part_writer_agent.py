"""
Part写作Agent V4 - 核心创作引擎

V4改动：
- 使用新的BaseAgent接口
- 支持进度跟踪
- 改进错误处理
- 实现标准化的Agent接口

R2改动（生成器式分块生成）：
- 单 Part 超过 3500 字时改为多片段循环续写
- 后续片段仅注入"上一片段末尾 800 字 + 下一片段计划"
- 解决 50 万字场景下 PART_WORD_MAX=10000 单次必爆 token 上限的问题
"""
import time
from typing import Dict, Any
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm
from core.config import PART_WORD_MIN, PART_WORD_MAX
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("part_writer", """你是一位番茄小说平台的顶级短篇作家。你的作品以快节奏、强冲突、高情感密度著称，读者一旦开始就无法放下。

## 番茄快节奏铁律（必须逐条遵守）

1. **第一句话就要抓人**：绝不能以环境描写、"XX醒来"、天气描写开头。第一句要么是动作、要么是对白、要么是悬念
2. **300字一推进**：每300字内必须有事件推进、情绪转折、或新悬念抛出。如果某段300字没有推进，删掉重写
3. **对白驱动**：对话占比≥40%。用对话讲故事，用对话展现性格，用对话推进情节
4. **短段落**：一个自然段不超过3行（手机一屏可见）。禁止大段文字堆砌
5. **感官代替标签**：禁止"他感到害怕""她觉得心碎"这类情绪标签。用动作、生理反应、环境细节来表现
6. **删除一切废话**：每个句子都必须有存在价值——删掉它，读者会少知道什么关键信息？如果答案是"没什么"，就删掉

## 绝对禁止

- 禁止"心中一沉""不禁笑了""不由得""竟然"等网文陈词滥调
- 禁止超过100字的纯环境描写（除非环境本身就是情节）
- 禁止大段心理独白（把内心活动转化为动作或对话）
- 禁止回忆闪回（除非回忆直接推进当前主线，且不超过200字）
- 禁止说教和价值观输出
- 禁止与前文矛盾的任何内容

## 上下文使用

你会收到：角色档案、世界观、已完成剧情摘要、上一部分结尾、角色状态快照。
这些是你的"记忆"，你必须严格遵守：
- **角色名称必须一字不差**：角色档案中的名字就是唯一正确的名字，绝对不能自行改名
- 角色的言行必须符合档案设定
- 已发生的事件不能否认或遗忘
- 上一部分结尾的情境必须自然承接
- 不能出现与前文矛盾的信息
- 不能引入角色档案中不存在的角色（除非是路人甲等无名字的临时角色）

## 一致性红线（V3新增，最高优先级）

以下问题一旦出现，视为严重缺陷：
- 角色在前面已死/已离开，后面又出现
- 角色突然知道了前面不可能知道的信息
- 角色性格突变（与前面已建立的人设矛盾）
- 时间线不连续（前面在白天，突然变成夜晚而没有交代）
- 已明确交代的物理规则被违反

## 输出格式

只输出小说正文。
不要输出Part标题、不要输出"---"分隔线、不要输出任何标注或说明。
直接从正文第一个字开始写。""")


# R2: 生成器式分块生成常量
CHUNK_WORDS = 3500        # 单片段目标字数（中文 ~5000 tokens，留余量给 prompt 上下文）
CHUNK_OVERLAP = 800       # 片段间重叠字数（保证衔接自然）
MAX_CHUNKS = 6            # 单 Part 最多片段数（3500 × 6 = 21000 字，足够 1 万字 Part）
PROGRESS_START = 55       # Phase3 Part 写作的起始进度（在 writing_service 中会动态推进）


PART_CHUNK_SYSTEM_PROMPT = """你是番茄小说平台顶级短篇作家，正在为一部连载小说续写某个 Part 的片段。

## 核心约束（片段级）

1. **只写这一片段，不要总结、不要预告、不要回顾**
2. **如果提供了【已写片段末尾】，必须从该结尾自然续接**——上一句如果是动作/对白，下一句必须直接承接
3. **如果提供了【下一片段计划】，本片段的结尾必须留出钩子或承接点**
4. 番茄快节奏铁律、绝对禁止、上下文使用、一致性红线——与 PartWriter 主系统提示一致
5. **字数硬约束**：本片段目标字数见下方【本片段目标】
6. **结尾必须是完整段落**——不允许在对话中间、动作进行时戛然而止

## 输出格式

只输出本片段的正文（自然段）。
不要输出"片段X/共Y""---"分隔线、不要输出任何标注或说明。
不要重复【已写片段末尾】中的最后一句话。
"""


class PartWriterAgent(BaseAgent):
    name = "Part写作Agent"
    description = "基于Part规划和全文上下文生成正文（生成器式分块）"
    version = "4.1.0"

    def execute(self, state, part_num: int, **kwargs) -> Dict[str, Any]:
        self.log_start()

        # 验证输入
        if not self.validate_input(state, part_num=part_num, **kwargs):
            return {"success": False, "error": "输入验证失败", "content": ""}

        try:
            # 获取完整上下文（V3：滚动摘要 + 详细前文 + 角色状态快照）
            self.update_progress(10, f"Part {part_num} 开始获取上下文...")
            context = state.get_part_context(part_num)
            context_len = len(context)
            self.update_progress(20, f"Part {part_num} 上下文长度: {context_len} 字符")

            # 获取当前Part规划
            outline = None
            if state.part_outline and part_num <= len(state.part_outline):
                outline = state.part_outline[part_num - 1]

            if not outline:
                self.log_error(f"Part {part_num}没有规划")
                return {"success": False, "error": f"Part {part_num}没有规划", "content": ""}

            # 获取本章伏笔任务
            self.update_progress(30, "获取伏笔任务...")
            foreshadow_info = self._get_foreshadow_for_part(state, part_num)

            target_words = outline.get("word_count", (PART_WORD_MIN + PART_WORD_MAX) // 2)
            hard_max = PART_WORD_MAX + 200  # 允许200字弹性

            self.update_progress(
                40, f"Part {part_num} 目标字数: {target_words}, 上限: {hard_max}, "
                    f"将按 {CHUNK_WORDS}字/片段 分块生成"
            )

            # R2: 生成器式分块生成
            full_text, chunk_count, total_elapsed = self._write_part_chunked(
                state=state,
                part_num=part_num,
                context=context,
                outline=outline,
                foreshadow_info=foreshadow_info,
                target_words=target_words,
                hard_max=hard_max,
            )

            word_count = len(full_text)
            self.update_progress(80, f"Part {part_num} 累计字数: {word_count}, 共 {chunk_count} 片段, 耗时 {total_elapsed:.1f}s")

            # 字数检查
            if word_count < PART_WORD_MIN * 0.7:
                self.update_progress(95, f"警告：Part {part_num}只有{word_count}字，严重不足")
            elif word_count > hard_max:
                self.update_progress(95, f"警告：Part {part_num}有{word_count}字，超出上限{hard_max}，将截断")
                full_text = self._truncate_to_complete_paragraph(full_text, hard_max)
                word_count = len(full_text)

            summary = f"Part {part_num}「{outline.get('title', '')}」\n字数: {word_count}字 (目标: {target_words}, 片段数: {chunk_count})"
            self.log_done(summary)

            return {
                "success": True,
                "content": full_text,
                "word_count": word_count,
                "target_words": target_words,
                "part_num": part_num,
                "summary": summary,
                "chunk_count": chunk_count,
            }

        except Exception as e:
            error_msg = f"Part {part_num} 生成异常: {e}"
            self.log_error(error_msg)
            return {
                "success": False,
                "error": str(e),
                "content": f"[Part {part_num}生成失败: {e}]"
            }

    def _write_part_chunked(
        self,
        state,
        part_num: int,
        context: str,
        outline: dict,
        foreshadow_info: str,
        target_words: int,
        hard_max: int,
    ) -> tuple:
        """生成器式分块生成 Part 全文。

        Args:
            state: StoryState
            part_num: 当前 Part 编号
            context: state.get_part_context(part_num) 输出的完整上下文
            outline: 当前 Part 规划 dict
            foreshadow_info: 伏笔任务字符串
            target_words: 目标字数
            hard_max: 上限字数

        Returns:
            (full_text, chunk_count, total_elapsed_seconds)
        """
        accumulated = ""
        chunk_idx = 0
        total_start = time.time()

        while len(accumulated) < target_words and chunk_idx < MAX_CHUNKS:
            chunk_idx += 1
            remaining = target_words - len(accumulated)
            is_last_target_chunk = (chunk_idx >= MAX_CHUNKS) or (remaining < CHUNK_WORDS)

            # 1) 准备 prev_tail：仅取上一片段末尾 CHUNK_OVERLAP 字
            prev_tail = accumulated[-CHUNK_OVERLAP:] if accumulated else ""

            # 2) 准备 next_plan：本片段核心事件
            if chunk_idx == 1:
                # 第一片段直接拿完整 Part 规划
                next_plan = (
                    f"本章目标: {target_words}字\n"
                    f"核心事件: {outline.get('core_event', '')}\n"
                    f"情绪目标: {outline.get('emotion_target', '')}\n"
                    f"关键对白: {outline.get('key_dialogue', '')}\n"
                    f"结尾钩子: {outline.get('end_hook', '')}\n"
                    f"节奏要求: {outline.get('pacing', '自然流畅')}\n"
                    f"因果关系: {outline.get('causality', '')}"
                )
            else:
                # 后续片段：按已写比例推进，让模型从 outline 推下一段
                progress_ratio = len(accumulated) / target_words if target_words else 0
                if progress_ratio < 0.4:
                    stage_hint = "继续推进核心事件的关键转折"
                elif progress_ratio < 0.75:
                    stage_hint = "进入核心事件的高潮部分"
                elif progress_ratio < 0.95:
                    stage_hint = "向结尾钩子收束，铺垫情绪释放"
                else:
                    stage_hint = "完成结尾钩子，干净收尾"
                next_plan = (
                    f"本章目标: {target_words}字 (已写 {len(accumulated)}字, 还需约 {remaining}字)\n"
                    f"本片段建议推进: {stage_hint}\n"
                    f"本章核心事件: {outline.get('core_event', '')}\n"
                    f"本章结尾钩子: {outline.get('end_hook', '')}"
                )

            # 3) 构造片段级 prompt
            chunk_user_prompt = self._build_chunk_prompt(
                part_num=part_num,
                chunk_idx=chunk_idx,
                is_first_chunk=(chunk_idx == 1),
                prev_tail=prev_tail,
                next_plan=next_plan,
                context=context if chunk_idx == 1 else "",  # 第一片段带完整上下文，后续片段不再带
                foreshadow_info=foreshadow_info if chunk_idx == 1 else "",
                outline=outline,
                chunk_target=min(CHUNK_WORDS, remaining + 200),
                target_words=target_words,
                hard_max=hard_max,
                written_so_far=len(accumulated),
            )

            # 4) 调用 LLM（max_tokens 控制在 ~5500，中文 1.5 tokens/字）
            chunk_max_tokens = min(CHUNK_WORDS * 2 + 300, 6000)

            chunk_start = time.time()
            self.update_progress(
                45 + (chunk_idx - 1) * 5,  # 45% → 70% 区间，每个片段 5%
                f"Part {part_num} 片段 {chunk_idx}/{MAX_CHUNKS} 生成中..."
            )
            chunk_text = call_llm(
                system_prompt=PART_CHUNK_SYSTEM_PROMPT,
                user_prompt=chunk_user_prompt,
                temperature=0.85,
                max_tokens=chunk_max_tokens,
                agent=self.name,
            )
            chunk_elapsed = time.time() - chunk_start

            # 5) 清理：去掉可能的前导重述
            chunk_text = chunk_text.strip()
            if prev_tail and chunk_text.startswith(prev_tail):
                chunk_text = chunk_text[len(prev_tail):].lstrip()

            # 6) 拼接
            accumulated += chunk_text
            self.update_progress(
                50 + (chunk_idx - 1) * 5,
                f"Part {part_num} 片段 {chunk_idx} 完成 (+{len(chunk_text)}字, {chunk_elapsed:.1f}s, "
                f"累计 {len(accumulated)}/{target_words}字)"
            )

            # R7-P1-5: 每完成一个 chunk 触发 checkpoint（崩溃可恢复）
            if self.checkpoint_callback is not None:
                try:
                    self.checkpoint_callback(part_num, chunk_idx, accumulated)
                except Exception as cp_err:
                    print(f"[PartWriterAgent] checkpoint_callback 失败（不影响主流程）: {cp_err}")

            # 7) 提前退出条件：模型认为本章写完（结尾是完整段落 + 已达到最小字数）
            if len(accumulated) >= PART_WORD_MIN:
                tail_stripped = accumulated.rstrip()
                if (
                    tail_stripped.endswith("\n\n")
                    or tail_stripped.endswith("。")
                    or tail_stripped.endswith("！")
                    or tail_stripped.endswith("？")
                    or tail_stripped.endswith("…")
                ) and len(chunk_text) < CHUNK_WORDS * 0.6:
                    # 自然收尾 + 输出偏短，认为模型主动完结
                    break

            # 8) 兜底：达到硬上限
            if len(accumulated) >= hard_max:
                break

        total_elapsed = time.time() - total_start
        return accumulated, chunk_idx, total_elapsed

    def _build_chunk_prompt(
        self,
        part_num: int,
        chunk_idx: int,
        is_first_chunk: bool,
        prev_tail: str,
        next_plan: str,
        context: str,
        foreshadow_info: str,
        outline: dict,
        chunk_target: int,
        target_words: int,
        hard_max: int,
        written_so_far: int,
    ) -> str:
        """构造片段级 user prompt。

        第一片段：完整上下文 + Part 规划 + 伏笔任务 + 字数约束
        后续片段：仅 prev_tail + next_plan + 本片段字数
        """
        if is_first_chunk:
            return f"""请创作第{part_num}部分（Part {part_num}）的第一个片段。

## Part规划
阶段：{outline.get('phase', '')}
核心事件：{outline.get('core_event', '')}
情绪目标：{outline.get('emotion_target', '')}
关键对白：{outline.get('key_dialogue', '')}
结尾钩子：{outline.get('end_hook', '')}
节奏要求：{outline.get('pacing', '自然流畅')}
与前面部分的因果关系：{outline.get('causality', '')}

## 字数硬约束
本章目标：{target_words}字
本章上限：{hard_max}字（系统会按 {CHUNK_WORDS}字/片段 续写多次）
本片段目标：约 {chunk_target}字（这是第 1 片段 / 共最多 {MAX_CHUNKS} 片段）

## 伏笔任务
{foreshadow_info}

## 故事上下文
{context}

## 创作指令
1. 第一句话直接进入情节，不要任何铺垫
2. 自然承接上一部分结尾的情境
3. 严格完成本片段的核心事件推进
4. 结尾实现钩子效果（但本章还有更多片段，不需要在此处完全收尾）
5. 本片段字数控制在 {max(1000, chunk_target - 200)}-{chunk_target + 200}字之间"""

        # 后续片段
        return f"""请续写 Part {part_num} 的第 {chunk_idx} 片段。

## 本片段上下文（上一片段末尾 {len(prev_tail)}字）
{prev_tail}

## 本片段计划
{next_plan}

## 字数约束
本章目标：{target_words}字（已写 {written_so_far}字, 剩余约 {target_words - written_so_far}字）
本片段目标：约 {chunk_target}字

## 创作指令
1. **从【上一片段末尾】最后一句自然续接**，不要重复或复述
2. 严格遵守番茄快节奏铁律（首句抓人、300字一推进、对白驱动、短段落、感官代替标签）
3. 角色名称必须与前文一致，言行必须符合档案
4. 结尾必须是完整段落——不允许在对话中间或动作进行时戛然而止
5. 本片段字数控制在 {max(1000, chunk_target - 200)}-{chunk_target + 200}字之间
6. 不要输出任何标注、解释、分隔线——只输出小说正文"""

    def validate_input(self, state, **kwargs) -> bool:
        """验证输入"""
        part_num = kwargs.get('part_num')
        if not part_num:
            self.log_error("缺少part_num参数")
            return False
        if not state:
            self.log_error("缺少state参数")
            return False
        return True

    def _get_foreshadow_for_part(self, state, part_num: int) -> str:
        """获取当前Part需要处理的伏笔"""
        if not state.foreshadowing:
            return "无伏笔任务"

        plant_items = []
        reveal_items = []

        for f in state.foreshadowing:
            if f.get("plant_part") == part_num:
                plant_items.append(
                    f"  - 埋设伏笔【{f.get('id', '')}】: {f.get('content', '')}\n"
                    f"    技巧: {f.get('hint', '自然融入')}"
                )
            if f.get("reveal_part") == part_num:
                reveal_items.append(
                    f"  - 揭晓伏笔【{f.get('id', '')}】: {f.get('content', '')}"
                )

        if not plant_items and not reveal_items:
            return "本章无伏笔任务"

        result = ""
        if plant_items:
            result += "【需要埋设的伏笔】\n" + "\n".join(plant_items) + "\n\n"
        if reveal_items:
            result += "【需要揭晓的伏笔】\n" + "\n".join(reveal_items)
        return result

    def _truncate_to_complete_paragraph(self, text: str, max_len: int) -> str:
        """截断到最后一个完整段落"""
        if len(text) <= max_len:
            return text

        truncated = text[:max_len]
        # 找最后一个完整段落（以\n\n或\n结尾）
        last_break = max(truncated.rfind("\n\n"), truncated.rfind("\n"))
        if last_break > max_len * 0.7:  # 不要截掉太多
            return truncated[:last_break].rstrip()
        return truncated.rstrip()
