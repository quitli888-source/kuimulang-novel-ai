"""
番茄小说AI创作系统 V5 - 创作服务层
封装V4的核心创作逻辑，支持SSE进度推送和暂停恢复
V5.1改动：集成进度管理器，实现实时创作进度推送
V5.2改动：实现完整的手动确认模式
"""
import json
import time
import asyncio
from pathlib import Path
from typing import Optional
from api.sse import SSEEmitter, EventType
from api.works import get_work_file
from core.config import get_app_config, MEMORY_DIR
from core.progress_manager import progress_manager
from core.error_handler import error_handler, ErrorType
from core.memory_manager import get_all_memory
from core.sliding_window import SlidingWindow
from core.logger import get_logger
logger = get_logger('writing_service')

class TempStoryState:
    """临时故事状态类，用于构建 PartWriterAgent 所需的上下文"""

    def __init__(self, data, memory=None, vector_store=None):
        self.inspiration = data.get('inspiration', '')
        self.core_elements = data.get('core_elements', {})
        self.market_positioning = data.get('market_positioning', {})
        self.world_setting = data.get('world_setting', '')
        self.characters = data.get('characters', [])
        self.part_outline = data.get('part_outline', [])
        self.foreshadowing = data.get('foreshadowing', [])
        self.parts = data.get('parts', {})
        self.part_summaries = data.get('part_summaries', {})
        self.current_plot_state = data.get('current_plot_state', '')
        self.character_state_track = data.get('character_state_track', {})
        self.memory = memory
        self.window = SlidingWindow(window_size=3)
        self.vector_store = vector_store

    def get_part_context(self, part_num):
        """获取指定部分的上下文信息（V6.1：委托给 SlidingWindow，失败回退旧实现）"""
        try:
            self._sync_window(part_num)
            self.window.update_foreshadowing(self.foreshadowing or [])
            self.window.update_character_state(self.character_state_track or {})

            def _legacy_extras(pn: int) -> str:
                sections = []
                if self.current_plot_state:
                    sections.append(f'【当前剧情进度】{self.current_plot_state}')
                    sections.append('')
                return '\n'.join(sections)
            vector_query = None
            if self.vector_store is not None and self.vector_store.enabled:
                outline = (self.part_outline or [])[part_num - 1] if part_num - 1 < len(self.part_outline or []) else None
                if isinstance(outline, dict):
                    core_event = outline.get('core_event', '')
                    emotion_target = outline.get('emotion_target', '')
                    vector_query = ' '.join((s for s in (core_event, emotion_target) if s)) or None
            return self.window.build(part_num, characters=self.characters, world_setting=self.world_setting, extra_context_provider=_legacy_extras, vector_store=self.vector_store, vector_query=vector_query)
        except Exception:
            return self._legacy_get_part_context(part_num)

    def _sync_window(self, part_num: int) -> None:
        """把 self.parts 中 < part_num 的所有 Part 灌入窗口。"""
        existing_in_window = set(self.window.parts.keys())
        for p_key in list(self.parts.keys()):
            try:
                p_int = int(p_key)
            except (TypeError, ValueError):
                continue
            if p_int >= part_num:
                continue
            if p_int in existing_in_window:
                continue
            text = self.parts[p_key]
            summary = self.part_summaries.get(p_key) or text[:200] + ('...' if len(text) > 200 else '')
            self.window.add_part(p_int, text, summary)
        for p_key, summary in self.part_summaries.items():
            try:
                p_int = int(p_key)
            except (TypeError, ValueError):
                continue
            if p_int < part_num and p_int not in self.window.summaries:
                self.window.summaries[p_int] = summary

    def _legacy_get_part_context(self, part_num):
        """保留的旧实现，作为滑动窗口失败时的回退路径。"""
        parts = []
        if self.characters:
            parts.append('【角色档案】')
            for c in self.characters:
                parts.append(f"- {c.get('name', '未知')}({c.get('role', '')}): {c.get('identity', '')}, 特质:{c.get('core_trait', '')}")
            parts.append('')
        if self.world_setting:
            parts.append(f'【世界观】{self.world_setting}')
            parts.append('')
        if self.foreshadowing:
            parts.append('【伏笔追踪】')
            for f_item in self.foreshadowing:
                parts.append(f"- {f_item.get('id', '')}: {f_item.get('content', '')} (埋于Part{f_item.get('plant_part', '?')}, 揭于Part{f_item.get('reveal_part', '?')})")
            parts.append('')
        completed_parts = sorted([k for k in self.part_summaries.keys()], key=lambda x: int(x))
        if completed_parts:
            parts.append('【已完成剧情摘要】')
            for p_num in completed_parts:
                parts.append(f'Part {p_num}: {self.part_summaries[p_num]}')
            parts.append('')
        prev_part = part_num - 1
        prev_key = str(prev_part)
        if prev_key in self.parts:
            prev_text = self.parts[prev_key]
            tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text
            parts.append(f'【上一部分（Part {prev_part}）结尾】')
            parts.append(tail)
        return '\n'.join(parts)
_writing_state: dict = {}
_pause_flags: dict = {}
_confirm_flags: dict = {}

class WritingState:

    def __init__(self, work_id: str):
        self.work_id = work_id
        self.phase = 'idle'
        self.current_part = 0
        self.total_parts = 0
        self.running = False
        self.paused = False

