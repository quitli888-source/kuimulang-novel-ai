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


class TempStoryState:
    """临时故事状态类，用于构建 PartWriterAgent 所需的上下文"""

    def __init__(self, data, memory=None, vector_store=None):
        self.inspiration = data.get("inspiration", "")
        self.core_elements = data.get("core_elements", {})
        self.market_positioning = data.get("market_positioning", {})
        self.world_setting = data.get("world_setting", "")
        self.characters = data.get("characters", [])
        self.part_outline = data.get("part_outline", [])
        self.foreshadowing = data.get("foreshadowing", [])
        self.parts = data.get("parts", {})
        self.part_summaries = data.get("part_summaries", {})
        self.current_plot_state = data.get("current_plot_state", "")
        self.character_state_track = data.get("character_state_track", {})
        self.memory = memory
        # V6.1: 真正的滑动窗口（最近 3 个 Part 原文 + 远端三级摘要）
        self.window = SlidingWindow(window_size=3)
        # R7-P0-4: 可选向量检索；writing_service 在构造时注入
        self.vector_store = vector_store

    def get_part_context(self, part_num):
        """获取指定部分的上下文信息（V6.1：委托给 SlidingWindow，失败回退旧实现）"""
        try:
            # 同步 data 中已存在的 Part 到窗口
            self._sync_window(part_num)
            self.window.update_foreshadowing(self.foreshadowing or [])
            self.window.update_character_state(self.character_state_track or {})

            def _legacy_extras(pn: int) -> str:
                sections = []
                if self.current_plot_state:
                    sections.append(f"【当前剧情进度】{self.current_plot_state}")
                    sections.append("")
                return "\n".join(sections)

            # R7-P0-4: 构造向量检索 query（用当前 Part 的 core_event + emotion_target）
            vector_query = None
            if self.vector_store is not None and self.vector_store.enabled:
                outline = (self.part_outline or [])[part_num - 1] if part_num - 1 < len(self.part_outline or []) else None
                if isinstance(outline, dict):
                    core_event = outline.get("core_event", "")
                    emotion_target = outline.get("emotion_target", "")
                    vector_query = " ".join(
                        s for s in (core_event, emotion_target) if s
                    ) or None

            return self.window.build(
                part_num,
                characters=self.characters,
                world_setting=self.world_setting,
                extra_context_provider=_legacy_extras,
                vector_store=self.vector_store,
                vector_query=vector_query,
            )
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
            summary = self.part_summaries.get(p_key) or (
                text[:200] + ("..." if len(text) > 200 else "")
            )
            self.window.add_part(p_int, text, summary)

        # 远端 Part 的一级摘要补齐
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

        # 角色档案
        if self.characters:
            parts.append("【角色档案】")
            for c in self.characters:
                parts.append(
                    f"- {c.get('name', '未知')}({c.get('role', '')}): "
                    f"{c.get('identity', '')}, 特质:{c.get('core_trait', '')}"
                )
            parts.append("")

        # 世界观
        if self.world_setting:
            parts.append(f"【世界观】{self.world_setting}")
            parts.append("")

        # 伏笔表
        if self.foreshadowing:
            parts.append("【伏笔追踪】")
            for f_item in self.foreshadowing:
                parts.append(
                    f"- {f_item.get('id', '')}: {f_item.get('content', '')} "
                    f"(埋于Part{f_item.get('plant_part', '?')}, 揭于Part{f_item.get('reveal_part', '?')})"
                )
            parts.append("")

        # 已完成Part摘要 - 使用字符串key
        completed_parts = sorted([k for k in self.part_summaries.keys()], key=lambda x: int(x))
        if completed_parts:
            parts.append("【已完成剧情摘要】")
            for p_num in completed_parts:
                parts.append(f"Part {p_num}: {self.part_summaries[p_num]}")
            parts.append("")

        # 上一Part结尾 - 使用字符串key
        prev_part = part_num - 1
        prev_key = str(prev_part)
        if prev_key in self.parts:
            prev_text = self.parts[prev_key]
            tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text
            parts.append(f"【上一部分（Part {prev_part}）结尾】")
            parts.append(tail)

        return "\n".join(parts)


# ---- 全局写作状态（跨请求共享） ----
_writing_state: dict = {}
_pause_flags: dict = {}  # work_id -> asyncio.Event
_confirm_flags: dict = {}  # work_id -> {"event": asyncio.Event, "result": str}


class WritingState:
    def __init__(self, work_id: str):
        self.work_id = work_id
        self.phase = "idle"
        self.current_part = 0
        self.total_parts = 0
        self.running = False
        self.paused = False


