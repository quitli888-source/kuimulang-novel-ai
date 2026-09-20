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
import traceback
from typing import TYPE_CHECKING

from api.sse import EventType
from core.memory_manager import get_all_memory
from core.text_utils import truncate
from core.logger import get_logger

if TYPE_CHECKING:
    from services.writing_service import WritingService

logger = get_logger('writing_phase_runners')


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
            s.data['part_outline'] = plot_result.get('part_outline', [])
            s.data['foreshadowing'] = plot_result.get('foreshadowing', [])
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


class Phase4Runner:
    """风格优化 + 评审阶段 —— StyleOptimizer + Logic/Emotion/Consistency Review"""

    def __init__(self, service: "WritingService"):
        self.service = service

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
            await s.emitter.emit(EventType.LOG, {'message': f'评审阶段：共 {len(part_nums)} 个 Part 待审查', 'work_id': s.work_id}, work_id=s.work_id)
            logic_agent = LogicReviewAgent()
            emotion_agent = EmotionReviewAgent()
            consistency_agent = ConsistencyReviewAgent()
            per_part_results: list = []
            for idx, part_num in enumerate(part_nums, start=1):
                part_key = str(part_num)
                part_text = s.data['parts'][part_key]
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'start', 'message': f'审查 Part {part_num} 逻辑...', 'work_id': s.work_id}, work_id=s.work_id)
                try:
                    logic_result = await asyncio.to_thread(logic_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[Phase4Runner] LogicReview Part {part_num} 失败: {e}')
                    logic_result = s._review_failure('logic', part_num, e)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 逻辑审查完成', 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'start', 'message': f'评估 Part {part_num} 情感...', 'work_id': s.work_id}, work_id=s.work_id)
                try:
                    emotion_result = await asyncio.to_thread(emotion_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[Phase4Runner] EmotionReview Part {part_num} 失败: {e}')
                    emotion_result = s._review_failure('emotion', part_num, e)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'end', 'message': f"Part {part_num} 情感评估: {emotion_result.get('emotion_score', 'N/A')}", 'work_id': s.work_id}, work_id=s.work_id)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'start', 'message': f'检查 Part {part_num} 一致性...', 'work_id': s.work_id}, work_id=s.work_id)
                try:
                    consistency_result = await asyncio.to_thread(consistency_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[Phase4Runner] ConsistencyReview Part {part_num} 失败: {e}')
                    consistency_result = s._review_failure('consistency', part_num, e)
                await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 一致性检查完成', 'work_id': s.work_id}, work_id=s.work_id)
                per_part_results.append({'part': part_num, 'logic_result': logic_result if isinstance(logic_result, dict) else {}, 'emotion_result': emotion_result if isinstance(emotion_result, dict) else {}, 'consistency_result': consistency_result if isinstance(consistency_result, dict) else {}})
                if part_nums:
                    part_progress = 85 + idx / len(part_nums) * 10
                    s.progress_callback(int(part_progress), f'Part {part_num} 评审完成 ({idx}/{len(part_nums)})')
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'start', 'message': '执行风格优化...', 'work_id': s.work_id}, work_id=s.work_id)
            style_agent = StyleOptimizerAgent()
            # R4-P1-x: StyleOptimizerAgent.execute 签名是 (state, part_num, part_text, ...)——
            # 此前只传 state 必 TypeError，被吞后 final_draft 直接取 parts，"风格优化"
            # 从未真正执行但前端显示完成。改为逐 Part 调用，失败的 Part 保留原文。
            # state_mock 含 part_outline/work_id（execute 内部会取 outline[part_num-1]）。
            s.data['final_draft'] = dict(s.data.get('parts', {}))
            style_ok = 0
            style_fail = 0
            for part_num in part_nums:
                part_key = str(part_num)
                try:
                    optimized = await asyncio.to_thread(style_agent.execute, state_mock, part_num, s.data['parts'][part_key])
                    if isinstance(optimized, str) and optimized.strip():
                        s.data['final_draft'][part_key] = optimized
                        style_ok += 1
                except Exception as e:
                    style_fail += 1
                    logger.info(f'[Phase4Runner] StyleOptimizer Part {part_num} 失败（保留原文）: {e}')
            s.data['review_report'] = s._aggregate_review_results(per_part_results)
            s.data['phase'] = 'phase4'
            total_words = sum((len(t) for t in s.data['final_draft'].values()))
            await s.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'end', 'message': f'风格优化完成 (成功 {style_ok}/{len(part_nums)}, 失败 {style_fail}, 总字数: {total_words})', 'work_id': s.work_id}, work_id=s.work_id)
            s._save()
            await s.emitter.emit(EventType.LOG, {'message': '风格优化完成', 'work_id': s.work_id}, work_id=s.work_id)
        except Exception as e:
            logger.info(f'[Phase4Runner] 出错: {e}')
            traceback.print_exc()
            await s.emitter.emit(EventType.ERROR, {'message': f'Phase4错误: {str(e)}', 'work_id': s.work_id}, work_id=s.work_id)
            s.data['final_draft'] = s.data.get('parts', {})
            s.data['phase'] = 'phase4'
            s._save()