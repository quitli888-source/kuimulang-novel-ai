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


@pytest.fixture(scope='session', autouse=True)
def _step_api_key_guard():
    """session 级 fixture：在每个测试 session 开始时检查 STEP_API_KEY。
    无 key 时 skip 整个模块，而不是让每个测试都自己报错。
    """
    if not os.environ.get('STEP_API_KEY', '').strip():
        pytest.skip('STEP_API_KEY not set; skipping live LLM test module', allow_module_level=True)


@pytest.fixture
def step_api_key() -> str:
    """测试函数级 fixture：直接拿 STEP_API_KEY；缺失自动 skip。"""
    return require_env('STEP_API_KEY')