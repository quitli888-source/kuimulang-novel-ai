"""
Round 2 回归测试（R2-1 / R2-2 / R2-7）—— 全部离线断言，不调 LLM。

覆盖：
  R2-7  Round 1 失败产物（ASCII 标点注水真实样本）密度回归 fixture：
         每个片段 strip_padding_chars() 后必须通过门禁同款 density_check
         （密度 ≤ 5/6 每千字、run = 0）、字数损失 < 5%
  R2-1  口径单点守护：门禁 density_check 与清洗器共用 ellipsis_units/emdash_units
         + run 正则（P1-3 守护探针：把正则改回 Unicode-only 时本测试必须 FAIL）；
         构造例——3 连 Unicode run 低密度也必须清零；40 单位/5000 字预算循环收敛
  R2-2  反凑字数 prompt 守护：3 个 prompts/*.txt + 2 个 agent 内嵌 fallback 的
         🚨 段必须同时包含 ASCII 变体禁令与替代写法关键词（防"只改一处"逃逸）

既支持 pytest 也支持 `python backend/tests/e2e/test_padding_regression.py` 直接跑。
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

from core.logger import get_logger
from core.text_utils import (ELLIPSIS_RUN_RE, EMDASH_RUN_RE,
                             ELLIPSIS_DENSITY_MAX, EMDASH_DENSITY_MAX,
                             ellipsis_units, emdash_units, strip_padding_chars)

logger = get_logger('test_padding_regression')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

FIXTURE_PATH = _HERE / 'fixtures' / 'padding_samples.json'


def _load_density_check():
    """从门禁脚本 import density_check（R2-1 口径单点的守护探针）。

    verify_step5_longform 模块级有 RUN_DIR.mkdir 副作用，导入时临时屏蔽，
    避免每次跑测试都在 data/verification/ 下留空目录。
    """
    verify_path = _HERE / 'verify_step5_longform.py'
    spec = importlib.util.spec_from_file_location('verify_step5_longform_under_test', verify_path)
    mod = importlib.util.module_from_spec(spec)
    import pathlib
    orig_mkdir = pathlib.Path.mkdir
    pathlib.Path.mkdir = lambda self, *a, **k: None
    try:
        spec.loader.exec_module(mod)
    finally:
        pathlib.Path.mkdir = orig_mkdir
    return mod.density_check


density_check = _load_density_check()


# ---------------- R2-7: 失败产物密度回归 fixture ----------------

def _load_samples():
    data = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
    return data['samples']


def test_fixture_samples_pass_density_gate():
    """R2-7: 每个 fixture 片段清洗后必须通过门禁同款 density_check，且字数损失 < 5%。"""
    samples = _load_samples()
    assert samples, 'fixture 为空'
    for s in samples:
        raw = s['text']
        cleaned = strip_padding_chars(raw)
        result = density_check(cleaned)
        loss_pct = (len(raw) - len(cleaned)) / max(len(raw), 1) * 100
        logger.info(f"[test_fixture] {s['source']} Part{s['part']}: "
                    f"{len(raw)}字 -> {len(cleaned)}字 (损失{loss_pct:.2f}%) "
                    f"ell/1k={result['ellipsis_per_1k']} emd/1k={result['emdash_per_1k']} "
                    f"runs={result['ellipsis_runs_3plus']}/{result['emdash_runs_2plus']} "
                    f"verdict={result['verdict']}")
        assert result['verdict'] == 'PASS', (
            f"{s['source']} Part{s['part']} 清洗后仍未过门禁: {result}")
        assert result['ellipsis_per_1k'] <= ELLIPSIS_DENSITY_MAX, result
        assert result['emdash_per_1k'] <= EMDASH_DENSITY_MAX, result
        assert result['ellipsis_runs_3plus'] == 0, result
        assert result['emdash_runs_2plus'] == 0, result
        assert loss_pct < 5, f"字数损失 {loss_pct:.2f}% 超限"


def test_fixture_raw_text_fails_gate():
    """fixture 有牙齿的证明：原始失败产物必须被门禁判 FAIL（否则回归测试是空转）。"""
    for s in _load_samples():
        result = density_check(s['text'])
        assert result['verdict'] == 'FAIL', f"{s['source']} Part{s['part']} 原文居然 PASS，fixture 失去回归意义"


# ---------------- R2-1: 口径单点 + 构造例 ----------------

def test_run_regexes_cover_ascii_variants():
    """R2-1 守护探针：run 正则必须同时覆盖 ASCII 变体（改回 Unicode-only 时本测试 FAIL）。"""
    assert re.search(ELLIPSIS_RUN_RE, '你敢对执法弟子动手...'), '省略号 run 正则漏 ASCII 点串'
    assert re.search(ELLIPSIS_RUN_RE, '………'), '省略号 run 正则漏 3 连 U+2026'
    assert re.search(EMDASH_RUN_RE, '声音--低沉'), '破折号 run 正则漏 ASCII 连字符'
    assert re.search(EMDASH_RUN_RE, '———'), '破折号 run 正则漏 3 连 U+2014'
    assert not re.search(ELLIPSIS_RUN_RE, '他说……罢了'), '2 连 U+2026 是合法用法，不应判 run'
    assert not re.search(EMDASH_RUN_RE, '他说——然后停住'), '2 连 U+2014 是合法用法，不应判 run'


def test_unit_functions_gate_semantics():
    """R2-1: 单位函数语义 = 门禁口径（省略号按字符、破折号按非重叠对）。"""
    assert ellipsis_units('……') == 2 and ellipsis_units('...') == 1 and ellipsis_units('....') == 1
    assert ellipsis_units('他说……半晌') == 2 and ellipsis_units('他说...半晌') == 1
    assert emdash_units('——') == 1 and emdash_units('--') == 1 and emdash_units('———') == 1
    assert emdash_units('————') == 2


def test_unicode_run_collapsed_even_at_low_density():
    """R2-1 验收 2: 3 连 Unicode run 在低密度下也必须清零（构造性保证，非密度触发的运气）。"""
    text = '他抬起头。' + '………' + '然后低声说了一句什么，转身离去。' * 3
    cleaned = strip_padding_chars(text)
    assert re.search(r'…{3,}', cleaned) is None, '3 连 Unicode 省略号未被收敛'
    assert re.search(r'——{2,}', cleaned) is None
    assert density_check(cleaned)['verdict'] == 'PASS'


def test_budget_loop_converges_after_deletion():
    """R2-1 修正点 3: 40 单位/5000 字 —— 一次删除后分母变小会反弹，必须循环收敛到 ≤5/1k。"""
    base = '少年林尘站在古井边，寒风卷起衣角。' * 90   # ≈ 4140 字
    text = base[:4000] + '...' * 40 + base[4000:]
    assert ellipsis_units(text) == 40
    cleaned = strip_padding_chars(text)
    n = max(len(cleaned), 1)
    density = ellipsis_units(cleaned) * 1000 / n
    logger.info(f'[test_budget_loop] 40 单位/{len(text)}字 -> {ellipsis_units(cleaned)} 单位/{len(cleaned)}字 = {density:.2f}/1k')
    assert density <= ELLIPSIS_DENSITY_MAX, f'预算修剪未循环收敛: {density:.2f}/1k'
    assert density_check(cleaned)['verdict'] == 'PASS'


def test_ascii_run_normalization_is_unit_neutral():
    """R2-1 修正点 2: ASCII 点串必须归一化为单 `…`（1 单位），绝不能变成 `……`（2 单位，密度翻倍）。"""
    long_raw = '他压低声音说' + '......' + '然后转身离去，消失在夜色里。' * 20
    cleaned = strip_padding_chars(long_raw)
    assert cleaned == '他压低声音说…' + '然后转身离去，消失在夜色里。' * 20, (
        f'ASCII 点串未归一化为单 `…`: {cleaned[:40]!r}')
    assert '……' not in cleaned
    # 归一化前后门禁单位不变（点串 3-5 个 = 1 单位 -> 单 `…` = 1 单位）
    assert ellipsis_units('你敢对执法弟子动手.....') == 1
    assert ellipsis_units(strip_padding_chars('他低声说' + '.....' + '然后转身离去，消失在夜色里。' * 20)) == 1
    # 连字符同理：2+ ASCII 连字符 = 1 单位 -> `——` = 1 单位
    long_dash = '少年低头' + '----' + '不再言语，只顾赶路。' * 20
    cleaned_dash = strip_padding_chars(long_dash)
    assert cleaned_dash == '少年低头——' + '不再言语，只顾赶路。' * 20, cleaned_dash[:40]
    assert '--' not in cleaned_dash


# ---------------- R2-2: 反凑字数 prompt 守护 ----------------

_PROMPT_FILES = [
    _REPO / 'prompts' / 'part_writer.txt',
    _REPO / 'prompts' / 'part_chunk.txt',
    _REPO / 'prompts' / 'style_optimizer.txt',
    _BACKEND / 'core' / 'agents' / 'part_writer_agent.py',
    _BACKEND / 'core' / 'agents' / 'style_optimizer_agent.py',
]
_ALT_KEYWORDS = ('叙述', '声音低下去', '什么也没问', '沉默良久')


def _anti_padding_section(text: str) -> str:
    """取 🚨 标记到下一个 `##` 标题之间的反凑字数规则段。"""
    i = text.index('🚨')
    j = text.find('##', i + 1)
    return text[i:j] if j > 0 else text[i:]


def test_anti_padding_prompt_covers_ascii_variants():
    """R2-2 验收 1: 5 处 prompt（3 外置 + 2 内嵌 fallback）的禁令段必须同时包含
    ASCII 变体与替代写法关键词 —— 把"prompt 漏改"变成 CI 可判失败。"""
    for path in _PROMPT_FILES:
        section = _anti_padding_section(path.read_text(encoding='utf-8'))
        assert '...' in section, f'{path.name} 反凑字数段未纳入 ASCII 点串变体'
        assert '--' in section, f'{path.name} 反凑字数段未纳入 ASCII 连字符变体'
        assert any(k in section for k in _ALT_KEYWORDS), (
            f'{path.name} 反凑字数段缺少替代写法（叙述化表达）')
        logger.info(f'[test_prompt] {path.name}: ASCII 变体 + 替代写法均在位')


if __name__ == '__main__':
    for fn in (test_fixture_samples_pass_density_gate, test_fixture_raw_text_fails_gate,
               test_run_regexes_cover_ascii_variants, test_unit_functions_gate_semantics,
               test_unicode_run_collapsed_even_at_low_density,
               test_budget_loop_converges_after_deletion,
               test_ascii_run_normalization_is_unit_neutral,
               test_anti_padding_prompt_covers_ascii_variants):
        fn()
        print(f'PASS {fn.__name__}')
    print('all padding regression tests passed')
