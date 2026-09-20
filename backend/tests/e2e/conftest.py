"""
P0-41: 集中管理 live LLM 测试所需的环境变量检查。

⚠️  严禁在测试文件中硬编码 API Key。
- 真实 key 必须在调用者 shell 环境（.env 或 CI secret）里设置
- 测试文件只能调用 require_env('STEP_API_KEY') 等 helper
- 缺失则 pytest.skip 而不是 FAIL，便于 CI 在无密钥环境下也能跑
"""
import os
import pytest


def require_env(name: str) -> str:
    """读取必填环境变量；缺失则 pytest.skip。

    Args:
        name: 环境变量名

    Returns:
        环境变量值

    Raises:
        pytest.skip.Exception: 缺失时跳过整个测试
    """
    val = os.environ.get(name, '').strip()
    if not val:
        pytest.skip(f'{name} not set; skipping live LLM test (see conftest.py)')
    return val


@pytest.fixture
def step_api_key() -> str:
    """测试函数级 fixture：直接拿 STEP_API_KEY；缺失自动 skip。

    R4-P1-x: 移除此前的 session 级 autouse guard —— allow_module_level=True 的
    autouse skip 会把**整个 session 的全部测试**（包括完全离线的
    test_resume / test_cost_persist / test_milestone_rolling）一并 skip，
    无密钥环境下 pytest 输出"全绿"但实际零覆盖。需要 live key 的测试
    请显式请求 step_api_key fixture（或调 require_env）。
    """
    return require_env('STEP_API_KEY')


# ---------------- R3-S2: live 脚本 pytest 化（--live opt-in，默认不跑） ----------------

def pytest_addoption(parser):
    """--live 开关（照抄 DSPy tests/conftest.py 模式）：live LLM 测试消耗真实
    配额，默认不跑；显式传 --live 才收集执行。"""
    parser.addoption('--live', action='store_true', default=False,
                     help='运行需要真实 LLM API key 的 live 测试（消耗真实配额，默认跳过）')


def pytest_configure(config):
    # 注册 live marker（不动仓库根 pytest.ini；未传 --live 时 live 测试全部 skip）
    config.addinivalue_line(
        'markers', 'live: 需要真实 LLM API key 的 live 测试（--live 显式开启后才运行）')


def pytest_collection_modifyitems(config, items):
    """DSPy 模式: 未传 --live 时全部 @pytest.mark.live 测试 skip（汇总可见 N skipped）。"""
    if config.getoption('--live'):
        return
    skip_live = pytest.mark.skip(reason='需要 --live 显式开启（live LLM 测试消耗真实配额）')
    for item in items:
        if 'live' in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def live_llm_key() -> str:
    """live 测试门禁：经 core.config 判定 API key 是否可用。

    R3-S2 坑 2: core/config.py 的 read_env 只解析 .env 文件、不读 os.environ
    （R1-A 已实证该语义）—— 照抄 require_env('STEP_API_KEY') 会在 .env 有 key
    时也永远 skip。这里经配置层取 part_writer 的 api_key，.env 有 key 即放行；
    确实未配置才 skip。
    """
    try:
        from core.config import get_llm_config_for_agent
        api_key = (get_llm_config_for_agent('part_writer').api_key or '').strip()
    except Exception:
        api_key = ''
    if not api_key:
        pytest.skip('未配置 LLM API key（.env 的 STEP_API_KEY），跳过 live LLM 测试')
    return api_key