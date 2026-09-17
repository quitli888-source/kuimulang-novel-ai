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
from core.llm_client import call_llm, call_llm_json
from core.config import PART_WORD_MIN, PART_WORD_MAX
from core.prompt_loader import load_prompt
from core.established_facts import facts_from_extractor_payload
from core.logger import get_logger
logger = get_logger('part_writer_agent')
SYSTEM_PROMPT = load_prompt('part_writer', '你是一位番茄小说平台的顶级短篇作家。你的作品以快节奏、强冲突、高情感密度著称，读者一旦开始就无法放下。\n\n## 番茄快节奏铁律（必须逐条遵守）\n\n1. **第一句话就要抓人**：绝不能以环境描写、"XX醒来"、天气描写开头。第一句要么是动作、要么是对白、要么是悬念\n2. **300字一推进**：每300字内必须有事件推进、情绪转折、或新悬念抛出。如果某段300字没有推进，删掉重写\n3. **对白驱动**：对话占比≥40%。用对话讲故事，用对话展现性格，用对话推进情节\n4. **短段落**：一个自然段不超过3行（手机一屏可见）。禁止大段文字堆砌\n5. **感官代替标签**：禁止"他感到害怕""她觉得心碎"这类情绪标签。用动作、生理反应、环境细节来表现\n6. **删除一切废话**：每个句子都必须有存在价值——删掉它，读者会少知道什么关键信息？如果答案是"没什么"，就删掉\n\n## 绝对禁止\n\n- 禁止"心中一沉""不禁笑了""不由得""竟然"等网文陈词滥调\n- 禁止超过100字的纯环境描写（除非环境本身就是情节）\n- 禁止大段心理独白（把内心活动转化为动作或对话）\n- 禁止回忆闪回（除非回忆直接推进当前主线，且不超过200字）\n- 禁止说教和价值观输出\n- 禁止与前文矛盾的任何内容\n\n## 上下文使用\n\n你会收到：角色档案、世界观、已完成剧情摘要、上一部分结尾、角色状态快照。\n这些是你的"记忆"，你必须严格遵守：\n- **角色名称必须一字不差**：角色档案中的名字就是唯一正确的名字，绝对不能自行改名\n- 角色的言行必须符合档案设定\n- 已发生的事件不能否认或遗忘\n- 上一部分结尾的情境必须自然承接\n- 不能出现与前文矛盾的信息\n- 不能引入角色档案中不存在的角色（除非是路人甲等无名字的临时角色）\n\n## 一致性红线（V3新增，最高优先级）\n\n以下问题一旦出现，视为严重缺陷：\n- 角色在前面已死/已离开，后面又出现\n- 角色突然知道了前面不可能知道的信息\n- 角色性格突变（与前面已建立的人设矛盾）\n- 时间线不连续（前面在白天，突然变成夜晚而没有交代）\n- 已明确交代的物理规则被违反\n\n## 输出格式\n\n只输出小说正文。\n不要输出Part标题、不要输出"---"分隔线、不要输出任何标注或说明。\n直接从正文第一个字开始写。')
CHUNK_WORDS = 3500
CHUNK_OVERLAP = 800
MAX_CHUNKS = 6
PROGRESS_START = 55
# R23-P1-11: 从 prompts/part_chunk.txt 加载（外置），保留简明 fallback
PART_CHUNK_SYSTEM_PROMPT = load_prompt('part_chunk',
    '你是番茄小说平台顶级短篇作家，正在为一部连载小说续写某个 Part 的片段。\n\n## 核心约束（片段级）\n\n1. **只写这一片段，不要总结、不要预告、不要回顾**\n2. **如果提供了【已写片段末尾】，必须从该结尾自然续接**\n3. **结尾必须是完整段落**——不允许在对话中间或动作进行时戛然而止\n\n## 输出格式\n\n只输出本片段的正文。\n不要重复【已写片段末尾】中的最后一句话。\n')
_FACTS_EXTRACTOR_SYSTEM_PROMPT = load_prompt('established_facts', '你是"小说事实抽取员"。从以下小说片段中提取"已确立的关键事实"。\n\n每条事实用一行："[category] text"\ncategory 限：character / location / object / event / trait / relationship / world_rule / foreshadow / knowledge\n最多提取 15 条最关键的事实。\n输出 JSON: {"facts": [{"category": "...", "text": "...", "quote": "...", "subject": "...", "predicate": "..."}]}\n')

