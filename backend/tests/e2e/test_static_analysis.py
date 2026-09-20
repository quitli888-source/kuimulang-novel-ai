"""
R3-S4: 静态门禁 —— 生产路径（core/services/api）不得有未定义变量（ruff F821）。

背景（Round 2 P0-1）：part_writer_agent.py:195 `expected_min_len=chunk_target // 2`
引用不存在的变量，NameError 在参数求值阶段抛出、被 except 吞成空内容，pytest 对
未执行到的行天然失明 —— 这类一行级回归本可被任何静态检查在提交前 1 秒捕获。
本机实证（Round 3 评审期）：修前全仓 F821 有且仅有 1 处命中 = P0-1 本身，零噪音。

降级模式（与 conftest require_env 同款）：ruff 缺失时 skip 而非 fail，
CI/无 ruff 环境也能跑套件。安装：pip install ruff（见 requirements-dev.txt）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_no_undefined_names_f821():
    """F821 门禁：core/services/api 三个生产目录必须零命中。

    失败信息附 ruff stdout，让未定义变量的文件:行号直接出现在 pytest 输出里。
    不扫 tests/（测试代码有条件 import 等模式，误报面大，无收益）。
    """
    ruff = shutil.which('ruff')
    if ruff is None:
        pytest.skip('ruff 未安装（pip install ruff 后启用本门禁）')
    r = subprocess.run(
        [ruff, 'check', '--isolated', '--select', 'F821',
         str(_BACKEND / 'core'), str(_BACKEND / 'services'), str(_BACKEND / 'api')],
        capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
    assert r.returncode == 0, f'存在未定义变量（F821）:\n{r.stdout}{r.stderr}'


# ---------------- S6: reasoning_tokens 可观测化 ----------------

def test_reasoning_tokens_logged_when_present(caplog):
    """S6: usage 带 completion_tokens_details.reasoning_tokens 时，日志出现
    reasoning_tokens=<n>/<completion>（冒烟/全量跑后由 Tester 统计占比，
    作为 Round 4 KML_*_MAX_TOKENS 预算校准的输入）。"""
    import logging
    from types import SimpleNamespace
    from unittest.mock import patch
    from core import llm_client

    class _Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='正文' * 100),
                                         finish_reason='stop')],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=6000,
                                      total_tokens=6100,
                                      completion_tokens_details=SimpleNamespace(reasoning_tokens=5000)))

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    with patch.object(llm_client, '_get_client_for_agent', lambda agent: (client, 'step-5-preview')):
        with caplog.at_level(logging.INFO, logger='kuaimulang.llm_client'):
            out = llm_client.call_llm(system_prompt='s', user_prompt='u', max_tokens=8000)
    assert out, 'fake 应返回正文'
    assert any('reasoning_tokens=5000/6000' in r.message for r in caplog.records), (
        f'未观察到 reasoning_tokens 日志: {[r.message for r in caplog.records if "Token" in r.message or "reasoning" in r.message]}')


def test_reasoning_tokens_absent_no_log(caplog):
    """S6 反向: fake usage 无 completion_tokens_details（step 供应商现状）→
    无 reasoning_tokens 日志、调用正常返回（getattr 链兜底，零行为变化）。"""
    import logging
    from types import SimpleNamespace
    from unittest.mock import patch
    from core import llm_client

    class _Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='正文' * 100),
                                         finish_reason='stop')],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=6000, total_tokens=6100))

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    with patch.object(llm_client, '_get_client_for_agent', lambda agent: (client, 'step-5-preview')):
        with caplog.at_level(logging.INFO, logger='kuaimulang.llm_client'):
            out = llm_client.call_llm(system_prompt='s', user_prompt='u', max_tokens=8000)
    assert out
    assert not any('reasoning_tokens=' in r.message for r in caplog.records), (
        '无 completion_tokens_details 时不应出现 reasoning_tokens 日志')


if __name__ == '__main__':
    test_no_undefined_names_f821()
    print('PASS test_no_undefined_names_f821')
    print('all static analysis tests passed')
