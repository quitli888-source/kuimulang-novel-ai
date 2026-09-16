"""
滑动窗口（Sliding Window）上下文管理器 V1

设计目标：
- 解决 50 万字 / 100 Part 场景下 prompt 单调膨胀、远端 Part 注意力衰减的问题。
- Part 写入时只保留最近 K 个 Part 的原文（约 1.5 万字），远端 Part 用
  摘要 / 滚动摘要 / 里程碑摘要 三级压缩。
- K 个 Part 之外：第 1 级 200 字摘要、第 2 级 500 字滚动摘要（每 5 Part 聚合）、
  第 3 级 2000 字里程碑（每 20 Part 一次）。

数据流：
- PartWriter 写入完成 → writing_service 调用 `add_part(part_num, text, summary)`
- 下一次创作新 Part 时 → `build(part_num, characters=, world_setting=, outline=)`
  返回完整的 prompt 上下文字符串。
"""
from typing import Optional
import os as _os
import json as _json


def _load_user_window_config() -> dict:
    """
    R15: 读取 data/window_config.json 作为用户 UI 配置。
    返回 {"window_size": int, "rolling_every": int, "milestone_every": int}（缺字段则省略）。
    R21-P2-30: 路径常量 WINDOW_CONFIG_FILE 同时被 sliding_window.py 和 config_api.py 共用。
    """
    try:
        if _os.path.exists(WINDOW_CONFIG_FILE):
            with open(WINDOW_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
                out = {}
                for k in ("window_size", "rolling_every", "milestone_every"):
                    if k in data:
                        try:
                            out[k] = int(data[k])
                        except (TypeError, ValueError):
                            pass
                return out
    except Exception:
        pass
    return {}


# R21-P2-30: 单一来源 —— backend/core/sliding_window.py 与 backend/api/config_api.py 共享
# project_root = 3 层 dirname（sliding_window.py 在 backend/core/）
_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
WINDOW_CONFIG_FILE = _os.path.join(_PROJECT_ROOT, "data", "window_config.json")


class SlidingWindow:
    """真正的滑动窗口上下文管理器。"""

    # 默认参数（按 Part 数量聚合）
    # R8-P1-4: ROLLING_EVERY 5 → 3；SUMMARY_L2_LEN 500 → 800。
    # 理由：3 Part 真实 E2E 场景下 R7 看不到二级滚动摘要；Part 3 写完立刻生成
    # "Part 1-2 的 800 字聚合"，Part 4 写作时 Logic Agent 就能对照远端一致性。
    # R15: 改为类默认值 DEFAULT_*，__init__ 可覆盖（支持 UI 手动调整）
    DEFAULT_WINDOW_SIZE = 6       # R12: 3 → 6
    DEFAULT_ROLLING_EVERY = 3    # R8: 5 → 3
    DEFAULT_MILESTONE_EVERY = 20
    SUMMARY_L1_LEN = 200         # 一级摘要目标长度（字）
    SUMMARY_L2_LEN = 800         # 二级滚动摘要目标长度（字）（R8: 500 → 800）
    SUMMARY_L3_LEN = 2000        # 三级里程碑摘要目标长度（字）

    def __init__(
        self,
        window_size: Optional[int] = None,
        rolling_every: Optional[int] = None,
        milestone_every: Optional[int] = None,
    ):
        # R12: window_size 3→6，覆盖更长上下文，减小长程一致性衰减
        # R15: 改为实例属性，支持 UI 手动调整（从前是类常量 self.ROLLING_EVERY）
        # 优先级：显式参数 > data/window_config.json > 环境变量 > 类默认值
        _file_defaults = _load_user_window_config()
        _ws = window_size if window_size is not None else (_file_defaults.get("window_size") or self.DEFAULT_WINDOW_SIZE)
        _re = rolling_every if rolling_every is not None else (_file_defaults.get("rolling_every") or self.DEFAULT_ROLLING_EVERY)
        _me = milestone_every if milestone_every is not None else (_file_defaults.get("milestone_every") or self.DEFAULT_MILESTONE_EVERY)
        self.window_size = _ws
        self.rolling_every = _re
        self.milestone_every = _me
        # 最近 K 个 Part 的原文
        self.parts: dict = {}               # {part_num: text}
        # 一级摘要（200 字）
        self.summaries: dict = {}            # {part_num: "200字摘要"}
        # 二级滚动摘要（500 字，每 5 Part 聚合一次）
        self.rolling_summaries: dict = {}   # {part_num: "500字二级摘要"}
        # 三级里程碑摘要（2000 字，每 20 Part 一次）
        self.milestones: dict = {}           # {milestone_num: "2000字里程碑"}
        # 伏笔表（角色相关状态）
        self.foreshadowing: list = []
        # 角色状态
        self.character_state: dict = {}

    # ----------------- 触发判断 -----------------
    def should_create_rolling_summary(self, part_num: int) -> bool:
        """判断当前 Part 是否需要生成二级滚动摘要（每 rolling_every 个 Part 一次）。"""
        return part_num > 0 and part_num % self.rolling_every == 0

    def should_create_milestone(self, part_num: int) -> bool:
        """判断当前 Part 是否需要生成里程碑摘要（每 milestone_every 个 Part 一次）。"""
        return part_num > 0 and part_num % self.milestone_every == 0

    # ----------------- 写入 / 滚动 -----------------
    def add_part(self, part_num: int, text: str, summary: str) -> None:
        """
        Part 写入后调用。
        - 写入原文到窗口（如果窗口已满则淘汰最早的）
        - 同时记录一级摘要
        - 当到达滚动 / 里程碑阈值时，由调用方负责调用 _aggregate_rolling /
          _aggregate_milestone（这里不主动调用 LLM，避免阻塞 IO）。

        R2 改造：入参校验。text / summary 必须是非空 str，否则抛 ValueError
        （避免静默写入 None 后在 build() 中 `len(None)` 抛 TypeError）。
        """
        # R2: 入参校验
        if not isinstance(text, str) or not text:
            raise ValueError(
                f"SlidingWindow.add_part: text must be non-empty str, "
                f"got {type(text).__name__}"
            )
        if not isinstance(summary, str) or not summary:
            raise ValueError(
                f"SlidingWindow.add_part: summary must be non-empty str, "
                f"got {type(summary).__name__}"
            )

        self.parts[part_num] = text
        self.summaries[part_num] = summary

        # 滚动淘汰：保留最近 window_size 个 Part 的原文，其余替换为摘要
        if len(self.parts) > self.window_size:
            kept_keys = sorted(self.parts.keys())[-self.window_size:]
            new_parts = {k: self.parts[k] for k in kept_keys}
            self.parts = new_parts

    def add_rolling_summary(self, part_num: int, rolling_text: str) -> None:
        """外部生成二级滚动摘要后注入窗口。"""
        self.rolling_summaries[part_num] = rolling_text

    def add_milestone(self, milestone_num: int, milestone_text: str) -> None:
        """外部生成里程碑后注入窗口。"""
        self.milestones[milestone_num] = milestone_text

    # ----------------- R15: 自动生成 rolling/milestone -----------------
    def maybe_generate_rolling_summary(self, part_num: int, *, world_setting: str = "") -> dict:
        """
        R15: 若 part_num 命中 rolling 触发点，自动调 LLM 生成二级滚动摘要。
        从 writing_service._phase3_writing 抽出来，所有路径（WritingService + PartWriterAgent
        直调）都会触发。
        Returns: {"generated": bool, "text": str, "char_count": int}
        """
        if not self.should_create_rolling_summary(part_num):
            return {"generated": False, "text": "", "char_count": 0}

        # 幂等：如果已经生成过，跳过
        if part_num in self.rolling_summaries and self.rolling_summaries[part_num]:
            return {"generated": False, "text": self.rolling_summaries[part_num],
                    "char_count": len(self.rolling_summaries[part_num]), "already_exists": True}

        # 取最近 rolling_every 个 Part 的一级摘要
        recent_keys = sorted([p for p in self.summaries.keys() if p < part_num])[-self.rolling_every:]
        if not recent_keys:
            return {"generated": False, "text": "", "char_count": 0, "reason": "no_summaries_yet"}

        recent_text = "\n".join(
            f"Part {p}: {self.summaries[p]}" for p in recent_keys
        )

        # Lazy import 避免循环依赖
        try:
            from core.llm_client import call_llm
            rolling = call_llm(
                system_prompt=(
                    "你是长篇小说剧情压缩助手。"
                    "将下面若干个 Part 的剧情概要压缩为一段 800 字以内的连贯剧情段，"
                    "保留关键人物、冲突、伏笔、角色位置/状态/伤势变化，"
                    "输出纯叙事文本，不要分点。"
                ),
                user_prompt=recent_text,
                temperature=0.3,
                max_tokens=1200,
                agent="rolling_summary",
            )
            rolling_text = (rolling or "")[:self.SUMMARY_L2_LEN]

            if not rolling_text.strip():
                # Fallback: 拼接一级摘要前 100 字
                fallback = "\n".join(
                    f"Part {p}: {self.summaries[p][:100]}" for p in recent_keys
                )
                rolling_text = fallback[:self.SUMMARY_L2_LEN]

            self.add_rolling_summary(part_num, rolling_text)
            return {"generated": True, "text": rolling_text,
                    "char_count": len(rolling_text), "triggered_at": recent_keys}
        except Exception as e:
            return {"generated": False, "text": "", "char_count": 0, "error": str(e)[:200]}

    def maybe_generate_milestone(self, part_num: int, *, world_setting: str = "") -> dict:
        """
        R15: 若 part_num 命中 milestone 触发点，自动调 LLM 生成里程碑摘要。
        Returns: {"generated": bool, "text": str, "char_count": int, "milestone_num": int}
        """
        if not self.should_create_milestone(part_num):
            return {"generated": False, "text": "", "char_count": 0}

        milestone_num = part_num // self.milestone_every

        # 幂等
        if milestone_num in self.milestones and self.milestones[milestone_num]:
            return {"generated": False, "text": self.milestones[milestone_num],
                    "char_count": len(self.milestones[milestone_num]),
                    "milestone_num": milestone_num, "already_exists": True}

        recent_keys = sorted([p for p in self.summaries.keys() if p < part_num])[-self.milestone_every:]
        if len(recent_keys) < self.milestone_every:
            return {"generated": False, "text": "", "char_count": 0, "reason": "not_enough_parts"}

        recent_text = "\n".join(
            f"Part {p}: {self.summaries[p]}" for p in recent_keys
        )

        char_state_lines = [f"- {name}: {st}" for name, st in (self.character_state or {}).items()]
        foreshadow_lines = [
            f"- {f.get('id', '')}: {f.get('content', '')}"
            for f in (self.foreshadowing or [])
        ]

        milestone_input = (
            recent_text
            + (f"\n【世界观】{world_setting}" if world_setting else "")
            + ("\n【角色状态】\n" + "\n".join(char_state_lines) if char_state_lines else "")
            + ("\n【伏笔】\n" + "\n".join(foreshadow_lines) if foreshadow_lines else "")
        )

        try:
            from core.llm_client import call_llm
            milestone = call_llm(
                system_prompt=(
                    "你是长篇小说剧情压缩助手。"
                    f"将下面 {self.milestone_every} 个 Part 的剧情概要压缩为 2000 字以内的全局脉络段，"
                    "涵盖主线、支线、关键转折、角色弧光，输出纯叙事文本，不要分点。"
                ),
                user_prompt=milestone_input,
                temperature=0.3,
                max_tokens=2500,
                agent="milestone_summary",
            )
            milestone_text = (milestone or "")[:self.SUMMARY_L3_LEN]

            if not milestone_text.strip():
                fallback = "\n".join(
                    f"Part {p}: {self.summaries[p][:100]}" for p in recent_keys
                )
                milestone_text = fallback[:self.SUMMARY_L3_LEN]

            self.add_milestone(milestone_num, milestone_text)
            return {"generated": True, "text": milestone_text,
                    "char_count": len(milestone_text),
                    "milestone_num": milestone_num, "triggered_at": recent_keys}
        except Exception as e:
            return {"generated": False, "text": "", "char_count": 0,
                    "milestone_num": milestone_num, "error": str(e)[:200]}

    def update_foreshadowing(self, foreshadowing: list) -> None:
        if foreshadowing:
            self.foreshadowing = list(foreshadowing)

    def update_character_state(self, character_state: dict) -> None:
        if character_state:
            self.character_state = dict(character_state)

    # ----------------- 组装上下文 -----------------
    def build(
        self,
        part_num: int,
        *,
        characters: Optional[list] = None,
        world_setting: Optional[str] = None,
        outline: Optional[dict] = None,
        extra_context_provider=None,
        vector_store=None,
        vector_query: Optional[str] = None,
        vector_top_k: int = 3,
    ) -> str:
        """
        组装 PartWriter 所需的完整 prompt 上下文。

        参数:
            part_num: 当前正在创作的 Part 编号
            characters: 角色档案列表
            world_setting: 世界观字符串
            outline: 当前 Part 的规划（可选，目前保留位）
            extra_context_provider: 可调用对象，返回旧版"角色状态快照 + 关键事实"
                字符串，用于无缝融合旧实现的输出（保持向后兼容）。
            vector_store: R7-P0-4 可选 VectorStore 实例；为 None 或 disabled 时整段跳过。
            vector_query: R7-P0-4 用作语义检索 query 的文本（一般是 outline 的 core_event / emotion_target）。
            vector_top_k: 检索 Top-K 数。

        返回: 多段拼装的 prompt 上下文文本。
        """
        sections = []

        # 1. 角色档案（长期常量）
        if characters:
            sections.append("【角色档案】")
            for c in characters:
                sections.append(
                    f"- {c.get('name', '未知')}({c.get('role', '')}): "
                    f"{c.get('identity', '')}, 特质:{c.get('core_trait', '')}, "
                    f"动机:{c.get('motivation', '')}, 秘密:{c.get('secret', '')}"
                )
            sections.append("")

        # 2. 世界观（长期常量）
        if world_setting:
            sections.append(f"【世界观】{world_setting}")
            sections.append("")

        # 3. 伏笔表（短期）
        if self.foreshadowing:
            sections.append("【伏笔追踪】")
            for f_item in self.foreshadowing:
                sections.append(
                    f"- {f_item.get('id', '')}: {f_item.get('content', '')} "
                    f"(埋于Part{f_item.get('plant_part', '?')}, "
                    f"揭于Part{f_item.get('reveal_part', '?')})"
                )
            sections.append("")

        # 4. 里程碑摘要（最早期的全局脉络）
        if self.milestones:
            sections.append("【里程碑摘要】")
            for m_num in sorted(self.milestones.keys()):
                sections.append(f"里程碑 #{m_num}：{self.milestones[m_num]}")
            sections.append("")

        # 5. 二级滚动摘要（中长期压缩）
        if self.rolling_summaries:
            sections.append("【近期滚动摘要】")
            for p_num in sorted(self.rolling_summaries.keys()):
                if p_num >= part_num:
                    continue
                sections.append(
                    f"Part {p_num} 聚合：{self.rolling_summaries[p_num]}"
                )
            sections.append("")

        # 6. 一级摘要（最近非窗口内的 Part）
        in_window = set(self.parts.keys())
        recent_summaries = sorted(
            [p for p in self.summaries.keys() if p < part_num and p not in in_window]
        )
        if recent_summaries:
            sections.append("【已完成剧情摘要（远端）】")
            for p_num in recent_summaries:
                sections.append(f"Part {p_num}: {self.summaries[p_num]}")
            sections.append("")

        # 7. 上一 Part 的详细结尾（窗口内最近的 Part）
        prev_part = part_num - 1
        if prev_part in self.parts:
            prev_text = self.parts[prev_part]
            tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text
            sections.append(f"【上一部分（Part {prev_part}）结尾——必须自然承接】")
            sections.append(tail)
            sections.append("")

        # 8. 窗口内其余最近 Part 的关键片段
        #    取每个 Part 的末尾 600 字作为"近期上下文"
        recent_in_window = sorted(
            [p for p in self.parts.keys() if p < part_num and p != prev_part],
            reverse=True,
        )
        if recent_in_window:
            sections.append("【滑动窗口内最近 Part 片段】")
            for p_num in recent_in_window:
                text = self.parts[p_num]
                tail = text[-600:] if len(text) > 600 else text
                sections.append(f"--- Part {p_num} 末尾 ---")
                sections.append(tail)
            sections.append("")

        # 9. 旧实现兼容：通过 extra_context_provider 注入角色状态快照 + 关键事实
        if callable(extra_context_provider):
            try:
                legacy = extra_context_provider(part_num)
                if legacy:
                    sections.append(legacy)
            except Exception:
                # 旧实现失败不影响主流程
                pass

        # 10. R7-P0-4: 向量检索双轨（可选；vector_store 未启用或无 query 时整段跳过）
        if vector_store is not None and vector_query:
            try:
                hits = vector_store.query(
                    vector_query, top_k=vector_top_k, exclude_part_num=part_num
                )
                if hits:
                    sections.append("【相关前文片段（向量检索 Top-K）】")
                    for hit_part_num, sim in hits:
                        text = vector_store.get_text(hit_part_num)
                        if not text:
                            continue
                        excerpt = text[-600:] if len(text) > 600 else text
                        sections.append(
                            f"--- Part {hit_part_num}（相似度 {sim:.2f}）---\n{excerpt}"
                        )
                    sections.append("")
            except Exception as vs_err:
                # 向量检索失败永远 silent-fallback，不影响主流程
                print(f"[SlidingWindow] vector_store 查询失败（已跳过）: {vs_err}")

        return "\n".join(sections)

    # ----------------- 调试 / 持久化辅助 -----------------
    def snapshot(self) -> dict:
        """导出当前窗口状态（用于 debug / 持久化）。"""
        return {
            "window_size": self.window_size,
            "parts": dict(self.parts),
            "summaries": dict(self.summaries),
            "rolling_summaries": dict(self.rolling_summaries),
            "milestones": dict(self.milestones),
            "foreshadowing": list(self.foreshadowing),
            "character_state": dict(self.character_state),
        }
