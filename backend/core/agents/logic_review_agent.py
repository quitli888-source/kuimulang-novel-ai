"""
逻辑校验Agent V5 - 检查情节逻辑、因果链、前文一致性

V5 改动（R8 合并 P0-1 + P0-2 + P1-2）：
- 强制注入"前文已确立事实清单"（state.established_facts）作为权威基线
- 输出协议改为"短 JSON 摘要（score/p0/p1/p2/pass/verdict ≤ 6 字段）"，
  杜绝 issues 数组无上限导致 JSON 截断（Bug F）
- Part 1 无前文 → 直接 pass=true, score=10
- 异常处理不再静默吞 error，记录 _error + _fallback 字段，tester 可观测
- 兼容旧字段 overall_score（向后兼容） + 新字段 score

R6-1（S1）改动：两段式明细补取 —— V5 短协议主路径逐字节不变；主解析成功且
p0_count>0 且 Part>1 时第二次小调用取有界明细（≤3 条/次、anchor 逐字校验、
计数守恒），写回 unified['issues'] 喂既有消费链（_build_revision_brief 的
l_p0 分支）。p0_count 始终是全场唯一计数权威（聚合器/劣化判据/G4 只认它）。
"""
import os
import re
from core.agents.base_agent import BaseAgent
from core.agents._helpers import sorted_part_nums  # P2-65: 取代 _sorted_part_nums 薄包装
from core.llm_client import call_llm_json
from core.config import get_json_max_tokens  # R1-C: JSON 调用显式 max_tokens 统一来源
from core.logger import get_logger
from core.prompt_loader import load_prompt

logger = get_logger('logic_review_agent')

SYSTEM_PROMPT = load_prompt("logic_review", """你是顶级小说逻辑审查专家，评分严格但公平。

## 评分规则

- **9-10分**：与前文完全一致，无任何矛盾
- **7-8分**：有 1-2 个小瑕疵但无致命矛盾
- **5-6分**：有 1-2 个 P1 级问题
- **3-4分**：有 P0 级核心矛盾（与前文直接冲突）
- **1-2分**：存在多处 P0 矛盾

## P0 级问题定义（任一即扣分）

- 角色在前面已死/已离开，后面又出现
- 物品位置/状态与前文冲突
- 角色突然知道前面不可能知道的信息
- 时间线不连续（前面在白天，突然变夜而无交代）
- 已确立的物理/世界规则被违反
- 角色名/地名/物品名与前文不一致

## P1 级问题

- 缺乏因果关系或动机不足
- 角色行为与已建立的人设轻微偏差
- 情绪过渡不自然

## 输出格式（V5 协议，**必须严格遵守**）

仅输出一个 JSON 对象，**严禁超过 6 个字段**：

```json
{
  "score": <1-10 整数>,
  "pass": <bool, score>=7 时为 true>,
  "p0_count": <int>,
  "p1_count": <int>,
  "p2_count": <int>,
  "verdict": "<一句话判断，含具体 Part 编号引用，≤ 60 字>"
}
```

## 强约束

1. 输出必须以 `{` 开始，以 `}` 结束；中间是合法 JSON；**不要任何额外解释、markdown 标题**
2. **issues 列表在主 JSON 中省略**（避免超长 JSON 被 max_tokens 截断；详细 issues 走后续 markdown 表）
3. **Part 1** 无前文基线时直接输出 `{"score":10,"pass":true,"p0_count":0,"p1_count":0,"p2_count":0,"verdict":"首部无前文基线，跳过一致性"}`
4. score 字段必须是 1-10 的整数（不接受小数、字符串）
5. 整体 JSON 长度**不超过 200 字符**——输出协议严格是"短摘要"形式
""")


