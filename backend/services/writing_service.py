"""
番茄小说AI创作系统 V5 - 创作服务层
封装V4的核心创作逻辑，支持SSE进度推送和暂停恢复
V5.1改动：集成进度管理器，实现实时创作进度推送
V5.2改动：实现完整的手动确认模式
R27-P1-7: 各阶段实现搬到 services/writing_phase_runners.py
"""
import json
import asyncio
import os
from typing import Optional
from api.sse import SSEEmitter, EventType
from api.works import get_work_file
from core.config import get_app_config
from core.established_facts import EstablishedFacts, DEPARTED_PREDICATES
from core.name_registry import render_name_roster_for_state  # R4-1
from core.progress_manager import progress_manager
from core.error_handler import error_handler
from core.memory_manager import get_all_memory
from core.sliding_window import SlidingWindow
from core.text_utils import truncate
from core.logger import get_logger
logger = get_logger('writing_service')

# R27-P1-7: Phase 拆解 —— 各阶段实现已搬到 services/writing_phase_runners.py
from services.writing_phase_runners import (  # noqa: E402
    Phase1Runner, Phase2Runner, Phase3Runner, Phase4Runner,
)

class TempStoryState:
    """临时故事状态类，用于构建 PartWriterAgent 所需的上下文"""

    def __init__(self, data, memory=None, vector_store=None):
        self.work_id = data.get('id', '') or ''  # P1-87: 用于 per-work cost_tracker 路由
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
        # R4-1: 角色规范名注册表（Phase 2 冻结；修复回路新建 TempStoryState 时
        # 自动拿到 registry，零额外接线）。与 data 共享同一 dict 引用 ——
        # alias_candidates 登记（R4-4）直接反映到 work JSON。
        self.name_registry = data.get('name_registry', {}) or {}
        # R1-D: e2e 路径消费链接通 —— 此前 TempStoryState 没有 established_facts，
        # PartWriterAgent 取事实块时 AttributeError 被吞、恒为空，事实只产不消。
        # 从 work JSON 反序列化，resume 后不归零（配合 Phase3Runner 每 Part 落盘）。
        self.established_facts = EstablishedFacts()
        _raw_ef = data.get('established_facts')
        if isinstance(_raw_ef, dict):
            try:
                self.established_facts.from_dict(_raw_ef)
            except Exception as ef_err:
                logger.info(f'[TempStoryState] established_facts 反序列化失败（不影响主流程）: {ef_err}')
        # R1-G: 剧情状态增量（rolling 触发点生成，叠加层不回写 outline 本体）
        self.story_deltas = data.get('story_deltas', {}) or {}
        self.memory = memory
        self.window = SlidingWindow(window_size=3)
        self.vector_store = vector_store

    def build_established_facts_block(self, current_part: int, categories=None) -> str:
        """R1-D: 与 StoryState.build_established_facts_block 同语义同标题文案。

        PartWriterAgent / LogicReviewAgent 的 hasattr 守卫命中本方法后，
        writer prompt 与 Logic 评审才第一次拿到"前文已确立事实清单"。
        """
        ef = getattr(self, 'established_facts', None)
        if ef is None:
            return ""
        try:
            block = ef.render_for_prompt(
                categories=categories,
                before_part_num=current_part,
            )
        except Exception:
            return ""
        if not block:
            return ""
        return "【前文已确立事实清单——只能对照本表评判一致性】\n" + block

    def get_part_context(self, part_num):
        """获取指定部分的上下文信息（V6.1：委托给 SlidingWindow，失败回退旧实现）"""
        try:
            self._sync_window(part_num)
            self.window.update_foreshadowing(self.foreshadowing or [])
            self.window.update_character_state(self.character_state_track or {})

            def _legacy_extras(pn: int) -> str:
                sections = []
                # R4-1: 角色名册段置首（位置即优先级；渲染独立于 facts，
                # 天然不受 established_facts max_total 截断影响）
                roster = render_name_roster_for_state(self)
                if roster:
                    sections.append(roster)
                    sections.append('')
                if self.current_plot_state:
                    sections.append(f'【当前剧情进度】{self.current_plot_state}')
                    sections.append('')
                # R1-E: 已退场角色严禁出场清单（数据源 character_state_track 中
                # 描述含退场谓词的条目；确定性账本，零 LLM 成本）
                departed_lines = self._departed_character_lines()
                if departed_lines:
                    sections.append('【已退场角色——严禁出场】')
                    sections.append(departed_lines)
                    sections.append('')
                # R1-G: 最近 2 个 Part 的剧情状态增量（与静态大纲冲突时以增量为准）
                delta_lines = self._story_delta_lines()
                if delta_lines:
                    sections.append('【剧情状态增量（大纲生成以来的实际变化；增量与大纲冲突时以增量为准）】')
                    sections.append(delta_lines)
                    sections.append('')
                return '\n'.join(sections)
            vector_query = None
            if self.vector_store is not None and self.vector_store.enabled:
                outline = (self.part_outline or [])[part_num - 1] if part_num - 1 < len(self.part_outline or []) else None
                if isinstance(outline, dict):
                    core_event = outline.get('core_event', '')
                    emotion_target = outline.get('emotion_target', '')
                    vector_query = ' '.join((s for s in (core_event, emotion_target) if s)) or None
            # R1-H: 关键词检索通道专名（object/location/world_rule 类 subject，
            # 近期优先取最多 10 个；供 sliding_window 子串匹配最近提及 Part）
            key_entities = self._key_entities_for_retrieval(part_num)
            return self.window.build(part_num, characters=self.characters, world_setting=self.world_setting, extra_context_provider=_legacy_extras, vector_store=self.vector_store, vector_query=vector_query, key_entities=key_entities)
        except Exception:
            return self._legacy_get_part_context(part_num)

    def _departed_character_lines(self) -> str:
        """R1-E: 从 character_state_track 筛描述含退场谓词的条目，渲染严禁出场段。"""
        track = self.character_state_track or {}
        lines = []
        for name, desc in track.items():
            if not name or not isinstance(desc, str) or not desc:
                continue
            if any(p in desc for p in DEPARTED_PREDICATES):
                lines.append(f'- {name}: {desc}（严禁在本 Part 出场；如以回忆/他人提及形式出现，不得与其现状矛盾）')
        return '\n'.join(lines)

    def _story_delta_lines(self) -> str:
        """R1-G: 最近 2 个 Part 的剧情增量拼接（增量与大纲冲突时以增量为准）。"""
        deltas = self.story_deltas or {}
        if not isinstance(deltas, dict) or not deltas:
            return ''

        def _key(k):
            try:
                return int(k)
            except (TypeError, ValueError):
                return 0

        lines = []
        for k in sorted(deltas.keys(), key=_key)[-2:]:
            d = deltas.get(k)
            if not isinstance(d, dict):
                continue
            seg = []
            if d.get('departed_characters'):
                seg.append(f"退场角色: {d['departed_characters']}")
            if d.get('new_objects'):
                seg.append(f"新物品/线索: {d['new_objects']}")
            if d.get('foreshadow_planted'):
                seg.append(f"新埋伏笔: {d['foreshadow_planted']}")
            if d.get('foreshadow_revealed'):
                seg.append(f"已揭晓伏笔: {d['foreshadow_revealed']}")
            if d.get('outline_adjustments'):
                seg.append(f"大纲修正: {d['outline_adjustments']}")
            if seg:
                lines.append(f'Part {k}: ' + '；'.join(str(x) for x in seg))
        return '\n'.join(lines)

    def _key_entities_for_retrieval(self, part_num: int) -> list:
        """R1-H: 关键词通道专名列表 —— established_facts 中 object/location/
        world_rule 三类当前有效 subject（近期 Part 优先，天然过滤被覆盖旧事实）。"""
        ef = getattr(self, 'established_facts', None)
        if ef is None:
            return []
        try:
            facts = ef.before_part(part_num)
        except Exception:
            return []
        names = []
        seen = set()
        for f in reversed(facts):
            if f.category not in ('object', 'location', 'world_rule'):
                continue
            subject = (f.subject or '').strip()
            if len(subject) >= 2 and subject not in seen:
                seen.add(subject)
                names.append(subject)
        return names

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
        # R4-1: 名册段同样进回退路径（窗口 build 异常时名册不消失；≤6 行）
        roster = render_name_roster_for_state(self)
        if roster:
            parts.append(roster)
            parts.append('')
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

