"""R11 10万字生产脚本：30 Parts × 3500 字 = 105,000 字 完整小说
4 阶段叙事弧：开端(1-3) → 发展(4-18) → 高潮(19-25) → 结局(26-30)
每 Part 实时落盘，防止中断丢失。
"""
import os
import sys
import json
import time

sys.path.insert(0, 'backend')

from core.sliding_window import SlidingWindow
from core.agents.part_writer_agent import PartWriterAgent
from core.cost_tracker import get_tracker

# ====== 30 Parts 大纲（4 阶段叙事弧）======
# 开端(1-3) 发展(4-18) 高潮(19-25) 结局(26-30)
OUTLINE = [
    # 开端
    {"phase": "开端", "title": "匿名包裹", "core_event": "林枫收到三年前沈渊失踪案关联的匿名包裹",
     "emotion_target": "悬疑不安", "key_dialogue": "三年前的那场雨还没停",
     "end_hook": "包裹里的红墨书签指向民国旧宅", "pacing": "慢起", "causality": "开场"},
    {"phase": "开端", "title": "民国旧宅", "core_event": "林枫潜入三年前沈渊失踪的民国旧宅，发现密室",
     "emotion_target": "紧张", "key_dialogue": "墙上红字写着你的名字",
     "end_hook": "密室里发现一箱档案，档案署名是钟楼", "pacing": "中等", "causality": "承 Part1 红墨书签"},
    {"phase": "开端", "title": "旧书商接头", "core_event": "林枫与旧书商老板娘接头，获得铜书签和名单",
     "emotion_target": "紧张戒备", "key_dialogue": "红墨名单里有你师父的名字",
     "end_hook": "陈锋的人已经盯上林枫", "pacing": "中等", "causality": "承 Part2 钟楼档案"},
    # 发展 (4-18)
    {"phase": "发展", "title": "钟楼首探", "core_event": "林枫夜探钟楼地下室，发现三年前的案卷",
     "emotion_target": "震撼", "key_dialogue": "沈渊没死，他换了身份",
     "end_hook": "地下室有人来过，地上有血", "pacing": "中等", "causality": "承 Part3 名单"},
    {"phase": "发展", "title": "血字线索", "core_event": "林枫追踪血字找到组织外围成员老陈",
     "emotion_target": "紧绷", "key_dialogue": "老陈上个月被灭口了，你查不到他",
     "end_hook": "老陈临死前塞给林枫一张纸条", "pacing": "中等", "causality": "承 Part4 血"},
    {"phase": "发展", "title": "纸条真相", "core_event": "纸条揭示沈渊三年前是主动潜伏",
     "emotion_target": "震惊复杂", "key_dialogue": "你师父让我告诉你，他没死，他在等",
     "end_hook": "组织已锁定林枫的位置", "pacing": "中等", "causality": "承 Part5 老陈"},
    {"phase": "发展", "title": "陈锋追杀", "core_event": "陈锋带人围堵林枫于废弃仓库",
     "emotion_target": "绝望", "key_dialogue": "把芯片交出来，给你全尸",
     "end_hook": "沈渊从暗道现身救下林枫", "pacing": "快", "causality": "承 Part6 锁定"},
    {"phase": "发展", "title": "搭档重逢", "core_event": "沈渊揭面，三年潜伏真相大白",
     "emotion_target": "悲壮", "key_dialogue": "三年了，我终于等到你查到这里",
     "end_hook": "组织的下一个目标是警署档案室", "pacing": "中等", "causality": "承 Part7 沈渊"},
    {"phase": "发展", "title": "证据搜集", "core_event": "二人联手搜集组织洗钱证据",
     "emotion_target": "冷静", "key_dialogue": "芯片里的账目是扳倒他们的关键",
     "end_hook": "警署内部有人泄密", "pacing": "中等", "causality": "承 Part8 档案室"},
    {"phase": "发展", "title": "内鬼初现", "core_event": "林枫发现档案室张勇副队长形迹可疑",
     "emotion_target": "压抑", "key_dialogue": "张勇签字的三份案卷都失踪了",
     "end_hook": "张勇似乎察觉到被调查", "pacing": "中等", "causality": "承 Part9 泄密"},
    {"phase": "发展", "title": "暗中较量", "core_event": "张勇开始反侦察，林枫被迫转入地下",
     "emotion_target": "紧张", "key_dialogue": "副队长不是我们的人",
     "end_hook": "张勇调取林枫的停职档案", "pacing": "中等", "causality": "承 Part10 内鬼"},
    {"phase": "发展", "title": "旧友反目", "core_event": "林枫的警校同学李明被张勇拉拢",
     "emotion_target": "愤怒", "key_dialogue": "林枫，停手吧，你斗不过他们",
     "end_hook": "李明偷偷给了林枫一份名单", "pacing": "中等", "causality": "承 Part11 反侦察"},
    {"phase": "发展", "title": "组织反击", "core_event": "组织策划制造一起命案栽赃林枫",
     "emotion_target": "危机", "key_dialogue": "明天的码头会有一具尸体",
     "end_hook": "沈渊截获了行动指令", "pacing": "快", "causality": "承 Part12 名单"},
    {"phase": "发展", "title": "码头截杀", "core_event": "林枫沈渊在码头阻止了组织的栽赃行动",
     "emotion_target": "激战", "key_dialogue": "李明已经被他们灭了口",
     "end_hook": "陈锋亲自带人赶到", "pacing": "快", "causality": "承 Part13 栽赃"},
    {"phase": "发展", "title": "组织头目", "core_event": "陈锋与林枫首次正面交锋",
     "emotion_target": "对峙", "key_dialogue": "林枫，你师父三年前就该死",
     "end_hook": "陈锋手腕上的蝎子刺青是关键", "pacing": "快", "causality": "承 Part14 截杀"},
    {"phase": "发展", "title": "真相一角", "core_event": "沈渊揭示陈锋的哥哥是组织头目",
     "emotion_target": "恍然大悟", "key_dialogue": "陈锋不是头目，他哥哥才是",
     "end_hook": "哥哥的代号是'乌鸦'", "pacing": "中等", "causality": "承 Part15 陈锋"},
    {"phase": "发展", "title": "布局反攻", "core_event": "二人决定从乌鸦入手，林枫假装投诚",
     "emotion_target": "隐忍", "key_dialogue": "我要见乌鸦，这是唯一的路",
     "end_hook": "乌鸦同意见林枫", "pacing": "中等", "causality": "承 Part16 乌鸦"},
    {"phase": "发展", "title": "潜入虎穴", "core_event": "林枫赴约见到乌鸦，发现乌鸦的真实身份",
     "emotion_target": "震惊", "key_dialogue": "乌鸦的真实身份令人难以置信",
     "end_hook": "林枫发出暗号给沈渊", "pacing": "中等", "causality": "承 Part17 投诚"},
    # 高潮 (19-25)
    {"phase": "高潮", "title": "身份揭露", "core_event": "乌鸦身份曝光，林枫暴露",
     "emotion_target": "危机四伏", "key_dialogue": "你是沈渊的徒弟，三年前就该一起死",
     "end_hook": "组织全员围捕林枫", "pacing": "快", "causality": "承 Part18 乌鸦"},
    {"phase": "高潮", "title": "警署对峙", "core_event": "沈渊带警力包围组织据点",
     "emotion_target": "激战", "key_dialogue": "钟楼所有出口都被我们封了",
     "end_hook": "陈锋劫持人质", "pacing": "快", "causality": "承 Part19 暴露"},
    {"phase": "高潮", "title": "人质危机", "core_event": "陈锋以人质要挟，要求见乌鸦",
     "emotion_target": "紧张", "key_dialogue": "让乌鸦来，否则我就杀人",
     "end_hook": "乌鸦现身，陈锋崩溃", "pacing": "快", "causality": "承 Part20 围捕"},
    {"phase": "高潮", "title": "兄弟反目", "core_event": "乌鸦与陈锋兄弟对峙，真相曝光",
     "emotion_target": "震撼", "key_dialogue": "三年前的事我们都被骗了",
     "end_hook": "组织二号人物叛变", "pacing": "快", "causality": "承 Part21 乌鸦"},
    {"phase": "高潮", "title": "二号现身", "core_event": "组织二号张勇现身，揭示真正主谋",
     "emotion_target": "剧变", "key_dialogue": "你们都只是棋子，真正的主谋是另一个人",
     "end_hook": "主谋的真实身份超出所有人预料", "pacing": "快", "causality": "承 Part22 张勇"},
    {"phase": "高潮", "title": "终极真相", "core_event": "真正的主谋是已退休的原市局局长",
     "emotion_target": "震撼到无言", "key_dialogue": "原市局局长是二十年前所有案件的主谋",
     "end_hook": "原市局局长已逃", "pacing": "快", "causality": "承 Part23 张勇"},
    {"phase": "高潮", "title": "追逃", "core_event": "林枫沈渊追捕原市局局长",
     "emotion_target": "燃", "key_dialogue": "他跑不出这座城",
     "end_hook": "原市局局长在钟楼顶层被捕", "pacing": "快", "causality": "承 Part24 真相"},
    {"phase": "高潮", "title": "钟楼终战", "core_event": "钟楼顶层终极对决",
     "emotion_target": "悲壮", "key_dialogue": "你们永远不会赢",
     "end_hook": "原市局局长被制服", "pacing": "快", "causality": "承 Part25 追逃"},
    # 结局 (26-30)
    {"phase": "结局", "title": "尘埃落定", "core_event": "案件全面告破，组织覆灭",
     "emotion_target": "平静", "key_dialogue": "沈渊的名字终于可以写回档案",
     "end_hook": "林枫重回警队", "pacing": "慢收", "causality": "承 Part26 终战"},
    {"phase": "结局", "title": "恢复警籍", "core_event": "林枫的停职处分被撤销",
     "emotion_target": "温暖", "key_dialogue": "欢迎归队，林枫",
     "end_hook": "林枫去看望沈渊的家人", "pacing": "慢收", "causality": "承 Part27 尘埃"},
    {"phase": "结局", "title": "故人探访", "core_event": "林枫去沈渊家，见到沈母",
     "emotion_target": "悲而不伤", "key_dialogue": "阿姨，沈渊是好警察",
     "end_hook": "沈母拿出沈渊的遗物", "pacing": "慢收", "causality": "承 Part28 恢复"},
    {"phase": "结局", "title": "遗物信件", "core_event": "沈渊三年前留下的信被发现",
     "emotion_target": "感动", "key_dialogue": "林枫，如果你读到这里，案子已经结了",
     "end_hook": "信中揭示沈渊的真正心愿", "pacing": "慢收", "causality": "承 Part29 探访"},
    {"phase": "结局", "title": "雨过天晴", "core_event": "新案件再起，林枫带新人继续前行",
     "emotion_target": "希望", "key_dialogue": "下一案，我们一起",
     "end_hook": "全剧终，新章开启", "pacing": "慢收", "causality": "承 Part30 遗物"},
]