# R6-1（S1）: 明细补取 prompt —— prompts/logic_review_detail.txt 文件优先，
# 内嵌 fallback 同款（R5-4 纪律：防缺文件部署行为分裂）。协议硬约束：
# ≤3 条/次（不做分页）+ anchor 必须逐字出自正文 + 字段长度上限。
DETAIL_SYSTEM_PROMPT = load_prompt("logic_review_detail", """你是顶级小说逻辑审查专家。主审查已判定本 Part 存在 P0 级逻辑矛盾，现在需要你列出问题明细。

## 输出协议（必须严格遵守）

仅输出一个 JSON 对象：

```json
{
  "issues": [
    {
      "idx": 1,
      "dimension": "<问题维度，如：物品状态/角色状态/时间线/信息越界/世界观规则/姓名地名>",
      "anchor": "<原文引文，不超过 30 字，必须逐字出自正文（从正文原样复制，不得改写）>",
      "claim": "<问题描述，不超过 50 字>",
      "conflict": "<与前文何处冲突，不超过 30 字>"
    }
  ],
  "returned": <本次实际列出的条数>,
  "total": <主审查判定的 P0 总数>
}
```

## 强约束

1. **最多列出 3 条**（P0 总数超过 3 时只列最严重的前 3 条，不得翻页、不得输出更多）
2. **anchor 必须从正文逐字复制**（客户端会做逐字校验，改写或幻觉的引文会被清空）
3. 输出必须以 `{` 开始，以 `}` 结束；不要任何额外解释、markdown 标题
4. dimension/anchor/claim/conflict 均不得超出上述长度上限
5. 只描述主审查已判定的 P0 矛盾，不得新增主审查未提及的问题；不得给出修正建议
""")

# R6-1（S1）: 明细协议字段上限（≈400 字符/3 条，12000 max_tokens 无截断风险）
_DETAIL_MAX_ITEMS = 3        # 最多 3 条/次（不做分页 —— 02_review §6.1）
_DETAIL_ANCHOR_MAX = 30      # anchor 原文引文上限
_DETAIL_CLAIM_MAX = 50       # claim 问题描述上限
_DETAIL_CONFLICT_MAX = 30    # conflict 冲突说明上限
_DETAIL_DIMENSION_MAX = 20   # dimension 维度名上限


# 用于从已被截断的 raw text 中抢救 score / p0_count 等关键字段
_FIRST_JSON_RE = re.compile(r"\{[^{}]*?(?:\{[^{}]*\}[^{}]*?)*\}", re.DOTALL)


def _coerce_score(value, default: int = 3) -> int:
    """把 LLM 返回的 score 安全转 int（容忍 str / float / None / 越界）。"""
    try:
        if isinstance(value, bool):
            return default
        n = int(round(float(value)))
        if n < 1:
            return 1
        if n > 10:
            return 10
        return n
    except Exception:
        return default


