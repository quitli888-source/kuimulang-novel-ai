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

    def __init__(self, data, memory=None):
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

            return self.window.build(
                part_num,
                characters=self.characters,
                world_setting=self.world_setting,
                extra_context_provider=_legacy_extras,
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

    def __init__(self, work_id: str, emitter: SSEEmitter, resume: bool = False):
        self.work_id = work_id
        self.emitter = emitter
        self.resume = resume
        self.work_path = get_work_file(work_id)
        self.cfg = get_app_config()

        # 加载作品数据
        self.data = json.loads(self.work_path.read_text(encoding="utf-8"))

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

    async def run(self):
        """执行完整创作流程"""
        print(f"[WritingService] run() 开始执行, work_id={self.work_id}, confirm_mode={self.cfg.confirm_mode}")
        _writing_state[self.work_id]["running"] = True

        try:
            # 初始化进度
            self.progress_callback(0, "开始创作流程")

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

            # Phase 3: 逐Part创作
            print("[WritingService] 进入Phase 3: 逐Part创作")
            self.progress_callback(55, "开始逐Part创作")
            await self._phase3_writing()
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
        """检查暂停信号"""
        flag = _pause_flags.get(self.work_id)
        if flag and flag.is_set():
            flag.clear()
            _writing_state[self.work_id]["paused"] = True
            await self.emitter.emit(EventType.LOG, {"message": "⏸ 已暂停，等待恢复...", "work_id": self.work_id}, work_id=self.work_id)
            # 等待resume信号
            while _pause_flags.get(self.work_id) and not _pause_flags[self.work_id].is_set():
                await asyncio.sleep(0.5)
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

    async def _phase3_writing(self):
        """Phase 3: 逐Part创作 - 调用PartWriterAgent"""
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
        temp_state = TempStoryState(self.data, memory_content)
        writer_agent = PartWriterAgent()

        # 设置进度回调
        writer_agent.set_progress_callback(self.progress_callback)

        for i in range(1, total + 1):
            await self._check_pause()
            _writing_state[self.work_id]["current_part"] = i

            # 计算当前进度
            part_progress = 55 + ((i - 1) / total) * 25
            self.progress_callback(int(part_progress), f"开始创作 Part {i}/{total}")

            await self.emitter.emit(EventType.AGENT_CALL, {
                "agent": "part_writer",
                "part": i,
                "status": "start",
                "message": f"开始创作 Part {i}/{total}",
                "work_id": self.work_id,
            }, work_id=self.work_id)

            try:
                # 使用asyncio.to_thread运行同步Agent调用
                part_result = await asyncio.to_thread(writer_agent.execute, temp_state, i)

                # 处理返回结果
                if isinstance(part_result, dict) and part_result.get("success"):
                    part_text = part_result.get("content", "")
                else:
                    part_text = part_result

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

                word_count = len(part_text)

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[WritingService] Part {i} 异常: {type(e).__name__}: {e}")
                print(f"[WritingService] Traceback: {tb}")
                await self.emitter.emit(EventType.ERROR, {"message": f"Part{i}创作失败: {str(e)}", "work_id": self.work_id}, work_id=self.work_id)
                self.data["parts"][str(i)] = f"[Part {i} 创作失败]"
                word_count = 0

            self.data["phase"] = f"phase3_part{i}"
            self._save()

            # 更新进度
            part_progress = 55 + (i / total) * 25
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
        """把 per-Part 评审结果聚合成新契约结构。

        输入：[{"part": N, "logic_result": {...}, "emotion_result": {...}, "consistency_result": {...}}, ...]
        输出：{
            "logic":       {avg_score, pass, total_issues, top_issue, parts_count},
            "emotion":     {avg_score, pass, avg_resonance, avg_immersion, parts_count},
            "consistency": {avg_score, pass, total_issues, top_issue, parts_count},
            "parts": [
                {"part": N, "logic_score": ..., "emotion_score": ..., "consistency_score": ...,
                 "p0_issues": [...], "p1_issues": [...], "summary": "..."},
                ...
            ]
        }
        """
        def _f(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _issues_by_level(issues: list, level: str) -> list:
            if not isinstance(issues, list):
                return []
            return [i for i in issues if isinstance(i, dict) and i.get("level") == level]

        logic_scores: list = []
        emotion_scores: list = []
        consistency_scores: list = []
        resonance_scores: list = []
        immersion_scores: list = []
        logic_pass: list = []
        emotion_pass: list = []
        consistency_pass: list = []
        logic_p0: list = []
        logic_p1: list = []
        consistency_p0: list = []
        consistency_p1: list = []

        parts_out: list = []

        for entry in per_part_results:
            part_num = entry.get("part")
            lr = entry.get("logic_result") or {}
            er = entry.get("emotion_result") or {}
            cr = entry.get("consistency_result") or {}

            l_score = _f(lr.get("overall_score"), 0.0)
            e_score = _f(er.get("emotion_score"), 0.0)
            c_score = _f(cr.get("overall_score"), 0.0)

            logic_scores.append(l_score)
            emotion_scores.append(e_score)
            consistency_scores.append(c_score)
            resonance_scores.append(_f(er.get("resonance_score"), 0.0))
            immersion_scores.append(_f(er.get("immersion_score"), 0.0))

            logic_pass.append(bool(lr.get("pass", l_score >= 6)))
            emotion_pass.append(bool(er.get("pass", e_score >= 6)))
            consistency_pass.append(bool(cr.get("pass", c_score >= 6)))

            l_p0 = _issues_by_level(lr.get("issues", []), "P0")
            l_p1 = _issues_by_level(lr.get("issues", []), "P1")
            c_p0 = _issues_by_level(cr.get("issues", []), "P0")
            c_p1 = _issues_by_level(cr.get("issues", []), "P1")
            # emotion agent 不分 P0/P1，但保留 weaknesses 作为可观察信息
            e_p1 = er.get("weaknesses", []) if isinstance(er.get("weaknesses"), list) else []
            e_p0 = er.get("enhancement_suggestions", []) if isinstance(er.get("enhancement_suggestions"), list) else []

            logic_p0.extend(l_p0)
            logic_p1.extend(l_p1)
            consistency_p0.extend(c_p0)
            consistency_p1.extend(c_p1)

            parts_out.append({
                "part": part_num,
                "logic_score": l_score,
                "emotion_score": e_score,
                "consistency_score": c_score,
                "p0_issues": list(l_p0) + list(c_p0) + list(e_p0),
                "p1_issues": list(l_p1) + list(c_p1) + list(e_p1),
                "summary": (
                    f"逻辑{l_score}/10 "
                    f"情感{e_score}/10 "
                    f"一致{c_score}/10"
                ),
            })

        def _avg(xs: list) -> float:
            return round(sum(xs) / len(xs), 2) if xs else 0.0

        def _first_issue(issues: list) -> str:
            if not issues:
                return ""
            i0 = issues[0]
            return (i0.get("description") or i0.get("suggestion") or "") if isinstance(i0, dict) else str(i0)

        return {
            "logic": {
                "avg_score": _avg(logic_scores),
                "pass": all(logic_pass) if logic_pass else False,
                "total_issues": len(logic_p0) + len(logic_p1),
                "p0_count": len(logic_p0),
                "p1_count": len(logic_p1),
                "top_issue": _first_issue(logic_p0) or _first_issue(logic_p1),
                "parts_count": len(per_part_results),
            },
            "emotion": {
                "avg_score": _avg(emotion_scores),
                "pass": all(emotion_pass) if emotion_pass else False,
                "avg_resonance": _avg(resonance_scores),
                "avg_immersion": _avg(immersion_scores),
                "parts_count": len(per_part_results),
            },
            "consistency": {
                "avg_score": _avg(consistency_scores),
                "pass": all(consistency_pass) if consistency_pass else False,
                "total_issues": len(consistency_p0) + len(consistency_p1),
                "p0_count": len(consistency_p0),
                "p1_count": len(consistency_p1),
                "top_issue": _first_issue(consistency_p0) or _first_issue(consistency_p1),
                "parts_count": len(per_part_results),
            },
            "parts": parts_out,
        }

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
        """保存作品数据"""
        self.work_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
