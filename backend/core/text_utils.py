"""
P2-20: 文本截断辅助函数 —— 统一管理 [:N] + '...' 模板散落各处的反模式。

约定：
  - truncate(text, n, suffix='…') -> str
  - 长度 <= n 时原样返回，不加 suffix（避免 'short...' 误导用户）
  - 默认 suffix='…'（单字符省略号），兼容中文排版
  - 所有 max-length 模板都走这个函数；禁止再内联 text[:N] + '...'

P0 修复（2026-09-18 反凑字数）：
  - 新增 strip_padding_chars(text) 共享给 part_writer_agent + style_optimizer_agent，
    把模型在字数压力下批量堆叠的省略号 / 破折号强制收敛。
  - 数据佐证：r14 minimax-m3 长篇 Part 19-30 破折号密度 19-49/1k，远超合法用法阈值。
"""
import re
from typing import Optional


def truncate(text: Optional[str], n: int = 200, suffix: str = '…') -> str:
    """截断 text 到 n 字符以内，超过时附加 suffix。

    Args:
        text: 原始文本；None 时返回空串
        n: 最大保留字符数
        suffix: 超过时附加的后缀，默认 '…'

    Returns:
        截断后的字符串
    """
    if not text:
        return ''
    if len(text) <= n:
        return text
    return text[:n] + suffix


def truncate_with_ellipsis(text: Optional[str], n: int = 200) -> str:
    """变体：固定用 ASCII '...' 作 suffix，便于日志/前端渲染。"""
    return truncate(text, n=n, suffix='...')


# =====================================================================
# P0 反凑字数清洗（2026-09-18）
# 背景：r14_xuanhuan_minimax_m3 长篇 Part 19-30 破折号密度 19-49/1k 字符，
#       典型注水模式：
#         "小子——\n声音低沉：\n'有一个人——'\n'还在等着你——'。"
#       或整段以 "——\n——\n——" 堆叠。
# 设计：合法用法（"……半晌"或1个 `——` 间隔）保留；注水强制收敛。
# =====================================================================

ELLIPSIS_RUN_MAX = 2          # 单段连续 `……` 上限（保留最多 2 个）
ELLIPSIS_DENSITY_MAX = 5      # 单 Part 每千字 `……` 上限（与门禁一致）
EMDASH_RUN_MAX = 2            # 单段连续 `——` 上限
EMDASH_DENSITY_MAX = 6        # 单 Part 每千字 `——` 上限（与门禁一致）

# =====================================================================
# R2-1（2026-09-20）ASCII 变体纳入 + 门禁口径单点统一
# 背景（Round 1 失败实证）：prompt 只禁 Unicode 后模型 100% 逃逸到 ASCII
#   `...`/`--`（Part 1 = 24 个 ASCII `...`、Part 2 = 39 个），旧正则只匹配
#   Unicode → 清洗器是彻底空操作；且旧 Pass 2/3 按"出现次数"计、门禁
#   density_check 按"字符/非重叠对"计（`……`=2 单位、`--`=1 单位），
#   两把尺子导致"清洗干净却门禁失败"。
# 现在两侧共用本文件的单点定义（ellipsis_units / emdash_units /
# ELLIPSIS_RUN_RE / EMDASH_RUN_RE），门禁直接 import，杜绝口径漂移。
# =====================================================================