class WritingService:
    """创作服务 - 协调所有Agent完成创作流程"""

    def __init__(self, work_id: str, emitter: SSEEmitter, resume: bool = False, restart: bool = False):
        self.work_id = work_id
        self.emitter = emitter
        self.resume = resume
        self.restart = restart
        self.work_path = get_work_file(work_id)
        self.cfg = get_app_config()

        # 加载作品数据
        self.data = json.loads(self.work_path.read_text(encoding="utf-8"))

        # R5-P0-1: restart=True 时把 phase 重置为 init，并清空 parts/part_summaries
        # （用户从断点恢复弹窗选"重新开始"时走这条路径）
        if self.restart:
            print(f"[WritingService] restart=True，重置 phase / parts / part_summaries")
            self.data["phase"] = "init"
            self.data["parts"] = {}
            self.data["part_summaries"] = {}
            # 保留 inspiration / title 等元数据
            self.data.pop("failed_parts", None)
            self.data.pop("final_draft", None)
            self.data.pop("review_report", None)
            self.data.pop("character_state_track", None)
            # 立即落盘，避免 Phase1 失败时还残留 phase3_part{N}
            self._save_initial_state()

        # 全局状态
        _writing_state[work_id] = {
            "phase": "idle",
            "current_part": 0,
            "total_parts": self.cfg.part_count,
            "running": False,
            "paused": False,
        }
        _pause_flags[work_id] = asyncio.Event()
        _confirm_flags[work_id] = {"event": asyncio.Event(), "result": ""}

        # 进度管理
        self.progress_callback = progress_manager.get_progress_callback(work_id, "WritingService")
        progress_manager.reset_progress(work_id)

        # R5-P0-3: 把 cost_tracker 绑定到当前 work + 还原持久化 history（双轨合并）
        try:
            from core.cost_tracker import get_tracker
            tracker = get_tracker()
            # 注意：若 writing_service 在 restart 路径下已 _save_initial_state 清空过 data，
            # 此时 data["cost_summary"] 已被新初始值覆盖（_save_initial_state 不写 cost_summary），
            # 但 attach_work 的去重逻辑保证安全。
            tracker.attach_work(
                self.work_id,
                self.data.get("cost_summary"),
                self.work_path.parent,
            )
        except Exception as attach_err:
            print(f"[WritingService] cost_tracker.attach_work 失败（不影响主流程）: {attach_err}")

        # R7-P0-4: 向量检索实例（默认 enabled=False；ENABLE_VECTOR_RAG=1 才启用）
        self.vector_store = None
        try:
            from core.vector_store import VectorStore
            self.vector_store = VectorStore(
                persist_dir=self.work_path.parent / "vectors",
                embedding_provider="none",
            )
            # 用 data.parts 重建索引（resume 场景）
            if self.vector_store.enabled:
                for p_key, p_text in (self.data.get("parts", {}) or {}).items():
                    try:
                        self.vector_store.add(int(p_key), p_text)
                    except Exception:
                        continue
        except Exception as vs_err:
            print(f"[WritingService] VectorStore 初始化失败（不影响主流程）: {vs_err}")
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
        print(f"[WritingService] run() 开始执行, work_id={self.work_id}, resume={self.resume}, confirm_mode={self.cfg.confirm_mode}")
        _writing_state[self.work_id]["running"] = True

        # R3-P0-3: resume 阶段识别
        saved_phase = str(self.data.get("phase", "init") or "init")
        skip_to_part = 0  # 0 表示需要跑 Phase1+2
        phase_already_4 = False
        if self.resume and saved_phase.startswith("phase3_part"):
            try:
                skip_to_part = int(saved_phase.replace("phase3_part", ""))
            except (TypeError, ValueError):
                skip_to_part = 0
        elif self.resume and saved_phase == "phase4":
            phase_already_4 = True

        # Phase 4 已完成（resume 命中）→ 直接 emit FINAL
        if phase_already_4:
            print(f"[WritingService] resume 命中 phase4，直接 emit FINAL")
            await self.emitter.emit(EventType.FINAL, {
                "work_id": self.work_id,
                "total_parts": self.cfg.part_count,
                "resumed": True,
            }, work_id=self.work_id)
            self.progress_callback(100, "创作流程已完成（resume 命中 phase4）")
            _writing_state[self.work_id]["running"] = False
            return

        try:
            # 初始化进度（按当前 phase 计算起始进度，避免从 0% 跳到 100%）
            if skip_to_part > 0:
                # 已在 phase3_part{N}，进度从 55% 起跳（与 _phase3_writing 内部一致）
                resume_ratio = skip_to_part / max(self.cfg.part_count, 1)
                start_progress = int(55 + resume_ratio * 25)
                self.progress_callback(start_progress, f"恢复创作，已完成 {skip_to_part} 个 Part")
            else:
                self.progress_callback(0, "开始创作流程")

            # Phase 1 & 2：仅在 skip_to_part == 0 时执行
            if skip_to_part == 0:
                # Phase 1: 灵感解析 → 世界观/角色/类型
                print("[WritingService] 进入Phase 1: 灵感解析")
                self.progress_callback(5, "开始灵感解析")
                await self._phase1_planning()
                print("[WritingService] Phase 1完成")
                self.progress_callback(25, "灵感解析完成")

                # Phase 1完成后，如果是手动确认模式，请求用户确认
                if self.cfg.confirm_mode:
                    await self._request_confirm(
                        "phase1_complete",
                        "✨ 灵感解析已完成！\n\n已生成以下内容：\n- 主角设定\n- 题材分类\n- 世界观基础\n\n是否继续进行情节规划？"
                    )

                # Phase 2: Part规划
                print("[WritingService] 进入Phase 2: 情节规划")
                self.progress_callback(30, "开始情节规划")
                await self._phase2_outline()
                print("[WritingService] Phase 2完成")
                self.progress_callback(50, "情节规划完成")

                # Phase 2完成后，如果是手动确认模式，请求用户确认
                if self.cfg.confirm_mode:
                    outline_count = len(self.data.get("part_outline", []))
                    await self._request_confirm(
                        "phase2_complete",
                        f"📋 情节规划已完成！\n\n已生成 {outline_count} 个Part的创作蓝图\n\n是否开始逐Part创作？"
                    )
            else:
                print(f"[WritingService] resume 跳过 Phase1+2，直接进 Phase3（从 Part {skip_to_part + 1} 开始）")

            # Phase 3: 逐Part创作（start_from 控制跳过已完成 Part）
            print(f"[WritingService] 进入Phase 3: 逐Part创作 (start_from={skip_to_part + 1})")
            await self._phase3_writing(start_from=skip_to_part + 1)
            print("[WritingService] Phase 3完成")
            self.progress_callback(80, "逐Part创作完成")

            # Phase 3完成后，如果是手动确认模式，请求用户确认
            if self.cfg.confirm_mode:
                total_words = sum(len(t) for t in self.data.get("parts", {}).values())
                await self._request_confirm(
                    "phase3_complete",
                    f"📝 所有Part创作已完成！\n\n总字数约: {total_words:,} 字\nPart数量: {self.cfg.part_count} 个\n\n是否继续进行风格优化？"
                )

            # Phase 4: 优化输出
            print("[WritingService] 进入Phase 4: 风格优化")
            self.progress_callback(85, "开始风格优化")
            await self._phase4_optimize()
            print("[WritingService] Phase 4完成")
            self.progress_callback(95, "风格优化完成")

            await self.emitter.emit(EventType.FINAL, {
                "work_id": self.work_id,
                "total_parts": self.cfg.part_count,
            }, work_id=self.work_id)
            print("[WritingService] 创作流程全部完成")
            self.progress_callback(100, "创作流程全部完成")

        except Exception as e:
            print(f"[WritingService] 创作流程出错: {e}")
            import traceback
            traceback.print_exc()
            
            # 使用错误处理系统处理错误
            error_info = error_handler.handle_error(e)
            recovery_suggestion = error_handler.get_recovery_suggestion(e)
            
            await self.emitter.emit(EventType.ERROR, {
                "message": error_info["message"],
                "error_type": error_info["error_type"],
                "details": error_info["details"],
                "recovery_suggestion": recovery_suggestion,
                "work_id": self.work_id,
            }, work_id=self.work_id)
            self.progress_callback(100, f"创作流程出错: {error_info['message']}")
        finally:
            _writing_state[self.work_id]["running"] = False
            print("[WritingService] run() 结束")

    async def _check_pause(self):
        """检查暂停信号

        R19-P1-17: 用 flag.wait() 替代 sleep(0.5) 轮询 —— 事件唤醒零延迟，不浪费 CPU。
        """
        flag = _pause_flags.get(self.work_id)
        if flag and flag.is_set():
            flag.clear()
            _writing_state[self.work_id]["paused"] = True
            await self.emitter.emit(EventType.LOG, {"message": "⏸ 已暂停，等待恢复...", "work_id": self.work_id}, work_id=self.work_id)
            # R19-P1-17: asyncio.Event.wait() 替代轮询 sleep —— resume 时 flag.set() 立即唤醒
            while _pause_flags.get(self.work_id) is flag:
                try:
                    await asyncio.wait_for(flag.wait(), timeout=None)
                except asyncio.TimeoutError:
                    continue
                if not flag.is_set():
                    # 防止 spurious wakeup：再次确认 flag 仍为 set 状态
                    continue
                break
            _writing_state[self.work_id]["paused"] = False
            await self.emitter.emit(EventType.LOG, {"message": "▶ 继续创作...", "work_id": self.work_id}, work_id=self.work_id)

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
        print(f"[WritingService] 请求用户确认: {confirm_id}")
        
        # 重置确认标志
        _confirm_flags[self.work_id] = {"event": asyncio.Event(), "result": ""}
        
        # 发送确认事件到前端
        await self.emitter.emit(EventType.CONFIRM, {
            "confirm_id": confirm_id,
            "message": message,
            "work_id": self.work_id,
        }, work_id=self.work_id)

        await self.emitter.emit(EventType.LOG, {"message": f"⏳ 等待用户确认: {confirm_id}", "work_id": self.work_id}, work_id=self.work_id)
        
        # 等待用户响应（最多等待10分钟）
        try:
            await asyncio.wait_for(_confirm_flags[self.work_id]["event"].wait(), timeout=600)
        except asyncio.TimeoutError:
            print(f"[WritingService] 确认超时，自动继续: {confirm_id}")
            _confirm_flags[self.work_id]["result"] = "proceed"
        
        result = _confirm_flags[self.work_id]["result"]
        print(f"[WritingService] 用户确认结果: {confirm_id} = {result}")
        
        if result == "cancel":
            await self.emitter.emit(EventType.LOG, {"message": f"❌ 用户取消了操作: {confirm_id}", "work_id": self.work_id}, work_id=self.work_id)
            raise Exception(f"用户取消了操作: {confirm_id}")
        else:
            await self.emitter.emit(EventType.LOG, {"message": f"✅ 用户确认继续: {confirm_id}", "work_id": self.work_id}, work_id=self.work_id)
    
    @staticmethod
    def handle_confirm_response(work_id: str, choice: str):
        """
        处理用户的确认响应（由API调用）
        
        Args:
            work_id: 作品ID
            choice: 用户选择 ("proceed" 或 "cancel")
        """
        print(f"[WritingService] 收到用户确认响应: work_id={work_id}, choice={choice}")
        if work_id in _confirm_flags:
            _confirm_flags[work_id]["result"] = choice
            _confirm_flags[work_id]["event"].set()

    async def _phase1_planning(self):
        """Phase 1: 灵感解析 - 调用InspirationAgent和GenreAgent"""
        print("[WritingService] _phase1_planning() 开始")
        await self.emitter.emit(EventType.PHASE, {"phase": "phase1", "name": "灵感解析", "work_id": self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {"message": "开始灵感解析...", "work_id": self.work_id}, work_id=self.work_id)

        inspiration = self.data.get("inspiration", "")
        print(f"[WritingService] 灵感: {inspiration}")
        from core.agents.inspiration_agent import InspirationAgent
        from core.agents.genre_agent import GenreAgent

        try:
            # 获取记忆内容
            memory_content = get_all_memory()
            print(f"[WritingService] 记忆内容长度: {len(memory_content)} 字符")

            # 1.1 调用InspirationAgent解析灵感
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "inspiration_agent",
                "status": "start",
                "message": "解析灵感要素...",
                "work_id": self.work_id,
            }, work_id=self.work_id)
            print("[WritingService] 准备调用InspirationAgent")

            inspiration_agent = InspirationAgent()
            # 使用asyncio.to_thread运行同步Agent调用，避免阻塞事件循环
            core_elements = await asyncio.to_thread(
                inspiration_agent.execute,
                type('State', (), {'inspiration': inspiration, 'memory': memory_content})()
            )
            print(f"[WritingService] InspirationAgent返回: {core_elements.get('protagonist', {}).get('identity', '未知')}")

            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "inspiration_agent",
                "status": "end",
                "message": f"主角: {core_elements.get('protagonist', {}).get('identity', '未知')}",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            # 1.2 调用GenreAgent判断题材
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "genre_agent",
                "status": "start",
                "message": "判断题材分类...",
                "work_id": self.work_id,
            }, work_id=self.work_id)
            print("[WritingService] 准备调用GenreAgent")

            genre_agent = GenreAgent()
            genre_result = await asyncio.to_thread(
                genre_agent.execute,
                type('State', (), {'core_elements': core_elements, 'memory': memory_content})()
            )
            print(f"[WritingService] GenreAgent返回: {genre_result.get('genre_primary', '')}")

            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "genre_agent",
                "status": "end",
                "message": f"题材: {genre_result.get('genre_primary', '')} > {genre_result.get('genre_secondary', '')}",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            # 保存结果到作品数据
            self.data["core_elements"] = core_elements
            self.data["genre"] = genre_result
            self.data["market_positioning"] = genre_result
            self.data["phase"] = "phase1"

            self._save()
            await self.emitter.emit(EventType.LOG, {"message": "灵感解析完成", "work_id": self.work_id}, work_id=self.work_id)
            print("[WritingService] _phase1_planning() 完成")

        except Exception as e:
            print(f"[WritingService] _phase1_planning() 出错: {e}")
            import traceback
            traceback.print_exc()
            await self.emitter.emit(EventType.ERROR, {"message": f"Phase1错误: {str(e)}", "work_id": self.work_id}, work_id=self.work_id)
            # 失败时使用默认值
            self.data["phase"] = "phase1"
            self._save()

    async def _phase2_outline(self):
        """Phase 2: 情节规划 - 调用PlotPlannerAgent"""
        await self.emitter.emit(EventType.PHASE, {"phase": "phase2", "name": "情节规划", "work_id": self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {"message": "开始情节规划...", "work_id": self.work_id}, work_id=self.work_id)

        from core.agents.plot_planner_agent import PlotPlannerAgent

        try:
            # 获取记忆内容
            memory_content = get_all_memory()

            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "plot_planner_agent",
                "status": "start",
                "message": "生成Part制创作蓝图...",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            plot_agent = PlotPlannerAgent()
            # 构建临时state对象供Agent使用
            class TempState:
                def __init__(self, data, memory):
                    self.inspiration = data.get("inspiration", "")
                    self.core_elements = data.get("core_elements", {})
                    self.market_positioning = data.get("market_positioning", {})
                    self.memory = memory

            temp_state = TempState(self.data, memory_content)
            # 使用asyncio.to_thread运行同步Agent调用
            plot_result = await asyncio.to_thread(plot_agent.execute, temp_state)

            # 提取并保存规划结果
            self.data["world_setting"] = plot_result.get("world_setting", "")
            self.data["characters"] = plot_result.get("characters", [])
            self.data["part_outline"] = plot_result.get("part_outline", [])
            self.data["foreshadowing"] = plot_result.get("foreshadowing", [])

            outline_count = len(plot_result.get("part_outline", []))
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "plot_planner_agent",
                "status": "end",
                "message": f"生成{outline_count}个Part的蓝图",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            self.data["phase"] = "phase2"
            self._save()
            await self.emitter.emit(EventType.LOG, {"message": "情节规划完成", "work_id": self.work_id}, work_id=self.work_id)

        except Exception as e:
            await self.emitter.emit(EventType.ERROR, {"message": f"Phase2错误: {str(e)}", "work_id": self.work_id}, work_id=self.work_id)
            self.data["phase"] = "phase2"
            self.data["part_outline"] = []
            self._save()

    async def _phase3_writing(self, start_from: int = 1):
        """Phase 3: 逐Part创作 - 调用PartWriterAgent

        R3-P0-3: 新增 start_from 参数，resume 时从已完成 Part 的下一个开始
        （如 phase3_part40 → start_from=41），避免重跑已完成 Part。
        """
        await self.emitter.emit(EventType.PHASE, {"phase": "phase3", "name": "章节创作", "work_id": self.work_id}, work_id=self.work_id)

        # 初始化 parts 和 part_summaries（如果不存在）
        self.data.setdefault("parts", {})
        self.data.setdefault("part_summaries", {})

        from core.agents.part_writer_agent import PartWriterAgent

        # 获取记忆内容
        memory_content = get_all_memory()

        total = self.cfg.part_count
        part_outline = self.data.get("part_outline", [])

        # 使用模块级 TempStoryState 类构建上下文
        temp_state = TempStoryState(self.data, memory_content, vector_store=self.vector_store)
        writer_agent = PartWriterAgent()

        # 设置进度回调
        writer_agent.set_progress_callback(self.progress_callback)

        # R7-P1-5: chunk 级 checkpoint 回调——每个 chunk 完成后写一次盘
        # （崩溃可恢复；不阻塞主流程，_save 是 best-effort 同步 IO）
        def _on_chunk_complete(part_num: int, chunk_idx: int, accumulated_text: str) -> None:
            try:
                self.data["parts"][str(part_num)] = accumulated_text
                # 同步 part_summaries（供 review agent 用）
                summary = accumulated_text[:200] + "..." if len(accumulated_text) > 200 else accumulated_text
                self.data["part_summaries"][str(part_num)] = summary
                # 落盘（best-effort：失败也不抛）
                self._save()
                # R7-P1-6: 推送 checkpoint_saved 进度事件（前端可显示）
                try:
                    # 这里只能同步发（callback 在非异步上下文）
                    from core.progress_manager import progress_manager as _pm
                    from api.sse import EventType as _Evt
                    _pm.emitter.emit_sync(
                        _Evt.LOG,
                        {
                            "level": "info",
                            "message": f"💾 Part {part_num} chunk {chunk_idx} checkpoint 已保存 ({len(accumulated_text)}字)",
                            "event_type": "checkpoint_saved",
                            "part": part_num,
                            "chunk": chunk_idx,
                            "words": len(accumulated_text),
                            "work_id": self.work_id,
                        },
                        work_id=self.work_id,
                    )
                except Exception:
                    pass
            except Exception as cp_err:
                print(f"[WritingService] chunk checkpoint 失败（不影响主流程）: {cp_err}")

        writer_agent.set_checkpoint_callback(_on_chunk_complete)

        for i in range(start_from, total + 1):
            await self._check_pause()
            _writing_state[self.work_id]["current_part"] = i

            # 计算当前进度（55% → 80%，按已完成 Part 比例推进）
            done_ratio = (i - start_from) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio * 25
            self.progress_callback(int(part_progress), f"开始创作 Part {i}/{total}")

            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "part_writer",
                "part": i,
                "status": "start",
                "message": f"开始创作 Part {i}/{total}",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            # R3-P0-4: Part 失败单 Part 重试循环（2 次重试，3s 间隔），
            # 仍失败 SSE 推 CONFIRM 让用户选"跳过/重试/终止"。
            # 10 分钟无响应默认"跳过"并填占位 + 标记 data["failed_parts"]。
            max_retries = 2
            part_text = ""
            word_count = 0
            skip_part = False

            for attempt in range(max_retries + 1):
                try:
                    # 使用asyncio.to_thread运行同步Agent调用
                    part_result = await asyncio.to_thread(writer_agent.execute, temp_state, i)

                    # 处理返回结果
                    if isinstance(part_result, dict) and part_result.get("success"):
                        part_text = part_result.get("content", "")
                    else:
                        part_text = part_result if isinstance(part_result, str) else ""

                    # 空文本视为失败
                    if not part_text:
                        raise RuntimeError(f"Part {i} 返回为空内容")

                    self.data["parts"][str(i)] = part_text

                    # 生成200字摘要用于后续上下文
                    summary = part_text[:200] + "..." if len(part_text) > 200 else part_text
                    self.data["part_summaries"][str(i)] = summary

                    # V6.1: 把新 Part 写入滑动窗口（下一 Part 自动滚动）
                    try:
                        temp_state.window.add_part(i, part_text, summary)
                        temp_state.window.update_foreshadowing(
                            self.data.get("foreshadowing", []) or []
                        )
                        temp_state.window.update_character_state(
                            self.data.get("character_state_track", {}) or {}
                        )
                    except Exception as win_err:
                        print(f"[WritingService] SlidingWindow.add_part 失败（不影响主流程）: {win_err}")

                    # R7-P0-4: 向量检索索引同步（仅在 enabled 时生效）
                    try:
                        if self.vector_store is not None and self.vector_store.enabled:
                            self.vector_store.add(i, part_text)
                    except Exception as vs_err:
                        print(f"[WritingService] vector_store.add 失败（不影响主流程）: {vs_err}")

                    # R4-P0-2: 二级滚动摘要生成（每 ROLLING_EVERY 个 Part 一次；
                    # R8-P1-4: 5 → 3，500 → 800）
                    if temp_state.window.should_create_rolling_summary(i):
                        try:
                            from core.llm_client import call_llm
                            # 取最近 ROLLING_EVERY 个 Part 的 1 级摘要
                            recent_keys = sorted(
                                [p for p in temp_state.window.summaries.keys() if p < i]
                            )[-temp_state.window.ROLLING_EVERY:]
                            recent_text = "\n".join(
                                f"Part {p}: {temp_state.window.summaries[p]}"
                                for p in recent_keys
                                if p in temp_state.window.summaries
                            )
                            # R8-P1-4: 字数 500 → 800（更详细，便于 Logic Agent 对照）
                            rolling = call_llm(
                                system_prompt=(
                                    "你是长篇小说剧情压缩助手。"
                                    "将下面若干个 Part 的剧情概要压缩为一段 800 字以内的连贯剧情段，"
                                    "保留关键人物、冲突、伏笔、角色位置/状态/伤势变化，"
                                    "输出纯叙事文本，不要分点。"
                                ),
                                user_prompt=recent_text or "(无最近摘要)",
                                temperature=0.3,
                                # R8: max_tokens 同步从 800 → 1200（容纳 800 字中文）
                                max_tokens=1200,
                                agent="rolling_summary",
                            )
                            # R8-P1-4: 截断 500 → 800
                            rolling_text = (rolling or "")[:800]
                            if not rolling_text.strip():
                                # Fallback: 拼接 ROLLING_EVERY 个一级摘要前 100 字
                                fallback = "\n".join(
                                    f"Part {p}: {temp_state.window.summaries[p][:100]}"
                                    for p in recent_keys
                                    if p in temp_state.window.summaries
                                )
                                rolling_text = fallback[:800]
                            temp_state.window.add_rolling_summary(i, rolling_text)
                            await self.emitter.emit(EventType.LOG, {
                                "message": f"📚 Part {i} 二级滚动摘要已生成（{len(rolling_text)} 字）",
                                "work_id": self.work_id,
                            }, work_id=self.work_id)
                        except Exception as roll_err:
                            print(f"[WritingService] 二级滚动摘要生成失败（不影响主流程）: {roll_err}")

                    # R5-P0-2: 三级里程碑摘要生成（每 20 个 Part 一次）——
                    # 把 20 个 Part 的 1 级摘要 + 关键角色状态 + 伏笔 + 世界观 压缩为 2000 字全局脉络段。
                    # 注意：add_milestone 第一参数是 milestone_num（i // 20），不是 part_num。
                    if temp_state.window.should_create_milestone(i):
                        try:
                            from core.llm_client import call_llm
                            recent_keys = sorted(
                                [p for p in temp_state.window.summaries.keys() if p < i]
                            )[-temp_state.window.MILESTONE_EVERY:]
                            recent_text = "\n".join(
                                f"Part {p}: {temp_state.window.summaries[p]}"
                                for p in recent_keys
                                if p in temp_state.window.summaries
                            )
                            char_state_lines = []
                            try:
                                for name, st in (temp_state.window.character_state or {}).items():
                                    char_state_lines.append(f"- {name}: {st}")
                            except Exception:
                                pass
                            foreshadow_lines = []
                            try:
                                for f_item in (temp_state.window.foreshadowing or []):
                                    foreshadow_lines.append(
                                        f"- {f_item.get('id', '')}: {f_item.get('content', '')}"
                                    )
                            except Exception:
                                pass
                            milestone_input = (
                                (recent_text or "(无最近摘要)") +
                                ("\n【世界观】" + (temp_state.world_setting or "") if getattr(temp_state, 'world_setting', '') else "") +
                                ("\n【角色状态】\n" + "\n".join(char_state_lines) if char_state_lines else "") +
                                ("\n【伏笔】\n" + "\n".join(foreshadow_lines) if foreshadow_lines else "")
                            )
                            milestone = call_llm(
                                system_prompt=(
                                    "你是长篇小说剧情压缩助手。"
                                    "将下面 20 个 Part 的剧情概要压缩为 2000 字以内的全局脉络段，"
                                    "涵盖主线、支线、关键转折、角色弧光，输出纯叙事文本，不要分点。"
                                ),
                                user_prompt=milestone_input,
                                temperature=0.3,
                                max_tokens=2500,
                                agent="milestone_summary",
                            )
                            milestone_text = (milestone or "")[:2000]
                            if not milestone_text.strip():
                                # Fallback: 拼接 20 个一级摘要前 100 字
                                milestone_text = "\n".join(
                                    f"Part {p}: {temp_state.window.summaries[p][:100]}"
                                    for p in recent_keys
                                    if p in temp_state.window.summaries
                                )[:2000]
                            # 注意签名差异：add_milestone(milestone_num, ...) 第一参是 milestone_num
                            milestone_num = i // temp_state.window.MILESTONE_EVERY
                            temp_state.window.add_milestone(milestone_num, milestone_text)
                            await self.emitter.emit(EventType.LOG, {
                                "message": f"🏔️ 里程碑 #{milestone_num} 摘要已生成（{len(milestone_text)} 字）",
                                "work_id": self.work_id,
                            }, work_id=self.work_id)
                        except Exception as m_err:
                            print(f"[WritingService] 三级里程碑摘要生成失败（不影响主流程）: {m_err}")

                    word_count = len(part_text)
                    break  # 成功

                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    print(f"[WritingService] Part {i} 第 {attempt + 1}/{max_retries + 1} 次尝试异常: {type(e).__name__}: {e}")
                    print(f"[WritingService] Traceback: {tb}")

                    if attempt < max_retries:
                        # 还有重试机会：间隔 3s 后重试（递增：3s / 6s）
                        await self.emitter.emit(EventType.LOG, {
                            "message": f"⚠️ Part {i} 第 {attempt + 1} 次失败，{3 * (attempt + 1)}s 后重试...",
                            "work_id": self.work_id,
                        }, work_id=self.work_id)
                        await asyncio.sleep(3 * (attempt + 1))
                        continue

                    # 已达最大重试次数：推 CONFIRM 让用户决策（仅手动确认模式）
                    if self.cfg.confirm_mode:
                        await self.emitter.emit(EventType.LOG, {
                            "message": f"❌ Part {i} 已重试 {max_retries} 次仍失败，等待用户决策",
                            "work_id": self.work_id,
                        }, work_id=self.work_id)
                        try:
                            await self._request_confirm(
                                f"part_{i}_failed",
                                f"⚠️ Part {i}/{total} 创作失败（已重试 {max_retries} 次）\n\n错误：{str(e)[:200]}\n\n选择「继续」将标记此 Part 为失败并跳过，「取消」将中断整个流程"
                            )
                            # 用户选择继续 → 跳过该 Part
                            skip_part = True
                        except Exception as confirm_err:
                            # 用户选择取消或确认流程异常 → 中断整个流程
                            print(f"[WritingService] 用户在 Part {i} 失败时选择取消: {confirm_err}")
                            raise
                    else:
                        # 非手动确认模式：自动跳过（保持 R2 行为，向后兼容）
                        skip_part = True

                    if skip_part:
                        # 标记失败 Part + 占位
                        await self.emitter.emit(EventType.ERROR, {
                            "message": f"Part{i}创作失败（已跳过）: {str(e)[:200]}",
                            "work_id": self.work_id,
                        }, work_id=self.work_id)
                        self.data["parts"][str(i)] = f"[Part {i} 创作失败]"
                        # R3-P0-4: 记录失败 Part 列表，供 Report.vue / 后续流程感知
                        failed_parts = list(self.data.get("failed_parts", []) or [])
                        if i not in failed_parts:
                            failed_parts.append(i)
                            self.data["failed_parts"] = failed_parts
                        word_count = 0
                        break

            self.data["phase"] = f"phase3_part{i}"
            self._save()

            # 更新进度
            done_ratio_after = (i - start_from + 1) / max(total - start_from + 1, 1)
            part_progress = 55 + done_ratio_after * 25
            self.progress_callback(int(part_progress), f"Part {i} 创作完成 ({word_count}字)")

            await self.emitter.emit(EventType.PART_COMPLETE, {
                "part": i,
                "words": word_count,
                "work_id": self.work_id,
            }, work_id=self.work_id)
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "part_writer",
                "part": i,
                "status": "end",
                "message": f"Part {i} 创作完成 ({word_count}字)",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            # 每完成一个Part后，如果是手动确认模式且是关键Part（如每5个Part或最后一个），请求确认
            if self.cfg.confirm_mode and (i % 5 == 0 or i == total):
                await self._request_confirm(
                    f"part_{i}_complete",
                    f"📄 Part {i}/{total} 创作完成！\n\n本Part字数: {word_count:,} 字\n累计进度: {i}/{total} Part\n\n是否继续创作下一个Part？"
                )

            # R3-P1-6: 每完成 10 个 Part 检查一次成本熔断，超过阈值时 SSE 推 CONFIRM
            # 仅在手动确认模式下推（自动模式继续跑，由用户事前在配置层决定上限）
            if self.cfg.confirm_mode and (i % 10 == 0 or i == total):
                try:
                    from core.cost_tracker import should_prompt_for_cost
                    if should_prompt_for_cost(work_id=self.work_id):
                        from core.cost_tracker import get_tracker
                        summary = get_tracker().get_summary()
                        await self._request_confirm(
                            f"cost_limit_{i}",
                            f"💰 已花费约 ¥{summary['estimated_cost_rmb']:.2f}（{summary['total_calls']} 次调用）\n\n是否继续创作？"
                        )
                except Exception as cost_err:
                    # 熔断检查失败不影响主流程
                    print(f"[WritingService] 成本熔断检查失败（不影响主流程）: {cost_err}")

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
        await self.emitter.emit(EventType.PHASE, {"phase": "phase4", "name": "风格优化", "work_id": self.work_id}, work_id=self.work_id)
        await self.emitter.emit(EventType.LOG, {"message": "开始风格优化...", "work_id": self.work_id}, work_id=self.work_id)

        from core.agents.style_optimizer_agent import StyleOptimizerAgent
        from core.agents.logic_review_agent import LogicReviewAgent
        from core.agents.emotion_review_agent import EmotionReviewAgent
        from core.agents.consistency_review_agent import ConsistencyReviewAgent

        try:
            # 4.1 准备 state mock（三个 Review Agent 都需要 state.part_outline / part_summaries / characters / world_setting / foreshadowing / parts / final_draft）
            state_mock = self._build_review_state_mock()

            # 4.2 收集已写 Part 编号（字符串 key → int），仅审查有正文的 Part
            part_nums = []
            for k, v in (self.data.get("parts", {}) or {}).items():
                if isinstance(v, str) and v.strip():
                    try:
                        part_nums.append(int(k))
                    except (TypeError, ValueError):
                        continue
            part_nums = sorted(set(part_nums))

            await self.emitter.emit(EventType.LOG, {
                "message": f"评审阶段：共 {len(part_nums)} 个 Part 待审查",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            # 4.3 串行调用三个 Review Agent（每 Part × 3 Agent）
            logic_agent = LogicReviewAgent()
            emotion_agent = EmotionReviewAgent()
            consistency_agent = ConsistencyReviewAgent()

            per_part_results: list = []  # [{part, logic_result, emotion_result, consistency_result}, ...]

            for idx, part_num in enumerate(part_nums, start=1):
                part_key = str(part_num)
                part_text = self.data["parts"][part_key]

                # ---- LogicReview ----
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "logic_review_agent",
                    "part": part_num,
                    "status": "start",
                    "message": f"审查 Part {part_num} 逻辑...",
                    "work_id": self.work_id,
                }, work_id=self.work_id)
                try:
                    logic_result = await asyncio.to_thread(
                        logic_agent.execute, state_mock, part_num, part_text
                    )
                except Exception as e:
                    print(f"[WritingService] LogicReview Part {part_num} 失败: {e}")
                    logic_result = self._review_failure("logic", part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "logic_review_agent",
                    "part": part_num,
                    "status": "end",
                    "message": f"Part {part_num} 逻辑审查完成",
                    "work_id": self.work_id,
                }, work_id=self.work_id)

                # ---- EmotionReview ----
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "emotion_review_agent",
                    "part": part_num,
                    "status": "start",
                    "message": f"评估 Part {part_num} 情感...",
                    "work_id": self.work_id,
                }, work_id=self.work_id)
                try:
                    emotion_result = await asyncio.to_thread(
                        emotion_agent.execute, state_mock, part_num, part_text
                    )
                except Exception as e:
                    print(f"[WritingService] EmotionReview Part {part_num} 失败: {e}")
                    emotion_result = self._review_failure("emotion", part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "emotion_review_agent",
                    "part": part_num,
                    "status": "end",
                    "message": f"Part {part_num} 情感评估: {emotion_result.get('emotion_score', 'N/A')}",
                    "work_id": self.work_id,
                }, work_id=self.work_id)

                # ---- ConsistencyReview ----
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "consistency_review_agent",
                    "part": part_num,
                    "status": "start",
                    "message": f"检查 Part {part_num} 一致性...",
                    "work_id": self.work_id,
                }, work_id=self.work_id)
                try:
                    consistency_result = await asyncio.to_thread(
                        consistency_agent.execute, state_mock, part_num, part_text
                    )
                except Exception as e:
                    print(f"[WritingService] ConsistencyReview Part {part_num} 失败: {e}")
                    consistency_result = self._review_failure("consistency", part_num, e)
                await self.emitter.emit(EventType.AGENT_CALL, {
                    "agent": "consistency_review_agent",
                    "part": part_num,
                    "status": "end",
                    "message": f"Part {part_num} 一致性检查完成",
                    "work_id": self.work_id,
                }, work_id=self.work_id)

                per_part_results.append({
                    "part": part_num,
                    "logic_result": logic_result if isinstance(logic_result, dict) else {},
                    "emotion_result": emotion_result if isinstance(emotion_result, dict) else {},
                    "consistency_result": consistency_result if isinstance(consistency_result, dict) else {},
                })

                # 阶段进度（85% → 95% 区间，按 Part 线性推进）
                if part_nums:
                    part_progress = 85 + (idx / len(part_nums)) * 10
                    self.progress_callback(int(part_progress), f"Part {part_num} 评审完成 ({idx}/{len(part_nums)})")

            # 4.4 风格优化（保留原风格优化 Agent 调用，作为终稿润色）
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "style_optimizer_agent",
                "status": "start",
                "message": "执行风格优化...",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            style_agent = StyleOptimizerAgent()
            try:
                style_result = await asyncio.to_thread(
                    style_agent.execute,
                    type('State', (), {'parts': self.data.get("parts", {})})()
                )
            except Exception as e:
                print(f"[WritingService] StyleOptimizer 失败（不影响主流程）: {e}")
                style_result = {}

            # 4.5 写入终稿
            self.data["final_draft"] = self.data.get("parts", {})

            # 4.6 聚合 review_report（新契约：logic/emotion/consistency 顶层聚合 + parts 数组）
            self.data["review_report"] = self._aggregate_review_results(per_part_results)

            self.data["phase"] = "phase4"

            total_words = sum(len(t) for t in self.data["final_draft"].values())
            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "style_optimizer_agent",
                "status": "end",
                "message": f"风格优化完成 (总字数: {total_words})",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            self._save()
            await self.emitter.emit(EventType.LOG, {"message": "风格优化完成", "work_id": self.work_id}, work_id=self.work_id)

        except Exception as e:
            import traceback
            print(f"[WritingService] _phase4_optimize 出错: {e}")
            traceback.print_exc()
            await self.emitter.emit(EventType.ERROR, {"message": f"Phase4错误: {str(e)}", "work_id": self.work_id}, work_id=self.work_id)
            # 优化失败时使用原始创作作为终稿
            self.data["final_draft"] = self.data.get("parts", {})
            self.data["phase"] = "phase4"
            self._save()

    def _build_review_state_mock(self):
        """构造一个轻量级 state mock 给 Review Agent 使用。

        R2：三个 Review Agent 都依赖 state.part_outline / part_summaries /
        characters / world_setting / foreshadowing / parts / final_draft。
        """
        from types import SimpleNamespace

        return SimpleNamespace(
            inspiration=self.data.get("inspiration", ""),
            core_elements=self.data.get("core_elements", {}),
            market_positioning=self.data.get("market_positioning", {}),
            world_setting=self.data.get("world_setting", ""),
            characters=self.data.get("characters", []),
            part_outline=self.data.get("part_outline", []),
            foreshadowing=self.data.get("foreshadowing", []),
            parts=dict(self.data.get("parts", {}) or {}),
            part_summaries=dict(self.data.get("part_summaries", {}) or {}),
            current_plot_state=self.data.get("current_plot_state", ""),
            character_state_track=self.data.get("character_state_track", {}),
            memory=None,
            final_draft=dict(self.data.get("final_draft", {}) or self.data.get("parts", {}) or {}),
        )

    @staticmethod
    def _review_failure(kind: str, part_num: int, err: Exception) -> dict:
        """Review Agent 失败时的降级返回（保持 schema 一致，前端不会拿到空 dict）。"""
        if kind == "logic":
            return {
                "pass": False, "overall_score": 3,
                "issues": [{"level": "P0", "dimension": "自动审查",
                            "location": f"Part {part_num}",
                            "description": f"逻辑审查Agent执行失败: {err}",
                            "suggestion": "需人工核查"}],
                "continuity_check": {"character_states": "未检查", "timeline": "未检查", "established_facts": "未检查"},
                "strengths": [], "verdict": f"审查失败（降级评分）: {err}",
            }
        if kind == "emotion":
            return {
                "pass": False, "emotion_score": 3, "resonance_score": 3, "immersion_score": 3,
                "emotion_target_met": False,
                "emotion_curve": {"start": "未知", "middle": "未知", "end": "未知"},
                "highlights": [], "weaknesses": [f"情感评估Agent执行失败: {err}"],
                "enhancement_suggestions": [],
                "verdict": f"评估失败（降级评分）: {err}",
            }
        if kind == "consistency":
            return {
                "pass": False, "overall_score": 3,
                "issues": [{"level": "P0", "dimension": "自动审查",
                            "character": "全局",
                            "location": f"Part {part_num}",
                            "description": f"一致性检查Agent执行失败: {err}",
                            "suggestion": "需人工核查"}],
                "character_states": {}, "verdict": f"检查失败（降级评分）: {err}",
            }
        return {}

    @staticmethod
    def _aggregate_review_results(per_part_results: list) -> dict:
        """R18-P1-8: 委托给 services.review_aggregator.aggregate_review_results（纯函数独立可测）。"""
        from services.review_aggregator import aggregate_review_results as _agg
        return _agg(per_part_results)

    async def rewrite_part(self, part_num: int):
        """重写指定Part - 调用PartWriterAgent"""
        await self.emitter.emit(EventType.LOG, {"message": f"开始重写 Part {part_num}...", "work_id": self.work_id}, work_id=self.work_id)

        from core.agents.part_writer_agent import PartWriterAgent

        try:
            # 获取记忆内容
            memory_content = get_all_memory()

            # 使用模块级 TempStoryState 类构建上下文
            temp_state = TempStoryState(self.data, memory_content)
            writer_agent = PartWriterAgent()

            # 使用asyncio.to_thread运行同步Agent调用
            part_result = await asyncio.to_thread(writer_agent.execute, temp_state, part_num)

            # 处理返回结果（part_writer_agent R2 返回 dict 结构）
            if isinstance(part_result, dict) and part_result.get("success"):
                part_text = part_result.get("content", "")
            else:
                part_text = part_result if isinstance(part_result, str) else ""

            self.data["parts"][str(part_num)] = part_text

            # 更新摘要
            summary = part_text[:200] + "..." if len(part_text) > 200 else part_text
            self.data["part_summaries"][str(part_num)] = summary

            self._save()

            # R2: 重写后同步滑动窗口（防止窗口与 self.data["parts"] 不一致）
            try:
                temp_state.window.add_part(part_num, part_text, summary)
            except Exception as win_err:
                print(f"[WritingService] rewrite_part 同步窗口失败（不影响主流程）: {win_err}")

            word_count = len(part_text)
            await self.emitter.emit(EventType.PART_COMPLETE, {"part": part_num, "words": word_count, "work_id": self.work_id}, work_id=self.work_id)
            await self.emitter.emit(EventType.LOG, {"message": f"Part {part_num} 重写完成 ({word_count}字)", "work_id": self.work_id}, work_id=self.work_id)

        except Exception as e:
            await self.emitter.emit(EventType.ERROR, {"message": f"Part {part_num} 重写失败: {str(e)}", "work_id": self.work_id}, work_id=self.work_id)
            self._save()
            await self.emitter.emit(EventType.PART_COMPLETE, {"part": part_num, "words": 0, "work_id": self.work_id}, work_id=self.work_id)

    def _save(self):
        """保存作品数据（R5-P0-3: cost_tracker 当前 summary 含 calls 列表写回 data）

        R17-P0-3: 高频写盘优化 —— 每次 _save 仍同步阻塞（向后兼容），
        但调用方可选用 _save_async() 在后台线程池中写盘，不阻塞事件循环。
        """
        try:
            from core.cost_tracker import get_tracker
            # 强制 flush cost_tracker 落盘，避免 R17 节流策略导致重启后丢数据
            try:
                get_tracker().force_flush()
            except Exception:
                pass
            # 同步当前进程的 cost_tracker 累计（含 calls 列表）到 data，便于 uvicorn 重启后
            # works.py get_work 能 attach_work() 还原历史（双轨持久化合并 source of truth）。
            self.data["cost_summary"] = get_tracker().get_summary()
        except Exception:
            # tracker 不可用时保留已有值
            pass
        try:
            self.work_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[WritingService] _save 失败: {e}")

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
            self.data["cost_summary"] = get_tracker().get_summary()
        except Exception:
            pass
        path = self.work_path
        data_snapshot = json.dumps(self.data, ensure_ascii=False, indent=2)
        # asyncio.to_thread 走默认 ThreadPoolExecutor，不阻塞 event loop
        try:
            await asyncio.to_thread(path.write_text, data_snapshot, encoding="utf-8")
        except Exception as e:
            print(f"[WritingService] _save_async 失败: {e}")

    def _save_initial_state(self):
        """R5-P0-1: 仅在 restart 路径下使用 —— 不经过 cost_tracker 的简易落盘。"""
        try:
            self.work_path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[WritingService] restart 重置落盘失败（不影响主流程）: {e}")