CHARACTERS = [
    {"name": "林枫", "role": "主角", "identity": "前刑警，三年前停职",
     "core_trait": "执拗、敏锐、不放弃", "motivation": "追查三年前搭档失踪案真相",
     "secret": "他收到过搭档的匿名警告，从未放弃过"},
    {"name": "沈渊", "role": "沈渊的师傅 / 失踪搭档", "identity": "原刑侦队长，三年前失踪",
     "core_trait": "冷静、缜密、大义", "motivation": "为破获大案主动潜伏在犯罪组织内部",
     "secret": "他的失踪是主动安排，三年后与林枫重逢"},
    {"name": "陈锋", "role": "反派执行官", "identity": "组织执行官",
     "core_trait": "狠辣、狡诈、忠犬", "motivation": "维护组织利益，灭口所有知情者",
     "secret": "他是组织二号人物乌鸦的亲弟弟"},
    {"name": "乌鸦", "role": "组织头目", "identity": "组织头目，陈锋之兄",
     "core_trait": "深谋远虑、城府极深", "motivation": "维持组织运转，洗钱",
     "secret": "真实身份是林枫警校时期的教官"},
    {"name": "张勇", "role": "内鬼", "identity": "档案室副队长",
     "core_trait": "伪装、贪财", "motivation": "被组织收买",
     "secret": "三年前就是他签署沈渊的'叛逃'结论"},
    {"name": "李明", "role": "警校同学", "identity": "现役警员",
     "core_trait": "摇摆、最终良心", "motivation": "在正义与利益间挣扎",
     "secret": "曾被组织短暂拉拢，最终选择帮助林枫"},
]