# 门禁（verify_step5_longform.density_check）run 判定模式 —— 逐字一致，勿漂移
ELLIPSIS_RUN_RE = r'…{3,}|\.{3,}'      # 3+ 连 U+2026 或 3+ 连 ASCII 点 = 一个 run
EMDASH_RUN_RE = r'——{2,}|-{2,}'        # 3+ 连 U+2014 或 2+ 连 ASCII 连字符 = 一个 run
_ELLIPSIS_RUN_RE = re.compile(ELLIPSIS_RUN_RE)
_EMDASH_RUN_RE = re.compile(EMDASH_RUN_RE)
# Pass 1 标准收敛：Unicode run 收敛到门禁 run 定义之下；ASCII run 归一化为
# 门禁单位中性的单符号（`\.{3,}`→单 `…`：3-5 点=1 单位→1 字符=1 单位，6+ 点=2
# 单位→1；`-{2,}`→`——`：`--`=1 单位→`——`=1 单位）。绝不做跨形态 1:1 归一化
# （`...`→`……` 会让门禁密度翻倍）。
_ELLIPSIS_STD_RE = re.compile(r'…{3,}')          # 3+ 连 U+2026 → '……'
_ELLIPSIS_ASCII_STD_RE = re.compile(r'\.{3,}')   # 3+ 连 ASCII 点 → 单 '…'
_EMDASH_STD_RE = re.compile(r'——{2,}')           # 3+ 连 U+2014 → '——'
_EMDASH_ASCII_STD_RE = re.compile(r'-{2,}')      # 2+ 连 ASCII 连字符 → '——'
# Pass 2 强力收敛（密度超限后启用）：3+ → 1，ASCII 并入同族、替换串同形态
_ELLIPSIS_AGGRESSIVE_RE = re.compile(r'(?:…{3,}|\.{3,})')
_EMDASH_AGGRESSIVE_RE = re.compile(r'(?:——{2,}|-{2,})')
# Pass 3 预算修剪用的"任意长度 run"枚举（含合法的 2 连），ASCII 与 Unicode 同族
_ELLIPSIS_ANY_RE = re.compile(r'…+|\.{3,}')
_EMDASH_ANY_RE = re.compile(r'——+|-{2,}')
# 对话串联模式：连续两行末尾都是 `——` —— 典型"小子——\n声音低沉：\n'有一个人——'" 注水
_DIALOG_DASH_RE = re.compile(r'——\s*\n\s*——')


def ellipsis_units(text: str) -> int:
    """省略号密度单位（门禁同款口径）：每个 `…` 计 1，每个 ASCII `...` 计 1。

    count 为非重叠计数：`……` = 2 单位、`...` = 1 单位、`....` = 1 单位。
    """
    return text.count('…') + text.count('...')


def emdash_units(text: str) -> int:
    """破折号密度单位（门禁同款口径）：`——` 与 ASCII `--` 各计 1（非重叠）。"""
    return text.count('——') + text.count('--')


def _ellipsis_run_units(run: str) -> int:
    """单个 ellipsis run 的门禁单位成本：N 个 U+2026 = N；N 个 ASCII 点 = N//3。"""
    return run.count('…') + len(run) // 3


def _emdash_run_units(run: str) -> int:
    """单个 emdash run 的门禁单位成本：N 个 U+2014/ASCII 连字符 = N//2（非重叠对）。"""
    return len(run) // 2


def _trim_runs_to_budget(text: str, runs, budget_units: int, unit_of) -> str:
    """按"门禁单位预算"修剪 run：保留出现顺序靠前的 run 直到预算用尽，其余整段删除。

    比"保留前 N 次"更准——单位预算直接对齐 density_check 的口径，`……`（2 单位）
    不会被当成 1 单位而超发。删除按整个 run 的区间（不逐字符），避免把合法的
    `……` 切成贴着正文的孤岛。
    """
    kept_units = 0
    to_drop = []
    for m in runs:
        cost = unit_of(m.group(0))
        if kept_units + cost <= budget_units:
            kept_units += cost
        else:
            to_drop.append(m)
    return _drop_runs(text, to_drop) if to_drop else text