def _normalize_detail_issues(raw_issues, part_text: str, p0_count: int,
                             part_num: int) -> list:
    """R6-1（S1）: detail 响应清洗 + 归一化 + 计数守恒（纯函数，零 LLM，可单测）。

    - 非 dict 条目丢弃；缺字段补空串；anchor/claim/conflict 超长截断；
      **anchor 必须逐字出自 part_text**（模型幻觉引文是本协议最大风险，
      确定性兜底：不满足则清空 anchor 但保留 issue）；最多 3 条/次；
    - 归一化到 consistency issue 形态（让既有消费链零改动生效：l_p0 非空后
      _build_revision_brief 自动把明细写进 brief）；
    - **计数守恒**（02_review §1.1 强制修正）：明细 issues 灌入后会走
      review_aggregator._count_by_level 的 "issues 优先" 路径，补取不足会
      人为压低聚合 residual P0（G4 soundness 漏洞）。归一化后 issues 数量
      恰等于 p0_count —— 少则补占位 issue（_detail_placeholder 标记，任何
      后续基于 issue 明细的比较必须跳过占位项），多则截断到 p0_count；
      清洗后为空 → issues=[]（聚合器数值兜底，行为退化为 V5，零回归）。

    Returns:
        [issue, ...]（level=='P0' 的 consistency 形态 issue）
    """
    text = part_text or ''
    cleaned: list = []
    for item in (raw_issues or []):
        if not isinstance(item, dict):
            continue
        anchor = str(item.get('anchor') or '').strip()[:_DETAIL_ANCHOR_MAX]
        if anchor and anchor not in text:
            anchor = ''  # 幻觉引文 → 清空 anchor 但保留 issue
        cleaned.append({
            'dimension': (str(item.get('dimension') or '').strip()[:_DETAIL_DIMENSION_MAX]
                          or '逻辑审查'),
            'character': str(item.get('character') or '').strip()[:_DETAIL_DIMENSION_MAX],
            'anchor': anchor,
            'claim': str(item.get('claim') or '').strip()[:_DETAIL_CLAIM_MAX],
            'conflict': str(item.get('conflict') or '').strip()[:_DETAIL_CONFLICT_MAX],
        })
        if len(cleaned) >= _DETAIL_MAX_ITEMS:
            break  # 协议上限：最多 3 条/次（不做分页）
    if not cleaned:
        return []  # 清洗后为空 → 行为退化为 V5（聚合器数值兜底计数）
    try:
        target = int(p0_count or 0)
    except (TypeError, ValueError):
        target = 0
    if target <= 0:
        return []
    normalized: list = []
    for d in cleaned[:target]:  # 多则截断到 p0_count
        anchor = d['anchor']
        claim, conflict = d['claim'], d['conflict']
        desc = (f"{claim}（原文：“{anchor}”）冲突：{conflict}" if anchor
                else f"{claim}冲突：{conflict}")
        normalized.append({
            'level': 'P0',
            'dimension': d['dimension'],
            'character': d['character'],
            'location': (f'Part {part_num}（锚点：{anchor[:20]}）' if anchor
                         else f'Part {part_num}'),
            'description': desc,
            'suggestion': '',
        })
    # 少则补占位 issue（带 _detail_placeholder 标记，下游差集/签名比较跳过）
    real_count = len(normalized)
    while len(normalized) < target:
        normalized.append({
            'level': 'P0', 'dimension': '逻辑审查', 'character': '',
            'location': f'Part {part_num}',
            'description': f'（明细补取未覆盖：p0_count={target}，'
                           f'returned={real_count}）',
            'suggestion': '', '_detail_placeholder': True,
        })
    return normalized


def _extract_summary_from_raw(raw_text: str) -> dict:
    """R8-P0-2: 当主 JSON 解析失败时，从 raw_text 中用 regex 抓"第一段 JSON"做兜底。"""
    if not raw_text:
        return {}
    # 1) 先抓 ```json ... ``` 块
    md_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
    candidate = md_match.group(1) if md_match else None
    # 2) 抓不到就抓第一个平衡的 { ... }
    if not candidate:
        # 找第一个 {，然后按括号配对找对应的 }
        start = raw_text.find("{")
        if start == -1:
            return {}
        depth = 0
        end = -1
        for i in range(start, len(raw_text)):
            ch = raw_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            return {}
        candidate = raw_text[start:end + 1]
    # 3) 尝试解析；解析失败返回空
    import json
    try:
        obj = json.loads(candidate)
    except Exception:
        # 4) 尝试宽松修复：去掉尾随逗号
        try:
            obj = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
        except Exception:
            return {}
    return obj if isinstance(obj, dict) else {}


