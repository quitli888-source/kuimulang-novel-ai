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
import time


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

# R1-D: 不参与全局 max_total 倒序截断的类别 —— 关键物品/地点/关系/世界规则
# 在 20+ Part 长跑后段必须仍可注入（此前全局截 30 条会先砍掉早期关键事实，
# "油纸包"类物品状态漂移正源于此）。event/trait 等叙事类仍受 max_total 约束。
_PROTECTED_CATEGORIES = ("object", "location", "relationship", "world_rule")

# R1-E: 退场谓词 —— derive_departed_characters 精确匹配 + prompt 注入子串匹配共用
DEPARTED_PREDICATES = ("死亡", "离开", "失踪", "退场")


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
        # R1-D: object/location/relationship/world_rule 四类豁免全局截断（关键实体
        # 状态后段必须可见），max_total 只约束 event/trait 等叙事类；每类仍受
        # max_per_category 约束，被覆盖的旧事实已在上面 filtered。
        selected.sort(key=lambda x: x.part_num, reverse=True)
        protected = [f for f in selected if f.category in _PROTECTED_CATEGORIES]
        others = [f for f in selected if f.category not in _PROTECTED_CATEGORIES][:max_total]
        selected = protected + others

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
    # R1-F: 与 prompts/established_facts.txt 的 ADD-only 协议对齐，条数指引 15 → 10
    for i, raw in enumerate(raw_list[:10], start=1):
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


def register_name_variants(registry_dict: dict, variants: list, part_num: int) -> list:
    """R4-4: 把 facts 抽取回报的疑似别名候选登记进 name_registry（只追加，不合并）。

    协议（prompts/established_facts.txt 新增可选字段）：variants 元素形如
    {"variant": "<名册外写法>", "canonical": "<名册规范名>", "evidence": "<原文 quote>"}。

    规则（回应 Round 1 拒绝理由：本函数代码里不存在任何合并逻辑）：
    - 只写 registry[canonical]['alias_candidates']（append
      {variant, part_num, evidence, timestamp}）
    - 不做任何 subject 改写、不动既有 facts、不改历史文本
    - canonical 不在 registry 中 → 跳过（不发明 canonical 名）
    - 晋升在渲染侧（core/name_registry.py）：同一 variant 出现 ≥2 次或带非空
      evidence → 展示为"已登记别名"；单次无证据候选只记录不注入 prompt

    Args:
        registry_dict: name_registry（build_name_registry 产物，原地修改）
        variants: payload['name_variants']（LLM 回报，容忍脏数据）
        part_num: 当前 Part 编号

    Returns:
        实际登记的条目列表 [{variant, canonical, part_num, evidence}, ...]
    """
    if not isinstance(registry_dict, dict) or not registry_dict:
        return []
    if not isinstance(variants, list):
        return []
    registered: list = []
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    for item in variants:
        if not isinstance(item, dict):
            continue
        variant = (item.get('variant') or '').strip()
        canonical = (item.get('canonical') or '').strip()
        evidence = (item.get('evidence') or '').strip()[:30]
        if not variant or not canonical or canonical not in registry_dict:
            continue
        info = registry_dict.get(canonical)
        if not isinstance(info, dict):
            continue
        candidates = info.get('alias_candidates')
        if not isinstance(candidates, list):
            candidates = []
            info['alias_candidates'] = candidates
        candidates.append({
            'variant': variant,
            'part_num': part_num,
            'evidence': evidence,
            'timestamp': now,
        })
        registered.append({'variant': variant, 'canonical': canonical,
                           'part_num': part_num, 'evidence': evidence})
    return registered


def derive_departed_characters(facts, character_names) -> dict:
    """R1-E: 从已确立事实派生"已退场角色"账本（纯函数，零 LLM 成本）。

    筛 category=='character' 且 predicate ∈ DEPARTED_PREDICATES（死亡/离开/失踪/退场）、
    subject ∈ character_names（只用 Phase 2 正式角色名，防常见词误报）的 fact，
    输出 {角色名: f'Part{part_num} {predicate}: {text}'}。

    predicate 显式入值：fact.text 可能用"战死/陨落"等近义表述而不含"死亡"字面量，
    下游 prompt 注入按退场谓词子串筛条目，缺了 predicate 会静默漏判。

    Args:
        facts: EstablishedFacts 实例或其 .facts 列表（容忍 None）
        character_names: 正式角色名可迭代对象

    Returns:
        {角色名: "PartN 死亡: 事实描述"}；无退场事实时返回 {}。
    """
    raw = getattr(facts, "facts", facts) or []
    names = {str(n).strip() for n in (character_names or []) if n and str(n).strip()}
    out: dict = {}
    for f in raw:
        if getattr(f, "category", "") != "character":
            continue
        predicate = (getattr(f, "predicate", "") or "").strip()
        if predicate not in DEPARTED_PREDICATES:
            continue
        subject = (getattr(f, "subject", "") or "").strip()
        if not subject or subject not in names:
            continue
        part_num = getattr(f, "part_num", 0)
        text = (getattr(f, "text", "") or "").strip()
        out[subject] = f"Part{part_num} {predicate}: {text}".strip()
    return out