WORLD = ("江南雨城，三年前一场雨夜刑警队长沈渊失踪，警署定性为'叛逃'。"
         "近年以旧书商公会为掩护的情报网活跃，暗号用红墨水书写。"
         "雨城背后潜伏着一个以钟楼为据点、由'老教官'建立的庞大犯罪组织。")

FORESHADOWING = [
    {"id": "F1", "content": "红墨书签", "plant_part": 1, "reveal_part": 4, "hint": "包裹里的红色书签"},
    {"id": "F2", "content": "密室档案", "plant_part": 2, "reveal_part": 5, "hint": "地下室铁箱里的档案"},
    {"id": "F3", "content": "钟楼", "plant_part": 2, "reveal_part": 4, "hint": "档案署名指向钟楼"},
    {"id": "F4", "content": "陈锋手腕蝎子刺青", "plant_part": 14, "reveal_part": 22, "hint": "陈锋手腕上的纹身"},
    {"id": "F5", "content": "乌鸦真实身份", "plant_part": 16, "reveal_part": 19, "hint": "林枫的教官"},
    {"id": "F6", "content": "张勇签字", "plant_part": 10, "reveal_part": 23, "hint": "档案室的签字"},
]


class State:
    def __init__(self, characters, world, outline, foreshadowing, window):
        self.characters = characters
        self.world_setting = world
        self.part_outline = outline
        self.foreshadowing = foreshadowing
        self.window = window
        self.parts = {}
        self.part_summaries = {}
        self.current_plot_state = ""
        self.character_state_track = {}
        self.established_facts = None

    def get_part_context(self, part_num):
        return self.window.build(
            part_num,
            characters=self.characters,
            world_setting=self.world_setting,
            outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None,
        )


