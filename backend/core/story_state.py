"""
番茄小说AI创作系统 - 共享状态管理 V4

V4改动：
- 结构化角色状态追踪（character_state_track）
- 利用ConsistencyReviewAgent返回的character_states
- 增强角色状态快照生成

V6.1 改动：
- 引入 SlidingWindow，把"全部摘要 + 末尾1200字"升级为真正的滑动窗口
  （最近 K 个 Part 原文 + 远端 Part 的三级摘要）
"""
import json
from pathlib import Path
from core.config import MEMORY_DIR
from core.sliding_window import SlidingWindow


class StoryState:
    """故事创作状态 - 所有Agent通过此类共享数据"""

    def __init__(self, inspiration: str):
        self.inspiration = inspiration  # 用户原始灵感
        self.phase = "init"  # 当前阶段

        # Phase 1: 灵感解析
        self.core_elements = None  # 核心要素提取
        self.genre = None  # 题材分类
        self.market_positioning = None  # 市场定位

        # Phase 2: 情节规划（V2: Part模式）
        self.world_setting = None  # 世界观设定
        self.characters = None  # 角色档案列表
        self.part_outline = None  # Part规划列表
        self.foreshadowing = []  # 伏笔追踪表

        # Phase 3: 创作（V2: Part模式）
        self.parts = {}  # {part_num: part_text}
        self.part_summaries = {}  # {part_num: "200字摘要"}
        self.current_plot_state = ""  # 全局剧情进度追踪（200字以内）

        # Phase 4: 复核
        self.review_report = None  # 复核报告

        # Phase 5: 优化
        self.final_draft = {}  # {part_num: optimized_text}
        self.title_options = None  # 标题选项
        self.tags = None  # 平台标签

        # V4: 结构化角色状态追踪
        # {char_name: {part_num: "状态描述", ...}}
        self.character_state_track = {}

        # V6.1: 真正的滑动窗口（默认保留最近 3 个 Part 原文）
        self.window = SlidingWindow(window_size=3)

    def save(self, filepath: str = None):
        """保存状态到JSON文件"""
        if filepath is None:
            filepath = str(MEMORY_DIR / "current_story_state.json")

        data = {
            "inspiration": self.inspiration,
            "phase": self.phase,
            "core_elements": self.core_elements,
            "genre": self.genre,
            "market_positioning": self.market_positioning,
            "world_setting": self.world_setting,
            "characters": self.characters,
            "part_outline": self.part_outline,
            "foreshadowing": self.foreshadowing,
            "parts": self.parts,
            "part_summaries": self.part_summaries,
            "current_plot_state": self.current_plot_state,
            "review_report": self.review_report,
            "final_draft": self.final_draft,
            "title_options": self.title_options,
            "tags": self.tags,
            "character_state_track": self.character_state_track,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, filepath: str = None):
        """从JSON文件加载状态"""
        if filepath is None:
            filepath = str(MEMORY_DIR / "current_story_state.json")

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        for key, value in data.items():
            setattr(self, key, value)

    def get_part_context(self, part_num: int) -> str:
        """
        获取当前Part的完整创作上下文。

        V6.1：委托给 SlidingWindow 组装；旧实现的"角色状态快照 +
        关键事实 + 当前剧情进度"通过 extra_context_provider 注入，
        确保行为平滑过渡。窗口失败时回退到旧实现，避免单点失败炸整链。
        """
        # 把当前 parts / summaries 同步给窗口（兼容旧代码可能直接修改 state.parts）
        try:
            self._sync_window(part_num)
            self.window.update_foreshadowing(self.foreshadowing or [])
            self.window.update_character_state(self.character_state_track or {})

            def _legacy_extras(pn: int) -> str:
                """把旧实现的"角色状态快照 + 关键事实 + 当前剧情进度"注入"""
                sections = []
                if self.current_plot_state:
                    sections.append(f"【当前剧情进度】{self.current_plot_state}")
                    sections.append("")
                completed_parts = sorted(
                    [k for k in self.part_summaries.keys() if int(k) < pn]
                )
                if self.characters and completed_parts:
                    char_states = self._build_character_state_snapshot(pn)
                    if char_states:
                        sections.append("【角色状态快照】")
                        sections.append(char_states)
                        sections.append("")
                if completed_parts:
                    key_facts = self._build_key_facts(pn)
                    if key_facts:
                        sections.append("【已确立的关键事实——不可违反】")
                        sections.append(key_facts)
                        sections.append("")
                return "\n".join(sections)

            return self.window.build(
                part_num,
                characters=self.characters,
                world_setting=self.world_setting,
                extra_context_provider=_legacy_extras,
            )
        except Exception:
            # 回退到旧实现（窗口失败时保证主流程不挂）
            return self._legacy_get_part_context(part_num)

    def _sync_window(self, part_num: int) -> None:
        """把 state.parts 中所有 < part_num 的原文灌入 SlidingWindow。"""
        existing_in_window = set(self.window.parts.keys())
        # 只灌入尚未在窗口里的 Part
        for p_num in sorted(self.parts.keys()):
            try:
                p_int = int(p_num)
            except (TypeError, ValueError):
                continue
            if p_int >= part_num:
                continue
            if p_int in existing_in_window:
                continue
            text = self.parts[p_num]
            summary = self.part_summaries.get(p_num, "")
            if not summary and text:
                summary = text[:200] + ("..." if len(text) > 200 else "")
            self.window.add_part(p_int, text, summary)

        # 一级摘要也补齐（窗口淘汰后远端 Part 只剩摘要）
        for p_num, summary in self.part_summaries.items():
            try:
                p_int = int(p_num)
            except (TypeError, ValueError):
                continue
            if p_int < part_num and p_int not in self.window.summaries:
                self.window.summaries[p_int] = summary

    def _legacy_get_part_context(self, part_num: int) -> str:
        """保留的旧实现，作为滑动窗口失败时的回退路径（V3增强版）。"""
        parts = []

        # 1. 角色档案
        if self.characters:
            parts.append("【角色档案】")
            for c in self.characters:
                parts.append(
                    f"- {c.get('name', '未知')}({c.get('role', '')}): "
                    f"{c.get('identity', '')}, 特质:{c.get('core_trait', '')}, "
                    f"动机:{c.get('motivation', '')}, 秘密:{c.get('secret', '')}"
                )
            parts.append("")

        # 2. 世界观
        if self.world_setting:
            parts.append(f"【世界观】{self.world_setting}")
            parts.append("")

        # 3. 伏笔表
        if self.foreshadowing:
            parts.append("【伏笔追踪】")
            for f_item in self.foreshadowing:
                parts.append(
                    f"- {f_item.get('id', '')}: {f_item.get('content', '')} "
                    f"(埋于Part{f_item.get('plant_part', '?')}, 揭于Part{f_item.get('reveal_part', '?')})"
                )
            parts.append("")

        # 4. 全部已完成Part的摘要
        completed_parts = sorted(self.part_summaries.keys())
        if completed_parts:
            parts.append("【已完成剧情摘要】")
            for p_num in completed_parts:
                summary = self.part_summaries[p_num]
                parts.append(f"Part {p_num}: {summary}")
            parts.append("")

        # 5. 上一Part的详细结尾（V3: 800→1200字）
        prev_part = part_num - 1
        if prev_part in self.parts:
            prev_text = self.parts[prev_part]
            tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text
            parts.append(f"【上一部分（Part {prev_part}）结尾——必须自然承接】")
            parts.append(tail)
            parts.append("")

        # 6. 当前剧情进度
        if self.current_plot_state:
            parts.append(f"【当前剧情进度】{self.current_plot_state}")
            parts.append("")

        # 7. 角色状态快照（V3新增）
        if self.characters and completed_parts:
            char_states = self._build_character_state_snapshot(part_num)
            if char_states:
                parts.append("【角色状态快照】")
                parts.append(char_states)
                parts.append("")

        # 8. 关键事实清单（V3新增）
        if completed_parts:
            key_facts = self._build_key_facts(part_num)
            if key_facts:
                parts.append("【已确立的关键事实——不可违反】")
                parts.append(key_facts)
                parts.append("")

        return "\n".join(parts)

    def update_character_states(self, part_num: int, consistency_result: dict):
        """V4新增：利用ConsistencyReviewAgent的结构化输出更新角色状态"""
        char_states = consistency_result.get("character_states", {})
        if not char_states:
            return
        for name, state_desc in char_states.items():
            if not name or not state_desc:
                continue
            if name not in self.character_state_track:
                self.character_state_track[name] = {}
            self.character_state_track[name][str(part_num)] = state_desc

    def _build_character_state_snapshot(self, current_part: int) -> str:
        """V4增强：优先使用结构化角色状态追踪，摘要文本匹配降级为补充"""
        lines = []

        # 第一优先级：结构化角色状态追踪
        if self.character_state_track:
            for char_name, part_states in self.character_state_track.items():
                # 获取该角色最新的状态（小于current_part的最大part_num）
                latest_part = "0"
                for p_str in part_states:
                    try:
                        if int(p_str) < current_part and int(p_str) > int(latest_part):
                            latest_part = p_str
                    except ValueError:
                        continue
                if latest_part in part_states:
                    lines.append(f"  - {char_name}（截至Part{latest_part}）: {part_states[latest_part]}")
        else:
            # 降级：从摘要文本中grep角色名（V3旧逻辑）
            for p_num in sorted(self.final_draft.keys()):
                if p_num < current_part and p_num in self.part_summaries:
                    summary = self.part_summaries[p_num]
                    for c in self.characters:
                        name = c.get('name', '')
                        if name and name in summary:
                            sentences = summary.split('。')
                            for s in sentences:
                                if name in s:
                                    lines.append(f"  - Part{p_num}结束时 {name}: {s.strip()}。")
                                    break

        return "\n".join(lines) if lines else ""

    def _build_key_facts(self, current_part: int) -> str:
        """V3新增：从已完成Part的摘要中提取关键事实"""
        facts = []
        for p_num in sorted(self.part_summaries.keys()):
            if p_num < current_part:
                summary = self.part_summaries[p_num]
                # 提取摘要中的关键事件（通常每句话就是一个事件）
                sentences = [s.strip() for s in summary.split('。') if s.strip()]
                for s in sentences[:3]:  # 每个Part最多取3个关键事件
                    facts.append(f"  - Part{p_num}: {s}")
        return "\n".join(facts) if facts else ""

    def get_characters_state(self) -> str:
        """获取角色当前状态摘要（用于逻辑校验）"""
        if not self.current_plot_state:
            return ""
        return self.current_plot_state

    def get_total_words(self) -> int:
        """获取终稿总字数"""
        return sum(len(t) for t in self.final_draft.values())
