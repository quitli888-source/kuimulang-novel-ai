"""
情节规划Agent V3 - 生成Part制创作蓝图

V3改动：
- Prompt外部化到 prompts/plot_planner.txt
"""
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm_json
import core.config as _config
from core.config import PART_COUNT, PART_WORD_MIN, PART_WORD_MAX, TARGET_WORD_COUNT, get_task_max_tokens
from core.prompt_loader import load_prompt
from core.logger import get_logger
logger = get_logger('plot_planner_agent')


def normalize_outline_word_counts(outline: list, target_total: int) -> list:
    """R2-4 防线 2: 大纲字数传导的确定性归一化（纯函数，只动 word_count 数字字段）。

    背景：exemplar 锚定（`word_count: PART_WORD_MIN+200` ≈ 2,700）让模型照抄低目标，
    Round 1 实证 Part 目标 2,700-4,645、预计总量 84k-92k，贴/低于 G2 下限 90,000。
    此前 Σ 偏离目标只 logger.info 警告、不动作。

    两段式算法（拒绝等比例抬升 —— 那会按比例保留模型原有的异常低值）：
      1) 先把所有低于 floor = max(PART_WORD_MIN, target_total//len*7//10) 的 Part 抬到 floor；
      2) 再把残差（target_total − Σ）按各 Part 当前值比例分摊给"非地板"Part
         （超配时反向压缩，仍不低于 floor；若全部 Part 都被抬到 floor 而无"非地板"
         Part 可摊，则退化为按当前值比例分摊给全部 Part —— 否则 Σ 会永远达不到目标）；
      3) 最后 clamp 到 [PART_WORD_MIN, PART_WORD_MAX]，并回收 clamp 吞掉的残差
         （对仍有上行空间的 Part 按剩余空间比例再分摊，最多 3 轮）。

    PART_WORD_MIN/MAX 经 core.config 模块属性**动态读取**（reload_config 后即时
    生效）—— 本函数在 Phase 2 运行时调用，不能依赖 import 时冻结的快照。

    Args:
        outline: part_outline 列表（dict 原地修改 word_count 后原样返回）
        target_total: 全文目标字数（运行时单例 target_word_count）

    Returns:
        归一化后的 outline（同一列表对象）
    """
    parts = [p for p in outline if isinstance(p, dict)]
    if not parts or target_total <= 0:
        return outline
    word_min, word_max = _config.PART_WORD_MIN, _config.PART_WORD_MAX
    floor = max(word_min, target_total // len(parts) * 7 // 10)
    # 第一段：低于 floor 的抬到 floor（绝对低值不按比例保留）
    for p in parts:
        wc = p.get('word_count')
        if not isinstance(wc, (int, float)) or isinstance(wc, bool) or wc < floor:
            p['word_count'] = floor
    total = sum(p['word_count'] for p in parts)
    residual = target_total - total
    if residual:
        # 第二段：残差按当前值比例分摊/压缩（只动非地板 Part，避免把刚抬起来的压回去）
        flexible = [p for p in parts if p['word_count'] > floor]
        if not flexible:
            # 全部 Part 都在地板上（模型整体锚低）—— 退化为按当前值比例分摊给全部
            flexible = parts
        flex_total = sum(p['word_count'] for p in flexible)
        if flexible and flex_total > 0:
            for p in flexible:
                adjusted = int(p['word_count'] + residual * p['word_count'] / flex_total)
                p['word_count'] = max(floor, adjusted)
    # clamp 到合法区间（不改 PART_WORD_MAX 硬上限语义）
    for p in parts:
        p['word_count'] = max(word_min, min(word_max, int(p['word_count'])))
    # 第三段（兜底）: clamp 到 word_max 会吞掉部分残差（典型：残差集中摊给个别
    # Part 被上限截断）—— 对仍有上行空间的 Part 按剩余空间比例回收，最多 3 轮。
    for _ in range(3):
        shortfall = target_total - sum(p['word_count'] for p in parts)
        if shortfall <= 0:
            break
        growable = [p for p in parts if p['word_count'] < word_max]
        room = sum(word_max - p['word_count'] for p in growable)
        if not growable or room <= 0:
            break
        take = min(shortfall, room)
        for p in growable:
            share = int(take * (word_max - p['word_count']) / room)
            p['word_count'] = min(word_max, p['word_count'] + share)
    return outline
SYSTEM_PROMPT = load_prompt('plot_planner', f'你是一位顶尖的番茄小说架构师，精通快节奏短篇的结构设计。\n\n## 核心原则：Part制创作\n\n番茄小说短篇是完整文章，不需要明显的章节分割。我们用{PART_COUNT}个"Part"来规划节奏，\n每个Part约{PART_WORD_MIN}-{PART_WORD_MAX}字，全文总计约{TARGET_WORD_COUNT}字。Part之间用"---"分隔即可，\n不需要章节标题，过渡要自然流畅。\n\n## 结构模板（{PART_COUNT} Part版）\n\n- Part 1（约{int(PART_WORD_MIN * 0.9)}字）：**开篇炸裂** - 200字内抛出核心冲突，建立悬念，锁定读者\n- Part 2（约{int(PART_WORD_MIN * 1.0)}字）：**冲突升级** - 第一个反转或重大发现，读者好奇心被彻底勾起\n- Part 3（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**深入与转折** - 情感线展开，出现情感高潮或第二次反转\n- Part 4（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**危机爆发** - 冲突达到顶点，主角陷入绝境\n- Part 5（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**终极高潮** - 真相揭晓，情感爆发，最强烈的冲击\n- Part 6（约{int(PART_WORD_MIN * 0.8)}字）：**余韵收尾** - 情感释放，留下回味，巧妙收束\n\n## 严格规则\n\n1. **因果链**：每个Part的核心事件必须由前一个Part的事件直接引发，不能凭空出现\n2. **字数控制**：每个Part严格在{PART_WORD_MIN}-{PART_WORD_MAX}字之间，全文字数控制在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}字\n3. **禁止时间跳跃**：除非情节绝对必要，不要出现"三天后""一个月后"这类跳转\n4. **线性叙事**：Part内部事件必须按时间线性推进\n5. **黄金开篇**：Part 1的前200字必须包含核心冲突或重大悬念\n6. **每Part必有钩子**：每个Part的结尾必须让读者无法停止\n7. **伏笔闭环**：所有伏笔必须在后半部分揭晓\n\n请以JSON格式输出所有内容。')

class PlotPlannerAgent(BaseAgent):
    name = '情节规划Agent'
    description = '生成世界观、角色档案、伏笔表和Part规划'

    def execute(self, state) -> dict:
        self.log_start()
        elements = state.core_elements
        genre = state.market_positioning
        user_prompt = f"""请为以下故事设计Part制创作蓝图：\n\n## 故事信息\n【原始灵感】{state.inspiration}\n【主角】{elements.get('protagonist', {}).get('identity', '未知')}，{elements.get('protagonist', {}).get('core_trait', '')}\n【核心冲突】{elements.get('conflict', {}).get('core_conflict', '未知')}\n【内心矛盾】{elements.get('conflict', {}).get('internal_conflict', '未知')}\n【外部冲突】{elements.get('conflict', {}).get('external_conflict', '未知')}\n【主题】{elements.get('theme', '未知')}\n【情感基调】{elements.get('emotion_tone', '未知')}\n【世界观关键词】{', '.join(elements.get('world_keywords', []))}\n【钩子点】{elements.get('hook_points', [])}\n\n## 市场定位\n【题材】{genre.get('genre_primary', '')} > {genre.get('genre_secondary', '')}\n【开篇建议】{genre.get('writing_strategy', {}).get('opening_hook', '')}\n【节奏建议】{genre.get('writing_strategy', {}).get('pace_advice', '')}\n【结尾建议】{genre.get('writing_strategy', {}).get('ending_advice', '')}\n\n## 要求\n- 总共{PART_COUNT}个Part，每Part约{PART_WORD_MIN}-{PART_WORD_MAX}字\n- 全文总字数控制在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}字\n- **角色名长度**：主要角色名控制在 2-4 字；≥5 字的名称必须在 characters 中给出 aliases（1-2 个口头简称，2-4 字），否则长名在正文中极易被缩写成名册外写法\n- 每个Part的word_count不得低于总均值{TARGET_WORD_COUNT // PART_COUNT}的70%（≥{TARGET_WORD_COUNT // PART_COUNT * 7 // 10}字），各Part之和必须落在[{int(TARGET_WORD_COUNT * 0.95)}, {int(TARGET_WORD_COUNT * 1.05)}]\n- 设计5-8个伏笔，形成完整闭环\n- 每个Part之间必须有明确的因果关系\n\n请输出完整JSON：\n{{\n    "world_setting": "世界观设定（200字以内精炼版本，只写与故事直接相关的规则）",\n    "characters": [\n        {{\n            "name": "角色名",\n            "role": "主角/核心配角/反派",\n            "identity": "身份（一句话）",\n            "core_trait": "核心特质（一句话）",\n            "motivation": "核心动机（一句话）",\n            "secret": "不为人知的秘密（一句话）",\n            "arc": "人物弧线（从X变成Y，一句话）",\n            "aliases": ["口头简称/别名（2-4 字；name≥5 字时必填，否则空数组）"]\n        }}\n    ],\n    "foreshadowing": [\n        {{\n            "id": "伏笔编号如F1",\n            "content": "伏笔内容（一句话）",\n            "plant_part": 埋设Part编号,\n            "reveal_part": 揭晓Part编号,\n            "hint": "埋设技巧（如何不露痕迹）"\n        }}\n    ],\n    "part_outline": [\n        {{\n            "part": 1,\n            "title": "Part标题（2-4字）",\n            "phase": "开篇炸裂/冲突升级/深入转折/危机爆发/终极高潮/余韵收尾",\n            "word_count": {max(PART_WORD_MIN, TARGET_WORD_COUNT // PART_COUNT)},\n            "core_event": "核心事件（具体发生了什么，50字以内）",\n            "emotion_target": "情绪目标（读者读完后应该有什么感受，15字以内）",\n            "key_dialogue": "关键对白（1-2句点睛对白）",\n            "end_hook": "结尾钩子（用什么吸引读者继续，20字以内）",\n            "causality": "与前一个Part的因果关系（Part1写'起始'）",\n            "pacing": "节奏描述（如'快-慢-快'或'持续紧张'）",\n            "foreshadow_plant": ["本章埋设的伏笔编号"],\n            "foreshadow_reveal": ["本章揭晓的伏笔编号"]\n        }}\n    ]\n}}\n\n注意：\n- part_outline必须是长度为{PART_COUNT}的数组\n- characters至少3个角色，每个角色的字段必须简洁（一句话）\n- foreshadowing至少5个伏笔，plant_part < reveal_part\n- 每个Part的word_count加起来应在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}之间（总和 = 总目标，不得低于总目标95%）\n- 每个Part的word_count不得低于总均值{TARGET_WORD_COUNT // PART_COUNT}的70%（即≥{TARGET_WORD_COUNT // PART_COUNT * 7 // 10}字）；高潮Part（危机爆发/终极高潮）可上浮至均值的120%；开场Part不得低于均值的70%\n- causality字段非常重要，必须写清Part之间的因果驱动关系\n- Part 1的causality写"起始"\n- pacing字段描述本Part内部的节奏变化"""
        try:
            # R2-5: 硬编码 8000 → 任务级单点 get_task_max_tokens('json_facts')
            # （默认 8000，20 Part 大纲 JSON 体积大，6000 曾被 reasoning 吃尽）
            result = call_llm_json(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.6, max_tokens=get_task_max_tokens('json_facts'), agent=self.name, work_id=getattr(state, 'work_id', None))
            outline = result.get('part_outline', [])
            if len(outline) < PART_COUNT:
                logger.info(f'  [警告] Part规划只有{len(outline)}个，不足{PART_COUNT}个')
            # R2-4 防线 2 挂载点 A: 先归一化再判断 —— 此前 Σ 偏离目标只警告不动作，
            # exemplar 锚定的低目标会直接传导成 G2 字数不足（Round 1 实证预计总量 84k-92k）
            before_total = sum((p.get('word_count', 0) for p in outline if isinstance(p, dict)))
            outline = normalize_outline_word_counts(outline, TARGET_WORD_COUNT)
            total_words = sum((p.get('word_count', 0) for p in outline if isinstance(p, dict)))
            if total_words != before_total:
                logger.info(f'  [归一化] Part规划总字数 {before_total} -> {total_words}（目标 {TARGET_WORD_COUNT}）')
            if total_words < int(TARGET_WORD_COUNT * 0.95) or total_words > int(TARGET_WORD_COUNT * 1.05):
                logger.info(f'  [警告] Part规划总字数{total_words}，目标{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}')
            self.log_done(f"世界观: {result['world_setting'][:50]}...\n角色: {len(result.get('characters', []))}个\n伏笔: {len(result.get('foreshadowing', []))}个\nPart: {len(outline)}个, 规划总字数: {total_words}")
            return result
        except Exception as e:
            self.log_error(str(e))
            return {'world_setting': '世界观设定解析失败', 'characters': [{'name': '主角', 'role': '主角', 'identity': '未知', 'core_trait': '', 'motivation': '', 'secret': '', 'arc': ''}], 'foreshadowing': [], 'part_outline': []}