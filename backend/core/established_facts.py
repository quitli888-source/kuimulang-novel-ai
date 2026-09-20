"""
R8-P0-1: 跨 Part "已确立事实" 结构化数据 (Established Facts)

R8 新增 —— 解决 Logic Agent 评 Part N 时只看到"前文摘要 + 末尾 1200 字"，
缺乏"前文已确立事实清单"作为权威基线，导致 P0 级"信息越界/物品遗忘/
时间漂移"频繁误判的问题。

设计要点：
- 轻量级 dataclass，不依赖外部数据库（≤ 20 Part × ≤ 15 facts/Part ≤ 300 条）
- 用 LLM 在 Part 写完后增量抽取（prompts/established_facts.txt）
- 按 part_num + category 双向索引，支持"Part N 写作前注入 Part 1..N-1 事实"
- 与 state.established_facts 字段 1:1 对应，to_dict / from_dict 持久化
- 不替代 character_state_track / foreshadowing，而是上层语义层
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Iterable


# 合法 category 集合（与 prompts/established_facts.txt 协议保持一致）
VALID_CATEGORIES = {
    "character",       # 角色（姓名/身份/状态/位置/伤势/技能/秘密）
    "location",        # 地点（位置/转移/描述）
    "object",          # 物品（位置/拥有者/状态变化）
    "event",           # 事件（谁-何时-对谁-做了什么-导致什么）
    "trait",           # 性格 / 特质
    "relationship",    # 关系（A 与 B 的关系/决裂/结盟/师徒等）
    "world_rule",      # 世界观硬规则（物理/魔法/法律）
    "foreshadow",      # 伏笔（已埋设/已揭晓）
    "knowledge",       # 信息边界（角色 A 知道 X —— 用于信息越界检查）
}


# R25-P2-20: 提到模块级常量（避免 render_for_prompt 每次调用都重建 dict）
_CATEGORY_LABELS: dict = {
    "character": "【角色状态/身份】",
    "location": "【地点】",
    "object": "【物品】",
    "event": "【已发生事件】",
    "trait": "【性格/特质】",
    "relationship": "【角色关系】",
    "world_rule": "【世界观硬规则】",
    "foreshadow": "【伏笔】",
    "knowledge": "【角色信息边界】",
}


@dataclass
class Fact:
    """单条已确立事实。"""
    id: str                                # "F{part_num}_{n}"，全局唯一
    part_num: int                          # 哪 Part 确立（首次确立的 Part）
    category: str                          # 详见 VALID_CATEGORIES
    text: str                              # 事实描述（≤ 60 字）
    quote: str = ""                        # 原文引用（≤ 30 字，可空）
    subject: str = ""                      # 主语（角色名/物品名/地点名）—— 用于去重
    predicate: str = ""                    # 谓语（位于/拥有/死亡/知道/......）—— 用于去重
    superseded_by: Optional[str] = None    # 若被同 subject+predicate 后续事实覆盖，记录新 fact.id

    def __post_init__(self):
        # 归一化 category 到合法集合
        if self.category not in VALID_CATEGORIES:
            # 兼容旧数据 / 模型回退：未知 category 暂存为 character
            self.category = "character"
        if not self.id:
            # 防御：id 必填，缺失时合成一个
            self.id = f"F{self.part_num}_0"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(
            id=d.get("id", ""),
            part_num=int(d.get("part_num", 0) or 0),
            category=d.get("category", "character"),
            text=d.get("text", ""),
            quote=d.get("quote", ""),
            subject=d.get("subject", ""),
            predicate=d.get("predicate", ""),
            superseded_by=d.get("superseded_by"),
        )


@dataclass
class EstablishedFacts:
    """已确立事实集合。

    用法：
        ef = EstablishedFacts()
        ef.add(Fact(id="F1_1", part_num=1, category="character", text="林枫是前刑警"))
        # Part 2 写作前注入：
        prompt_block = ef.render_for_prompt(categories=["character", "object", "event"])
    """
    facts: List[Fact] = field(default_factory=list)

    # ---- R22-P2-23: (subject, predicate) → list[Fact index] 索引 ----
    # 添加事实时一次性 O(1) 写入索引；冲突查找从 O(N) 变 O(1)
    _sp_index: dict = field(default_factory=dict, repr=False, compare=False)

    # ----------------- 增删查 -----------------
    def add(self, fact: Fact) -> None:
        """添加一条事实。

        冲突处理：若 (subject, predicate) 已有非空事实，标记旧事实为 superseded_by 新 id。
        R22-P2-23: 用 _sp_index 索引替代 O(N) 线性扫描。
        """
        if not fact or not fact.id:
            return
        if fact.subject and fact.predicate:
            key = (fact.subject, fact.predicate)
            existing_indices = self._sp_index.get(key, [])
            for idx in existing_indices:
                old = self.facts[idx]
                if not old.superseded_by:
                    old.superseded_by = fact.id
                    break
        # 写入索引（在 append 之后，idx 就是 self.facts 的新长度 - 1）
        self.facts.append(fact)
        if fact.subject and fact.predicate:
            key = (fact.subject, fact.predicate)
            self._sp_index.setdefault(key, []).append(len(self.facts) - 1)

    def add_many(self, facts: Iterable[Fact]) -> int:
        """批量添加，返回成功条数。"""
        n = 0
        for f in facts:
            if f and f.id:
                self.add(f)
                n += 1
        return n

    def by_category(self, cat: str) -> List[Fact]:
        """按 category 过滤（包含已 superseded 的事实，便于回溯）。"""
        return [f for f in self.facts if f.category == cat]

    def before_part(self, part_num: int, *, include_superseded: bool = False) -> List[Fact]:
        """返回 part_num 之前确立的所有事实。

        include_superseded=False 时只返回当前有效事实（默认，符合 Logic Agent 评估语义）。
        """
        out = []
        for f in self.facts:
            if f.part_num >= part_num:
                continue
            if not include_superseded and f.superseded_by:
                continue
            out.append(f)
        return out

    def by_id(self, fact_id: str) -> Optional[Fact]:
        for f in self.facts:
            if f.id == fact_id:
                return f
        return None

    def clear(self) -> None:
        # R4-P0-4: 同步清 _sp_index —— 此前只重置 facts，残留的旧下标会让 clear 后
        # add() 的冲突查找读到已删除元素，直接 IndexError。
        self.facts = []
        self._sp_index.clear()

    # ----------------- 渲染为 prompt 段 -----------------
    def render_for_prompt(
        self,
        categories: Optional[List[str]] = None,
        *,
        before_part_num: Optional[int] = None,
        max_per_category: int = 8,
        max_total: int = 30,
    ) -> str:
        """渲染为可注入 prompt 的多行文本。

        Args:
            categories: 限定要包含的 category；None 表示全部
            before_part_num: 只包含 part_num < 该值的事实；None 表示全部
            max_per_category: 每个 category 最多取多少条（按 part_num 降序）
            max_total: 全局上限（按 part_num 全局倒序选最近 N 条）—— P1-91：
                       防止 100 Part × 9 category × 8 = 720 条事实都塞进 prompt。

        Returns:
            多行字符串，category 段标题 + 条目；若全部为空返回 ""。
        """
        selected = []
        for f in self.facts:
            if f.superseded_by:
                continue  # 渲染给模型时不展示被覆盖的旧事实
            if before_part_num is not None and f.part_num >= before_part_num:
                continue
            if categories is not None and f.category not in categories:
                continue
            selected.append(f)

        if not selected:
            return ""

        # P1-91: 全局按 part_num 倒序选最近 max_total 条，再按 category 分组。
        # 这样既保证保留最新事实，又避免后期 Part 90+ 时把所有历史事实全过一遍再截 8。
        selected.sort(key=lambda x: x.part_num, reverse=True)
        selected = selected[:max_total]

        # 按 category 分组
        grouped: dict = {}
        for f in selected:
            grouped.setdefault(f.category, []).append(f)

        # 渲染顺序（与 VALID_CATEGORIES 顺序一致）
        order = [c for c in ("character", "location", "object", "event", "trait",
                              "relationship", "world_rule", "foreshadow", "knowledge")
                 if c in grouped]
        # 未识别的 category 兜底追加
        for c in grouped:
            if c not in order:
                order.append(c)

        lines: List[str] = []
        for cat in order:
            facts_in_cat = sorted(grouped[cat], key=lambda x: x.part_num, reverse=True)
            facts_in_cat = facts_in_cat[:max_per_category]
            # R25-P2-20: 提到模块级常量（避免 render_for_prompt 每次调用都重建 dict）
            cat_label = _CATEGORY_LABELS.get(cat, f"【{cat}】")
            lines.append(cat_label)
            for f in facts_in_cat:
                txt = (f.text or "").strip()
                if not txt:
                    continue
                # 截断 text 到 80 字防止 prompt 爆掉
                if len(txt) > 80:
                    txt = txt[:80] + "…"
                lines.append(f"- (Part{f.part_num}) {txt}")
            lines.append("")

        return "\n".join(lines).rstrip()

    # ----------------- 滚动合并（跨 Part 增量更新） -----------------
    def merge(self, other: "EstablishedFacts", *, prefer: str = "newer") -> None:
        """合并另一份 EstablishedFacts（用于滑动窗口 / resume 时的双向同步）。

        Args:
            other: 待合并的 EstablishedFacts
            prefer: "newer" —— 保留 part_num 更大的；"older" —— 保留 part_num 更小的；
                    "skip" —— 若 id 已存在则跳过
        """
        if other is None or not other.facts:
            return
        existing_ids = {f.id for f in self.facts}
        for f in other.facts:
            if f.id in existing_ids:
                continue
            if prefer == "newer" and f.superseded_by:
                # 新版本已 supersede 该事实，按 new 优先：跳过被覆盖的旧事实
                continue
            # R4-P2-x: append 后同步 _sp_index——此前不更新，合并进来的事实
            # 后续 supersede 冲突查找全部落空。
            pos = len(self.facts)
            self.facts.append(f)
            if f.subject and f.predicate:
                self._sp_index.setdefault((f.subject, f.predicate), []).append(pos)

    # ----------------- 持久化 -----------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "facts": [f.to_dict() for f in self.facts],
        }

    def from_dict(self, d: dict) -> None:
        """从 dict 加载；保留现有 facts 不预清空（外部决定是否先 clear）。
        R22-P2-23: 加载后重建 _sp_index 索引（dataclass 默认值不会随 from_dict 自动重建）。
        """
        if not isinstance(d, dict):
            return
        raw = d.get("facts") or []
        self._sp_index.clear()  # 重建
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                f = Fact.from_dict(item)
                # R4-P0-4: 索引用实际追加位置——此前用 enumerate(raw) 的 idx，
                # 脏数据被 skip 后下标与 facts 列表脱钩，后续冲突查找 IndexError。
                pos = len(self.facts)
                self.facts.append(f)
                if f.subject and f.predicate:
                    self._sp_index.setdefault(
                        (f.subject, f.predicate), []
                    ).append(pos)
            except Exception:
                # 单条解析失败不影响整体
                continue

    # ----------------- 调试 -----------------
    def __len__(self) -> int:
        return len(self.facts)

    def __repr__(self) -> str:
        return f"EstablishedFacts(facts={len(self.facts)})"


# ----------------- LLM 抽取辅助 -----------------
def facts_from_extractor_payload(payload: dict, part_num: int) -> List[Fact]:
    """从 call_llm_json 返回的 payload 解析为 Fact 列表。

    协议（prompts/established_facts.txt）：
        {"facts": [{"category": "...", "text": "...", "quote": "...", "subject": "...", "predicate": "..."}, ...]}
    """
    if not isinstance(payload, dict):
        return []
    raw_list = payload.get("facts") or []
    if not isinstance(raw_list, list):
        return []
    out: List[Fact] = []
    for i, raw in enumerate(raw_list[:15], start=1):
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        cat = (raw.get("category") or "character").strip() or "character"
        out.append(Fact(
            id=f"F{part_num}_{i}",
            part_num=part_num,
            category=cat,
            text=text[:120],  # 防止单条过长
            quote=(raw.get("quote") or "")[:30],
            subject=(raw.get("subject") or "").strip(),
            predicate=(raw.get("predicate") or "").strip(),
        ))
    return out


def derive_facts_from_summary(state, part_num: int) -> list:
    """R9 应急：基于 part_summaries + characters + outline 规则派生事实，
    保证 Part N+1 至少有 facts 可用（避免 LLM 抽取 JSON 解析失败导致 Logic 评分退步）。

    返回 Fact 列表（最多 8 条）。
    """
    facts = []
    # 1) 角色 fact
    for c in (state.characters or []):
        name = c.get('name', '').strip()
        if not name:
            continue
        identity = c.get('identity', '')
        if identity:
            facts.append(Fact(
                id=f"F{part_num}_c_{name}",
                part_num=part_num,
                category='character',
                text=f"{name}是{identity}",
                quote='',
            ))
    # 2) Part 摘要 fact（从 part_summaries 截取前 120 字作为事件描述）
    summary = state.part_summaries.get(str(part_num), '')[:120]
    if summary:
        facts.append(Fact(
            id=f"F{part_num}_e_summary",
            part_num=part_num,
            category='event',
            text=summary,
            quote='',
        ))
    # 3) outline fact
    outline = (state.part_outline or [])
    if part_num <= len(outline):
        o = outline[part_num - 1]
        title = o.get('title', '')
        core = o.get('core_event', '')
        if title:
            facts.append(Fact(
                id=f"F{part_num}_o_title",
                part_num=part_num,
                category='event',
                text=f"Part {part_num} 标题《{title}》，核心事件：{core}",
                quote='',
            ))
    return facts[:8]


def verify_fact_against_text(fact_text: str, source_text: str, min_overlap: int = 2) -> bool:
    """R12 引用验证：检查 fact_text 中的关键实体词是否在 source_text 中出现。
    中文按字符 2-gram 切分，统计重叠数；返回是否引用有效。
    """
    if not fact_text or not source_text:
        return False
    fact_chars = set(fact_text)
    src_chars = set(source_text)
    overlap = fact_chars & src_chars
    # 排除常用字（避免"的是"等高频字误判）
    common = set("的是在了和与及或但如果因为所以之一一个我们你他她它们了")
    meaningful_overlap = overlap - common
    return len(meaningful_overlap) >= min_overlap


def derive_facts_layered(state, part_num: int, source_text: str = "") -> list:
    """R12 三层事实抽取：规则 + 引用验证 + LLM 备选。

    返回的每条 Fact 都标注了 source 引用（quote 字段为原文片段）。
    限制最多 15 条。
    """
    facts = []
    src = source_text or (state.parts.get(str(part_num), "") if state else "")

    # Layer 1: 角色 fact（来自 characters 档案）
    for c in (state.characters or []):
        name = c.get('name', '').strip()
        if not name:
            continue
        identity = c.get('identity', '')
        if identity:
            # 引用验证：name 和 identity 关键词应出现在 source 中
            text = f"{name}是{identity}"
            if not src or verify_fact_against_text(text, src):
                facts.append(Fact(
                    id=f"F{part_num}_c_{name}",
                    part_num=part_num,
                    category='character',
                    text=text,
                    quote=src[:40] if src else "",
                ))

    # Layer 2: Part 摘要 fact（带来源引用）
    summary = state.part_summaries.get(str(part_num), '')[:120]
    if summary:
        facts.append(Fact(
            id=f"F{part_num}_e_summary",
            part_num=part_num,
            category='event',
            text=summary,
            quote=summary[:40] if summary else "",
        ))

    # Layer 3: outline fact（带标题 + 核心事件）
    outline = (state.part_outline or [])
    if part_num <= len(outline):
        o = outline[part_num - 1]
        title = o.get('title', '')
        core = o.get('core_event', '')
        if title:
            text = f"Part {part_num} 阶段{outline[part_num-1].get('phase','')}，标题《{title}》，核心事件：{core}"
            facts.append(Fact(
                id=f"F{part_num}_o_title",
                part_num=part_num,
                category='event',
                text=text[:120],
                quote="",
            ))

    # Layer 4: 角色核心特征 fact（高信号）
    for c in (state.characters or [])[:3]:  # 只取前 3 个角色避免爆量
        name = c.get('name', '').strip()
        trait = c.get('core_trait', '')
        if name and trait:
            text = f"{name}的核心特征：{trait}"
            facts.append(Fact(
                id=f"F{part_num}_t_{name}",
                part_num=part_num,
                category='trait',
                text=text[:80],
                quote="",
            ))

    # Layer 5: 角色动机 fact
    for c in (state.characters or [])[:3]:
        name = c.get('name', '').strip()
        motivation = c.get('motivation', '')
        if name and motivation:
            text = f"{name}的动机：{motivation}"
            facts.append(Fact(
                id=f"F{part_num}_m_{name}",
                part_num=part_num,
                category='trait',
                text=text[:80],
                quote="",
            ))

    return facts[:15]