def earliest_departure_parts(facts, character_names) -> dict:
    """R8-P0-1（S1）: 每个退场角色取"最早死亡记录"（纯函数，零 LLM）。

    与 derive_departed_characters 的分工（**后者一字不改**——R1-E 预检与名册段
    依赖其 last-write-wins 语义，改动会波及写作 prompt）：derive_* 对每个角色
    是字典覆盖写，只保留最后一条退场记录。converge 实证林渊真实死亡链是
    F4_1（Part 4 首死，被无脸族主切断咽喉）→ F5_1/F6_10/F6_2（Part 5-6 尸体
    悬井/坠井）→ F7_1/F8_1（Part 7-8 确认死亡），账本只留 "Part8 死亡"——
    导致 Part 6 的残留 P0（"悬在红雾里的林渊动了"）与 Part 6 大纲违规
    （"林渊亲自出手，镇压命纹"）在 dep_part=8 下漏判。

    本函数对每个角色取**最小 part_num** 的退场记录，两份语义并存、各自锁定。

    实现期修正（对 §1.1.9 规格的必要偏离）：死亡链前段（F4_1 等）在 converge
    实证里 category 是 'event'（LLM 按事件记录死亡），故 category 过滤取
    ('character', 'event') 并集——只认 'character' 会漏掉首死记录使 dep_part
    退回 8，违背"取最早死亡 Part"的强制修正。谓词 + subject∈名册双条件防误报。

    Args:
        facts: EstablishedFacts 实例 / 其 .facts 列表 / dict 形态（容忍 None）
        character_names: 正式角色名可迭代对象（防常见词误报）

    Returns:
        {角色名: {'character': str, 'dep_part': int,
                  'earliest_record': "PartN 谓词: 事实",
                  'last_record': "PartM 谓词: 事实"}}；
        无退场事实 / 脏数据 fail-open 返回 {}。
    """
    raw = getattr(facts, "facts", None)
    if raw is None and isinstance(facts, dict):
        raw = facts.get('facts')  # work JSON 的 dict 形态（与 R6-6 探测器同款归一）
    if not isinstance(raw, (list, tuple)):
        raw = []
    names = {str(n).strip() for n in (character_names or []) if n and str(n).strip()}
    earliest: dict = {}   # subject -> (part_num, record)
    latest: dict = {}     # subject -> (part_num, record)

    def _field(obj, name, default=''):
        # dict 形态（work JSON）与 Fact 对象双兼容——纯 getattr 对 dict 恒返回
        # 默认值（dict 无属性访问），账本会静默为空
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    for f in raw:
        # 实现期修正（对 02_review §1.1.9 规格的必要偏离，验收标准 1 驱动）：
        # converge 实证林渊死亡链前段 F4_1/F6_10/F7_1 的 category 是 'event'
        # （LLM 抽取按"事件"记录死亡），只认 'character' 会漏掉首死记录、
        # dep_part 退回 8——与强制修正一（取最早死亡 Part）的意图冲突。
        # 谓词（死亡/离开/失踪/退场）+ subject∈名册角色名已足以防误报，
        # 故 category 取 ('character', 'event') 并集。
        if _field(f, "category") not in ("character", "event"):
            continue
        predicate = (_field(f, "predicate") or "").strip()
        if predicate not in DEPARTED_PREDICATES:
            continue
        subject = (_field(f, "subject") or "").strip()
        if not subject or subject not in names:
            continue
        try:
            part_num = int(_field(f, "part_num", 0) or 0)
        except (TypeError, ValueError):
            continue
        record = f"Part{part_num} {predicate}: {(_field(f, 'text') or '').strip()}".strip()
        if subject not in earliest or part_num < earliest[subject][0]:
            earliest[subject] = (part_num, record)
        if subject not in latest or part_num >= latest[subject][0]:
            latest[subject] = (part_num, record)
    return {name: {'character': name, 'dep_part': ep[0],
                   'earliest_record': ep[1], 'last_record': latest[name][1],
                   'last_dep_part': latest[name][0]}
            for name, ep in earliest.items()}


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
