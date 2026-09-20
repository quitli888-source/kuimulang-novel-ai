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
ELLIPSIS_DENSITY_MAX = 5      # 单 Part 每千字 `……` 上限
EMDASH_RUN_MAX = 2            # 单段连续 `——` 上限
EMDASH_DENSITY_MAX = 6        # 单 Part 每千字 `——` 上限

_ELLIPSIS_RE = re.compile(r'…{4,}')              # 4+ 连续 → 收敛
_EMDASH_RE = re.compile(r'——{3,}')               # 3+ 连续 → 收敛
_ELLIPSIS_AGGRESSIVE_RE = re.compile(r'…{3,}')   # 密度超限后启用 3+ → 1
_EMDASH_AGGRESSIVE_RE = re.compile(r'——{2,}')    # 密度超限后启用 2+ → 1
# 对话串联模式：连续两行末尾都是 `——` —— 典型"小子——\n声音低沉：\n'有一个人——'" 注水
_DIALOG_DASH_RE = re.compile(r'——\s*\n\s*——')


def _count_runs(text: str, unit: str) -> int:
    """按"出现次数"计数（一个 `……`/`——` 算 1 次），与密度阈值的语义对齐。"""
    return len(re.findall(re.escape(unit) + '+', text))


def strip_padding_chars(text: Optional[str]) -> str:
    """把模型在字数压力下批量堆叠的 `……` / `——` 强制收敛。

    三遍扫描：
      1. 标准收敛（保留少量合法用法）：4+ 连 `……` → 2；3+ 连 `——` → 2
      2. 密度检测：单 Part `……` 或 `——` 密度仍超阈值（按**出现次数**计）→
         启用 3+ 连 `……` → 1；2+ 连 `——` → 1，并收敛对话串联 `——\\n——`
      3. **字符预算**：如果单 Part 的 `……` 或 `——` 出现次数仍超密度阈值（典型场景：
         minimax-m3 每行对白末尾堆一个 `——`，分隔后不连续但累积密度爆表），按
         "保留前 N 次、删除其余"的策略修剪——**按整次（run）删除，绝不从中间切断**。

    Args:
        text: 模型输出的原始文本（None 时返回空串）。

    Returns:
        清洗后的文本。合法文学用法（"……半晌才说" 或 "他说——然后停住"）保留；
        注水用法（连续 3+ 个省略号、对话串联 `——\\n` 多行、整段全 `——` 堆叠）被强制收敛。
    """
    if not text:
        return ''
    # Pass 1: 标准收敛
    text = _ELLIPSIS_RE.sub('……' * ELLIPSIS_RUN_MAX, text)
    text = _EMDASH_RE.sub('——' * EMDASH_RUN_MAX, text)
    # Pass 2: 密度检测 → 强力收敛（口径统一为"出现次数"：此前省略号按字符计、
    # 破折号按次数计，同一函数两套单位，短文本下合法用法被过度清洗）
    n_chars = max(len(text), 1)
    ell_density = _count_runs(text, '…') * 1000 / n_chars
    emd_density = _count_runs(text, '——') * 1000 / n_chars
    if ell_density > ELLIPSIS_DENSITY_MAX:
        text = _ELLIPSIS_AGGRESSIVE_RE.sub('……', text)
    if emd_density > EMDASH_DENSITY_MAX:
        text = _EMDASH_AGGRESSIVE_RE.sub('——', text)
        text = _DIALOG_DASH_RE.sub('——', text)
    # Pass 2 收敛会缩短文本，刷新分母后重算密度（供 Pass 3 预算判断）
    n_chars = max(len(text), 1)
    ell_density = _count_runs(text, '…') * 1000 / n_chars
    emd_density = _count_runs(text, '——') * 1000 / n_chars

    # Pass 3: 字符预算 —— 仍超阈值则按"保留前 N 次"修剪
    # 关键场景：r14 part 19-30 几乎每行对白末尾一个 `——`，连续 run 极少但累积密度爆表。
    # R4-P2-x: 删除按整个 run 的区间（不逐字符），否则会把一个合法的 `……` 切成
    # 贴着正文的孤岛单引号；同时清掉未使用的 keep 变量。
    if ell_density > ELLIPSIS_DENSITY_MAX or emd_density > EMDASH_DENSITY_MAX:
        ell_budget = max(ELLIPSIS_RUN_MAX, (ELLIPSIS_DENSITY_MAX * n_chars) // 1000)
        emd_budget = max(EMDASH_RUN_MAX, (EMDASH_DENSITY_MAX * n_chars) // 1000)
        if ell_density > ELLIPSIS_DENSITY_MAX:
            ell_runs = list(re.finditer(r'…+', text))
            if len(ell_runs) > ell_budget:
                text = _drop_runs(text, ell_runs[ell_budget:])
        if emd_density > EMDASH_DENSITY_MAX:
            emd_runs = list(re.finditer(r'——+', text))
            if len(emd_runs) > emd_budget:
                text = _drop_runs(text, emd_runs[emd_budget:])
    return text


def _drop_runs(text: str, runs) -> str:
    """删除指定的 run 区间（按 start 排序、从后往前删，保证区间不失效）。"""
    chars = list(text)
    for m in sorted(runs, key=lambda r: r.start(), reverse=True):
        for pos in range(m.start(), m.end()):
            chars[pos] = ''
    return ''.join(chars)
    return text