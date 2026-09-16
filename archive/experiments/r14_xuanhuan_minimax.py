"""R14: 用 MiniMax-M3 跑一篇 10 万字玄幻小说
- 30 Parts × 3500 字 = 105,000 字
- 4 阶段叙事弧（开端 1-3 → 发展 4-18 → 高潮 19-25 → 结局 26-30）
- 真实调用 minimax_m3 (https://api.minimax.cn/v1, model=MiniMax-M3)
"""
import os
import sys
import json
import time

sys.path.insert(0, 'backend')

# ====== 玄幻小说大纲（30 Parts × 4 阶段叙事弧）======
OUTLINE = [
    # 开端（Part 1-3，奠定世界观 + 主角身世 + 初始冲突）
    {"phase": "开端", "title": "废脉少年", "core_event": "十六岁少年林荒在青云宗外门弟子觉醒仪式上被测出废脉，被贬为杂役",
     "emotion_target": "压抑不甘", "key_dialogue": "我林荒今日虽废，他日必登绝顶",
     "end_hook": "深夜独居时废脉突然剧烈搏动，引出体内封印", "pacing": "慢起", "causality": "开场"},

    {"phase": "开端", "title": "血月封印", "core_event": "林荒在禁地后山意外发现一座千年古棺，棺中血月与他体内废脉共鸣",
     "emotion_target": "震撼", "key_dialogue": "你等了千年的传人，终于来了",
     "end_hook": "古棺主人传他'九幽噬天诀'，代价是三十年寿元", "pacing": "中等", "causality": "承 Part1 废脉"},

    {"phase": "开端", "title": "初露锋芒", "core_event": "林荒修炼噬天诀后第一战，击败内门弟子陈玄，打破'废脉不能修炼'的铁律",
     "emotion_target": "热血", "key_dialogue": "你说废脉不能修炼？那我今天让你看看什么叫奇迹",
     "end_hook": "青云宗主注意到林荒，要他参加三月后的宗门大比", "pacing": "快", "causality": "承 Part2 血月"},

    # 发展（Part 4-18，铺垫 + 多场战斗 + 阴谋浮现）
    {"phase": "发展", "title": "宗门大比", "core_event": "林荒在宗门大比中连胜三场，杀入决赛",
     "emotion_target": "激昂", "key_dialogue": "我林荒的目标，是第一名",
     "end_hook": "决赛对手是宗主之子楚云霄，比赛被人设局陷害", "pacing": "快", "causality": "承 Part3 锋芒"},

    {"phase": "发展", "title": "陷害风波", "core_event": "林荒被诬陷作弊，证据直指他与魔道勾结",
     "emotion_target": "愤怒压抑", "key_dialogue": "我没有勾结魔道！我林荒行的端坐的正",
     "end_hook": "师尊柳如烟独自前往宗门长老会为他作证", "pacing": "中等", "causality": "承 Part4 大比"},

    {"phase": "发展", "title": "师尊真相", "core_event": "柳如烟揭露大比背后是宗主想借机清洗异己，林荒被卷入宗门权力斗争",
     "emotion_target": "信任崩塌", "key_dialogue": "你看到的真相，只是他们想让你看到的",
     "end_hook": "林荒决定暂时离开青云宗，外出游历", "pacing": "中等", "causality": "承 Part5 陷害"},

    {"phase": "发展", "title": "血战黑风岭", "core_event": "林荒在黑风岭遭遇魔修截杀，靠噬天诀吞噬对方修为反杀",
     "emotion_target": "嗜血", "key_dialogue": "你们的修为，都归我了",
     "end_hook": "魔修临死透露'千年血棺'与青云宗某长老有关", "pacing": "快", "causality": "承 Part6 真相"},

    {"phase": "发展", "title": "古墓遗迹", "core_event": "林荒追踪线索进入一处远古大能遗迹，获得第二卷残篇'天地熔炉'",
     "emotion_target": "震撼", "key_dialogue": "这里埋葬的不是大能，而是另一个我",
     "end_hook": "遗迹守护者现身考验，留下'九幽'一脉的线索", "pacing": "中等", "causality": "承 Part7 黑风岭"},

    {"phase": "发展", "title": "异火邂逅", "core_event": "林荒在荒漠遇见异火'幽冥寒炎'，经历九死一生收服",
     "emotion_target": "炽烈", "key_dialogue": "你若不从，我便炼你万年",
     "end_hook": "异火认主，与九幽噬天诀完美契合", "pacing": "快", "causality": "承 Part8 古墓"},

    {"phase": "发展", "title": "师尊失踪", "core_event": "林荒回到青云宗，发现柳如烟已被宗主囚禁于锁妖塔",
     "emotion_target": "焦急悲愤", "key_dialogue": "如烟，等我",
     "end_hook": "宗主以此要挟林荒交出九幽噬天诀", "pacing": "快", "causality": "承 Part9 异火"},

    {"phase": "发展", "title": "锁妖塔内", "core_event": "林荒夜探锁妖塔，与柳如烟重逢得知宗主才是千年前封印血棺之人",
     "emotion_target": "震撼愤怒", "key_dialogue": "你们口中的魔道，正是你们一手造就",
     "end_hook": "宗主出现，林荒九死一生逃出锁妖塔", "pacing": "中等", "causality": "承 Part10 师尊"},

    {"phase": "发展", "title": "天剑盟主", "core_event": "林荒流亡中结识天剑盟主之女苏婉，得其庇护",
     "emotion_target": "温情", "key_dialogue": "公子若不弃，婉儿愿陪你走这一程",
     "end_hook": "天剑盟主欲招林荒为婿，但提出苛刻条件", "pacing": "中等", "causality": "承 Part11 锁妖塔"},

    {"phase": "发展", "title": "盟约交易", "core_event": "林荒提出以'协助天剑盟对抗魔道'为盟约，盟主同意但要求林荒先破敌将",
     "emotion_target": "冷静", "key_dialogue": "我可以帮你，但你也要帮我",
     "end_hook": "天剑盟的敌将正是青云宗宗主", "pacing": "中等", "causality": "承 Part12 天剑"},

    {"phase": "发展", "title": "破敌血战", "core_event": "林荒用异火 +噬天诀破了宗主布下的'九幽噬魂阵'，协助天剑盟获胜",
     "emotion_target": "燃", "key_dialogue": "今日我林荒，便替天行道",
     "end_hook": "宗主重伤逃遁，临走揭露林荒'千年血棺'主人的真实身份", "pacing": "快", "causality": "承 Part13 盟约"},

    {"phase": "发展", "title": "千年真相", "core_event": "林荒回到古墓寻找血月留下的记忆碎片，得知自己是千年被封印大能的转世",
     "emotion_target": "宿命震撼", "key_dialogue": "原来我林荒，从来都不是废脉",
     "end_hook": "血月的最后意识残留下来保护林荒", "pacing": "中等", "causality": "承 Part14 破敌"},

    {"phase": "发展", "title": "联盟集结", "core_event": "天剑盟、正道七宗、散修联盟集结，组成讨伐宗主的大军",
     "emotion_target": "壮阔", "key_dialogue": "今日，我们替天行道",
     "end_hook": "林荒任先锋，剑指青云宗", "pacing": "中等", "causality": "承 Part15 真相"},

    {"phase": "发展", "title": "决战前夜", "core_event": "大军抵达青云宗山门，林荒与柳如烟重逢，立下明日必胜的誓言",
     "emotion_target": "悲壮", "key_dialogue": "明日之后，要么一起看日出，要么一起埋骨青云",
     "end_hook": "宗主现身于青云之巅，独战天下群雄", "pacing": "中等", "causality": "承 Part16 联盟"},

    {"phase": "发展", "title": "青云决战", "core_event": "林荒率联盟与宗主于青云山巅决战，九幽噬天诀 VS 宗主万年修为",
     "emotion_target": "极致热血", "key_dialogue": "这一战，为了千年的冤屈",
     "end_hook": "宗主被逼出最后底牌——召唤远古魔神", "pacing": "快", "causality": "承 Part17 前夜"},

    # 高潮（Part 19-25，揭示全部真相 + 多重反转 + 至高对决）
    {"phase": "高潮", "title": "魔神降临", "core_event": "远古魔神降临，宗主沦为傀儡，林荒被一击重伤",
     "emotion_target": "绝望", "key_dialogue": "难道九幽一脉的传承，就要断在这里",
     "end_hook": "血月残魂在林荒识海中浮现，传授最后一招'九幽灭世'", "pacing": "快", "causality": "承 Part18 决战"},

    {"phase": "高潮", "title": "九幽灭世", "core_event": "林荒燃烧寿元发动九幽灭世，与魔神同归于尽",
     "emotion_target": "悲壮燃点", "key_dialogue": "我以我命，祭九幽",
     "end_hook": "魔神被封印，林荒倒下，生死不明", "pacing": "快", "causality": "承 Part19 魔神"},

    {"phase": "高潮", "title": "九幽之门", "core_event": "林荒在九幽之门中见到千年前的自己，得知血月即是前世的自己",
     "emotion_target": "震撼", "key_dialogue": "千年前的我等你千年，今天我们合二为一",
     "end_hook": "林荒继承前世全部修为，重生归来", "pacing": "中等", "causality": "承 Part20 灭世"},

    {"phase": "高潮", "title": "浴血重生", "core_event": "林荒从血泊中站起，千年修为归来，实力登顶大陆巅峰",
     "emotion_target": "热血沸腾", "key_dialogue": "千年的债，今日该还了",
     "end_hook": "宗主惊觉林荒修为突破至'大帝'境界", "pacing": "快", "causality": "承 Part21 九幽之门"},

    {"phase": "高潮", "title": "大帝之战", "core_event": "林荒以大帝之姿与宗主展开最终对决，撕裂天地",
     "emotion_target": "极致震撼", "key_dialogue": "宗主，你可曾想过千年后会败于一个废脉少年",
     "end_hook": "宗主绝望之际，揭露自己也是九幽一脉的叛徒", "pacing": "快", "causality": "承 Part22 重生"},

    {"phase": "高潮", "title": "叛徒真相", "core_event": "宗主是千年前九幽一脉的二弟子，因嫉妒血月天资而陷害其封印",
     "emotion_target": "真相大白", "key_dialogue": "师兄，对不起，我来晚了",
     "end_hook": "宗主被林荒击杀，临死悔恨交加", "pacing": "中等", "causality": "承 Part23 大帝之战"},

    {"phase": "高潮", "title": "恩怨了结", "core_event": "林荒解除宗主控制，救出被囚禁的青云宗弟子",
     "emotion_target": "释然", "key_dialogue": "从今往后，青云宗不再有压迫",
     "end_hook": "林荒将宗主遗留的'九幽令'交给苏婉", "pacing": "中等", "causality": "承 Part24 叛徒"},

    # 结局（Part 26-30，尘埃落定 + 新篇章）
    {"phase": "结局", "title": "重整河山", "core_event": "林荒扶持新任宗主，重建青云宗秩序",
     "emotion_target": "平静担当", "key_dialogue": "青云宗的未来，不该系于一人之念",
     "end_hook": "柳如烟决定陪伴林荒修行", "pacing": "慢收", "causality": "承 Part25 了结"},

    {"phase": "结局", "title": "情定三生", "core_event": "林荒、苏婉、柳如烟三人在青云之巅立下'九幽之约'",
     "emotion_target": "温情", "key_dialogue": "这一世，我们不再分离",
     "end_hook": "三人决定共闯天外天，寻找传说中更高的境界", "pacing": "慢收", "causality": "承 Part26 重整"},

    {"phase": "结局", "title": "故人重逢", "core_event": "林荒在天外天秘境见到前世师尊留下的遗言",
     "emotion_target": "感动", "key_dialogue": "徒儿，你终于走到这一步了",
     "end_hook": "师尊遗言揭露天外天更高境界的秘密", "pacing": "慢收", "causality": "承 Part27 情定"},

    {"phase": "结局", "title": "新章开启", "core_event": "林荒决定走出这片大陆，去看看更广阔的世界",
     "emotion_target": "希望", "key_dialogue": "这片大陆的传说已尽，天外天的故事才刚刚开始",
     "end_hook": "全剧终，林荒、苏婉、柳如烟踏上新的征程", "pacing": "慢收", "causality": "承 Part28 重逢"},

    {"phase": "结局", "title": "九幽传说", "core_event": "尾声：千年后，一位少年在古棺前觉醒，仿佛看见了千年前的自己",
     "emotion_target": "余韵悠长", "key_dialogue": "九幽一脉，传承不绝",
     "end_hook": "系列完结，新篇可期", "pacing": "慢收", "causality": "承 Part29 新章"},
]

