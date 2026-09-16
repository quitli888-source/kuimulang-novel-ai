"""
情节规划Agent V3 - 生成Part制创作蓝图

V3改动：
- Prompt外部化到 prompts/plot_planner.txt
"""
from core.agents.base_agent import BaseAgent
from core.llm_client import call_llm_json
from core.config import PART_COUNT, PART_WORD_MIN, PART_WORD_MAX, TARGET_WORD_COUNT
from core.prompt_loader import load_prompt
from core.logger import get_logger
logger = get_logger('plot_planner_agent')
SYSTEM_PROMPT = load_prompt('plot_planner', f'你是一位顶尖的番茄小说架构师，精通快节奏短篇的结构设计。\n\n## 核心原则：Part制创作\n\n番茄小说短篇是完整文章，不需要明显的章节分割。我们用{PART_COUNT}个"Part"来规划节奏，\n每个Part约{PART_WORD_MIN}-{PART_WORD_MAX}字，全文总计约{TARGET_WORD_COUNT}字。Part之间用"---"分隔即可，\n不需要章节标题，过渡要自然流畅。\n\n## 结构模板（{PART_COUNT} Part版）\n\n- Part 1（约{int(PART_WORD_MIN * 0.9)}字）：**开篇炸裂** - 200字内抛出核心冲突，建立悬念，锁定读者\n- Part 2（约{int(PART_WORD_MIN * 1.0)}字）：**冲突升级** - 第一个反转或重大发现，读者好奇心被彻底勾起\n- Part 3（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**深入与转折** - 情感线展开，出现情感高潮或第二次反转\n- Part 4（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**危机爆发** - 冲突达到顶点，主角陷入绝境\n- Part 5（约{int((PART_WORD_MIN + PART_WORD_MAX) // 2)}字）：**终极高潮** - 真相揭晓，情感爆发，最强烈的冲击\n- Part 6（约{int(PART_WORD_MIN * 0.8)}字）：**余韵收尾** - 情感释放，留下回味，巧妙收束\n\n## 严格规则\n\n1. **因果链**：每个Part的核心事件必须由前一个Part的事件直接引发，不能凭空出现\n2. **字数控制**：每个Part严格在{PART_WORD_MIN}-{PART_WORD_MAX}字之间，全文字数控制在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}字\n3. **禁止时间跳跃**：除非情节绝对必要，不要出现"三天后""一个月后"这类跳转\n4. **线性叙事**：Part内部事件必须按时间线性推进\n5. **黄金开篇**：Part 1的前200字必须包含核心冲突或重大悬念\n6. **每Part必有钩子**：每个Part的结尾必须让读者无法停止\n7. **伏笔闭环**：所有伏笔必须在后半部分揭晓\n\n请以JSON格式输出所有内容。')