def strip_padding_chars(text: Optional[str]) -> str:
    """把模型在字数压力下批量堆叠的 `……` / `——`（含 ASCII `...` / `--` 变体）强制收敛。

    三遍扫描（R2-1 起 ASCII 与 Unicode 同族处理，密度/预算全部走门禁单位口径）：
      1. 标准收敛（阈值对齐门禁 run 定义）：3+ 连 U+2026 → `……`；3+ 连 ASCII 点 →
         单 `…`；3+ 连 U+2014 → `——`；2+ 连 ASCII 连字符 → `——`。循环到稳定，
         保证"清洗后不存在任何门禁 run"是构造性事实，而非密度触发的运气。
      2. 密度检测：单 Part `……` / `——` 密度仍超阈值（ellipsis_units/emdash_units
         口径，与 density_check 逐字一致）→ 强力收敛 3+ → 1，并收敛对话串联。
      3. **字符预算**：仍超阈值则按"保留前 N 个单位、删除其余"修剪。删除会缩短
         文本、分母变小、密度可能反弹（构造例：5,000 字/40 单位 → 删 15 字后
         25 单位 = 5.01/1k 仍 FAIL），故循环收敛：删完按同一口径重算，仍超则
         再删；前 5 轮保留 RUN_MAX 地板（防误伤合法用法），之后去掉地板硬剪。

    Args:
        text: 模型输出的原始文本（None 时返回空串）。

    Returns:
        清洗后的文本。合法文学用法（"……半晌才说" 或 "他说——然后停住"）保留；
        注水用法（连续 3+ 个省略号、对话串联 `——\n` 多行、整段全 `——` 堆叠、
        ASCII `...`/`--` 变体）被强制收敛。
    """
    if not text:
        return ''
    # Pass 1: 标准收敛（循环到稳定：ASCII→Unicode 归一化可能与相邻 U+2014/U+2026
    # 拼成新的 3+ run，需再收敛一轮；实测 ≤3 轮稳定，8 轮为安全上限）
    for _ in range(8):
        new_text = _ELLIPSIS_STD_RE.sub('……' * ELLIPSIS_RUN_MAX, text)
        new_text = _ELLIPSIS_ASCII_STD_RE.sub('…', new_text)
        new_text = _EMDASH_STD_RE.sub('——' * EMDASH_RUN_MAX, new_text)
        new_text = _EMDASH_ASCII_STD_RE.sub('——', new_text)
        if new_text == text and not (_ELLIPSIS_RUN_RE.search(text) or _EMDASH_RUN_RE.search(text)):
            break
        text = new_text
    # Pass 2: 密度检测 → 强力收敛（口径统一为门禁的 ellipsis_units/emdash_units；
    # 此前省略号按字符计、破折号按次数计，同一函数两套单位）
    n_chars = max(len(text), 1)
    if ellipsis_units(text) * 1000 / n_chars > ELLIPSIS_DENSITY_MAX:
        text = _ELLIPSIS_AGGRESSIVE_RE.sub('…', text)
    if emdash_units(text) * 1000 / n_chars > EMDASH_DENSITY_MAX:
        text = _EMDASH_AGGRESSIVE_RE.sub('——', text)
        text = _DIALOG_DASH_RE.sub('——', text)

    # Pass 3: 字符预算（门禁单位口径）—— 循环收敛
    # 关键场景：r14 part 19-30 几乎每行对白末尾一个 `——`，连续 run 极少但累积密度爆表；
    # Round 1 实证的同型逃逸：模型把 `……` 全部换成 ASCII `...`（旧正则为空操作）。
    for round_idx in range(12):
        n_chars = max(len(text), 1)
        ell_over = ellipsis_units(text) * 1000 / n_chars > ELLIPSIS_DENSITY_MAX
        emd_over = emdash_units(text) * 1000 / n_chars > EMDASH_DENSITY_MAX
        if not ell_over and not emd_over:
            break
        # 前 5 轮保留"每族至少 RUN_MAX 个单位"的地板；5 轮后仍超（仅可能发生在
        # 极端短文本）→ 去掉地板硬剪，保证清洗后 density_check 必然 PASS。
        ell_floor = ELLIPSIS_RUN_MAX if round_idx < 5 else 0
        emd_floor = EMDASH_RUN_MAX if round_idx < 5 else 0
        if ell_over:
            budget = max(ell_floor, ELLIPSIS_DENSITY_MAX * n_chars // 1000)
            text = _trim_runs_to_budget(text, _ELLIPSIS_ANY_RE.finditer(text), budget, _ellipsis_run_units)
        if emd_over:
            budget = max(emd_floor, EMDASH_DENSITY_MAX * n_chars // 1000)
            text = _trim_runs_to_budget(text, _EMDASH_ANY_RE.finditer(text), budget, _emdash_run_units)
    return text


def _drop_runs(text: str, runs) -> str:
    """删除指定的 run 区间（按 start 排序、从后往前删，保证区间不失效）。"""
    chars = list(text)
    for m in sorted(runs, key=lambda r: r.start(), reverse=True):
        for pos in range(m.start(), m.end()):
            chars[pos] = ''
    return ''.join(chars)