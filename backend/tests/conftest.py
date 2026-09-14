"""
R5-P3-5: pytest conftest —— 把 backend/ 加入 sys.path，便于 import。
pytest.ini 不强制依赖 pytest_asyncio；3 个 E2E 脚本既支持 pytest 也支持直接 python 跑。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
