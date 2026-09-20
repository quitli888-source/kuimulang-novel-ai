"""
角色一致性检查Agent V4 - 角色跨Part一致性检查

V4改动：
- 错误处理不再默认通过，改为降级评分+标记P0
"""
from core.agents.base_agent import BaseAgent
from core.agents._helpers import sorted_part_nums  # P2-65
from core.llm_client import call_llm_json
from core.config import get_json_max_tokens  # R1-C: JSON 调用显式 max_tokens 统一来源
from core.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("consistency_review", """你是一位专注于角色一致性的文学审校专家。

你的唯一任务是：检查小说中角色在不同Part之间是否保持一致。

## 检查维度（只关注角色）

1. **角色名称一致性（强制工序，必须逐步执行）**
   - 第一步·名册逐一核对：对上方【角色名册】中的**每一个**角色，在正文中找出它的**所有**实际写法，逐个列出"写法→出现次数"；只有名册规范名本身算正确写法
   - 第二步·判定：任何不在名册内的写法，只要上下文表明指该角色（包括规范名的截断、缩写、增删字），即为 P0 名称不一致；名册"已登记别名"行中的写法是合法简称，不计为错误
   - 第三步·引证：每个 P0/P1 issue 的 description 必须同时包含四项——被质疑的原文引文（≤40 字）、名册规范名、正文实际写法、该写法在正文中的出现次数
   - 是否凭空出现了角色档案中没有的角色？

2. **角色状态连续性**
   - 角色在前面已死/昏迷/离开，后面是否又出现了？
   - 角色的物理位置是否突然变化（前文在A地，后文突然在B地）？
   - 角色是否突然受伤或痊愈，没有过渡？

3. **角色知识合理性**
   - 角色是否知道了他不可能知道的信息？
   - 角色是否遗忘了他应该知道的信息？

4. **角色性格一致性**
   - 角色是否突然性格大变（与前面建立的人设矛盾）？
   - 角色的说话方式是否突然改变？

5. **角色关系一致性**
   - 角色之间的关系是否前后矛盾（如陌生人突然变老友）？
   - 角色之间的情感是否突兀变化？

## 问题分级

- **P0（致命）**：角色在前文已死/离开，后文又出现；角色名字不一致
- **P1（严重）**：角色知识范围越界；性格突变；关系矛盾
- **P2（轻微）**：对话风格轻微不一致；可优化的小细节

## 输出格式

以JSON格式输出：
{
    "pass": true/false,
    "overall_score": 1-10,
    "issues": [
        {
            "level": "P0/P1/P2",
            "dimension": "名称一致性/状态连续性/知识合理性/性格一致性/关系一致性",
            "character": "涉及的角色名",
            "location": "问题位置（Part X中的XX段落）",
            "description": "问题描述",
            "suggestion": "修正建议"
        }
    ],
    "character_states": {
        "角色名": "当前状态描述（位置、身体状况、心理状态）"
    },
    "verdict": "一句话判断"
}""")