# 更新 .env 使用新 key
print("=" * 70)
print("R11 10万字生产脚本 — 30 Parts × 3500 字 = 105,000 字")
print("=" * 70)

# 直接设置新 key 到环境（覆盖 .env）
NEW_KEY = 'dfJjLgI755TGpRvd4YKCg1C2BYcubVn9MRfYZgvAS7BfWXYcCszGUCbecFwsfKuv'
os.environ['STEP_API_KEY'] = NEW_KEY

# 写 .env（覆盖旧 key）
with open('.env', 'w', encoding='utf-8') as f:
    f.write(f"""# ============================================================
# 奎木狼AI小说创作系统 V7 - 配置文件
# ============================================================
STEP_API_KEY={NEW_KEY}
STEP_BASE_URL=https://api.stepfun.com/step_plan/v1
STEP_MODEL=step-3.7-flash
STEP_JSON_MODEL=step-3.7-flash
""")
print("✓ .env 已更新为新 API key")

# 实时落盘的文件
OUTPUT_FILE = 'r11_100k_novel.json'
LOG_FILE = 'r11_100k_log.txt'

# 重新加载（确保新 key 生效）
import importlib
import core.config as _cfg
importlib.reload(_cfg)

window = SlidingWindow(window_size=3)
state = State(CHARACTERS, WORLD, OUTLINE, FORESHADOWING, window)
writer = PartWriterAgent()
tracker = get_tracker()

# 检查是否有旧进度（支持断点续跑）
import os.path
existing = {}
if os.path.exists(OUTPUT_FILE):
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        if existing.get('parts'):
            print(f"✓ 检测到旧进度：{len(existing['parts'])} Part 已完成，从 Part {len(existing['parts'])+1} 继续")
            state.parts = existing.get('parts', {})
            state.part_summaries = existing.get('part_summaries', {})
            for pn, pt in state.parts.items():
                window.add_part(int(pn), pt, state.part_summaries.get(pn, ''))
    except Exception as e:
        print(f"⚠ 读取旧进度失败: {e}")

start_idx = len(state.parts) + 1
total_chars_so_far = sum(len(t) for t in state.parts.values())

print(f"\n从 Part {start_idx} 开始，目标 Part 30")
print(f"已累计字数: {total_chars_so_far:,}")
print(f"目标字数: 105,000（30 Parts × 3,500）")

# 阶段计数
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
            print(f"  ✗ Part {part_num} 生成内容过短（{len(text) if text else 0} 字），跳过")
            continue
        word_count = len(text)
        total_chars_so_far += word_count
        print(f"  ✓ 完成: {dt:.1f}s, 字数={word_count}, 累计={total_chars_so_far:,}")

        state.parts[str(part_num)] = text
        state.part_summaries[str(part_num)] = text[:200]
        window.add_part(part_num, text, text[:200])

        # 每 Part 实时落盘
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
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"  ✗ Part {part_num} 异常: {type(e).__name__}: {str(e)[:200]}")
        # 保存当前进度
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"Part {part_num} FAIL: {e}\n")
        continue

# 总结
total = sum(len(t) for t in state.parts.values())
summary = tracker.get_summary()
print(f"\n{'='*70}")
print(f"完成统计")
print(f"{'='*70}")
print(f"已完成 Part 数: {len(state.parts)}/30")
print(f"总字数: {total:,} / 105,000 目标")
print(f"总 LLM 调用: {summary.get('total_calls')}")
print(f"总 token: {summary.get('total_tokens'):,}")
print(f"预估成本: ¥{summary.get('estimated_cost', 0):.4f}")
print(f"叙事弧: 开端={phase_count['开端']}/3 发展={phase_count['发展']}/15 高潮={phase_count['高潮']}/7 结局={phase_count['结局']}/5")
