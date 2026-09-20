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
from core.logger import get_logger
logger = get_logger('sliding_window')

def _load_user_window_config() -> dict:
    """
    R15: 读取 data/window_config.json 作为用户 UI 配置。
    返回 {"window_size": int, "rolling_every": int, "milestone_every": int}（缺字段则省略）。
    R21-P2-30: 路径常量 WINDOW_CONFIG_FILE 同时被 sliding_window.py 和 config_api.py 共用。
    P2-103: 解析失败改打 warning（之前是 debug，不可见）。
    """
    try:
        if _os.path.exists(WINDOW_CONFIG_FILE):
            with open(WINDOW_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = _json.load(f)
                out = {}
                for k in ('window_size', 'rolling_every', 'milestone_every'):
                    if k in data:
                        try:
                            out[k] = int(data[k])
                        except (TypeError, ValueError) as type_err:
                            logger.warning(f'window_config.json 字段 {k}={data[k]!r} 不是合法整数: {type_err}')
                return out
    except Exception as parse_err:
        logger.warning(f'window_config.json 解析失败，回退默认: {type(parse_err).__name__}: {parse_err}')
    return {}
_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
WINDOW_CONFIG_FILE = _os.path.join(_PROJECT_ROOT, 'data', 'window_config.json')

class SlidingWindow:
    """真正的滑动窗口上下文管理器。"""
    DEFAULT_WINDOW_SIZE = 6
    DEFAULT_ROLLING_EVERY = 3
    DEFAULT_MILESTONE_EVERY = 20
    SUMMARY_L1_LEN = 200
    SUMMARY_L2_LEN = 800
    SUMMARY_L3_LEN = 2000

    def __init__(self, window_size: Optional[int]=None, rolling_every: Optional[int]=None, milestone_every: Optional[int]=None):
        _file_defaults = _load_user_window_config()
        _ws = window_size if window_size is not None else _file_defaults.get('window_size') or self.DEFAULT_WINDOW_SIZE
        _re = rolling_every if rolling_every is not None else _file_defaults.get('rolling_every') or self.DEFAULT_ROLLING_EVERY
        _me = milestone_every if milestone_every is not None else _file_defaults.get('milestone_every') or self.DEFAULT_MILESTONE_EVERY
        self.window_size = _ws
        self.rolling_every = _re
        self.milestone_every = _me
        self.parts: dict = {}
        self.summaries: dict = {}
        self.rolling_summaries: dict = {}
        self.milestones: dict = {}
        self.foreshadowing: list = []
        self.character_state: dict = {}

    def should_create_rolling_summary(self, part_num: int) -> bool:
        """判断当前 Part 是否需要生成二级滚动摘要（每 rolling_every 个 Part 一次）。"""
        return part_num > 0 and part_num % self.rolling_every == 0

    def should_create_milestone(self, part_num: int) -> bool:
        """判断当前 Part 是否需要生成里程碑摘要（每 milestone_every 个 Part 一次）。"""
        return part_num > 0 and part_num % self.milestone_every == 0

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
        if not isinstance(text, str) or not text:
            raise ValueError(f'SlidingWindow.add_part: text must be non-empty str, got {type(text).__name__}')
        if not isinstance(summary, str) or not summary:
            raise ValueError(f'SlidingWindow.add_part: summary must be non-empty str, got {type(summary).__name__}')
        self.parts[part_num] = text
        self.summaries[part_num] = summary
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

    def maybe_generate_rolling_summary(self, part_num: int, *, world_setting: str='', work_id: Optional[str]=None) -> dict:
        """
        R15: 若 part_num 命中 rolling 触发点，自动调 LLM 生成二级滚动摘要。
        从 writing_service._phase3_writing 抽出来，所有路径（WritingService + PartWriterAgent
        直调）都会触发。
        Returns: {"generated": bool, "text": str, "char_count": int}

        P1-87: 新增 work_id 关键字参数 —— 透传给 cost_tracker 做 per-work 计费路由。
        """
        if not self.should_create_rolling_summary(part_num):
            return {'generated': False, 'text': '', 'char_count': 0}
        if part_num in self.rolling_summaries and self.rolling_summaries[part_num]:
            return {'generated': False, 'text': self.rolling_summaries[part_num], 'char_count': len(self.rolling_summaries[part_num]), 'already_exists': True}
        recent_keys = sorted([p for p in self.summaries.keys() if p < part_num])[-self.rolling_every:]
        if not recent_keys:
            return {'generated': False, 'text': '', 'char_count': 0, 'reason': 'no_summaries_yet'}
        recent_text = '\n'.join((f'Part {p}: {self.summaries[p]}' for p in recent_keys))
        try:
            from core.llm_client import call_llm
            rolling = call_llm(system_prompt='你是长篇小说剧情压缩助手。将下面若干个 Part 的剧情概要压缩为一段 800 字以内的连贯剧情段，保留关键人物、冲突、伏笔、角色位置/状态/伤势变化，输出纯叙事文本，不要分点。', user_prompt=recent_text, temperature=0.3, max_tokens=1200, agent='rolling_summary', work_id=work_id)
            rolling_text = (rolling or '')[:self.SUMMARY_L2_LEN]
            if not rolling_text.strip():
                fallback = '\n'.join((f'Part {p}: {self.summaries[p][:100]}' for p in recent_keys))
                rolling_text = fallback[:self.SUMMARY_L2_LEN]
            self.add_rolling_summary(part_num, rolling_text)
            return {'generated': True, 'text': rolling_text, 'char_count': len(rolling_text), 'triggered_at': recent_keys}
        except Exception as e:
            return {'generated': False, 'text': '', 'char_count': 0, 'error': str(e)[:200]}

    def maybe_generate_milestone(self, part_num: int, *, world_setting: str='', work_id: Optional[str]=None) -> dict:
        """
        R15: 若 part_num 命中 milestone 触发点，自动调 LLM 生成里程碑摘要。
        Returns: {"generated": bool, "text": str, "char_count": int, "milestone_num": int}

        P1-87: 新增 work_id 关键字参数 —— 透传给 cost_tracker 做 per-work 计费路由。
        """
        if not self.should_create_milestone(part_num):
            return {'generated': False, 'text': '', 'char_count': 0}
        milestone_num = part_num // self.milestone_every
        if milestone_num in self.milestones and self.milestones[milestone_num]:
            return {'generated': False, 'text': self.milestones[milestone_num], 'char_count': len(self.milestones[milestone_num]), 'milestone_num': milestone_num, 'already_exists': True}
        recent_keys = sorted([p for p in self.summaries.keys() if p < part_num])[-self.milestone_every:]
        if len(recent_keys) < self.milestone_every:
            return {'generated': False, 'text': '', 'char_count': 0, 'reason': 'not_enough_parts'}
        recent_text = '\n'.join((f'Part {p}: {self.summaries[p]}' for p in recent_keys))
        char_state_lines = [f'- {name}: {st}' for name, st in (self.character_state or {}).items()]
        foreshadow_lines = [f"- {f.get('id', '')}: {f.get('content', '')}" for f in self.foreshadowing or []]
        milestone_input = recent_text + (f'\n【世界观】{world_setting}' if world_setting else '') + ('\n【角色状态】\n' + '\n'.join(char_state_lines) if char_state_lines else '') + ('\n【伏笔】\n' + '\n'.join(foreshadow_lines) if foreshadow_lines else '')
        try:
            from core.llm_client import call_llm
            milestone = call_llm(system_prompt=f'你是长篇小说剧情压缩助手。将下面 {self.milestone_every} 个 Part 的剧情概要压缩为 2000 字以内的全局脉络段，涵盖主线、支线、关键转折、角色弧光，输出纯叙事文本，不要分点。', user_prompt=milestone_input, temperature=0.3, max_tokens=2500, agent='milestone_summary', work_id=work_id)
            milestone_text = (milestone or '')[:self.SUMMARY_L3_LEN]
            if not milestone_text.strip():
                fallback = '\n'.join((f'Part {p}: {self.summaries[p][:100]}' for p in recent_keys))
                milestone_text = fallback[:self.SUMMARY_L3_LEN]
            self.add_milestone(milestone_num, milestone_text)
            return {'generated': True, 'text': milestone_text, 'char_count': len(milestone_text), 'milestone_num': milestone_num, 'triggered_at': recent_keys}
        except Exception as e:
            return {'generated': False, 'text': '', 'char_count': 0, 'milestone_num': milestone_num, 'error': str(e)[:200]}

    def update_foreshadowing(self, foreshadowing: list) -> None:
        if foreshadowing:
            self.foreshadowing = list(foreshadowing)

    def update_character_state(self, character_state: dict) -> None:
        if character_state:
            self.character_state = dict(character_state)

    def build(self, part_num: int, *, characters: Optional[list]=None, world_setting: Optional[str]=None, outline: Optional[dict]=None, extra_context_provider=None, vector_store=None, vector_query: Optional[str]=None, vector_top_k: int=3) -> str:
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
        if characters:
            sections.append('【角色档案】')
            for c in characters:
                sections.append(f"- {c.get('name', '未知')}({c.get('role', '')}): {c.get('identity', '')}, 特质:{c.get('core_trait', '')}, 动机:{c.get('motivation', '')}, 秘密:{c.get('secret', '')}")
            sections.append('')
        if world_setting:
            sections.append(f'【世界观】{world_setting}')
            sections.append('')
        if self.foreshadowing:
            sections.append('【伏笔追踪】')
            for f_item in self.foreshadowing:
                sections.append(f"- {f_item.get('id', '')}: {f_item.get('content', '')} (埋于Part{f_item.get('plant_part', '?')}, 揭于Part{f_item.get('reveal_part', '?')})")
            sections.append('')
        if self.milestones:
            sections.append('【里程碑摘要】')
            for m_num in sorted(self.milestones.keys()):
                sections.append(f'里程碑 #{m_num}：{self.milestones[m_num]}')
            sections.append('')
        if self.rolling_summaries:
            sections.append('【近期滚动摘要】')
            for p_num in sorted(self.rolling_summaries.keys()):
                if p_num >= part_num:
                    continue
                sections.append(f'Part {p_num} 聚合：{self.rolling_summaries[p_num]}')
            sections.append('')
        in_window = set(self.parts.keys())
        recent_summaries = sorted([p for p in self.summaries.keys() if p < part_num and p not in in_window])
        if recent_summaries:
            sections.append('【已完成剧情摘要（远端）】')
            for p_num in recent_summaries:
                sections.append(f'Part {p_num}: {self.summaries[p_num]}')
            sections.append('')
        prev_part = part_num - 1
        if prev_part in self.parts:
            prev_text = self.parts[prev_part]
            tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text
            sections.append(f'【上一部分（Part {prev_part}）结尾——必须自然承接】')
            sections.append(tail)
            sections.append('')
        recent_in_window = sorted([p for p in self.parts.keys() if p < part_num and p != prev_part], reverse=True)
        if recent_in_window:
            sections.append('【滑动窗口内最近 Part 片段】')
            for p_num in recent_in_window:
                text = self.parts[p_num]
                tail = text[-600:] if len(text) > 600 else text
                sections.append(f'--- Part {p_num} 末尾 ---')
                sections.append(tail)
            sections.append('')
        if callable(extra_context_provider):
            try:
                legacy = extra_context_provider(part_num)
                if legacy:
                    sections.append(legacy)
            except Exception:
                logger.debug('sliding_window: silent except (P2-19)', exc_info=True)
        if vector_store is not None and vector_query:
            try:
                hits = vector_store.query(vector_query, top_k=vector_top_k, exclude_part_num=part_num)
                if hits:
                    sections.append('【相关前文片段（向量检索 Top-K）】')
                    for hit_part_num, sim in hits:
                        text = vector_store.get_text(hit_part_num)
                        if not text:
                            continue
                        excerpt = text[-600:] if len(text) > 600 else text
                        sections.append(f'--- Part {hit_part_num}（相似度 {sim:.2f}）---\n{excerpt}')
                    sections.append('')
            except Exception as vs_err:
                logger.info(f'[SlidingWindow] vector_store 查询失败（已跳过）: {vs_err}')
        return '\n'.join(sections)

    def snapshot(self) -> dict:
        """导出当前窗口状态（用于 debug / 持久化）。"""
        return {'window_size': self.window_size, 'parts': dict(self.parts), 'summaries': dict(self.summaries), 'rolling_summaries': dict(self.rolling_summaries), 'milestones': dict(self.milestones), 'foreshadowing': list(self.foreshadowing), 'character_state': dict(self.character_state)}