class PartWriterAgent(BaseAgent):
    name = 'Part写作Agent'
    description = '基于Part规划和全文上下文生成正文（生成器式分块）'
    version = '4.1.0'

    def execute(self, state, part_num: int, **kwargs) -> Dict[str, Any]:
        self.log_start()
        if not self.validate_input(state, part_num=part_num, **kwargs):
            return {'success': False, 'error': '输入验证失败', 'content': ''}
        try:
            self.update_progress(10, f'Part {part_num} 开始获取上下文...')
            context = state.get_part_context(part_num)
            context_len = len(context)
            self.update_progress(20, f'Part {part_num} 上下文长度: {context_len} 字符')
            outline = None
            if state.part_outline and part_num <= len(state.part_outline):
                outline = state.part_outline[part_num - 1]
            if not outline:
                self.log_error(f'Part {part_num}没有规划')
                return {'success': False, 'error': f'Part {part_num}没有规划', 'content': ''}
            self.update_progress(30, '获取伏笔任务...')
            foreshadow_info = self._get_foreshadow_for_part(state, part_num)
            target_words = outline.get('word_count', (PART_WORD_MIN + PART_WORD_MAX) // 2)
            hard_max = PART_WORD_MAX + 200
            self.update_progress(40, f'Part {part_num} 目标字数: {target_words}, 上限: {hard_max}, 将按 {CHUNK_WORDS}字/片段 分块生成')
            full_text, chunk_count, total_elapsed = self._write_part_chunked(state=state, part_num=part_num, context=context, outline=outline, foreshadow_info=foreshadow_info, target_words=target_words, hard_max=hard_max)
            word_count = len(full_text)
            self.update_progress(80, f'Part {part_num} 累计字数: {word_count}, 共 {chunk_count} 片段, 耗时 {total_elapsed:.1f}s')
            if word_count < PART_WORD_MIN * 0.7:
                self.update_progress(95, f'警告：Part {part_num}只有{word_count}字，严重不足')
            elif word_count > hard_max:
                self.update_progress(95, f'警告：Part {part_num}有{word_count}字，超出上限{hard_max}，将截断')
                full_text = self._truncate_to_complete_paragraph(full_text, hard_max)
                word_count = len(full_text)
            try:
                self._extract_and_register_facts(state=state, part_num=part_num, part_text=full_text)
            except Exception as ef_err:
                logger.info(f'[PartWriterAgent] R8-P0-1 事实抽取失败（不影响主流程）: {ef_err}')
            if state is not None and hasattr(state, 'window') and (state.window is not None):
                window = state.window
                try:
                    if part_num not in window.summaries:
                        window.add_part(part_num, full_text, full_text[:window.SUMMARY_L1_LEN])
                except Exception as add_err:
                    logger.info(f'[PartWriterAgent] R15 window.add_part 异常: {add_err}')
                world_setting = getattr(state, 'world_setting', '') or ''
                try:
                    roll_result = window.maybe_generate_rolling_summary(part_num, world_setting=world_setting)
                    if roll_result.get('generated'):
                        logger.info(f"[PartWriterAgent] R15 Part {part_num} 触发 rolling 摘要（{roll_result['char_count']} 字）")
                    elif roll_result.get('error'):
                        logger.info(f"[PartWriterAgent] R15 rolling 生成失败: {roll_result['error']}")
                except Exception as roll_err:
                    logger.info(f'[PartWriterAgent] R15 rolling 异常（不影响主流程）: {roll_err}')
                try:
                    mile_result = window.maybe_generate_milestone(part_num, world_setting=world_setting)
                    if mile_result.get('generated'):
                        logger.info(f"[PartWriterAgent] R15 Part {part_num} 触发 milestone #{mile_result['milestone_num']}（{mile_result['char_count']} 字）")
                    elif mile_result.get('error'):
                        logger.info(f"[PartWriterAgent] R15 milestone 生成失败: {mile_result['error']}")
                except Exception as mile_err:
                    logger.info(f'[PartWriterAgent] R15 milestone 异常（不影响主流程）: {mile_err}')
            summary = f"Part {part_num}「{outline.get('title', '')}」\n字数: {word_count}字 (目标: {target_words}, 片段数: {chunk_count})"
            self.log_done(summary)
            return {'success': True, 'content': full_text, 'word_count': word_count, 'target_words': target_words, 'part_num': part_num, 'summary': summary, 'chunk_count': chunk_count}
        except Exception as e:
            error_msg = f'Part {part_num} 生成异常: {e}'
            self.log_error(error_msg)
            return {'success': False, 'error': str(e), 'content': f'[Part {part_num}生成失败: {e}]'}

    def _write_part_chunked(self, state, part_num: int, context: str, outline: dict, foreshadow_info: str, target_words: int, hard_max: int) -> tuple:
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
        accumulated = ''
        chunk_idx = 0
        total_start = time.time()
        while len(accumulated) < target_words and chunk_idx < MAX_CHUNKS:
            chunk_idx += 1
            remaining = target_words - len(accumulated)
            is_last_target_chunk = chunk_idx >= MAX_CHUNKS or remaining < CHUNK_WORDS
            prev_tail = accumulated[-CHUNK_OVERLAP:] if accumulated else ''
            if chunk_idx == 1:
                next_plan = f"本章目标: {target_words}字\n核心事件: {outline.get('core_event', '')}\n情绪目标: {outline.get('emotion_target', '')}\n关键对白: {outline.get('key_dialogue', '')}\n结尾钩子: {outline.get('end_hook', '')}\n节奏要求: {outline.get('pacing', '自然流畅')}\n因果关系: {outline.get('causality', '')}"
            else:
                progress_ratio = len(accumulated) / target_words if target_words else 0
                if progress_ratio < 0.4:
                    stage_hint = '继续推进核心事件的关键转折'
                elif progress_ratio < 0.75:
                    stage_hint = '进入核心事件的高潮部分'
                elif progress_ratio < 0.95:
                    stage_hint = '向结尾钩子收束，铺垫情绪释放'
                else:
                    stage_hint = '完成结尾钩子，干净收尾'
                next_plan = f"本章目标: {target_words}字 (已写 {len(accumulated)}字, 还需约 {remaining}字)\n本片段建议推进: {stage_hint}\n本章核心事件: {outline.get('core_event', '')}\n本章结尾钩子: {outline.get('end_hook', '')}"
            chunk_user_prompt = self._build_chunk_prompt(part_num=part_num, chunk_idx=chunk_idx, is_first_chunk=chunk_idx == 1, prev_tail=prev_tail, next_plan=next_plan, context=context if chunk_idx == 1 else '', foreshadow_info=foreshadow_info if chunk_idx == 1 else '', outline=outline, chunk_target=min(CHUNK_WORDS, remaining + 200), target_words=target_words, hard_max=hard_max, written_so_far=len(accumulated), state=state)
            chunk_max_tokens = min(CHUNK_WORDS * 2 + 300, 12000)
            chunk_start = time.time()
            self.update_progress(45 + (chunk_idx - 1) * 5, f'Part {part_num} 片段 {chunk_idx}/{MAX_CHUNKS} 生成中...')
            chunk_text = ''
            for retry_attempt in range(3):
                cur_max = chunk_max_tokens * (1 + retry_attempt)
                try:
                    chunk_text = call_llm(system_prompt=PART_CHUNK_SYSTEM_PROMPT, user_prompt=chunk_user_prompt, temperature=0.8, max_tokens=cur_max, agent=self.name)
                except Exception as e:
                    logger.info(f'[PartWriterAgent] Part {part_num} 片段 {chunk_idx} 第 {retry_attempt + 1} 次调用异常: {e}')
                    chunk_text = ''
                if chunk_text and chunk_text.strip():
                    break
                logger.info(f'[PartWriterAgent] Part {part_num} 片段 {chunk_idx} 第 {retry_attempt + 1} 次返回空内容（max_tokens={cur_max}），重试...')
            chunk_elapsed = time.time() - chunk_start
            chunk_text = chunk_text.strip()
            if prev_tail and chunk_text.startswith(prev_tail):
                chunk_text = chunk_text[len(prev_tail):].lstrip()
            accumulated += chunk_text
            self.update_progress(50 + (chunk_idx - 1) * 5, f'Part {part_num} 片段 {chunk_idx} 完成 (+{len(chunk_text)}字, {chunk_elapsed:.1f}s, 累计 {len(accumulated)}/{target_words}字)')
            if self.checkpoint_callback is not None:
                try:
                    self.checkpoint_callback(part_num, chunk_idx, accumulated)
                except Exception as cp_err:
                    logger.info(f'[PartWriterAgent] checkpoint_callback 失败（不影响主流程）: {cp_err}')
            if len(accumulated) >= PART_WORD_MIN:
                tail_stripped = accumulated.rstrip()
                if (tail_stripped.endswith('\n\n') or tail_stripped.endswith('。') or tail_stripped.endswith('！') or tail_stripped.endswith('？') or tail_stripped.endswith('…')) and len(chunk_text) < CHUNK_WORDS * 0.6:
                    break
            if len(accumulated) >= hard_max:
                break
        total_elapsed = time.time() - total_start
        return (accumulated, chunk_idx, total_elapsed)

    def _build_chunk_prompt(self, part_num: int, chunk_idx: int, is_first_chunk: bool, prev_tail: str, next_plan: str, context: str, foreshadow_info: str, outline: dict, chunk_target: int, target_words: int, hard_max: int, written_so_far: int, state=None) -> str:
        """构造片段级 user prompt。

        第一片段：完整上下文 + Part 规划 + 伏笔任务 + 字数约束
        后续片段：仅 prev_tail + next_plan + 本片段字数
        """
        if is_first_chunk:
            facts_block = ''
            if state is not None:
                try:
                    facts_block = state.build_established_facts_block(part_num) or ''
                except Exception:
                    facts_block = ''
            facts_paragraph = '\n' + facts_block + '\n' if facts_block else '\n（这是第一部分，没有前文事实清单）\n'
            prev_part_anchor = ''
            if state is not None and part_num > 1 and hasattr(state, 'parts'):
                try:
                    prev_text = state.parts.get(str(part_num - 1), '') or state.parts.get(part_num - 1, '')
                    if prev_text:
                        prev_part_anchor = f'\n## ⚠ Part {part_num - 1} 结尾最后 600 字（你的开篇必须直接承接以下情境，地点/时间/在场人物/动作状态保持一致）\n{prev_text[-600:]}\n'
                except Exception:
                    prev_part_anchor = ''
            return f"""请创作第{part_num}部分（Part {part_num}）的第一个片段。\n\n## Part规划\n阶段：{outline.get('phase', '')}\n核心事件：{outline.get('core_event', '')}\n情绪目标：{outline.get('emotion_target', '')}\n关键对白：{outline.get('key_dialogue', '')}\n结尾钩子：{outline.get('end_hook', '')}\n节奏要求：{outline.get('pacing', '自然流畅')}\n与前面部分的因果关系：{outline.get('causality', '')}\n\n## 字数硬约束\n本章目标：{target_words}字\n本章上限：{hard_max}字（系统会按 {CHUNK_WORDS}字/片段 续写多次）\n本片段目标：约 {chunk_target}字（这是第 1 片段 / 共最多 {MAX_CHUNKS} 片段）\n\n## 伏笔任务\n{foreshadow_info}\n\n## 故事上下文\n{context}\n{facts_paragraph}\n{prev_part_anchor}\n\n## 创作指令（R12 强化）\n1. **【强约束】开篇必须从 Part {(part_num - 1 if part_num > 1 else '0')} 结尾情境直接续接** —— 地点、时辰、在场人物、动作状态保持一致，不允许场景跳跃\n2. 第一句话直接进入情节，不要任何铺垫\n3. 自然承接上一部分结尾的情境\n4. 严格完成本片段的核心事件推进\n5. 结尾实现钩子效果（但本章还有更多片段，不需要在此处完全收尾）\n6. 本片段字数控制在 {max(1000, chunk_target - 200)}-{chunk_target + 200}字之间\n\n## 强制约束（R8 新增）\n\n- 严禁与【前文已确立事实清单】（或第 1 部分时的"无前文"提示）中的任何事实矛盾\n- 严禁使用清单中没有的"已知信息"（如某物品在清单中未出现，不得假设角色持有）\n- 新引入的角色名/地名/物品名不要与前文已有的同名实体混淆（如不要让两个不同角色共享同一个名字）"""
        return f'请续写 Part {part_num} 的第 {chunk_idx} 片段。\n\n## 本片段上下文（上一片段末尾 {len(prev_tail)}字）\n{prev_tail}\n\n## 本片段计划\n{next_plan}\n\n## 字数约束\n本章目标：{target_words}字（已写 {written_so_far}字, 剩余约 {target_words - written_so_far}字）\n本片段目标：约 {chunk_target}字\n\n## 创作指令\n1. **从【上一片段末尾】最后一句自然续接**，不要重复或复述\n2. 严格遵守番茄快节奏铁律（首句抓人、300字一推进、对白驱动、短段落、感官代替标签）\n3. 角色名称必须与前文一致，言行必须符合档案\n4. 结尾必须是完整段落——不允许在对话中间或动作进行时戛然而止\n5. 本片段字数控制在 {max(1000, chunk_target - 200)}-{chunk_target + 200}字之间\n6. 不要输出任何标注、解释、分隔线——只输出小说正文'

    def validate_input(self, state, **kwargs) -> bool:
        """验证输入"""
        part_num = kwargs.get('part_num')
        if not part_num:
            self.log_error('缺少part_num参数')
            return False
        if not state:
            self.log_error('缺少state参数')
            return False
        return True

    def _extract_and_register_facts(self, state, part_num: int, part_text: str) -> int:
        """R8-P0-1: Part 写完后用 LLM 增量抽取"已确立事实"并 append 到 state.established_facts。

        失败时静默（best-effort；Logic Agent 评 Part N+1 时回退到只用 part_summaries）。
        返回成功追加的 fact 数量。
        """
        if state is None or not part_text or len(part_text) < 100:
            return 0
        from core.established_facts import EstablishedFacts
        ef = getattr(state, 'established_facts', None)
        if ef is None:
            state.established_facts = EstablishedFacts()
            ef = state.established_facts
        elif not isinstance(ef, EstablishedFacts):
            new_ef = EstablishedFacts()
            try:
                new_ef.from_dict(ef if isinstance(ef, dict) else {})
            except Exception:
                logger.debug('part_writer_agent: silent except (P2-19)', exc_info=True)
            state.established_facts = new_ef
            ef = new_ef
        already_extracted = any((getattr(f, 'part_num', None) == part_num for f in ef.facts))
        if already_extracted:
            return 0
        try:
            user_prompt = f'Part {part_num} 全文（约 {len(part_text)} 字）：\n\n{part_text}\n\n请按 system prompt 的协议输出 JSON。'
            payload = call_llm_json(system_prompt=_FACTS_EXTRACTOR_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.0, max_tokens=1800, agent='established_facts')
        except Exception as call_err:
            logger.info(f'[PartWriterAgent] R8-P0-1 事实抽取 LLM 调用失败: {call_err}')
            return 0
        if not isinstance(payload, dict):
            return 0
        new_facts = facts_from_extractor_payload(payload, part_num)
        if not new_facts:
            return 0
        added = ef.add_many(new_facts)
        if added == 0:
            try:
                from core.established_facts import derive_facts_layered
                rule_facts = derive_facts_layered(state, part_num, part_text)
                added = ef.add_many(rule_facts)
            except Exception as _:
                logger.debug('part_writer_agent: silent except (P2-19)', exc_info=True)
        if added:
            try:
                self.update_progress(90, f'📑 Part {part_num} 已抽取 {added} 条事实写入 established_facts')
            except Exception:
                logger.debug('part_writer_agent: silent except (P2-19)', exc_info=True)
        return added

    def _get_foreshadow_for_part(self, state, part_num: int) -> str:
        """获取当前Part需要处理的伏笔"""
        if not state.foreshadowing:
            return '无伏笔任务'
        plant_items = []
        reveal_items = []
        for f in state.foreshadowing:
            if f.get('plant_part') == part_num:
                plant_items.append(f"  - 埋设伏笔【{f.get('id', '')}】: {f.get('content', '')}\n    技巧: {f.get('hint', '自然融入')}")
            if f.get('reveal_part') == part_num:
                reveal_items.append(f"  - 揭晓伏笔【{f.get('id', '')}】: {f.get('content', '')}")
        if not plant_items and (not reveal_items):
            return '本章无伏笔任务'
        result = ''
        if plant_items:
            result += '【需要埋设的伏笔】\n' + '\n'.join(plant_items) + '\n\n'
        if reveal_items:
            result += '【需要揭晓的伏笔】\n' + '\n'.join(reveal_items)
        return result

    def _truncate_to_complete_paragraph(self, text: str, max_len: int) -> str:
        """截断到最后一个完整段落"""
        if len(text) <= max_len:
            return text
        truncated = text[:max_len]
        last_break = max(truncated.rfind('\n\n'), truncated.rfind('\n'))
        if last_break > max_len * 0.7:
            return truncated[:last_break].rstrip()
        return truncated.rstrip()