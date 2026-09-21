"""
R27-P1-7: Phase 拆解 —— 把 WritingService._phase{1,2,3,4}_* 的核心实现
抽到独立的 PhaseRunner 类，让 writing_service.py 退化为调度/协调器。

每个 Runner 都持有 service 引用，访问 service.data / service.emitter /
service.cfg / service._save / service._check_pause / service._request_confirm
等共享状态，零行为变化。

公开类：
  Phase1Runner.run()      灵感解析（Inspiration + Genre Agent）
  Phase2Runner.run()      情节规划（PlotPlanner Agent）
  Phase3Runner.run(start_from)  逐 Part 创作 + 滑动窗口二级/三级摘要
  Phase4Runner.run()      风格优化 + 三 Review Agent 串行评审
"""
import asyncio
import os
import time
import traceback
from typing import TYPE_CHECKING

from api.sse import EventType
from core.config import get_task_max_tokens
from core.memory_manager import get_all_memory
from core.text_utils import truncate
from core.logger import get_logger

if TYPE_CHECKING:
    from services.writing_service import WritingService

logger = get_logger('writing_phase_runners')

# R4-6: 伏笔回收复检关键词截取长度（content 前 N 字做正文子串命中；取 8-12 中值）
FORESHADOW_KEYWORD_LEN = 10
# R4-6: content 短于该长度视为无有效关键词（2 字泛词子串误报率高），跳过不复检
FORESHADOW_MIN_CONTENT_LEN = 4


def check_foreshadow_reveal(foreshadowing: list, part_num: int, part_text: str) -> list:
    """R4-6: 伏笔回收确定性复检（纯函数，零 LLM）。

    对 reveal_part == part_num 的伏笔，取 content 前 FORESHADOW_KEYWORD_LEN 字做
    part_text 子串命中检查；未命中返回未回收伏笔的 id 列表。只告警不阻断
    （回忆/他人提及形式合法，终判交 Phase 4）。

    Args:
        foreshadowing: s.data['foreshadowing']（元素为 dict，容忍脏数据）
        part_num: 当前 Part 编号
        part_text: 本 Part 正文

    Returns:
        未命中伏笔的 id 列表（content 过短/空 foreshadowing/part_text 为空时
        返回 []，不误报）。
    """
    if not foreshadowing or not part_text:
        return []
    unrevealed: list = []
    for f in foreshadowing:
        if not isinstance(f, dict):
            continue
        if f.get('reveal_part') != part_num:
            continue
        content = (f.get('content') or '').strip()
        if len(content) < FORESHADOW_MIN_CONTENT_LEN:
            continue  # 无有效关键词，跳过（避免 2 字泛词误报）
        keyword = content[:FORESHADOW_KEYWORD_LEN]
        if keyword not in part_text:
            unrevealed.append(f.get('id', ''))
    return unrevealed


class Phase1Runner:
    """灵感解析阶段 —— InspirationAgent + GenreAgent"""

    def __init__(self, service: "WritingService"):
        self.service = service

    async def run(self) -> None:
        s = self.service
        logger.info('[Phase1Runner] 开始')
        await s.emitter.emit(EventType.PHASE, {'phase': 'phase1', 'name': '灵感解析', 'work_id': s.work_id}, work_id=s.work_id)
        await s.emitter.emit(EventType.LOG, {'message': '开始灵感解析...', 'work_id': s.work_id}, work_id=s.work_id)
        inspiration = s.data.get('inspiration', '')
        logger.info(f'[Phase1Runner] 灵感: {inspiration}')
        from core.agents.inspiration_agent import InspirationAgent
        from core.agents.genre_agent import GenreAgent
        try:
            memory_content = get_all_memory()
            logger.info(f'[Phase1Runner] 记忆内容长度: {len(memory_content)} 字符')
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'inspiration_agent', 'status': 'start', 'message': '解析灵感要素...', 'work_id': s.work_id}, work_id=s.work_id)
            logger.info('[Phase1Runner] 准备调用InspirationAgent')
            inspiration_agent = InspirationAgent()
            core_elements = await asyncio.to_thread(inspiration_agent.execute, type('State', (), {'inspiration': inspiration, 'memory': memory_content})())
            logger.info(f"[Phase1Runner] InspirationAgent返回: {core_elements.get('protagonist', {}).get('identity', '未知')}")
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'inspiration_agent', 'status': 'end', 'message': f"主角: {core_elements.get('protagonist', {}).get('identity', '未知')}", 'work_id': s.work_id}, work_id=s.work_id)
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'genre_agent', 'status': 'start', 'message': '判断题材分类...', 'work_id': s.work_id}, work_id=s.work_id)
            logger.info('[Phase1Runner] 准备调用GenreAgent')
            genre_agent = GenreAgent()
            genre_result = await asyncio.to_thread(genre_agent.execute, type('State', (), {'core_elements': core_elements, 'memory': memory_content})())
            logger.info(f"[Phase1Runner] GenreAgent返回: {genre_result.get('genre_primary', '')}")
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'genre_agent', 'status': 'end', 'message': f"题材: {genre_result.get('genre_primary', '')} > {genre_result.get('genre_secondary', '')}", 'work_id': s.work_id}, work_id=s.work_id)
            s.data['core_elements'] = core_elements
            s.data['genre'] = genre_result
            s.data['market_positioning'] = genre_result
            s.data['phase'] = 'phase1'
            s._save()
            await s.emitter.emit(EventType.LOG, {'message': '灵感解析完成', 'work_id': s.work_id}, work_id=s.work_id)
            logger.info('[Phase1Runner] 完成')
        except Exception as e:
            logger.info(f'[Phase1Runner] 出错: {e}')
            traceback.print_exc()
            await s.emitter.emit(EventType.ERROR, {'message': f'Phase1错误: {str(e)}', 'work_id': s.work_id}, work_id=s.work_id)
            # R4-P1-x: 重新抛出 —— 此前吞异常后 run() 会带着空 core_elements 继续跑后续
            # 阶段，产出基于空设定的垃圾稿，且 phase 被写成成功态（resume 时误判为已完成）。
            # 失败时不写 phase / 不 _save，保留现场供 resume 重试。
            raise