# ====== 玄幻角色 ======
CHARACTERS = [
    {"name": "林荒", "role": "主角", "identity": "青云宗外门弟子，被测出废脉",
     "core_trait": "坚韧不屈、隐忍热血", "motivation": "打破'废脉不能修炼'的铁律",
     "secret": "体内封印着千年前被陷害的大能'血月'，是他的转世身"},
    {"name": "血月", "role": "前世身", "identity": "千年前九幽一脉大弟子，被师弟陷害封印",
     "core_trait": "深沉冷静", "motivation": "等待传人重振九幽一脉",
     "secret": "其真身为九幽大帝，被封印千年修为仍存"},
    {"name": "柳如烟", "role": "师尊", "identity": "青云宗内门长老",
     "core_trait": "温婉坚韧、深明大义", "motivation": "保护林荒，揭露宗门黑幕",
     "secret": "曾与血月是旧识，知晓千年血棺真相"},
    {"name": "宗主（楚云霄）", "role": "反派", "identity": "青云宗宗主，楚云霄之父",
     "core_trait": "深沉阴鸷、野心勃勃", "motivation": "夺取九幽噬天诀，成就大帝",
     "secret": "千年前九幽一脉二弟子，因嫉妒血月天资而陷害其封印"},
    {"name": "苏婉", "role": "盟友/爱人", "identity": "天剑盟主之女",
     "core_trait": "温柔聪慧、外柔内刚", "motivation": "帮助林荒对抗宗主",
     "secret": "天剑盟主独女，掌握天剑盟核心资源"},
    {"name": "陈玄", "role": "前期反派", "identity": "青云宗内门弟子",
     "core_trait": "傲慢、嫉妒心强", "motivation": "打压废脉，维护'天才'地位",
     "secret": "受宗主暗中指使，是宗主棋局中的第一颗子"},
]

