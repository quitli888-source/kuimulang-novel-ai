"""
Part写作Agent V4 - 核心创作引擎

V4改动：
- 使用新的BaseAgent接口
- 支持进度跟踪
- 改进错误处理
- 实现标准化的Agent接口
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


class PartWriterAgent(BaseAgent):
    name = "Part写作Agent"
    description = "基于Part规划和全文上下文生成正文"
    version = "4.0.0"

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

            self.update_progress(40, f"Part {part_num} 目标字数: {target_words}, 上限: {hard_max}")
            self.update_progress(50, "准备调用LLM...")

            user_prompt = f"""请创作第{part_num}部分（Part {part_num}）的正文。

## Part规划
阶段：{outline.get('phase', '')}
核心事件：{outline.get('core_event', '')}
情绪目标：{outline.get('emotion_target', '')}
关键对白：{outline.get('key_dialogue', '')}
结尾钩子：{outline.get('end_hook', '')}
节奏要求：{outline.get('pacing', '自然流畅')}
与前面部分的因果关系：{outline.get('causality', '')}

## 字数硬约束
目标字数：{target_words}字
最少：{PART_WORD_MIN}字
最多：{hard_max}字
（如果写到{hard_max}字还没完成核心事件，加速收束，不要拖延）

## 伏笔任务
{foreshadow_info}

## 故事上下文
{context}

## 创作指令
1. 第一句话直接进入情节，不要任何铺垫
2. 自然承接上一部分结尾的情境
3. 严格完成核心事件
4. 达到情绪目标
5. 结尾实现钩子效果
6. 字数控制在{PART_WORD_MIN}-{hard_max}字之间"""

            self.update_progress(60, "调用LLM生成内容...")
            call_start_time = time.time()
            
            text = call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.85,
                max_tokens=hard_max + 500,  # token略多于字数
                agent=self.name,
            )
            
            call_end_time = time.time()
            self.update_progress(80, f"LLM调用完成，耗时: {call_end_time - call_start_time:.2f}秒")

            # 字数检查
            word_count = len(text)
            self.update_progress(90, f"生成字数: {word_count}")
            
            if word_count < PART_WORD_MIN * 0.7:
                self.update_progress(95, f"警告：Part {part_num}只有{word_count}字，严重不足")
            elif word_count > hard_max:
                self.update_progress(95, f"警告：Part {part_num}有{word_count}字，超出上限{hard_max}，将截断")
                text = self._truncate_to_complete_paragraph(text, hard_max)

            summary = f"Part {part_num}「{outline.get('title', '')}」\n" f"字数: {word_count}字 (目标: {target_words})"
            self.log_done(summary)
            
            return {
                "success": True,
                "content": text,
                "word_count": word_count,
                "target_words": target_words,
                "part_num": part_num,
                "summary": summary
            }

        except Exception as e:
            error_msg = f"Part {part_num} 生成异常: {e}"
            self.log_error(error_msg)
            return {
                "success": False,
                "error": str(e),
                "content": f"[Part {part_num}生成失败: {e}]"
            }

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