class Phase2Runner:
    """情节规划阶段 —— PlotPlannerAgent"""

    def __init__(self, service: "WritingService"):
        self.service = service

    async def run(self) -> None:
        s = self.service
        await s.emitter.emit(EventType.PHASE, {'phase': 'phase2', 'name': '情节规划', 'work_id': s.work_id}, work_id=s.work_id)
        await s.emitter.emit(EventType.LOG, {'message': '开始情节规划...', 'work_id': s.work_id}, work_id=s.work_id)
        from core.agents.plot_planner_agent import PlotPlannerAgent
        try:
            memory_content = get_all_memory()
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'plot_planner_agent', 'status': 'start', 'message': '生成Part制创作蓝图...', 'work_id': s.work_id}, work_id=s.work_id)
            plot_agent = PlotPlannerAgent()

            class TempState:

                def __init__(self, data, memory):
                    self.inspiration = data.get('inspiration', '')
                    self.core_elements = data.get('core_elements', {})
                    self.market_positioning = data.get('market_positioning', {})
                    self.memory = memory

            temp_state = TempState(s.data, memory_content)
            plot_result = await asyncio.to_thread(plot_agent.execute, temp_state)
            s.data['world_setting'] = plot_result.get('world_setting', '')
            s.data['characters'] = plot_result.get('characters', [])
            # R4-1: 角色规范名注册表 —— 从 Phase 2 characters 一次性冻结权威名源
            # （无合并逻辑：不推断/不合并，回应 Round 1 拒绝理由）。幂等：已有非空
            # registry 时不覆盖（resume/重跑安全）；characters 为空则不写（Phase 2
            # 质量问题由名册缺失暴露给评审，不用空名册掩盖）。
            try:
                from core.name_registry import build_name_registry
                _characters = s.data.get('characters') or []
                if _characters and not (s.data.get('name_registry') or {}):
                    s.data['name_registry'] = build_name_registry(_characters)
                    logger.info(f'[Phase2Runner] R4-1 名册冻结: {len(s.data["name_registry"])} 个规范名')
                elif not _characters:
                    logger.info('[Phase2Runner] R4-1 characters 为空，不写 name_registry')
            except Exception as nr_err:
                logger.info(f'[Phase2Runner] R4-1 name_registry 构建失败（不影响主流程）: {nr_err}')
            s.data['part_outline'] = plot_result.get('part_outline', [])
            s.data['foreshadowing'] = plot_result.get('foreshadowing', [])
            # R2-4 防线 2 挂载点 B: Phase 2 出口再归一化一次 —— 覆盖"prompt 修了但
            # 模型仍返回低值"（与 plot_planner_agent 内的挂载点 A 构成双保险，
            # 纯本地确定性数字变换，零 LLM 成本、零文学性风险）
            try:
                from core.agents.plot_planner_agent import normalize_outline_word_counts
                _before = sum((p.get('word_count', 0) for p in s.data['part_outline'] if isinstance(p, dict)))
                s.data['part_outline'] = normalize_outline_word_counts(s.data['part_outline'], s.cfg.target_word_count)
                _after = sum((p.get('word_count', 0) for p in s.data['part_outline'] if isinstance(p, dict)))
                if _after != _before:
                    logger.info(f'[Phase2Runner] R2-4 大纲字数归一化: {_before} -> {_after}（目标 {s.cfg.target_word_count}）')
            except Exception as norm_err:
                logger.info(f'[Phase2Runner] 大纲字数归一化失败（不影响主流程）: {norm_err}')
            outline_count = len(plot_result.get('part_outline', []))
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'plot_planner_agent', 'status': 'end', 'message': f'生成{outline_count}个Part的蓝图', 'work_id': s.work_id}, work_id=s.work_id)
            s.data['phase'] = 'phase2'
            s._save()
            await s.emitter.emit(EventType.LOG, {'message': '情节规划完成', 'work_id': s.work_id}, work_id=s.work_id)
        except Exception as e:
            await s.emitter.emit(EventType.ERROR, {'message': f'Phase2错误: {str(e)}', 'work_id': s.work_id}, work_id=s.work_id)
            # R4-P1-x: 重新抛出 —— 此前吞异常清空 part_outline 并把 phase 写成 'phase2'，
            # Phase3 会拿空 outline 继续写，resume 时还会把失败的规划当已完成跳过。
            raise