WORLD = (
    "九州大陆，以宗门林立、修真文明为根基。青云宗为正道七宗之首，千年前曾有一位大能'血月'创下九幽一脉，"
    "后因师弟陷害被封印于血棺之内，宗门内传其传承为'九幽噬天诀'。"
    "千年后，血月残魂寄于一名废脉少年体内，揭开一段跨越千年的复仇与传承。"
)

FORESHADOWING = [
    {"id": "F1", "content": "废脉体内异常", "plant_part": 1, "reveal_part": 2, "hint": "林荒觉醒仪式上废脉异常搏动"},
    {"id": "F2", "content": "血月传承", "plant_part": 2, "reveal_part": 14, "hint": "古棺主人传授九幽噬天诀"},
    {"id": "F3", "content": "宗主身份伏笔", "plant_part": 5, "reveal_part": 23, "hint": "柳如烟'你们口中的魔道正是你们一手造就'"},
    {"id": "F4", "content": "天外天存在", "plant_part": 28, "reveal_part": 29, "hint": "师尊遗言揭露更高境界"},
    {"id": "F5", "content": "异火与噬天诀契合", "plant_part": 9, "reveal_part": 14, "hint": "幽冥寒炎认主"},
    {"id": "F6", "content": "千年血棺", "plant_part": 7, "reveal_part": 15, "hint": "魔修临死透露血棺与宗门有关"},
]