class ConsistencyReviewAgent(BaseAgent):
    name = "角色一致性检查Agent"
    description = "检查角色在多个Part之间的一致性"

    def execute(self, state, part_num: int, part_text: str) -> dict:
        """
        检查当前Part的角色一致性。

        Args:
            state: StoryState
            part_num: 当前Part编号
            part_text: 当前Part正文

        Returns:
            dict: 一致性检查结果
        """
        self.log_start()

        # 第一Part不需要检查一致性（没有前文）
        if part_num <= 1:
            self.log_done(f"Part {part_num} 是第一部分，跳过角色一致性检查")
            return {
                "pass": True, "overall_score": 10, "issues": [],
                "character_states": {}, "verdict": "第一部分，无需检查"
            }

        # 构建角色档案信息
        characters_info = ""
        if state.characters:
            characters_info = "\n".join(
                f"- {c['name']}({c['role']}): {c['identity']}, 特质:{c['core_trait']}, "
                f"动机:{c['motivation']}, 秘密:{c['secret']}"
                for c in state.characters
            )

        # R7-P0-2: 用 int keys 排序后比较
        prev_summaries = ""
        for p_num in sorted_part_nums(state):
            if p_num < part_num:
                prev_summaries += f"Part {p_num}: {state.part_summaries[str(p_num)]}\n"

        # R4-1: 角色名册段 + 名称判定指引 —— 名册是唯一权威名源，把评审从
        # "印象式比对"变为"对照名册可判定"（state 无 registry 时退化为
        # characters 名列表，兼容旧 work JSON）
        roster_block = ""
        try:
            from core.name_registry import render_name_roster_for_state
            roster_block = render_name_roster_for_state(state) or ""
        except Exception:
            roster_block = ""
        name_guidance = ""
        if roster_block:
            name_guidance = (
                "\n## 名称一致性判定指引（R4-1）\n"
                "- 名字不在名册内即为 P0 名称不一致（无名字路人除外）\n"
                "- 名册外写法若上下文表明与名册角色为同一人，同样判 P0，"
                "并在 description 中给出两种写法\n"
            )

        # 前文结尾
        prev_tail = ""
        final_draft = self._get_final_draft(state) or {}
        if (part_num - 1) in final_draft:
            prev_text = final_draft[part_num - 1]
            prev_tail = prev_text[-800:]

        # R4-6: 系统预检警告段（consistency_flags：退场再现 + 伏笔未回收，有则展示，
        # ≤10 行）—— 只加展示段，不动 schema、不动评分逻辑、不动其他分支
        # R5-S6（P1-3 止损）: foreshadow_unrevealed 不再注入评审 prompt —— R4-6 实证
        # 7/7 误报（Phase2 content 是剧情描述句，前 10 字子串命中结构上不可能成功），
        # 继续注入只会干扰评审判断（20 Part 还会挤占 ≤10 行上限把退场 flag 截断掉）；
        # work.json 留痕与 verify 汇总行不变（观测通道保留，只是不污染评审上下文）。
        flags_block = ''
        flags = getattr(state, 'consistency_flags', None) or []
        if isinstance(flags, list) and flags:
            flag_lines = []
            visible = [fl for fl in flags
                       if isinstance(fl, dict) and fl.get('type') != 'foreshadow_unrevealed']
            for fl in visible[:10]:
                if fl.get('character'):
                    flag_lines.append(
                        f"- Part {fl.get('part', '?')} 已退场角色 {fl.get('character')} 出现 "
                        f"{fl.get('count', '?')} 次（退场记录: {fl.get('departed_record', '?')}）")
            if flag_lines:
                flags_block = ('\n## ⚠ 系统预检警告（确定性复检发现的可疑点，请重点核对；'
                               '回忆/他人提及形式合法，勿仅因此判 P0）\n'
                               + '\n'.join(flag_lines) + '\n')

        user_prompt = f"""请检查Part {part_num}的角色一致性。

## 角色档案
{characters_info}

{roster_block}{name_guidance}
## 前文剧情摘要
{prev_summaries}

## 前一部分（Part {part_num - 1}）结尾
{prev_tail}

## Part {part_num} 正文
{part_text}

请以JSON格式输出角色一致性检查结果。重点关注：
- 角色是否前后名字一致
- 已死/已离开的角色是否再次出现
- 角色是否知道了不可能知道的信息
- 角色性格是否突变{flags_block}"""

        try:
            result = call_llm_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                # R1-C: 显式传 max_tokens（默认 4000 会被推理模型 reasoning 吃光）
                max_tokens=get_json_max_tokens(),
                agent=self.name,
                work_id=getattr(state, 'work_id', None),  # P1-87: per-work 计费路由
            )

            p0_count = len([i for i in result.get("issues", []) if i.get("level") == "P0"])
            p1_count = len([i for i in result.get("issues", []) if i.get("level") == "P1"])

            self.log_done(
                f"Part {part_num} 一致性评分: {result.get('overall_score', 0)}/10\n"
                f"问题: P0x{p0_count} P1x{p1_count}\n"
                f"判定: {result.get('verdict', '')}"
            )
            return result

        except Exception as e:
            self.log_error(str(e))
            return {
                "pass": False, "overall_score": 3,
                "issues": [{"level": "P0", "dimension": "自动审查", "character": "全局",
                            "location": f"Part {part_num}", "description": f"一致性检查Agent执行失败: {e}",
                            "suggestion": "需人工核查此Part的角色一致性"}],
                "character_states": {}, "verdict": f"检查失败（已降级评分）: {e}",
            }

    def _get_final_draft(self, state):
        """安全获取终稿"""
        return getattr(state, 'final_draft', {})