class Phase3Runner:
    """逐 Part 创作阶段 —— PartWriterAgent + 滑动窗口二级/三级摘要"""

    def __init__(self, service: "WritingService"):
        self.service = service

    async def run(self, start_from: int = 1) -> None:
        s = self.service
        await s.emitter.emit(EventType.PHASE, {'phase': 'phase3', 'name': '章节创作', 'work_id': s.work_id}, work_id=s.work_id)
        s.data.setdefault('parts', {})
        s.data.setdefault('part_summaries', {})
        from core.agents.part_writer_agent import PartWriterAgent
        from services.writing_service import TempStoryState
        memory_content = get_all_memory()
        total = s.cfg.part_count
        temp_state = TempStoryState(s.data, memory_content, vector_store=s.vector_store)
        writer_agent = PartWriterAgent()
        writer_agent.set_progress_callback(s.progress_callback)

        def _on_chunk_complete(part_num: int, chunk_idx: int, accumulated_text: str) -> None:
            try:
                # P1-46: 走增量写盘 + 临时文件原子替换（避免 50 万字全量重写）
                summary = truncate(accumulated_text, n=200, suffix="...")
                s._save_chunk_progress(part_num, accumulated_text, summary)
                try:
                    from core.progress_manager import progress_manager as _pm
                    from api.sse import EventType as _Evt
                    _pm.emitter.emit_sync(_Evt.LOG, {'level': 'info', 'message': f'💾 Part {part_num} chunk {chunk_idx} checkpoint 已保存 ({len(accumulated_text)}字)', 'event_type': 'checkpoint_saved', 'part': part_num, 'chunk': chunk_idx, 'words': len(accumulated_text), 'work_id': s.work_id}, work_id=s.work_id)
                except Exception:
                    logger.debug('writing_phase_runners: silent except (P2-19)', exc_info=True)
            except Exception as cp_err:
                logger.info(f'[Phase3Runner] chunk checkpoint 失败（不影响主流程）: {cp_err}')

        writer_agent.set_checkpoint_callback(_on_chunk_complete)
        for i in range(start_from, total + 1):
            await s._check_pause()
            from services.writing_service import _writing_state
            _writing_state[s.work_id]['current_part'] = i
            # R1-E: 本 Part 迭代用的正式角色名（Phase 2 档案；防常见词误报）
            char_names = [c.get('name', '') for c in (s.data.get('characters') or []) if isinstance(c, dict) and c.get('name')]
            done_ratio = (i - start_from) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio * 25
            s.progress_callback(int(part_progress), f'开始创作 Part {i}/{total}')
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'part_writer', 'part': i, 'status': 'start', 'message': f'开始创作 Part {i}/{total}', 'work_id': s.work_id}, work_id=s.work_id)
            max_retries = 2
            part_text = ''
            word_count = 0
            skip_part = False
            for attempt in range(max_retries + 1):
                try:
                    part_result = await asyncio.to_thread(writer_agent.execute, temp_state, i)
                    if isinstance(part_result, dict) and part_result.get('success'):
                        part_text = part_result.get('content', '')
                    else:
                        part_text = part_result if isinstance(part_result, str) else ''
                    if not part_text:
                        raise RuntimeError(f'Part {i} 返回为空内容')
                    s.data['parts'][str(i)] = part_text
                    summary = truncate(part_text, n=200, suffix="...")
                    s.data['part_summaries'][str(i)] = summary
                    # R1-D: 事实块与 parts/summaries 同批落盘 —— 此前只挂 temp_state
                    # 实例不写 work JSON，resume 后从零开始；此处保证每 Part 边界
                    # established_facts 已持久化（下面 s._save() 同批写出）。
                    try:
                        ef_obj = getattr(temp_state, 'established_facts', None)
                        if ef_obj is not None:
                            s.data['established_facts'] = ef_obj.to_dict()
                    except Exception as ef_save_err:
                        logger.info(f'[Phase3Runner] established_facts 落盘失败（不影响主流程）: {ef_save_err}')
                    # R1-E: 退场账本 —— 从已确立事实确定性派生"已死/离开/失踪/退场"
                    # 角色，合并写入 character_state_track（不清空历史），刷新
                    # temp_state 让滑动窗口/milestone 首次拿到非空角色状态。
                    try:
                        from core.established_facts import derive_departed_characters
                        departed = derive_departed_characters(getattr(temp_state, 'established_facts', None), char_names)
                        if departed:
                            track = dict(s.data.get('character_state_track') or {})
                            track.update(departed)
                            s.data['character_state_track'] = track
                            temp_state.character_state_track = track
                    except Exception as dep_err:
                        logger.info(f'[Phase3Runner] 退场账本派生失败（不影响主流程）: {dep_err}')
                    # R1-E: 确定性预检 —— 本 Part 正文再现"前文已退场"角色只告警不阻断
                    # （回忆/他人提及形式合法，终判交 Phase 4）。退场清单只取
                    # part_num < i 的事实，避免把"本 Part 内的死亡场景本身"算作出场。
                    try:
                        from core.established_facts import derive_departed_characters as _ddc
                        ef_facts = [f for f in (getattr(temp_state, 'established_facts', None).facts
                                                if getattr(temp_state, 'established_facts', None) is not None else [])
                                    if getattr(f, 'part_num', 0) < i]
                        departed_before = _ddc(ef_facts, char_names)
                        for c_name in departed_before:
                            hits = part_text.count(c_name)
                            if hits:
                                logger.warning(
                                    f'[Phase3Runner] R1-E 预检: Part {i} 中已退场角色 "{c_name}" '
                                    f'出现 {hits} 次（退场记录: {departed_before[c_name]}）——仅告警不阻断'
                                )
                                flags = list(s.data.get('consistency_flags') or [])
                                flags.append({'part': i, 'character': c_name, 'count': hits,
                                              'departed_record': departed_before[c_name]})
                                s.data['consistency_flags'] = flags
                    except Exception as pre_err:
                        logger.info(f'[Phase3Runner] 退场角色预检失败（不影响主流程）: {pre_err}')
                    # R4-6: 伏笔回收确定性复检（零 LLM）—— reveal_part == i 的伏笔，
                    # 取 content 前 FORESHADOW_KEYWORD_LEN 字做正文子串命中检查，
                    # 未命中追加 consistency_flags 并告警（只告警不阻断，终判交 Phase 4）
                    try:
                        for _fid in check_foreshadow_reveal(s.data.get('foreshadowing') or [], i, part_text):
                            logger.warning(
                                f'[Phase3Runner] R4-6 预检: Part {i} 伏笔 [{_fid}] 未在正文中检出回收关键词'
                                f'——仅告警不阻断'
                            )
                            flags = list(s.data.get('consistency_flags') or [])
                            flags.append({'part': i, 'type': 'foreshadow_unrevealed', 'foreshadow_id': _fid})
                            s.data['consistency_flags'] = flags
                    except Exception as fs_err:
                        logger.info(f'[Phase3Runner] R4-6 伏笔回收复检失败（不影响主流程）: {fs_err}')
                    try:
                        temp_state.window.add_part(i, part_text, summary)
                        temp_state.window.update_foreshadowing(s.data.get('foreshadowing', []) or [])
                        temp_state.window.update_character_state(s.data.get('character_state_track', {}) or {})
                    except Exception as win_err:
                        logger.info(f'[Phase3Runner] SlidingWindow.add_part 失败（不影响主流程）: {win_err}')
                    try:
                        if s.vector_store is not None and s.vector_store.enabled:
                            s.vector_store.add(i, part_text)
                    except Exception as vs_err:
                        logger.info(f'[Phase3Runner] vector_store.add 失败（不影响主流程）: {vs_err}')
                    # P0-90: 委托给 SlidingWindow 自带方法，删除 Phase3Runner 重复实现
                    # （PartWriterAgent.execute 已先调用过 window.maybe_generate_*，
                    # 这里再做一次幂等查询：已存在的会跳过，无 LLM 重复调用）
                    if temp_state.window.should_create_rolling_summary(i):
                        try:
                            world_setting = getattr(temp_state, 'world_setting', '') or ''
                            roll_result = temp_state.window.maybe_generate_rolling_summary(i, world_setting=world_setting, work_id=getattr(temp_state, 'work_id', None))
                            if roll_result.get('generated'):
                                await s.emitter.emit(EventType.LOG, {'message': f'📚 Part {i} 二级滚动摘要已生成（{roll_result.get("char_count", 0)} 字）', 'work_id': s.work_id}, work_id=s.work_id)
                                # R1-G: 剧情状态增量与 rolling 摘要同触点合并一次轻量调用
                                # （每 3 Part 一次，不回写 outline 本体，叠加层注入 prompt）
                                try:
                                    delta = await self._generate_story_delta(i)
                                    if delta:
                                        deltas = dict(s.data.get('story_deltas') or {})
                                        deltas[str(i)] = delta
                                        s.data['story_deltas'] = deltas
                                        await s.emitter.emit(EventType.LOG, {'message': f'🧭 Part {i} 剧情状态增量已记录', 'work_id': s.work_id}, work_id=s.work_id)
                                except Exception as delta_err:
                                    logger.info(f'[Phase3Runner] story_delta 生成失败（不影响主流程）: {delta_err}')
                        except Exception as roll_err:
                            logger.info(f'[Phase3Runner] 二级滚动摘要生成失败（不影响主流程）: {roll_err}')
                    if temp_state.window.should_create_milestone(i):
                        try:
                            world_setting = getattr(temp_state, 'world_setting', '') or ''
                            mile_result = temp_state.window.maybe_generate_milestone(i, world_setting=world_setting, work_id=getattr(temp_state, 'work_id', None))
                            if mile_result.get('generated'):
                                await s.emitter.emit(EventType.LOG, {'message': f'🏔️ 里程碑 #{mile_result.get("milestone_num", 0)} 摘要已生成（{mile_result.get("char_count", 0)} 字）', 'work_id': s.work_id}, work_id=s.work_id)
                        except Exception as m_err:
                            logger.info(f'[Phase3Runner] 三级里程碑摘要生成失败（不影响主流程）: {m_err}')
                    word_count = len(part_text)
                    # R2-4 附加（零成本可观测）: 每 Part 完成日志追加累计/预计总量，
                    # 便于长跑中监控 G2 趋势（仅 logger，不改行为）
                    _done_parts = len([k for k, v in s.data['parts'].items() if isinstance(v, str) and v.strip()])
                    _total_chars = sum(len(v) for v in s.data['parts'].values() if isinstance(v, str))
                    _avg_per_part = _total_chars // max(_done_parts, 1)
                    logger.info(f'[Phase3Runner] R2-4 Part {i} 完成: 累计 {_total_chars} 字 / {_done_parts} Part'
                                f'（均速 {_avg_per_part} 字/Part，按当前均速预计总量 {_avg_per_part * total} 字，目标 {s.cfg.target_word_count} 字）')
                    break
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.info(f'[Phase3Runner] Part {i} 第 {attempt + 1}/{max_retries + 1} 次尝试异常: {type(e).__name__}: {e}')
                    logger.info(f'[Phase3Runner] Traceback: {tb}')
                    if attempt < max_retries:
                        await s.emitter.emit(EventType.LOG, {'message': f'⚠️ Part {i} 第 {attempt + 1} 次失败，{3 * (attempt + 1)}s 后重试...', 'work_id': s.work_id}, work_id=s.work_id)
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    if s.cfg.confirm_mode:
                        await s.emitter.emit(EventType.LOG, {'message': f'❌ Part {i} 已重试 {max_retries} 次仍失败，等待用户决策', 'work_id': s.work_id}, work_id=s.work_id)
                        try:
                            await s._request_confirm(f'part_{i}_failed', f'⚠️ Part {i}/{total} 创作失败（已重试 {max_retries} 次）\n\n错误：{str(e)[:200]}\n\n选择「继续」将标记此 Part 为失败并跳过，「取消」将中断整个流程')
                            skip_part = True
                        except Exception as confirm_err:
                            logger.info(f'[Phase3Runner] 用户在 Part {i} 失败时选择取消: {confirm_err}')
                            raise
                    else:
                        skip_part = True
                    if skip_part:
                        await s.emitter.emit(EventType.ERROR, {'message': f'Part{i}创作失败（已跳过）: {str(e)[:200]}', 'work_id': s.work_id}, work_id=s.work_id)
                        s.data['parts'][str(i)] = f'[Part {i} 创作失败]'
                        failed_parts = list(s.data.get('failed_parts', []) or [])
                        if i not in failed_parts:
                            failed_parts.append(i)
                            s.data['failed_parts'] = failed_parts
                        word_count = 0
                        break
            s.data['phase'] = f'phase3_part{i}'
            s._save()
            done_ratio_after = (i - start_from + 1) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio_after * 25
            s.progress_callback(int(part_progress), f'Part {i} 创作完成 ({word_count}字)')
            await s.emitter.emit(EventType.PART_COMPLETE, {'part': i, 'words': word_count, 'work_id': s.work_id}, work_id=s.work_id)
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'part_writer', 'part': i, 'status': 'end', 'message': f'Part {i} 创作完成 ({word_count}字)', 'work_id': s.work_id}, work_id=s.work_id)
            if s.cfg.confirm_mode and (i % 5 == 0 or i == total):
                await s._request_confirm(f'part_{i}_complete', f'📄 Part {i}/{total} 创作完成！\n\n本Part字数: {word_count:,} 字\n累计进度: {i}/{total} Part\n\n是否继续创作下一个Part？')
            if s.cfg.confirm_mode and (i % 10 == 0 or i == total):
                try:
                    from core.cost_tracker import should_prompt_for_cost
                    if should_prompt_for_cost(work_id=s.work_id):
                        from core.cost_tracker import get_tracker
                        summary = get_tracker(work_id=s.work_id).get_summary()
                        await s._request_confirm(f'cost_limit_{i}', f"💰 已花费约 ¥{summary['estimated_cost_rmb']:.2f}（{summary['total_calls']} 次调用）\n\n是否继续创作？")
                except Exception as cost_err:
                    logger.info(f'[Phase3Runner] 成本熔断检查失败（不影响主流程）: {cost_err}')

    async def _generate_story_delta(self, part_num: int) -> dict:
        """R1-G: rolling 触发点合并一次轻量 LLM 调用，输出本 Part 相对静态大纲的增量。

        输入本 Part 摘要 + 下一 Part outline 条目，输出 JSON：
        {"departed_characters": [], "new_objects": [], "foreshadow_planted": [],
         "foreshadow_revealed": [], "outline_adjustments": "..."}
        调用方写入 s.data['story_deltas'][str(part_num)]（叠加层，不回写 outline）。
        仅在 PartWriterAgent 已成功生成 rolling 摘要后调用，失败不影响主流程。
        """
        from core.llm_client import call_llm_json
        s = self.service
        summary = (s.data.get('part_summaries', {}) or {}).get(str(part_num), '')
        outline = s.data.get('part_outline') or []
        next_entry = outline[part_num] if part_num < len(outline) else None
        if not summary or not isinstance(next_entry, dict):
            return {}
        system_prompt = (
            '你是长篇小说剧情状态追踪员。根据"本 Part 实际剧情摘要"和"下一 Part 的原定大纲"，'
            '输出本 Part 相对静态大纲已发生的实际变化。只输出一个 JSON 对象，不要任何其他内容。'
        )
        user_prompt = (
            f'## 本 Part（Part {part_num}）实际剧情摘要\n{summary}\n\n'
            f'## 下一 Part（Part {part_num + 1}）原定大纲\n'
            f'核心事件：{next_entry.get("core_event", "")}\n'
            f'结尾钩子：{next_entry.get("end_hook", "")}\n'
            f'因果关系：{next_entry.get("causality", "")}\n\n'
            '请输出 JSON：\n'
            '{"departed_characters": ["本 Part 中死亡/离开/失踪的角色名"],'
            ' "new_objects": ["本 Part 新出现且后续关键物品/线索名"],'
            ' "foreshadow_planted": ["本 Part 新埋设的伏笔（一句话）"],'
            ' "foreshadow_revealed": ["本 Part 已揭晓的伏笔（一句话）"],'
            ' "outline_adjustments": "下一 Part 写作时需要按实际剧情修正的点（没有则空字符串）"}'
        )
        payload = call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            # R2-5: 硬编码 2000 → 任务级单点 get_task_max_tokens('json_facts')
            # （默认 8000：2000 对推理模型偏紧，reasoning 即可吃光）
            max_tokens=get_task_max_tokens('json_facts'),
            agent='story_delta',
            work_id=s.work_id,
        )
        return payload if isinstance(payload, dict) else {}


