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