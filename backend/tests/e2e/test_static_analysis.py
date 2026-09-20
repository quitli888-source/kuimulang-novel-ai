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


if __name__ == '__main__':
    test_no_undefined_names_f821()
    print('PASS test_no_undefined_names_f821')
    print('all static analysis tests passed')