class Phase4Runner:
    """风格优化 + 评审阶段 —— StyleOptimizer + Logic/Emotion/Consistency Review"""

    def __init__(self, service: "WritingService"):
        self.service = service

    async def _final_name_audit(self, s, part_nums: list, consistency_agent,
                                state_mock) -> dict:
        """R5-1: final_draft 确定性终审（双探测器 + 有界动作，零 LLM 扫描）。

        探测器 A（违禁对扫描，vale Terms 模式）+ 探测器 B（facts 主语 oracle）。
        词典四路沉淀：alias_candidate / 各检查点 derive_name_pairs（R5-2 接线）/
        revision_log 历史 name_spotfix（本方法恢复）/ 终审 B 重审产出。

        动作（02_review §2.1 终审动作，全部有界）：
        - A 类 blocking 且配对过 4 条安全闸 → 定点修复，**只写 final_draft**
          （禁止 _save_chunk_progress —— 那会覆盖 parts、改变 G2/G3 统计源）；
          跨 Part 传播护栏：仅首见 Part 或本 Part count>=2 才自动修，否则降级
          advisory（防把陌生 Part 的合法配角名抹掉）；applied_verified 条目零
          LLM 直接修，首次发现补 1 次 consistency 重审，仍报同一错误名 → 回退
          该 Part final_draft 并记 unfixed_blocking；
        - A 类 advisory / B 类 → 针对性重审预算（每 Part 1 次、每跑
          max(2, PARTS//4) 次；env KML_NAME_AUDIT_REREVIEW_BUDGET 可覆盖，
          显式 0 = 关闭重审只告警）；**B 永不自动改文本、永不进 G4**；
        - 全部 finding 落 s.data['name_audit_log']，词典沉淀随 s._save() 落盘。
        """
        from services.consistency_repair import (apply_name_spotfix,
                                                 apply_safety_gates,
                                                 derive_name_pairs)
        from services.name_audit import (
            append_audit_log, audit_name_drift, is_blocking,
            record_name_pairs, recover_drift_dict_from_revision_log,
            review_reports_name,
        )
        registry = s.data.get('name_registry') or {}
        if not isinstance(registry, dict):
            registry = {}
        final_draft = s.data.get('final_draft') or {}
        if not isinstance(final_draft, dict):
            final_draft = {}
        facts_raw = s.data.get('established_facts')
        drift_dict = recover_drift_dict_from_revision_log(s.data)
        scan = audit_name_drift(final_draft, drift_dict, facts_raw, registry)
        if scan['scanned'] == 0:
            return scan
        departed_names = [n for n in (s.data.get('character_state_track') or {}) if n]

        # 针对性重审预算：每 Part 1 次 + 每跑 max(2, PARTS//4) 次（env 可覆盖）
        raw_budget = os.environ.get('KML_NAME_AUDIT_REREVIEW_BUDGET', '')
        if raw_budget.strip():
            try:
                budget = max(0, int(raw_budget))
            except (TypeError, ValueError):
                budget = max(2, (len(part_nums) or s.cfg.part_count or 0) // 4)
        else:
            budget = max(2, (len(part_nums) or s.cfg.part_count or 0) // 4)

        run_entries: list = []

        def _log(part_num, wrong, right, count_before, count_after, action, pair_source):
            entry = {'part': part_num, 'wrong': wrong, 'right': right,
                     'count_before': count_before, 'count_after': count_after,
                     'action': action, 'trigger': 'final_audit',
                     'pair_source': pair_source,
                     'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}
            append_audit_log(s.data, entry)
            run_entries.append(entry)
            return entry

        async def _rereview(part_num: int, part_text: str) -> dict:
            # 只刷新 final_draft 一个属性（consistency agent 要拿前文结尾）
            state_mock.final_draft = dict(s.data.get('final_draft') or {})
            try:
                return await asyncio.to_thread(
                    consistency_agent.execute, state_mock, part_num, part_text)
            except Exception as e:
                logger.info(f'[Phase4Runner] 终审重审 Part {part_num} 失败: {e}')
                return s._review_failure('consistency', part_num, e)

        # ---- A 类 blocking：定点修复（跨 Part 传播护栏 + 验证分工） ----
        for f in list(scan['residual_blocking']):
            part_num, wrong, right = f['part'], f['wrong'], f['right']
            part_key = str(part_num)
            text = final_draft.get(part_key) or ''
            entry = drift_dict.get(wrong) or {}
            # 跨 Part 传播护栏：仅首见 Part 或本 Part count>=2 才自动修
            if part_num != entry.get('first_seen_part') and f['count'] < 2:
                _log(part_num, wrong, right, f['count'], f['count'],
                     'downgraded_advisory', entry.get('source', ''))
                logger.info(f'[Phase4Runner] 终审: Part {part_num} 错误名 "{wrong}" 单次出现于'
                            f'非首见 Part，降级 advisory（防止误抹合法配角名）')
                continue
            gated = apply_safety_gates(
                [{'wrong': wrong, 'right': right, 'source': entry.get('source', ''),
                  'evidence': entry.get('evidence', '')}], registry, departed_names)
            if not gated:
                _log(part_num, wrong, right, f['count'], f['count'],
                     'gate_rejected', entry.get('source', ''))
                logger.warning(f'[Phase4Runner] 终审: Part {part_num} 配对 "{wrong}"→"{right}" '
                               f'未过安全闸，保留待人工核查')
                continue
            fixed, ok, reason = apply_name_spotfix(text, gated)
            if not ok:
                _log(part_num, wrong, right, f['count'], f['count'],
                     'unfixed_blocking', entry.get('source', ''))
                logger.warning(f'[Phase4Runner] 终审: Part {part_num} 定点替换校验失败（{reason}）')
                continue
            count_after = fixed.count(wrong)
            if entry.get('applied_verified'):
                # 已被"重审通过"的定点修复验证过的配对 → 零 LLM 直接修
                final_draft[part_key] = fixed
                record_name_pairs(s.data, gated, part_num, 'final_audit',
                                  applied_verified=True, gates_passed=True)
                _log(part_num, wrong, right, f['count'], count_after,
                     'spotfixed', entry.get('source', ''))
                self._append_final_audit_revision(s, part_num, wrong, right,
                                                  entry.get('source', ''))
                logger.info(f'[Phase4Runner] 终审: Part {part_num} 已配对 "{wrong}"→"{right}" '
                            f'×{f["count"]} 直接定点修复（零 LLM）')
                continue
            # 首次发现：修完补 1 次 consistency 重审；仍报同一错误名 → 回退
            review = await _rereview(part_num, fixed)
            if review_reports_name(review, wrong):
                _log(part_num, wrong, right, f['count'], text.count(wrong),
                     'unfixed_blocking', entry.get('source', ''))
                logger.warning(f'[Phase4Runner] 终审: Part {part_num} 定点修复后重审仍报 '
                               f'"{wrong}"，回退该 Part final_draft 到修复前文本')
                continue
            final_draft[part_key] = fixed
            record_name_pairs(s.data, gated, part_num, 'final_audit',
                              applied_verified=True, gates_passed=True)
            _log(part_num, wrong, right, f['count'], count_after,
                 'spotfixed', entry.get('source', ''))
            self._append_final_audit_revision(s, part_num, wrong, right,
                                              entry.get('source', ''))
            logger.info(f'[Phase4Runner] 终审: Part {part_num} "{wrong}"→"{right}" '
                        f'×{f["count"]} 定点修复并经 1 次重审验证')

        # ---- A 类 advisory + B 类：针对性重审预算（有界，B 永不改文本） ----
        candidates = []
        for f in scan['residual_advisory']:
            candidates.append({'part': f['part'], 'wrong': f['wrong'],
                               'right': f['right'], 'tier': 2, 'kind': 'advisory'})
        for f in scan['canonical_absent']:
            # tier 1 = 该 canonical 在其他 Part 正文出现过（名字在全书在用）
            in_use = any(
                isinstance(t, str) and f['canonical'] in t
                for k, t in final_draft.items() if str(k) != str(f['part']))
            candidates.append({'part': f['part'], 'wrong': '',
                               'right': f['canonical'],
                               'tier': 1 if in_use else 2, 'kind': 'canonical_absent'})
        # R6-6（S6）: 退场角色复现探测器（advisory-only）—— 复用
        # derive_departed_characters（R1-E 同源），扫终审时点的 final_draft；
        # tier 1（与 canonical_absent"名字在全书在用"同档）、Part 升序，与 B
        # 共用既有双重预算（每 Part 1 次、每跑 max(2, PARTS//4) 次，公式不
        # 放宽）；findings 永不进 residual_blocking、永不自动改文本、永不进 G4
        try:
            from services.name_audit import scan_departed_reappearance
            char_names = [c.get('name', '') for c in (s.data.get('characters') or [])
                          if isinstance(c, dict) and c.get('name')]
            if not char_names:
                char_names = [n for n in registry if isinstance(n, str)]
            for f in scan_departed_reappearance(final_draft, facts_raw, char_names):
                candidates.append({'part': f['part'], 'wrong': '',
                                   'right': f['character'], 'tier': 1,
                                   'kind': 'departed_reappearance'})
                _log(f['part'], '', f['character'], f['count'], f['count'],
                     'departed_flagged', 'departed_reappearance')
                logger.info(f'[Phase4Runner] 终审: Part {f["part"]} 退场角色 '
                            f'"{f["character"]}" 复现 ×{f["count"]}（advisory，'
                            f'例：{f["samples"][0][:40] if f["samples"] else ""}）')
        except Exception as dep_err:
            logger.info(f'[Phase4Runner] 退场复现扫描异常（不影响主流程）: {dep_err}')
        candidates.sort(key=lambda c: (c['tier'], c['part']))
        rereviewed_parts: set = set()
        used = 0
        for c in candidates:
            part_num, part_key = c['part'], str(c['part'])
            text = final_draft.get(part_key) or ''
            c_count = text.count(c['wrong']) if c['wrong'] else 0
            if part_num in rereviewed_parts or used >= budget:
                _log(part_num, c['wrong'], c['right'], c_count, c_count,
                     'budget_skipped', c['kind'])
                continue
            rereviewed_parts.add(part_num)
            used += 1
            review = await _rereview(part_num, text)
            # B/advisory 重审是"证据注入器"：产出名称 P0 且配对可推导 → 沉淀
            # （gates_passed 不置位 —— 守住"B 永不影响 G4"的裁定）
            pairs = derive_name_pairs(text, review, registry)
            if pairs:
                record_name_pairs(s.data, pairs, part_num, 'final_audit')
            _log(part_num, c['wrong'], c['right'], c_count, c_count,
                 'rereviewed', c['kind'])
            logger.info(f'[Phase4Runner] 终审: Part {part_num} {c["kind"]} 告警'
                        f'（{"错误名 " + c["wrong"] if c["wrong"] else "规范名缺席 " + c["right"]}）'
                        f'已注入 1 次针对性重审（预算 {used}/{budget}），不自动改文本')

        # ---- 残留重算（与 verify 侧 summarize_name_audit 同口径；只认本轮处置，
        # 防止 resume 后上一轮的 spotfixed/downgraded 记录压制本轮 blocking 残留） ----
        fixed_keys = {(e['part'], e['wrong']) for e in run_entries
                      if e.get('action') == 'spotfixed' and (e.get('count_after') or 0) == 0}
        downgraded_keys = {(e['part'], e['wrong']) for e in run_entries
                           if e.get('action') == 'downgraded_advisory'}
        scan['residual_blocking'] = [
            f for f in scan['residual_blocking']
            if (f['part'], f['wrong']) not in fixed_keys | downgraded_keys]
        scan['residual_advisory'] = (
            scan['residual_advisory']
            + [f for f in scan['findings']
               if f['kind'] == 'forbidden_name' and f['blocking']
               and (f['part'], f['wrong']) in downgraded_keys])
        logger.info(f'[Phase4Runner] 终审名称审计: 扫描 {scan["scanned"]} Part，'
                    f'findings {len(scan["findings"])}，fixed {len(fixed_keys)}，'
                    f'residual_blocking {len(scan["residual_blocking"])}，'
                    f'residual_advisory {len(scan["residual_advisory"])}，'
                    f'canonical_absent {len(scan["canonical_absent"])}')
        for f in scan['residual_blocking']:
            logger.warning(f'[Phase4Runner] 终审 blocking 残留: Part {f["part"]} '
                           f'"{f["wrong"]}"→"{f["right"]}" ×{f["count"]}（进 G4）')
        return scan

    @staticmethod
    def _append_final_audit_revision(s, part_num: int, wrong: str, right: str,
                                     pair_source: str) -> None:
        """R5-2: 终审定点修复落 revision_log（trigger='final_audit'，只增不改）。

        纯观测字段（聚合器不读 revision_log，不影响 revision_stats 与门禁），
        供全量跑后统计各检查点贡献。
        """
        try:
            log = list(s.data.get('revision_log') or [])
            log.append({'part': part_num, 'p0_before': 0, 'revision_attempted': True,
                        'revision_passed': True, 'residual_p0': 0,
                        'trigger': 'final_audit', 'type': 'name_spotfix',
                        'wrong_name': wrong, 'right_name': right,
                        'pair_source': pair_source,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')})
            s.data['revision_log'] = log
        except Exception as e:
            logger.info(f'[Phase4Runner] 终审 revision_log 落盘失败（不影响主流程）: {e}')

    async def run(self) -> None:
        s = self.service
        await s.emitter.emit(EventType.PHASE, {'phase': 'phase4', 'name': '风格优化', 'work_id': s.work_id}, work_id=s.work_id)
        await s.emitter.emit(EventType.LOG, {'message': '开始风格优化...', 'work_id': s.work_id}, work_id=s.work_id)
        from core.agents.style_optimizer_agent import StyleOptimizerAgent
        from core.agents.logic_review_agent import LogicReviewAgent
        from core.agents.emotion_review_agent import EmotionReviewAgent
        from core.agents.consistency_review_agent import ConsistencyReviewAgent
        try:
            state_mock = s._build_review_state_mock()
            part_nums: list = []
            for k, v in (s.data.get('parts', {}) or {}).items():
                # R4-P3-x: 排除 '[Part N 创作失败]' 占位符——非空串会进评审，
                # 白烧 3 次 LLM 调用并产出无意义评审。
                if isinstance(v, str) and v.strip() and not v.startswith('[Part '):
                    try:
                        part_nums.append(int(k))
                    except (TypeError, ValueError):
                        continue
            part_nums = sorted(set(part_nums))
            # R6-4（S4）: resume 跳过已审 Part —— phase4_review_progress 只增不改，
            # 降级条目（_fallback/429 降级，needs_rerun=True）不计入完成；同 Part
            # 多条只取最后一条（重审过的 Part 后写覆盖）；repair_note 的键
            # （含 first_pass_p0/revision_*）原样合并，聚合口径与全新跑一致
            latest_progress: dict = {}
            for _e in (s.data.get('phase4_review_progress') or []):
                if isinstance(_e, dict) and not _e.get('needs_rerun') \
                        and _e.get('part') in part_nums:
                    latest_progress[_e['part']] = _e
            per_part_results: list = []
            for _p in sorted(latest_progress):
                _e = latest_progress[_p]
                per_part_results.append({
                    'part': _p,
                    'logic_result': _e.get('logic_result') or {},
                    'emotion_result': _e.get('emotion_result') or {},
                    'consistency_result': _e.get('consistency_result') or {},
                    **(_e.get('repair_note') or {})})
            done_parts = set(latest_progress)
            remaining = [p for p in part_nums if p not in done_parts]
            if done_parts:
                logger.info(f'[Phase4Runner] R6-4 resume: {len(done_parts)} 个 Part '
                            f'已审（progress 跳过），剩余 {len(remaining)} 个待审查')
            await s.emitter.emit(EventType.LOG, {'message': f'评审阶段：共 {len(remaining)} 个 Part 待审查', 'work_id': s.work_id}, work_id=s.work_id)
            logic_agent = LogicReviewAgent()
            emotion_agent = EmotionReviewAgent()
            consistency_agent = ConsistencyReviewAgent()
            # R1-I: 三评审并行 —— 此前串行 20 Part = 60 次顺序 LLM 调用（估 40-60min）。
            # 三个 agent 为独立实例、call_llm_json 每次自建 client，无共享可变状态；
            # Semaphore(3) 防 provider 限流。AGENT_CALL start 全部先发、end 按完成顺序发。
            review_semaphore = asyncio.Semaphore(3)
            # R5-S6（P1-1）: 跨 Part 连续修复失败计数（达 3 告警不停机 —— 全量无人值守
            # 跑的黑洞防线：修复回路持续失败必须有事前可见的信号）
            consec_fail = 0

            async def _run_review(agent, kind: str, part_num: int, part_text: str) -> dict:
                async with review_semaphore:
                    try:
                        return await asyncio.to_thread(agent.execute, state_mock, part_num, part_text)
                    except Exception as e:
                        logger.info(f'[Phase4Runner] {kind} Part {part_num} 失败: {e}')
                        return s._review_failure(kind, part_num, e)

            for idx, part_num in enumerate(remaining, start=1):
                part_key = str(part_num)
                part_text = s.data['parts'][part_key]
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'start', 'message': f'审查 Part {part_num} 逻辑...', 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'start', 'message': f'评估 Part {part_num} 情感...', 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'start', 'message': f'检查 Part {part_num} 一致性...', 'work_id': s.work_id}, work_id=s.work_id)
                logic_result, emotion_result, consistency_result = await asyncio.gather(
                    _run_review(logic_agent, 'logic', part_num, part_text),
                    _run_review(emotion_agent, 'emotion', part_num, part_text),
                    _run_review(consistency_agent, 'consistency', part_num, part_text),
                )
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 逻辑审查完成', 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'end', 'message': f"Part {part_num} 情感评估: {emotion_result.get('emotion_score', 'N/A')}", 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 一致性检查完成', 'work_id': s.work_id}, work_id=s.work_id)
                per_part_results.append({'part': part_num, 'logic_result': logic_result if isinstance(logic_result, dict) else {}, 'emotion_result': emotion_result if isinstance(emotion_result, dict) else {}, 'consistency_result': consistency_result if isinstance(consistency_result, dict) else {}})
                # R1-J: P0 定向修复回路（最多 1 轮重写；无 P0 时零行为变化）
                repair_note = None
                try:
                    from services.consistency_repair import ConsistencyRepairer
                    repairer = ConsistencyRepairer(s, logic_agent, consistency_agent)
                    repair_note = await repairer.maybe_repair_part(
                        part_num, part_text, logic_result, consistency_result, state_mock)
                    if repair_note:
                        per_part_results[-1].update(repair_note)
                        logger.info(f'[Phase4Runner] Part {part_num} 修复回路: {repair_note.get("revision_passed")}')
                except Exception as repair_err:
                    logger.info(f'[Phase4Runner] Part {part_num} 修复回路异常（保留原文，不影响主流程）: {repair_err}')
                # R5-S6（P1-1）: 连续修复失败告警（达 3 告警不停机，resume/人工可查
                # revision_log；无修复或修复通过的 Part 清零计数）
                if repair_note and repair_note.get('revision_attempted'):
                    if repair_note.get('revision_passed'):
                        consec_fail = 0
                    else:
                        consec_fail += 1
                        if consec_fail >= 3:
                            logger.warning(
                                f'[Phase4Runner] 连续 {consec_fail} 个 Part 修复未通过'
                                f'（最近: Part {part_num}），建议人工介入核查 revision_log')
                else:
                    consec_fail = 0
                # R6-4（S4）: 评审增量落盘 —— per_part_results 此前是纯内存 list，
                # 崩溃即全损（reval 实证 3 小时三审+修复结果丢失）。needs_rerun 标记
                # 降级结果（_fallback/429 降级）不计入完成，resume 时重审该 Part
                # （与 R6-3 退避配合：兜底不是主路径）。
                _progress = s.data.setdefault('phase4_review_progress', [])
                _progress.append({
                    'part': part_num,
                    'logic_result': logic_result,
                    'emotion_result': emotion_result,
                    'consistency_result': consistency_result,
                    'repair_note': repair_note,
                    'needs_rerun': bool((logic_result or {}).get('_fallback')
                        or str((consistency_result or {}).get('verdict', '')).startswith('检查失败')),
                })
                s._save()
                if remaining:
                    part_progress = 85 + idx / len(remaining) * 10
                    s.progress_callback(int(part_progress), f'Part {part_num} 评审完成 ({idx}/{len(remaining)})')
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'start', 'message': '执行风格优化...', 'work_id': s.work_id}, work_id=s.work_id)
            style_agent = StyleOptimizerAgent()
            # R4-P1-x: StyleOptimizerAgent.execute 签名是 (state, part_num, part_text, ...)——
            # 此前只传 state 必 TypeError，被吞后 final_draft 直接取 parts，"风格优化"
            # 从未真正执行但前端显示完成。改为逐 Part 调用，失败的 Part 保留原文。
            # state_mock 含 part_outline/work_id（execute 内部会取 outline[part_num-1]）。
            s.data['final_draft'] = dict(s.data.get('parts', {}))
            # R6-4（S4/R6-7）: KML_SKIP_STYLE=1 跳过风格优化循环 —— 复评工具
            # （reval + KML_SKIP_STYLE 聚焦三审+修复+名称终审，~2.5h）。G1/G2/G3
            # 统计源是 parts、G4 是 review_report + name_audit(final_draft)，
            # 跳风格不改变任何门禁口径；名称终审与聚合保留。
            skip_style = os.environ.get('KML_SKIP_STYLE', '') == '1'
            if skip_style:
                logger.info('[Phase4Runner] KML_SKIP_STYLE=1：跳过风格优化（保留名称终审与聚合）')
                await s.emitter.emit(EventType.LOG, {'message': 'KML_SKIP_STYLE=1：跳过风格优化（保留名称终审与聚合）', 'work_id': s.work_id}, work_id=s.work_id)
            style_ok = 0
            style_fail = 0
            if not skip_style:
                # R6-4（S4）: 风格 checkpoint 叠加 —— optimized 的 Part 用 checkpoint
                # 文本覆盖 parts 副本；跳过条件 = 已 checkpoint 且 parts 当前长度
                # == source_len（resume 后 parts 可能被修复改变，长度不匹配则重新
                # 优化 —— 防陈旧风格稿）。
                style_ckpt: dict = {}
                for _e in (s.data.get('phase4_style_progress') or []):
                    if isinstance(_e, dict) and _e.get('status') == 'optimized' \
                            and isinstance(_e.get('text'), str):
                        style_ckpt[_e.get('part')] = _e
                for part_num in part_nums:
                    part_key = str(part_num)
                    _ck = style_ckpt.get(part_num)
                    if _ck is not None and len(s.data['parts'].get(part_key) or '') == _ck.get('source_len'):
                        s.data['final_draft'][part_key] = _ck['text']
                for part_num in part_nums:
                    part_key = str(part_num)
                    original_text = s.data['parts'][part_key]
                    _ck = style_ckpt.get(part_num)
                    if _ck is not None and len(original_text) == _ck.get('source_len'):
                        style_ok += 1
                        logger.info(f'[Phase4Runner] R6-4 resume: Part {part_num} 风格优化'
                                    f'已 checkpoint（source_len={_ck.get("source_len")}），跳过')
                        continue
                    # R2-3 保险 1: 字数下限守卫 —— 此前只判 isinstance(str) and strip()，
                    # 431 字短返即可覆盖 5212 字原文（Round 1 实证 final_draft['1']=431）。
                    # 统一公式 max(2000, 原文*0.6) 无需 is_over 分支：Part 有硬上限
                    # hard_max=PART_WORD_MAX+200（超长即截断），合法润色稿只需压到
                    # target_max；要误伤需 原文*0.6 > target_max（即原文 > 1.67 倍上限），
                    # 而原文 ≤ 上限+200，条件不可达。
                    min_acceptable = max(2000, int(len(original_text) * 0.6))
                    try:
                        optimized = await asyncio.to_thread(style_agent.execute, state_mock, part_num, original_text)
                        if isinstance(optimized, str) and len(optimized.strip()) >= min_acceptable:
                            s.data['final_draft'][part_key] = optimized
                            style_ok += 1
                            # R6-4（S4）: 风格优化逐 Part 落盘 —— 此前是 Phase 4 最长的
                            # 不落盘窗口（每 Part 1-3 次尝试、单次 4-10 分钟）
                            s.data.setdefault('phase4_style_progress', []).append(
                                {'part': part_num, 'status': 'optimized', 'text': optimized,
                                 'source_len': len(original_text)})
                            s._save()
                        else:
                            style_fail += 1
                            logger.warning(f'[Phase4Runner] Part {part_num} 优化稿 {len(optimized or "")} 字 < 下限 {min_acceptable}（原文 {len(original_text)} 字），保留原文')
                            s.data.setdefault('phase4_style_progress', []).append(
                                {'part': part_num, 'status': 'kept',
                                 'source_len': len(original_text)})
                            s._save()
                    except Exception as e:
                        style_fail += 1
                        logger.info(f'[Phase4Runner] StyleOptimizer Part {part_num} 失败（保留原文）: {e}')
                        s.data.setdefault('phase4_style_progress', []).append(
                            {'part': part_num, 'status': 'kept',
                             'source_len': len(original_text)})
                        s._save()
            # R5-1: 违禁词典 + final_draft 确定性终审（双探测器，零 LLM 扫描）。
            # 必须在 final_draft 建成之后（扫得到交付文本）、聚合之前
            # （name_audit 进得了 G4 detail）；独立 try/except —— 异常只告警，
            # 不得冒泡到外层 except（那会把 final_draft 重置为 parts、丢掉修复）。
            try:
                await self._final_name_audit(s, part_nums, consistency_agent, state_mock)
            except Exception as audit_err:
                logger.info(f'[Phase4Runner] 终审名称审计异常（不影响主流程）: {audit_err}')
            s.data['review_report'] = s._aggregate_review_results(per_part_results)
            s.data['phase'] = 'phase4'
            total_words = sum((len(t) for t in s.data['final_draft'].values()))
            _style_msg = ('风格优化已跳过（KML_SKIP_STYLE=1，保留名称终审与聚合）'
                          if skip_style else
                          f'风格优化完成 (成功 {style_ok}/{len(part_nums)}, 失败 {style_fail}, 总字数: {total_words})')
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'end', 'message': _style_msg, 'work_id': s.work_id}, work_id=s.work_id)
            s._save()
            await s.emitter.emit(EventType.LOG, {'message': _style_msg, 'work_id': s.work_id}, work_id=s.work_id)
        except Exception as e:
            logger.info(f'[Phase4Runner] 出错: {e}')
            traceback.print_exc()
            await s.emitter.emit(EventType.ERROR, {'message': f'Phase4错误: {str(e)}', 'work_id': s.work_id}, work_id=s.work_id)
            s.data['final_draft'] = s.data.get('parts', {})
            s.data['phase'] = 'phase4'
            s._save()