# P0-45: per-work 锁 —— 防止两个 WritingService 实例并发 mutate 同一 work_id 的状态。
# asyncio 是单线程，但跨 BackgroundTasks / 多次 start 仍可能交替 mutate。
import threading
_writing_state_locks: dict = {}  # work_id -> threading.Lock
_writing_state_locks_guard = threading.Lock()


def _get_state_lock(work_id: str) -> threading.Lock:
    """P0-45: 按 work_id 取/建一个 threading.Lock 保护 _writing_state 写入。"""
    with _writing_state_locks_guard:
        lock = _writing_state_locks.get(work_id)
        if lock is None:
            lock = threading.Lock()
            _writing_state_locks[work_id] = lock
        return lock

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
        with _get_state_lock(work_id):
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
        R27-P1-7: 各 phase 实际实现已搬到 services/writing_phase_runners.py，
        本方法仅做编排 + resume 跳过 + confirm_mode 串接。
        """
        logger.info(f'[WritingService] run() 开始执行, work_id={self.work_id}, resume={self.resume}, confirm_mode={self.cfg.confirm_mode}')
        _writing_state[self.work_id]['running'] = True
        saved_phase = str(self.data.get('phase', 'init') or 'init')
        skip_to_part = 0
        phase_already_4 = False
        # R4-P1-x: resume 到 phase1/phase2 时按数据存在性跳过——此前只处理 phase3_part/phase4，
        # docstring 承诺的"phase1 完成→跳过"从未实现，resume 会把 Phase1/2 全量重跑并覆盖已有规划。
        skip_planning = False   # Phase1+2 均已完成
        skip_phase1_only = False  # 仅 Phase1 已完成（core_elements 存在但 outline 为空）
        if self.resume and saved_phase.startswith('phase3_part'):
            try:
                skip_to_part = int(saved_phase.replace('phase3_part', ''))
            except (TypeError, ValueError):
                skip_to_part = 0
        elif self.resume and saved_phase == 'phase4':
            phase_already_4 = True
        elif self.resume and saved_phase in ('phase1', 'phase2'):
            has_core = bool(self.data.get('core_elements'))
            has_outline = bool(self.data.get('part_outline'))
            if has_core and has_outline:
                skip_planning = True
                parts_keys = [int(k) for k in (self.data.get('parts') or {}) if str(k).isdigit()]
                if parts_keys:
                    skip_to_part = max(parts_keys)
                logger.info(f'[WritingService] resume: Phase1+2 均已完成，跳过规划阶段（已有 {skip_to_part} 个 Part）')
            elif has_core:
                skip_phase1_only = True
                logger.info('[WritingService] resume: Phase1 已完成，仅重跑 Phase2 情节规划')
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
            if skip_to_part == 0 and not skip_planning:
                if not skip_phase1_only:
                    logger.info('[WritingService] 进入Phase 1: 灵感解析')
                    self.progress_callback(5, '开始灵感解析')
                    await self._phase1_planning()
                    logger.info('[WritingService] Phase 1完成')
                    self.progress_callback(25, '灵感解析完成')
                    if self.cfg.confirm_mode:
                        await self._request_confirm('phase1_complete', '✨ 灵感解析已完成！\n\n已生成以下内容：\n- 主角设定\n- 题材分类\n- 世界观基础\n\n是否继续进行情节规划？')
                else:
                    logger.info('[WritingService] 跳过Phase 1（已有 core_elements）')
                    self.progress_callback(25, '灵感解析已完成（resume 跳过）')
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
        _confirm_flags[self.work_id] = {'event': asyncio.Event(), 'result': '', 'confirm_id': confirm_id}
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
    def handle_confirm_response(work_id: str, choice: str, confirm_id: Optional[str]=None):
        """
        处理用户的确认响应（由API调用）

        R4-P1-x: confirm_id 校验 —— 过期响应（如 600s 超时自动 proceed 之后用户才点取消）
        不得作用到下一个 confirm 上。confirm_id 不匹配时忽略并记录。
        """
        logger.info(f'[WritingService] 收到用户确认响应: work_id={work_id}, choice={choice}, confirm_id={confirm_id}')
        pending = _confirm_flags.get(work_id)
        if not pending:
            return
        if confirm_id is not None and pending.get('confirm_id') != confirm_id:
            logger.info(f'[WritingService] 忽略错位确认响应: 当前等待 {pending.get("confirm_id")}, 收到 {confirm_id}')
            return
        pending['result'] = choice
        pending['event'].set()

    async def _phase1_planning(self):
        """R27-P1-7: Phase 1 实际实现已搬到 Phase1Runner；保留方法名供旧调用方。"""
        await Phase1Runner(self).run()

    async def _phase2_outline(self):
        """R27-P1-7: Phase 2 实际实现已搬到 Phase2Runner；保留方法名供旧调用方。"""
        await Phase2Runner(self).run()

    async def _phase3_writing(self, start_from: int = 1):
        """R27-P1-7: Phase 3 实际实现已搬到 Phase3Runner；保留方法名供旧调用方（如 test_resume.py）。"""
        await Phase3Runner(self).run(start_from=start_from)

    async def _phase4_optimize(self):
        """R27-P1-7: Phase 4 实际实现已搬到 Phase4Runner；保留方法名供旧调用方。"""
        await Phase4Runner(self).run()
    def _build_review_state_mock(self):
        """构造一个轻量级 state mock 给 Review Agent 使用。

        R2：三个 Review Agent 都依赖 state.part_outline / part_summaries /
        characters / world_setting / foreshadowing / parts / final_draft。
        P1-87: 携带 work_id 让 review agents 把 cost 计入 per-work tracker。
        R1-D: 挂上 build_established_facts_block（从 work JSON 加载已确立事实渲染），
        LogicReviewAgent 的 hasattr 守卫即自动生效，评审首次拿到前文事实基线。
        R4-1: 携带 name_registry（角色规范名注册表），两个评审 agent 的名册段
        数据源；旧 work JSON 无该键时退化为 characters 名列表。
        """
        from types import SimpleNamespace
        mock = SimpleNamespace(work_id=self.work_id, inspiration=self.data.get('inspiration', ''), core_elements=self.data.get('core_elements', {}), market_positioning=self.data.get('market_positioning', {}), world_setting=self.data.get('world_setting', ''), characters=self.data.get('characters', []), part_outline=self.data.get('part_outline', []), foreshadowing=self.data.get('foreshadowing', []), parts=dict(self.data.get('parts', {}) or {}), part_summaries=dict(self.data.get('part_summaries', {}) or {}), current_plot_state=self.data.get('current_plot_state', ''), character_state_track=self.data.get('character_state_track', {}), name_registry=self.data.get('name_registry', {}) or {}, consistency_flags=self.data.get('consistency_flags', []) or [], memory=None, final_draft=dict(self.data.get('final_draft', {}) or self.data.get('parts', {}) or {}))
        review_facts = EstablishedFacts()
        raw_ef = self.data.get('established_facts')
        if isinstance(raw_ef, dict):
            try:
                review_facts.from_dict(raw_ef)
            except Exception as ef_err:
                logger.info(f'[_build_review_state_mock] established_facts 反序列化失败（不影响主流程）: {ef_err}')

        def _build_facts_block(current_part, categories=None):
            try:
                block = review_facts.render_for_prompt(categories=categories, before_part_num=current_part)
            except Exception:
                return ''
            if not block:
                return ''
            return '【前文已确立事实清单——只能对照本表评判一致性】\n' + block

        mock.build_established_facts_block = _build_facts_block
        return mock

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
            # R4-P0-2: 先算 summary 再落盘 —— 此前 truncate 未导入，NameError 发生在
            # parts 写入之后、summary 写入之前，异常路径 _save() 会把"新正文 + 旧摘要"的半份状态落盘。
            summary = truncate(part_text, n=200, suffix="...")
            self.data['parts'][str(part_num)] = part_text
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
        """保存作品数据（R5-P0-3: cost_tracker 当前 summary 写回 data）

        R4-P2-x:
        - 走临时文件 + os.replace 原子替换 —— 此前 write_text 非原子，崩溃/断电会留下
          截断的 work JSON，之后 get_work / WritingService.__init__ 全部失败。
        - cost_summary 剔除 calls 大数组（与 _save_chunk_progress 一致，P0-79 约定）——
          600 calls × ~200B 每 Part 边界全量重写纯属浪费，历史由 .cost.json 承载。
        - tmp 路径带 pid 后缀，避免并发实例共用同一 tmp 文件产生混合落盘。
        """
        try:
            from core.cost_tracker import get_tracker
            try:
                get_tracker(work_id=self.work_id).force_flush()
            except Exception:
                logger.debug('writing_service: silent except (P2-19)', exc_info=True)
            full_summary = get_tracker(work_id=self.work_id).get_summary()
            self.data['cost_summary'] = {k: v for k, v in full_summary.items() if k != 'calls'}
        except Exception:
            logger.debug('writing_service: silent except (P2-19)', exc_info=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        tmp_path = self.work_path.with_suffix(f'.json.tmp.{os.getpid()}')
        try:
            tmp_path.write_text(payload, encoding='utf-8')
            os.replace(tmp_path, self.work_path)
        except Exception as e:
            logger.info(f'[WritingService] _save 失败: {e}')
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                logger.debug('writing_service: silent except (P2-19)', exc_info=True)

    def _save_chunk_progress(self, part_num: int, text: str, summary: str = None) -> None:
        """P1-46: 高频 checkpoint 路径 —— 只写 data['parts'][N] + data['part_summaries'][N]
        与 cost_summary 聚合统计，不带全量 calls 列表（避免 50 万字 + 600 calls 全量重写）。

        P0-79 修复：剔除 cost_summary['calls'] 大数组；calls 全量历史由 CostTracker
        单独落盘到 {work_id}.cost.json（与 work JSON 解耦）。
        写盘走临时文件 + os.replace 原子替换 → 失败时回退 _save()。
        """
        self.data['parts'][str(part_num)] = text
        if summary is not None:
            self.data['part_summaries'][str(part_num)] = summary
        # cost_summary 仅存聚合统计（剔除每条 calls，避免 600 calls × ~200B 拖垮写盘）
        try:
            from core.cost_tracker import get_tracker
            full_summary = get_tracker(work_id=self.work_id).get_summary()
            self.data['cost_summary'] = {k: v for k, v in full_summary.items() if k != 'calls'}
        except Exception:
            logger.debug('writing_service: silent except (P2-19)', exc_info=True)
        # 写到临时文件后原子重命名（避免半写状态）；tmp 带 pid 后缀防并发实例互相覆盖
        tmp_path = self.work_path.with_suffix(f'.json.tmp.{os.getpid()}')
        try:
            tmp_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
            # R1-B: 此前误写 _os.replace（本模块只 import os，无 _os 别名）——
            # 每个 chunk checkpoint 必抛 NameError 回退全量 _save()，轻量路径死亡。
            os.replace(tmp_path, self.work_path)
        except Exception as e:
            logger.info(f'[WritingService] _save_chunk_progress 失败，回退 _save: {e}')
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                logger.debug('writing_service: silent except (P2-19)', exc_info=True)
            self._save()

    def _save_initial_state(self):
        """R5-P0-1: 仅在 restart 路径下使用 —— 不经过 cost_tracker 的简易落盘。"""
        try:
            self.work_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.info(f'[WritingService] restart 重置落盘失败（不影响主流程）: {e}')