class WritingService:
    """创作服务 - 协调所有Agent完成创作流程"""

    def __init__(self, work_id: str, emitter: SSEEmitter, resume: bool=False, restart: bool=False):
        self.work_id = work_id
        self.emitter = emitter
        self.resume = resume
        self.restart = restart
        self.work_path = get_work_file(work_id)
        self.cfg = get_app_config()
        self.data = json.loads(self.work_path.read_text(encoding='utf-8'))
        if self.restart:
            logger.info(f'[WritingService] restart=True，重置 phase / parts / part_summaries')
            self.data['phase'] = 'init'
            self.data['parts'] = {}
            self.data['part_summaries'] = {}
            self.data.pop('failed_parts', None)
            self.data.pop('final_draft', None)
            self.data.pop('review_report', None)
            self.data.pop('character_state_track', None)
            self._save_initial_state()
        _writing_state[work_id] = {'phase': 'idle', 'current_part': 0, 'total_parts': self.cfg.part_count, 'running': False, 'paused': False}
        _pause_flags[work_id] = asyncio.Event()
        _confirm_flags[work_id] = {'event': asyncio.Event(), 'result': ''}
        self.progress_callback = progress_manager.get_progress_callback(work_id, 'WritingService')
        progress_manager.reset_progress(work_id)
        try:
            from core.cost_tracker import get_tracker
            tracker = get_tracker(work_id=self.work_id)
            tracker.attach_work(self.work_id, self.data.get('cost_summary'), self.work_path.parent)
        except Exception as attach_err:
            logger.info(f'[WritingService] cost_tracker.attach_work 失败（不影响主流程）: {attach_err}')
        self.vector_store = None
        try:
            from core.vector_store import VectorStore
            self.vector_store = VectorStore(persist_dir=self.work_path.parent / 'vectors', embedding_provider='none')
            if self.vector_store.enabled:
                for p_key, p_text in (self.data.get('parts', {}) or {}).items():
                    try:
                        self.vector_store.add(int(p_key), p_text)
                    except Exception:
                        continue
        except Exception as vs_err:
            logger.info(f'[WritingService] VectorStore 初始化失败（不影响主流程）: {vs_err}')
            self.vector_store = None

    async def run(self):
        """执行完整创作流程

        R3-P0-3: resume=True 时根据 data["phase"] 跳过已完成阶段：
          - phase1 已完成 → 跳过 _phase1_planning
          - phase2 已完成 → 跳过 _phase2_outline
          - phase3_part{N} → 从 Part N+1 开始写（避免重跑已完成 Part）
          - phase4 完成 → 直接 emit FINAL 并返回
        confirm_mode 行为不变。
        """
        logger.info(f'[WritingService] run() 开始执行, work_id={self.work_id}, resume={self.resume}, confirm_mode={self.cfg.confirm_mode}')
        _writing_state[self.work_id]['running'] = True
        saved_phase = str(self.data.get('phase', 'init') or 'init')
        skip_to_part = 0
        phase_already_4 = False
        if self.resume and saved_phase.startswith('phase3_part'):
            try:
                skip_to_part = int(saved_phase.replace('phase3_part', ''))
            except (TypeError, ValueError):
                skip_to_part = 0
        elif self.resume and saved_phase == 'phase4':
            phase_already_4 = True
        if phase_already_4:
            logger.info(f'[WritingService] resume 命中 phase4，直接 emit FINAL')
            await self.emitter.emit(EventType.FINAL, {'work_id': self.work_id, 'total_parts': self.cfg.part_count, 'resumed': True}, work_id=self.work_id)
            self.progress_callback(100, '创作流程已完成（resume 命中 phase4）')
            _writing_state[self.work_id]['running'] = False
            return
        try:
            if skip_to_part > 0:
                resume_ratio = skip_to_part / max(self.cfg.part_count, 1)
                start_progress = int(55 + resume_ratio * 25)
                self.progress_callback(start_progress, f'恢复创作，已完成 {skip_to_part} 个 Part')
            else:
                self.progress_callback(0, '开始创作流程')
            if skip_to_part == 0:
                logger.info('[WritingService] 进入Phase 1: 灵感解析')
                self.progress_callback(5, '开始灵感解析')
                await self._phase1_planning()
                logger.info('[WritingService] Phase 1完成')
                self.progress_callback(25, '灵感解析完成')
                if self.cfg.confirm_mode:
                    await self._request_confirm('phase1_complete', '✨ 灵感解析已完成！\n\n已生成以下内容：\n- 主角设定\n- 题材分类\n- 世界观基础\n\n是否继续进行情节规划？')
                logger.info('[WritingService] 进入Phase 2: 情节规划')
                self.progress_callback(30, '开始情节规划')
                await self._phase2_outline()
                logger.info('[WritingService] Phase 2完成')
                self.progress_callback(50, '情节规划完成')
                if self.cfg.confirm_mode:
                    outline_count = len(self.data.get('part_outline', []))
                    await self._request_confirm('phase2_complete', f'📋 情节规划已完成！\n\n已生成 {outline_count} 个Part的创作蓝图\n\n是否开始逐Part创作？')
            else:
                logger.info(f'[WritingService] resume 跳过 Phase1+2，直接进 Phase3（从 Part {skip_to_part + 1} 开始）')
            logger.info(f'[WritingService] 进入Phase 3: 逐Part创作 (start_from={skip_to_part + 1})')
            await self._phase3_writing(start_from=skip_to_part + 1)
            logger.info('[WritingService] Phase 3完成')
            self.progress_callback(80, '逐Part创作完成')
            if self.cfg.confirm_mode:
                total_words = sum((len(t) for t in self.data.get('parts', {}).values()))
                await self._request_confirm('phase3_complete', f'📝 所有Part创作已完成！\n\n总字数约: {total_words:,} 字\nPart数量: {self.cfg.part_count} 个\n\n是否继续进行风格优化？')
            logger.info('[WritingService] 进入Phase 4: 风格优化')
            self.progress_callback(85, '开始风格优化')
            await self._phase4_optimize()
            logger.info('[WritingService] Phase 4完成')
            self.progress_callback(95, '风格优化完成')
            await self.emitter.emit(EventType.FINAL, {'work_id': self.work_id, 'total_parts': self.cfg.part_count}, work_id=self.work_id)
            logger.info('[WritingService] 创作流程全部完成')
            self.progress_callback(100, '创作流程全部完成')
        except Exception as e:
            logger.info(f'[WritingService] 创作流程出错: {e}')
            import traceback
            traceback.print_exc()
            error_info = error_handler.handle_error(e)
            recovery_suggestion = error_handler.get_recovery_suggestion(e)
            await self.emitter.emit(EventType.ERROR, {'message': error_info['message'], 'error_type': error_info['error_type'], 'details': error_info['details'], 'recovery_suggestion': recovery_suggestion, 'work_id': self.work_id}, work_id=self.work_id)
            self.progress_callback(100, f"创作流程出错: {error_info['message']}")
        finally:
            _writing_state[self.work_id]['running'] = False
            logger.info('[WritingService] run() 结束')

    async def _check_pause(self):
        """检查暂停信号

        R19-P1-17: 用 flag.wait() 替代 sleep(0.5) 轮询 —— 事件唤醒零延迟，不浪费 CPU。
        """
        flag = _pause_flags.get(self.work_id)
        if flag and flag.is_set():
            flag.clear()
            _writing_state[self.work_id]['paused'] = True
            await self.emitter.emit(EventType.LOG, {'message': '⏸ 已暂停，等待恢复...', 'work_id': self.work_id}, work_id=self.work_id)
            while _pause_flags.get(self.work_id) is flag:
                try:
                    await asyncio.wait_for(flag.wait(), timeout=None)
                except asyncio.TimeoutError:
                    continue
                if not flag.is_set():
                    continue
                break
            _writing_state[self.work_id]['paused'] = False
            await self.emitter.emit(EventType.LOG, {'message': '▶ 继续创作...', 'work_id': self.work_id}, work_id=self.work_id)

    async def _request_confirm(self, confirm_id: str, message: str):
        """
        请求用户确认（仅在confirm_mode=True时调用）
        
        Args:
            confirm_id: 确认标识符
            message: 显示给用户的确认消息
            
        Returns:
            str: 用户的选择 ("proceed" 或 "cancel")
            
        Raises:
            Exception: 如果用户选择取消
        """
        logger.info(f'[WritingService] 请求用户确认: {confirm_id}')
        _confirm_flags[self.work_id] = {'event': asyncio.Event(), 'result': ''}
        await self.emitter.emit(EventType.CONFIRM, {'confirm_id': confirm_id, 'message': message, 'work_id': self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {'message': f'⏳ 等待用户确认: {confirm_id}', 'work_id': self.work_id}, work_id=self.work_id)
        try:
            await asyncio.wait_for(_confirm_flags[self.work_id]['event'].wait(), timeout=600)
        except asyncio.TimeoutError:
            logger.info(f'[WritingService] 确认超时，自动继续: {confirm_id}')
            _confirm_flags[self.work_id]['result'] = 'proceed'
        result = _confirm_flags[self.work_id]['result']
        logger.info(f'[WritingService] 用户确认结果: {confirm_id} = {result}')
        if result == 'cancel':
            await self.emitter.emit(EventType.LOG, {'message': f'❌ 用户取消了操作: {confirm_id}', 'work_id': self.work_id}, work_id=self.work_id)
            raise Exception(f'用户取消了操作: {confirm_id}')
        else:
            await self.emitter.emit(EventType.LOG, {'message': f'✅ 用户确认继续: {confirm_id}', 'work_id': self.work_id}, work_id=self.work_id)

    @staticmethod
    def handle_confirm_response(work_id: str, choice: str):
        """
        处理用户的确认响应（由API调用）
        
        Args:
            work_id: 作品ID
            choice: 用户选择 ("proceed" 或 "cancel")
        """
        logger.info(f'[WritingService] 收到用户确认响应: work_id={work_id}, choice={choice}')
        if work_id in _confirm_flags:
            _confirm_flags[work_id]['result'] = choice
            _confirm_flags[work_id]['event'].set()

    async def _phase1_planning(self):
        """Phase 1: 灵感解析 - 调用InspirationAgent和GenreAgent"""
        logger.info('[WritingService] _phase1_planning() 开始')
        await self.emitter.emit(EventType.PHASE, {'phase': 'phase1', 'name': '灵感解析', 'work_id': self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {'message': '开始灵感解析...', 'work_id': self.work_id}, work_id=self.work_id)
        inspiration = self.data.get('inspiration', '')
        logger.info(f'[WritingService] 灵感: {inspiration}')
        from core.agents.inspiration_agent import InspirationAgent
        from core.agents.genre_agent import GenreAgent
        try:
            memory_content = get_all_memory()
            logger.info(f'[WritingService] 记忆内容长度: {len(memory_content)} 字符')
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'inspiration_agent', 'status': 'start', 'message': '解析灵感要素...', 'work_id': self.work_id}, work_id=self.work_id)
            logger.info('[WritingService] 准备调用InspirationAgent')
            inspiration_agent = InspirationAgent()
            core_elements = await asyncio.to_thread(inspiration_agent.execute, type('State', (), {'inspiration': inspiration, 'memory': memory_content})())
            logger.info(f"[WritingService] InspirationAgent返回: {core_elements.get('protagonist', {}).get('identity', '未知')}")
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'inspiration_agent', 'status': 'end', 'message': f"主角: {core_elements.get('protagonist', {}).get('identity', '未知')}", 'work_id': self.work_id}, work_id=self.work_id)
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'genre_agent', 'status': 'start', 'message': '判断题材分类...', 'work_id': self.work_id}, work_id=self.work_id)
            logger.info('[WritingService] 准备调用GenreAgent')
            genre_agent = GenreAgent()
            genre_result = await asyncio.to_thread(genre_agent.execute, type('State', (), {'core_elements': core_elements, 'memory': memory_content})())
            logger.info(f"[WritingService] GenreAgent返回: {genre_result.get('genre_primary', '')}")
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'genre_agent', 'status': 'end', 'message': f"题材: {genre_result.get('genre_primary', '')} > {genre_result.get('genre_secondary', '')}", 'work_id': self.work_id}, work_id=self.work_id)
            self.data['core_elements'] = core_elements
            self.data['genre'] = genre_result
            self.data['market_positioning'] = genre_result
            self.data['phase'] = 'phase1'
            self._save()
            await self.emitter.emit(EventType.LOG, {'message': '灵感解析完成', 'work_id': self.work_id}, work_id=self.work_id)
            logger.info('[WritingService] _phase1_planning() 完成')
        except Exception as e:
            logger.info(f'[WritingService] _phase1_planning() 出错: {e}')
            import traceback
            traceback.print_exc()
            await self.emitter.emit(EventType.ERROR, {'message': f'Phase1错误: {str(e)}', 'work_id': self.work_id}, work_id=self.work_id)
            self.data['phase'] = 'phase1'
            self._save()

    async def _phase2_outline(self):
        """Phase 2: 情节规划 - 调用PlotPlannerAgent"""
        await self.emitter.emit(EventType.PHASE, {'phase': 'phase2', 'name': '情节规划', 'work_id': self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {'message': '开始情节规划...', 'work_id': self.work_id}, work_id=self.work_id)
        from core.agents.plot_planner_agent import PlotPlannerAgent
        try:
            memory_content = get_all_memory()
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'plot_planner_agent', 'status': 'start', 'message': '生成Part制创作蓝图...', 'work_id': self.work_id}, work_id=self.work_id)
            plot_agent = PlotPlannerAgent()

            class TempState:

                def __init__(self, data, memory):
                    self.inspiration = data.get('inspiration', '')
                    self.core_elements = data.get('core_elements', {})
                    self.market_positioning = data.get('market_positioning', {})
                    self.memory = memory
            temp_state = TempState(self.data, memory_content)
            plot_result = await asyncio.to_thread(plot_agent.execute, temp_state)
            self.data['world_setting'] = plot_result.get('world_setting', '')
            self.data['characters'] = plot_result.get('characters', [])
            self.data['part_outline'] = plot_result.get('part_outline', [])
            self.data['foreshadowing'] = plot_result.get('foreshadowing', [])
            outline_count = len(plot_result.get('part_outline', []))
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'plot_planner_agent', 'status': 'end', 'message': f'生成{outline_count}个Part的蓝图', 'work_id': self.work_id}, work_id=self.work_id)
            self.data['phase'] = 'phase2'
            self._save()
            await self.emitter.emit(EventType.LOG, {'message': '情节规划完成', 'work_id': self.work_id}, work_id=self.work_id)
        except Exception as e:
            await self.emitter.emit(EventType.ERROR, {'message': f'Phase2错误: {str(e)}', 'work_id': self.work_id}, work_id=self.work_id)
            self.data['phase'] = 'phase2'
            self.data['part_outline'] = []
            self._save()

    async def _phase3_writing(self, start_from: int=1):
        """Phase 3: 逐Part创作 - 调用PartWriterAgent

        R3-P0-3: 新增 start_from 参数，resume 时从已完成 Part 的下一个开始
        （如 phase3_part40 → start_from=41），避免重跑已完成 Part。
        """
        await self.emitter.emit(EventType.PHASE, {'phase': 'phase3', 'name': '章节创作', 'work_id': self.work_id}, work_id=self.work_id)
        self.data.setdefault('parts', {})
        self.data.setdefault('part_summaries', {})
        from core.agents.part_writer_agent import PartWriterAgent
        memory_content = get_all_memory()
        total = self.cfg.part_count
        part_outline = self.data.get('part_outline', [])
        temp_state = TempStoryState(self.data, memory_content, vector_store=self.vector_store)
        writer_agent = PartWriterAgent()
        writer_agent.set_progress_callback(self.progress_callback)

        def _on_chunk_complete(part_num: int, chunk_idx: int, accumulated_text: str) -> None:
            try:
                self.data['parts'][str(part_num)] = accumulated_text
                summary = accumulated_text[:200] + '...' if len(accumulated_text) > 200 else accumulated_text
                self.data['part_summaries'][str(part_num)] = summary
                self._save()
                try:
                    from core.progress_manager import progress_manager as _pm
                    from api.sse import EventType as _Evt
                    _pm.emitter.emit_sync(_Evt.LOG, {'level': 'info', 'message': f'💾 Part {part_num} chunk {chunk_idx} checkpoint 已保存 ({len(accumulated_text)}字)', 'event_type': 'checkpoint_saved', 'part': part_num, 'chunk': chunk_idx, 'words': len(accumulated_text), 'work_id': self.work_id}, work_id=self.work_id)
                except Exception:
                    pass
            except Exception as cp_err:
                logger.info(f'[WritingService] chunk checkpoint 失败（不影响主流程）: {cp_err}')
        writer_agent.set_checkpoint_callback(_on_chunk_complete)
        for i in range(start_from, total + 1):
            await self._check_pause()
            _writing_state[self.work_id]['current_part'] = i
            done_ratio = (i - start_from) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio * 25
            self.progress_callback(int(part_progress), f'开始创作 Part {i}/{total}')
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'part_writer', 'part': i, 'status': 'start', 'message': f'开始创作 Part {i}/{total}', 'work_id': self.work_id}, work_id=self.work_id)
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
                    self.data['parts'][str(i)] = part_text
                    summary = part_text[:200] + '...' if len(part_text) > 200 else part_text
                    self.data['part_summaries'][str(i)] = summary
                    try:
                        temp_state.window.add_part(i, part_text, summary)
                        temp_state.window.update_foreshadowing(self.data.get('foreshadowing', []) or [])
                        temp_state.window.update_character_state(self.data.get('character_state_track', {}) or {})
                    except Exception as win_err:
                        logger.info(f'[WritingService] SlidingWindow.add_part 失败（不影响主流程）: {win_err}')
                    try:
                        if self.vector_store is not None and self.vector_store.enabled:
                            self.vector_store.add(i, part_text)
                    except Exception as vs_err:
                        logger.info(f'[WritingService] vector_store.add 失败（不影响主流程）: {vs_err}')
                    if temp_state.window.should_create_rolling_summary(i):
                        try:
                            from core.llm_client import call_llm
                            recent_keys = sorted([p for p in temp_state.window.summaries.keys() if p < i])[-temp_state.window.ROLLING_EVERY:]
                            recent_text = '\n'.join((f'Part {p}: {temp_state.window.summaries[p]}' for p in recent_keys if p in temp_state.window.summaries))
                            rolling = call_llm(system_prompt='你是长篇小说剧情压缩助手。将下面若干个 Part 的剧情概要压缩为一段 800 字以内的连贯剧情段，保留关键人物、冲突、伏笔、角色位置/状态/伤势变化，输出纯叙事文本，不要分点。', user_prompt=recent_text or '(无最近摘要)', temperature=0.3, max_tokens=1200, agent='rolling_summary')
                            rolling_text = (rolling or '')[:800]
                            if not rolling_text.strip():
                                fallback = '\n'.join((f'Part {p}: {temp_state.window.summaries[p][:100]}' for p in recent_keys if p in temp_state.window.summaries))
                                rolling_text = fallback[:800]
                            temp_state.window.add_rolling_summary(i, rolling_text)
                            await self.emitter.emit(EventType.LOG, {'message': f'📚 Part {i} 二级滚动摘要已生成（{len(rolling_text)} 字）', 'work_id': self.work_id}, work_id=self.work_id)
                        except Exception as roll_err:
                            logger.info(f'[WritingService] 二级滚动摘要生成失败（不影响主流程）: {roll_err}')
                    if temp_state.window.should_create_milestone(i):
                        try:
                            from core.llm_client import call_llm
                            recent_keys = sorted([p for p in temp_state.window.summaries.keys() if p < i])[-temp_state.window.MILESTONE_EVERY:]
                            recent_text = '\n'.join((f'Part {p}: {temp_state.window.summaries[p]}' for p in recent_keys if p in temp_state.window.summaries))
                            char_state_lines = []
                            try:
                                for name, st in (temp_state.window.character_state or {}).items():
                                    char_state_lines.append(f'- {name}: {st}')
                            except Exception:
                                pass
                            foreshadow_lines = []
                            try:
                                for f_item in temp_state.window.foreshadowing or []:
                                    foreshadow_lines.append(f"- {f_item.get('id', '')}: {f_item.get('content', '')}")
                            except Exception:
                                pass
                            milestone_input = (recent_text or '(无最近摘要)') + ('\n【世界观】' + (temp_state.world_setting or '') if getattr(temp_state, 'world_setting', '') else '') + ('\n【角色状态】\n' + '\n'.join(char_state_lines) if char_state_lines else '') + ('\n【伏笔】\n' + '\n'.join(foreshadow_lines) if foreshadow_lines else '')
                            milestone = call_llm(system_prompt='你是长篇小说剧情压缩助手。将下面 20 个 Part 的剧情概要压缩为 2000 字以内的全局脉络段，涵盖主线、支线、关键转折、角色弧光，输出纯叙事文本，不要分点。', user_prompt=milestone_input, temperature=0.3, max_tokens=2500, agent='milestone_summary')
                            milestone_text = (milestone or '')[:2000]
                            if not milestone_text.strip():
                                milestone_text = '\n'.join((f'Part {p}: {temp_state.window.summaries[p][:100]}' for p in recent_keys if p in temp_state.window.summaries))[:2000]
                            milestone_num = i // temp_state.window.MILESTONE_EVERY
                            temp_state.window.add_milestone(milestone_num, milestone_text)
                            await self.emitter.emit(EventType.LOG, {'message': f'🏔️ 里程碑 #{milestone_num} 摘要已生成（{len(milestone_text)} 字）', 'work_id': self.work_id}, work_id=self.work_id)
                        except Exception as m_err:
                            logger.info(f'[WritingService] 三级里程碑摘要生成失败（不影响主流程）: {m_err}')
                    word_count = len(part_text)
                    break
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    logger.info(f'[WritingService] Part {i} 第 {attempt + 1}/{max_retries + 1} 次尝试异常: {type(e).__name__}: {e}')
                    logger.info(f'[WritingService] Traceback: {tb}')
                    if attempt < max_retries:
                        await self.emitter.emit(EventType.LOG, {'message': f'⚠️ Part {i} 第 {attempt + 1} 次失败，{3 * (attempt + 1)}s 后重试...', 'work_id': self.work_id}, work_id=self.work_id)
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    if self.cfg.confirm_mode:
                        await self.emitter.emit(EventType.LOG, {'message': f'❌ Part {i} 已重试 {max_retries} 次仍失败，等待用户决策', 'work_id': self.work_id}, work_id=self.work_id)
                        try:
                            await self._request_confirm(f'part_{i}_failed', f'⚠️ Part {i}/{total} 创作失败（已重试 {max_retries} 次）\n\n错误：{str(e)[:200]}\n\n选择「继续」将标记此 Part 为失败并跳过，「取消」将中断整个流程')
                            skip_part = True
                        except Exception as confirm_err:
                            logger.info(f'[WritingService] 用户在 Part {i} 失败时选择取消: {confirm_err}')
                            raise
                    else:
                        skip_part = True
                    if skip_part:
                        await self.emitter.emit(EventType.ERROR, {'message': f'Part{i}创作失败（已跳过）: {str(e)[:200]}', 'work_id': self.work_id}, work_id=self.work_id)
                        self.data['parts'][str(i)] = f'[Part {i} 创作失败]'
                        failed_parts = list(self.data.get('failed_parts', []) or [])
                        if i not in failed_parts:
                            failed_parts.append(i)
                            self.data['failed_parts'] = failed_parts
                        word_count = 0
                        break
            self.data['phase'] = f'phase3_part{i}'
            self._save()
            done_ratio_after = (i - start_from + 1) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio_after * 25
            self.progress_callback(int(part_progress), f'Part {i} 创作完成 ({word_count}字)')
            await self.emitter.emit(EventType.PART_COMPLETE, {'part': i, 'words': word_count, 'work_id': self.work_id}, work_id=self.work_id)
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'part_writer', 'part': i, 'status': 'end', 'message': f'Part {i} 创作完成 ({word_count}字)', 'work_id': self.work_id}, work_id=self.work_id)
            if self.cfg.confirm_mode and (i % 5 == 0 or i == total):
                await self._request_confirm(f'part_{i}_complete', f'📄 Part {i}/{total} 创作完成！\n\n本Part字数: {word_count:,} 字\n累计进度: {i}/{total} Part\n\n是否继续创作下一个Part？')
            if self.cfg.confirm_mode and (i % 10 == 0 or i == total):
                try:
                    from core.cost_tracker import should_prompt_for_cost
                    if should_prompt_for_cost(work_id=self.work_id):
                        from core.cost_tracker import get_tracker
                        summary = get_tracker(work_id=self.work_id).get_summary()
                        await self._request_confirm(f'cost_limit_{i}', f"💰 已花费约 ¥{summary['estimated_cost_rmb']:.2f}（{summary['total_calls']} 次调用）\n\n是否继续创作？")
                except Exception as cost_err:
                    logger.info(f'[WritingService] 成本熔断检查失败（不影响主流程）: {cost_err}')

    async def _phase4_optimize(self):
        """Phase 4: 风格优化 + 评审

        R2 改造：对每个 Part 串行调用三个 Review Agent（Logic / Emotion / Consistency），
        收集 per-Part 打分，再聚合成顶层 summary + parts 数组。

        新 data 契约：
            {
                "logic":        {avg_score, pass, total_issues, top_issue},
                "emotion":      {avg_score, pass, avg_resonance, ...},
                "consistency":  {avg_score, pass, total_issues, ...},
                "parts": [
                    {"part": N, "logic_score": ..., "emotion_score": ..., "consistency_score": ...,
                     "p0_issues": [...], "p1_issues": [...], "summary": "..."},
                    ...
                ]
            }
        """
        await self.emitter.emit(EventType.PHASE, {'phase': 'phase4', 'name': '风格优化', 'work_id': self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {'message': '开始风格优化...', 'work_id': self.work_id}, work_id=self.work_id)
        from core.agents.style_optimizer_agent import StyleOptimizerAgent
        from core.agents.logic_review_agent import LogicReviewAgent
        from core.agents.emotion_review_agent import EmotionReviewAgent
        from core.agents.consistency_review_agent import ConsistencyReviewAgent
        try:
            state_mock = self._build_review_state_mock()
            part_nums = []
            for k, v in (self.data.get('parts', {}) or {}).items():
                if isinstance(v, str) and v.strip():
                    try:
                        part_nums.append(int(k))
                    except (TypeError, ValueError):
                        continue
            part_nums = sorted(set(part_nums))
            await self.emitter.emit(EventType.LOG, {'message': f'评审阶段：共 {len(part_nums)} 个 Part 待审查', 'work_id': self.work_id}, work_id=self.work_id)
            logic_agent = LogicReviewAgent()
            emotion_agent = EmotionReviewAgent()
            consistency_agent = ConsistencyReviewAgent()
            per_part_results: list = []
            for idx, part_num in enumerate(part_nums, start=1):
                part_key = str(part_num)
                part_text = self.data['parts'][part_key]
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'start', 'message': f'审查 Part {part_num} 逻辑...', 'work_id': self.work_id}, work_id=self.work_id)
                try:
                    logic_result = await asyncio.to_thread(logic_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[WritingService] LogicReview Part {part_num} 失败: {e}')
                    logic_result = self._review_failure('logic', part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'logic_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 逻辑审查完成', 'work_id': self.work_id}, work_id=self.work_id)
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'start', 'message': f'评估 Part {part_num} 情感...', 'work_id': self.work_id}, work_id=self.work_id)
                try:
                    emotion_result = await asyncio.to_thread(emotion_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[WritingService] EmotionReview Part {part_num} 失败: {e}')
                    emotion_result = self._review_failure('emotion', part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'emotion_review_agent', 'part': part_num, 'status': 'end', 'message': f"Part {part_num} 情感评估: {emotion_result.get('emotion_score', 'N/A')}", 'work_id': self.work_id}, work_id=self.work_id)
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'start', 'message': f'检查 Part {part_num} 一致性...', 'work_id': self.work_id}, work_id=self.work_id)
                try:
                    consistency_result = await asyncio.to_thread(consistency_agent.execute, state_mock, part_num, part_text)
                except Exception as e:
                    logger.info(f'[WritingService] ConsistencyReview Part {part_num} 失败: {e}')
                    consistency_result = self._review_failure('consistency', part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'consistency_review_agent', 'part': part_num, 'status': 'end', 'message': f'Part {part_num} 一致性检查完成', 'work_id': self.work_id}, work_id=self.work_id)
                per_part_results.append({'part': part_num, 'logic_result': logic_result if isinstance(logic_result, dict) else {}, 'emotion_result': emotion_result if isinstance(emotion_result, dict) else {}, 'consistency_result': consistency_result if isinstance(consistency_result, dict) else {}})
                if part_nums:
                    part_progress = 85 + idx / len(part_nums) * 10
                    self.progress_callback(int(part_progress), f'Part {part_num} 评审完成 ({idx}/{len(part_nums)})')
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'start', 'message': '执行风格优化...', 'work_id': self.work_id}, work_id=self.work_id)
            style_agent = StyleOptimizerAgent()
            try:
                style_result = await asyncio.to_thread(style_agent.execute, type('State', (), {'parts': self.data.get('parts', {})})())
            except Exception as e:
                logger.info(f'[WritingService] StyleOptimizer 失败（不影响主流程）: {e}')
                style_result = {}
            self.data['final_draft'] = self.data.get('parts', {})
            self.data['review_report'] = self._aggregate_review_results(per_part_results)
            self.data['phase'] = 'phase4'
            total_words = sum((len(t) for t in self.data['final_draft'].values()))
            await self.emitter.emit(EventType.AGENT_CALL, {'agent': 'style_optimizer_agent', 'status': 'end', 'message': f'风格优化完成 (总字数: {total_words})', 'work_id': self.work_id}, work_id=self.work_id)
            self._save()
            await self.emitter.emit(EventType.LOG, {'message': '风格优化完成', 'work_id': self.work_id}, work_id=self.work_id)
        except Exception as e:
            import traceback
            logger.info(f'[WritingService] _phase4_optimize 出错: {e}')
            traceback.print_exc()
            await self.emitter.emit(EventType.ERROR, {'message': f'Phase4错误: {str(e)}', 'work_id': self.work_id}, work_id=self.work_id)
            self.data['final_draft'] = self.data.get('parts', {})
            self.data['phase'] = 'phase4'
            self._save()

    def _build_review_state_mock(self):
        """构造一个轻量级 state mock 给 Review Agent 使用。

        R2：三个 Review Agent 都依赖 state.part_outline / part_summaries /
        characters / world_setting / foreshadowing / parts / final_draft。
        """
        from types import SimpleNamespace
        return SimpleNamespace(inspiration=self.data.get('inspiration', ''), core_elements=self.data.get('core_elements', {}), market_positioning=self.data.get('market_positioning', {}), world_setting=self.data.get('world_setting', ''), characters=self.data.get('characters', []), part_outline=self.data.get('part_outline', []), foreshadowing=self.data.get('foreshadowing', []), parts=dict(self.data.get('parts', {}) or {}), part_summaries=dict(self.data.get('part_summaries', {}) or {}), current_plot_state=self.data.get('current_plot_state', ''), character_state_track=self.data.get('character_state_track', {}), memory=None, final_draft=dict(self.data.get('final_draft', {}) or self.data.get('parts', {}) or {}))

    @staticmethod
    def _review_failure(kind: str, part_num: int, err: Exception) -> dict:
        """Review Agent 失败时的降级返回（保持 schema 一致，前端不会拿到空 dict）。"""
        if kind == 'logic':
            return {'pass': False, 'overall_score': 3, 'issues': [{'level': 'P0', 'dimension': '自动审查', 'location': f'Part {part_num}', 'description': f'逻辑审查Agent执行失败: {err}', 'suggestion': '需人工核查'}], 'continuity_check': {'character_states': '未检查', 'timeline': '未检查', 'established_facts': '未检查'}, 'strengths': [], 'verdict': f'审查失败（降级评分）: {err}'}
        if kind == 'emotion':
            return {'pass': False, 'emotion_score': 3, 'resonance_score': 3, 'immersion_score': 3, 'emotion_target_met': False, 'emotion_curve': {'start': '未知', 'middle': '未知', 'end': '未知'}, 'highlights': [], 'weaknesses': [f'情感评估Agent执行失败: {err}'], 'enhancement_suggestions': [], 'verdict': f'评估失败（降级评分）: {err}'}
        if kind == 'consistency':
            return {'pass': False, 'overall_score': 3, 'issues': [{'level': 'P0', 'dimension': '自动审查', 'character': '全局', 'location': f'Part {part_num}', 'description': f'一致性检查Agent执行失败: {err}', 'suggestion': '需人工核查'}], 'character_states': {}, 'verdict': f'检查失败（降级评分）: {err}'}
        return {}

    @staticmethod
    def _aggregate_review_results(per_part_results: list) -> dict:
        """R18-P1-8: 委托给 services.review_aggregator.aggregate_review_results（纯函数独立可测）。"""
        from services.review_aggregator import aggregate_review_results as _agg
        return _agg(per_part_results)

    async def rewrite_part(self, part_num: int):
        """重写指定Part - 调用PartWriterAgent"""
        await self.emitter.emit(EventType.LOG, {'message': f'开始重写 Part {part_num}...', 'work_id': self.work_id}, work_id=self.work_id)
        from core.agents.part_writer_agent import PartWriterAgent
        try:
            memory_content = get_all_memory()
            temp_state = TempStoryState(self.data, memory_content)
            writer_agent = PartWriterAgent()
            part_result = await asyncio.to_thread(writer_agent.execute, temp_state, part_num)
            if isinstance(part_result, dict) and part_result.get('success'):
                part_text = part_result.get('content', '')
            else:
                part_text = part_result if isinstance(part_result, str) else ''
            self.data['parts'][str(part_num)] = part_text
            summary = part_text[:200] + '...' if len(part_text) > 200 else part_text
            self.data['part_summaries'][str(part_num)] = summary
            self._save()
            try:
                temp_state.window.add_part(part_num, part_text, summary)
            except Exception as win_err:
                logger.info(f'[WritingService] rewrite_part 同步窗口失败（不影响主流程）: {win_err}')
            word_count = len(part_text)
            await self.emitter.emit(EventType.PART_COMPLETE, {'part': part_num, 'words': word_count, 'work_id': self.work_id}, work_id=self.work_id)
            await self.emitter.emit(EventType.LOG, {'message': f'Part {part_num} 重写完成 ({word_count}字)', 'work_id': self.work_id}, work_id=self.work_id)
        except Exception as e:
            await self.emitter.emit(EventType.ERROR, {'message': f'Part {part_num} 重写失败: {str(e)}', 'work_id': self.work_id}, work_id=self.work_id)
            self._save()
            await self.emitter.emit(EventType.PART_COMPLETE, {'part': part_num, 'words': 0, 'work_id': self.work_id}, work_id=self.work_id)

    def _save(self):
        """保存作品数据（R5-P0-3: cost_tracker 当前 summary 含 calls 列表写回 data）

        R17-P0-3: 高频写盘优化 —— 每次 _save 仍同步阻塞（向后兼容），
        但调用方可选用 _save_async() 在后台线程池中写盘，不阻塞事件循环。
        """
        try:
            from core.cost_tracker import get_tracker
            try:
                get_tracker(work_id=self.work_id).force_flush()
            except Exception:
                pass
            self.data['cost_summary'] = get_tracker(work_id=self.work_id).get_summary()
        except Exception:
            pass
        try:
            self.work_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.info(f'[WritingService] _save 失败: {e}')

    async def _save_async(self):
        """R17-P0-3: 异步版 _save —— 高频 checkpoint 路径使用，不阻塞 asyncio event loop。

        - 通过 asyncio.to_thread() 把 json.dumps + write_text 放到默认 executor
        - 调用方 await _save_async() 即可（写入完成后才返回）
        """
        try:
            from core.cost_tracker import get_tracker
            try:
                get_tracker().force_flush()
            except Exception:
                pass
            self.data['cost_summary'] = get_tracker().get_summary()
        except Exception:
            pass
        path = self.work_path
        data_snapshot = json.dumps(self.data, ensure_ascii=False, indent=2)
        try:
            await asyncio.to_thread(path.write_text, data_snapshot, encoding='utf-8')
        except Exception as e:
            logger.info(f'[WritingService] _save_async 失败: {e}')

    def _save_initial_state(self):
        """R5-P0-1: 仅在 restart 路径下使用 —— 不经过 cost_tracker 的简易落盘。"""
        try:
            self.work_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.info(f'[WritingService] restart 重置落盘失败（不影响主流程）: {e}')