class LogicReviewAgent(BaseAgent):
    name = "逻辑校验Agent"
    description = "检查情节逻辑和前文一致性（V5：结构化事实基线 + 短 JSON 输出）"
    version = "5.0.0"

    # P2-65: 删除 _sorted_part_nums 薄包装；调用点直接用 sorted_part_nums(state)

    def execute(self, state, part_num: int, part_text: str) -> dict:
        self.log_start()

        outline = state.part_outline[part_num - 1] if state.part_outline else {}
        characters_info = ""
        if state.characters:
            characters_info = "\n".join(
                f"- {c['name']}({c['role']}): {c['core_trait']}, 动机:{c['motivation']}, 秘密:{c['secret']}"
                for c in state.characters
            )

        # R8-P0-1: 拼装前文基线
        prev_context = ""
        if part_num > 1 and state.part_summaries:
            prev_context = "【前文摘要】\n"
            for p_num in sorted_part_nums(state):
                if p_num < part_num:
                    prev_context += f"Part {p_num}: {state.part_summaries[str(p_num)]}\n"

        # R8-P0-1: 注入"前文已确立事实清单"（state.established_facts）
        facts_block = ""
        try:
            if hasattr(state, "build_established_facts_block"):
                facts_block = state.build_established_facts_block(part_num) or ""
        except Exception:
            facts_block = ""

        # R4-1: 角色名册段（与 facts_block 同级）—— Logic 判"姓名/身份冲突"时
        # 先看到权威名源；state 无 registry 时退化为 characters 名列表
        roster_block = ""
        try:
            from core.name_registry import render_name_roster_for_state
            roster_block = render_name_roster_for_state(state) or ""
        except Exception:
            roster_block = ""

        if part_num > 1 and part_num - 1 in state.parts:
            prev_tail = state.parts[part_num - 1][-500:]
            prev_context += f"\n【前一部分（Part {part_num-1}）结尾】\n{prev_tail}"

        has_prev = bool(prev_context or facts_block)
        prev_note = (
            "（注意：这是第一部分，没有前文，不需要检查前文一致性）"
            if not has_prev else ""
        )

        # R8-P0-1: 末尾追加"评分强约束"（让模型把"前文事实表"当成唯一基线）
        scoring_constraints = """
## 评分强约束（V5）

1. 评判 Part N 时，【前文已确立事实清单】是**唯一**权威基线；不得凭空把 Part N 内的"角色自我陈述"也当作"前文事实"
2. Part N 中出现【前文已确立事实清单】中**没有**的"已知信息" → 标 P0（信息越界）
3. Part N 中角色状态/位置/伤势与【前文已确立事实清单】不一致 → 标 P0
4. Part N 中新引入的角色名/地名/物品名与前文**不一致** → 标 P0
5. Part 1 → 直接 pass=true, score=10, p0_count=0
6. Part N (N>1) 若【前文已确立事实清单】为空且【前文摘要】也为空 → p0_count=0, 默认 score=7
"""

        user_prompt = f"""请审查Part {part_num}的逻辑一致性。

## Part规划
核心事件：{outline.get('core_event', '')}
情绪目标：{outline.get('emotion_target', '')}
与前文因果关系：{outline.get('causality', '')}

## 角色档案
{characters_info}

## 世界观
{state.world_setting}

## 伏笔任务
埋设：{outline.get('foreshadow_plant', [])}
揭晓：{outline.get('foreshadow_reveal', [])}

{facts_block}

{roster_block}
{prev_context}

## Part {part_num}正文
{part_text}

请以JSON格式输出审查结果。{prev_note}{scoring_constraints}"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                # R1-C: 显式传 max_tokens —— 此前吃默认 4000，推理模型 reasoning
                # 计入 max_tokens，空 content 会直接走降级评分。
                max_tokens=get_json_max_tokens(),
                agent=self.name,
                work_id=getattr(state, 'work_id', None),  # P1-87: per-work 计费路由
            )
            if not isinstance(result, dict):
                raise ValueError(f"Logic Agent 返回非 dict: {type(result).__name__}")

            # 兼容旧字段：把 result 规整为统一 schema
            score = _coerce_score(
                result.get("score", result.get("overall_score", 3))
            )
            p0 = int(result.get("p0_count", 0) or 0)
            p1 = int(result.get("p1_count", 0) or 0)
            p2 = int(result.get("p2_count", result.get("p2_count", 0)) or 0)
            pass_flag = bool(result.get("pass", score >= 7))
            # Part 1 强制 pass
            if part_num <= 1:
                pass_flag = True
                score = 10
                p0 = 0
                p1 = 0
                p2 = 0
            verdict = (result.get("verdict", "") or "")[:80]

            unified = {
                "score": score,
                "overall_score": score,  # 向后兼容老测试
                "pass": pass_flag,
                "p0_count": p0,
                "p1_count": p1,
                "p2_count": p2,
                "issues": result.get("issues", []),  # V5 已不输出，但保留兼容
                "verdict": verdict,
                "continuity_check": result.get("continuity_check", {
                    "character_states": "ok" if p0 == 0 else "violated",
                    "timeline": "ok" if p0 == 0 else "violated",
                    "established_facts": "ok" if p0 == 0 else "violated",
                }),
                "strengths": result.get("strengths", []),
                "_protocol": "V5",
            }

            # R6-1（S1）: 两段式明细补取 —— 条件全部满足才发起第二次小调用：
            # 主 JSON 解析成功（本路径）、p0_count>0、part_num>1（Part 1 强制
            # p0=0）、KML_LOGIC_DETAIL != '0'（env kill-switch，缺省开）。
            # 条件不满足时上方 V5 主路径逐字节不变；detail 失败（异常/非 dict）
            # 只落 issues=[] + 观测字段，行为退化为今天的 V5（零回归）。
            if p0 > 0 and part_num > 1 and os.environ.get('KML_LOGIC_DETAIL', '') != '0':
                detail = None
                try:
                    detail = call_llm_json(
                        system_prompt=DETAIL_SYSTEM_PROMPT,
                        user_prompt=user_prompt + '\n\n请列出上述 Part 的 P0 问题明细'
                                               '（最多 3 条；anchor 必须逐字出自正文）。',
                        temperature=0.2,
                        max_tokens=get_json_max_tokens(),
                        agent=self.name,
                        work_id=getattr(state, 'work_id', None),  # P1-87: per-work 计费路由
                    )
                except Exception as detail_err:
                    logger.info(f'[LogicReviewAgent] Part {part_num} 明细补取失败'
                                f'（不影响主路径）: {detail_err}')
                normalized = []
                if isinstance(detail, dict):
                    normalized = _normalize_detail_issues(
                        detail.get('issues'), part_text, p0, part_num)
                unified['issues'] = normalized
                unified['_detail_protocol'] = 'V5+detail'
                unified['_detail_returned'] = len([i for i in normalized
                                                   if not i.get('_detail_placeholder')])
                unified['_detail_total'] = p0
                logger.info(f'[LogicReviewAgent] Part {part_num} 明细补取: '
                            f'{unified["_detail_returned"]}/{p0} 条真实明细'
                            f'（issues 总数 {len(normalized)}，计数守恒）')

            self.log_done(
                f"Part {part_num} 逻辑评分(V5): {score}/10 "
                f"P0x{p0} P1x{p1} P2x{p2} pass={pass_flag}\n"
                f"verdict: {verdict}"
            )
            return unified

        except Exception as e:
            # R8-P0-2: 不再静默吞 error；尝试从原始 raw_text 抢救 score
            raw = getattr(e, "raw_text", "") or ""
            rescued = _extract_summary_from_raw(raw)
            if rescued:
                self.log_error(f"[JSON] 主解析失败已用 fallback 救回: {e}")
                score = _coerce_score(
                    rescued.get("score", rescued.get("overall_score", 3))
                )
                p0 = int(rescued.get("p0_count", 0) or 0)
                p1 = int(rescued.get("p1_count", 0) or 0)
                p2 = int(rescued.get("p2_count", 0) or 0)
                pass_flag = bool(rescued.get("pass", score >= 7))
                if part_num <= 1:
                    score = 10
                    pass_flag = True
                    p0 = p1 = p2 = 0
                return {
                    "score": score,
                    "overall_score": score,
                    "pass": pass_flag,
                    "p0_count": p0,
                    "p1_count": p1,
                    "p2_count": p2,
                    "issues": [],
                    "verdict": (rescued.get("verdict", "") or f"fallback: {e}")[:80],
                    "continuity_check": {
                        "character_states": "未检查",
                        "timeline": "未检查",
                        "established_facts": "未检查",
                    },
                    "strengths": [],
                    "_protocol": "V5",
                    "_fallback": True,
                    "_error": str(e),
                }
            # 完全失败
            self.log_error(f"[JSON] 解析完全失败: {e}")
            return {
                "score": 3,
                "overall_score": 3,
                "pass": False,
                "p0_count": 1,
                "p1_count": 0,
                "p2_count": 0,
                "issues": [],
                "verdict": f"审查失败（已降级评分）: {e}",
                "continuity_check": {
                    "character_states": "未检查",
                    "timeline": "未检查",
                    "established_facts": "未检查",
                },
                "strengths": [],
                "_protocol": "V5",
                "_fallback": True,
                "_error": str(e),
            }