class PlotPlannerAgent(BaseAgent):
    name = '情节规划Agent'
    description = '生成世界观、角色档案、伏笔表和Part规划'

    def execute(self, state) -> dict:
        self.log_start()
        elements = state.core_elements
        genre = state.market_positioning
        user_prompt = f"""请为以下故事设计Part制创作蓝图：\n\n## 故事信息\n【原始灵感】{state.inspiration}\n【主角】{elements.get('protagonist', {}).get('identity', '未知')}，{elements.get('protagonist', {}).get('core_trait', '')}\n【核心冲突】{elements.get('conflict', {}).get('core_conflict', '未知')}\n【内心矛盾】{elements.get('conflict', {}).get('internal_conflict', '未知')}\n【外部冲突】{elements.get('conflict', {}).get('external_conflict', '未知')}\n【主题】{elements.get('theme', '未知')}\n【情感基调】{elements.get('emotion_tone', '未知')}\n【世界观关键词】{', '.join(elements.get('world_keywords', []))}\n【钩子点】{elements.get('hook_points', [])}\n\n## 市场定位\n【题材】{genre.get('genre_primary', '')} > {genre.get('genre_secondary', '')}\n【开篇建议】{genre.get('writing_strategy', {}).get('opening_hook', '')}\n【节奏建议】{genre.get('writing_strategy', {}).get('pace_advice', '')}\n【结尾建议】{genre.get('writing_strategy', {}).get('ending_advice', '')}\n\n## 要求\n- 总共{PART_COUNT}个Part，每Part约{PART_WORD_MIN}-{PART_WORD_MAX}字\n- 全文总字数控制在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}字\n- 设计5-8个伏笔，形成完整闭环\n- 每个Part之间必须有明确的因果关系\n\n请输出完整JSON：\n{{\n    "world_setting": "世界观设定（200字以内精炼版本，只写与故事直接相关的规则）",\n    "characters": [\n        {{\n            "name": "角色名",\n            "role": "主角/核心配角/反派",\n            "identity": "身份（一句话）",\n            "core_trait": "核心特质（一句话）",\n            "motivation": "核心动机（一句话）",\n            "secret": "不为人知的秘密（一句话）",\n            "arc": "人物弧线（从X变成Y，一句话）"\n        }}\n    ],\n    "foreshadowing": [\n        {{\n            "id": "伏笔编号如F1",\n            "content": "伏笔内容（一句话）",\n            "plant_part": 埋设Part编号,\n            "reveal_part": 揭晓Part编号,\n            "hint": "埋设技巧（如何不露痕迹）"\n        }}\n    ],\n    "part_outline": [\n        {{\n            "part": 1,\n            "title": "Part标题（2-4字）",\n            "phase": "开篇炸裂/冲突升级/深入转折/危机爆发/终极高潮/余韵收尾",\n            "word_count": {PART_WORD_MIN + 200},\n            "core_event": "核心事件（具体发生了什么，50字以内）",\n            "emotion_target": "情绪目标（读者读完后应该有什么感受，15字以内）",\n            "key_dialogue": "关键对白（1-2句点睛对白）",\n            "end_hook": "结尾钩子（用什么吸引读者继续，20字以内）",\n            "causality": "与前一个Part的因果关系（Part1写'起始'）",\n            "pacing": "节奏描述（如'快-慢-快'或'持续紧张'）",\n            "foreshadow_plant": ["本章埋设的伏笔编号"],\n            "foreshadow_reveal": ["本章揭晓的伏笔编号"]\n        }}\n    ]\n}}\n\n注意：\n- part_outline必须是长度为{PART_COUNT}的数组\n- characters至少3个角色，每个角色的字段必须简洁（一句话）\n- foreshadowing至少5个伏笔，plant_part < reveal_part\n- 每个Part的word_count加起来应在{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}之间\n- causality字段非常重要，必须写清Part之间的因果驱动关系\n- Part 1的causality写"起始"\n- pacing字段描述本Part内部的节奏变化"""
        try:
            result = call_llm_json(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.6, max_tokens=8000, agent=self.name)
            outline = result.get('part_outline', [])
            if len(outline) < PART_COUNT:
                logger.info(f'  [警告] Part规划只有{len(outline)}个，不足{PART_COUNT}个')
            total_words = sum((p.get('word_count', 0) for p in outline))
            if total_words < int(TARGET_WORD_COUNT * 0.85) or total_words > int(TARGET_WORD_COUNT * 1.15):
                logger.info(f'  [警告] Part规划总字数{total_words}，目标{int(TARGET_WORD_COUNT * 0.95)}-{int(TARGET_WORD_COUNT * 1.05)}')
            self.log_done(f"世界观: {result['world_setting'][:50]}...\n角色: {len(result.get('characters', []))}个\n伏笔: {len(result.get('foreshadowing', []))}个\nPart: {len(outline)}个, 规划总字数: {total_words}")
            return result
        except Exception as e:
            self.log_error(str(e))
            return {'world_setting': '世界观设定解析失败', 'characters': [{'name': '主角', 'role': '主角', 'identity': '未知', 'core_trait': '', 'motivation': '', 'secret': '', 'arc': ''}], 'foreshadowing': [], 'part_outline': []}