print("=" * 70)
print("R14: MiniMax-M3 跑玄幻长篇 10 万字")
print("=" * 70)
print(f"机制: window_size=6 + RAG + 三层 facts + 强承接")
print(f"目标字数: 105,000 (30 Parts × 3,500)")
print(f"主角: 林荒 / 反派: 宗主 / 师尊: 柳如烟 / 盟友: 苏婉")
print(f"伏笔: {len(FORESHADOWING)} 条（千年血月/九幽传承/宗主身份）")
print(f"阶段: 开端 3 + 发展 15 + 高潮 7 + 结局 5 = 30 Parts")

# ====== 强制切到 minimax_m3 provider ======
from core.config import load_llm_config, save_llm_config, ActiveLLMConfig
cfg = load_llm_config()
print(f"\n当前 active_provider_id: {cfg.active_provider_id}")
if cfg.active_provider_id != 'minimax_m3':
    cfg.active_provider_id = 'minimax_m3'
    cfg.per_agent_enabled = False
    # 同步写盘
    import json
    with open('data/llm_config.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['active_provider_id'] = 'minimax_m3'
    data['per_agent_enabled'] = False
    with open('data/llm_config.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  → 已切换到 minimax_m3")
else:
    print(f"  → 已是最小 m3，跳过")


class State:
    def __init__(self, characters, world, outline, foreshadowing, window, vector_store=None):
        self.characters = characters
        self.world_setting = world
        self.part_outline = outline
        self.foreshadowing = foreshadowing
        self.window = window
        self.vector_store = vector_store
        self.parts = {}
        self.part_summaries = {}
        self.current_plot_state = ""
        self.character_state_track = {}
        self.established_facts = None

    def get_part_context(self, part_num):
        try:
            kwargs = dict(
                characters=self.characters,
                world_setting=self.world_setting,
                outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None,
            )
            import inspect
            sig = inspect.signature(self.window.build)
            if 'vector_store' in sig.parameters:
                kwargs['vector_store'] = self.vector_store
                kwargs['vector_query'] = self.part_outline[part_num - 1].get('core_event', '') if part_num <= len(self.part_outline) else ''
                kwargs['vector_top_k'] = 3
            return self.window.build(part_num, **kwargs)
        except Exception as e:
            return ""


OUTPUT_FILE = 'r14_xuanhuan_minimax_m3_novel.json'

# ====== R12 升级机制 ======
from core.sliding_window import SlidingWindow
from core.vector_store import VectorStore
from core.agents.part_writer_agent import PartWriterAgent
from core.cost_tracker import get_tracker

os.environ['ENABLE_VECTOR_RAG'] = '1'

window = SlidingWindow(window_size=6)
vector_store = VectorStore() if os.environ.get('ENABLE_VECTOR_RAG') == '1' else None
state = State(CHARACTERS, WORLD, OUTLINE, FORESHADOWING, window, vector_store)
writer = PartWriterAgent()
tracker = get_tracker()

# 断点续跑
import os.path
existing = {}
if os.path.exists(OUTPUT_FILE):
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        if existing.get('parts'):
            print(f"✓ 检测到旧进度：{len(existing['parts'])} Part 已完成，从 Part {len(existing['parts'])+1} 继续")
            for pn_str, pt in existing['parts'].items():
                pn = int(pn_str)
                summary = existing.get('part_summaries', {}).get(pn_str, pt[:200])
                state.parts[pn_str] = pt
                state.part_summaries[pn_str] = summary
                window.add_part(pn, pt, summary)
                if vector_store:
                    vector_store.add(pn, pt)
    except Exception as e:
        print(f"⚠ 读取旧进度失败: {e}")

start_idx = len(state.parts) + 1
total_chars_so_far = sum(len(t) for t in state.parts.values())
print(f"\n从 Part {start_idx} 开始，目标 Part 30")
print(f"已累计字数: {total_chars_so_far:,}")

# 清除 client 缓存
from core import llm_client
llm_client._client_cache.clear()
llm_client._client = None

phase_count = {"开端": 0, "发展": 0, "高潮": 0, "结局": 0}

for part_num in range(start_idx, 31):
    phase = OUTLINE[part_num - 1]['phase']
    phase_count[phase] += 1
    print(f"\n{'='*70}")
    print(f"[{time.strftime('%H:%M:%S')}] Part {part_num}/30 [{phase}] {OUTLINE[part_num-1]['title']}")
    print(f"{'='*70}")

    t0 = time.time()
    try:
        result = writer.execute(state, part_num)
        dt = time.time() - t0
        text = result.get('content', '') if isinstance(result, dict) else result
        if not text or len(text) < 100:
            print(f"  ✗ Part {part_num} 内容过短（{len(text) if text else 0} 字），跳过")
            continue
        word_count = len(text)
        total_chars_so_far += word_count
        print(f"  ✓ 完成: {dt:.1f}s, 字数={word_count}, 累计={total_chars_so_far:,}")

        state.parts[str(part_num)] = text
        state.part_summaries[str(part_num)] = text[:200]
        window.add_part(part_num, text, text[:200])
        if vector_store:
            vector_store.add(part_num, text)

        save_data = {
            "outline": OUTLINE,
            "characters": CHARACTERS,
            "world_setting": WORLD,
            "foreshadowing": FORESHADOWING,
            "parts": state.parts,
            "part_summaries": state.part_summaries,
            "total_chars": total_chars_so_far,
            "phase_count": phase_count,
            "last_completed": part_num,
            "llm_provider": "minimax_m3 (https://api.minimax.cn/v1)",
            "model": "MiniMax-M3",
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"  ✗ Part {part_num} 异常: {type(e).__name__}: {str(e)[:200]}")
        with open(OUTPUT_FILE + '.err.log', 'a', encoding='utf-8') as f:
            f.write(f"Part {part_num} FAIL: {e}\n")
        continue

total = sum(len(t) for t in state.parts.values())
summary = tracker.get_summary()
print(f"\n{'='*70}")
print(f"R14 完成统计")
print(f"{'='*70}")
print(f"已完成 Part: {len(state.parts)}/30")
print(f"总字数: {total:,} / 105,000 目标")
print(f"总 LLM 调用: {summary.get('total_calls')}")
print(f"总 token: {summary.get('total_tokens'):,}")
print(f"预估成本: ¥{summary.get('estimated_cost', 0):.4f}")
print(f"叙事弧: 开端={phase_count['开端']}/3 发展={phase_count['发展']}/15 高潮={phase_count['高潮']}/7 结局={phase_count['结局']}/5")