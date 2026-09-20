"""
R4-1: 角色规范名注册表（Name Registry）—— Phase 2 characters 一次性冻结的权威名源。

背景：姓名约束此前只是 prompts/part_writer.txt 里一句软约束，且 sliding_window 的
【角色档案】把名字与身份/特质混排，名字无特殊地位；冒烟 A 实证 Part 1"林渊"→
Part 2"林万重"漂移被 consistency 判 P0。注册表把名字升格为"显式名册禁令"，
写作端 + 两个评审端共用同一权威名源。

设计（Reviewer Round 4 批准，正面回应 Round 1 对"实体别名归一"的拒绝理由）：
- **无合并逻辑**：不推断、不合并；不用编辑距离 / 2-gram / embedding / 相似度。
  注册表只从 Phase 2 characters 档案冻结 canonical 名，合并决策从代码中彻底移除
- 别名只以"已登记别名 / 候选"**追加展示**，不改写任何历史 fact 或正文
- 候选晋升（graphiti 式"不确定不生效"）：同一 variant 出现 ≥2 次或带非空 quote
  证据 → render 展示为"已登记别名"；单次无证据候选只记录不注入 prompt
- 数据与渲染分离：build_name_registry 纯数据、render_name_roster 纯渲染，均可单测

数据形态：
    {角色名: {"role": str, "introduced_part": 1, "aliases": [], "alias_candidates": []}}
    alias_candidates 元素：{"variant": str, "part_num": int, "evidence": str, "timestamp": str}
"""
from core.logger import get_logger

logger = get_logger('name_registry')

# 候选晋升规则：同一 variant 出现次数下限（或带非空 evidence）—— 单次无证据候选
# 只记录不注入 prompt，防止抽取侧误报把两个真实不同角色在 prompt 里绑成同一人
PROMOTION_MIN_OCCURRENCES = 2

_ROSTER_TITLE = '【角色名册——唯一正确写法（最高优先级）】'
_ROSTER_RULE = '规则：本段与【角色档案】冲突时以本段为准；禁止发明名册之外的有名有姓角色'


def build_name_registry(characters: list) -> dict:
    """从 Phase 2 characters 冻结规范名注册表（纯函数，零 LLM 成本）。

    只取 name 非空的 dict 条目；重名 / 空名跳过并 logger.info 留痕（Phase 2 质量
    问题由名册缺失暴露给评审，不用空名册掩盖）。**不推断、不合并**。

    Args:
        characters: Phase 2 plot_result['characters']（元素为 dict，字段
                    name/role/identity/core_trait/motivation/secret/arc）

    Returns:
        {角色名: {"role", "introduced_part", "aliases", "alias_candidates"}}
    """
    if not isinstance(characters, list):
        return {}
    registry: dict = {}
    for c in characters:
        if not isinstance(c, dict):
            continue
        name = (c.get('name') or '').strip()
        if not name:
            logger.info('[name_registry] 跳过无 name 字段的 characters 条目（Phase 2 质量问题）')
            continue
        if name in registry:
            # 重名不做合并（Round 1 拒绝理由的负面清单）—— 跳过并留痕
            logger.info(f'[name_registry] 跳过重名角色 "{name}"（Phase 2 质量问题，不做合并）')
            continue
        registry[name] = {
            'role': (c.get('role') or '').strip(),
            'introduced_part': 1,
            'aliases': [],
            'alias_candidates': [],
        }
    return registry


def _departed_desc(departed_track, name: str) -> str:
    """R1-E 退场账本（{角色名: 'PartN 死亡: 事实描述'}）中取该角色的退场描述。"""
    if not isinstance(departed_track, dict):
        return ''
    desc = departed_track.get(name)
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return ''


def promoted_candidates(info: dict) -> list:
    """按晋升规则筛出可展示的别名候选（同一 variant ≥2 次或带非空 evidence）。

    返回按 variant 去重后的展示条目列表（优先取带证据的首条）。
    """
    candidates = [c for c in (info.get('alias_candidates') or [])
                  if isinstance(c, dict) and (c.get('variant') or '').strip()]
    by_variant: dict = {}
    for c in candidates:
        variant = c['variant'].strip()
        by_variant.setdefault(variant, []).append(c)
    promoted = []
    for variant, group in by_variant.items():
        if len(group) >= PROMOTION_MIN_OCCURRENCES or any(
                (g.get('evidence') or '').strip() for g in group):
            promoted.append(next(
                (g for g in group if (g.get('evidence') or '').strip()), group[0]))
    return promoted


def _registered_alias_lines(registry: dict) -> list:
    """已登记别名行：正式 aliases + 晋升后的 alias_candidates（分开展示）。"""
    lines = []
    for name, info in registry.items():
        if not name or not isinstance(info, dict):
            continue
        for alias in (info.get('aliases') or []):
            alias = alias.strip() if isinstance(alias, str) else ''
            if alias:
                lines.append(f'- 已登记别名：{alias} = {name}')
        for cand in promoted_candidates(info):
            variant = cand['variant'].strip()
            part_num = cand.get('part_num', '?')
            lines.append(
                f'- 已登记别名：{variant} = {name}（Part{part_num} 误写登记；'
                f'正文仍必须使用规范名"{name}"）')
    return lines


def render_name_roster(registry: dict, departed_track: dict | None = None) -> str:
    """渲染角色名册段（纯函数，可单测；与数据分离）。

    输出结构：标题 + 每角色一行（含已登记别名/退场标注）+ 已登记别名行 + 规则行。
    空 registry 返回 ''（Phase 2 角色档案为空时不注入空段）。

    Args:
        registry: build_name_registry 的产物
        departed_track: R1-E character_state_track（{角色名: 退场描述}），
                        描述含退场谓词的条目标注"已退场（PartN）——严禁出场"
    """
    if not isinstance(registry, dict) or not registry:
        return ''
    lines = [_ROSTER_TITLE]
    for name, info in registry.items():
        if not name or not isinstance(info, dict):
            continue
        role = (info.get('role') or '').strip()
        seg = f'- {name}' + (f'（{role}）' if role else '')
        seg += f'：全书唯一正确写法是"{name}"，任何其他写法（包括读音相近、偏旁相近的名字）都是错误'
        dep = _departed_desc(departed_track, name)
        if dep:
            seg += f'；已退场（{dep}）——严禁出场'
        lines.append(seg)
    lines.extend(_registered_alias_lines(registry))
    lines.append(_ROSTER_RULE)
    return '\n'.join(lines)


def render_name_roster_for_state(state) -> str:
    """从 state 渲染名册段：registry 优先；无 registry 时退化为 characters 名列表
    （兼容 S1 未生效的旧 work JSON）。"""
    registry = getattr(state, 'name_registry', None)
    if isinstance(registry, dict) and registry:
        departed = getattr(state, 'character_state_track', None)
        return render_name_roster(
            registry, departed if isinstance(departed, dict) else None)
    characters = getattr(state, 'characters', None) or []
    names: list = []
    for c in characters:
        if isinstance(c, dict):
            n = (c.get('name') or '').strip()
            if n and n not in names:
                names.append(n)
    if not names:
        return ''
    lines = [_ROSTER_TITLE]
    for n in names:
        lines.append(f'- {n}：全书唯一正确写法是"{n}"，任何其他写法（包括读音相近、偏旁相近的名字）都是错误')
    lines.append(_ROSTER_RULE)
    return '\n'.join(lines)
