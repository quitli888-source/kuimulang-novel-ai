"""
R26-P3-40: 从 backend/core/llm_providers.PRESET_PROVIDERS 自动生成 .env.example

约定：
  - .env.example 只列占位 key（不写真实 key）
  - 默认 base_url / model / json_model 都用注释形式给出，便于用户按需覆盖
  - 新增供应商只需改 PRESET_PROVIDERS，重跑本脚本即可更新 .env.example
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core.llm_providers import PRESET_PROVIDERS  # noqa: E402

HEADER = """# ============================================================
#  番茄小说AI创作系统 V7 - 配置文件
#
#  使用步骤：
#  1. 复制本文件，重命名为 .env（注意前面有个点！）
#  2. 填入你的 API Key（只需填一个你使用的供应商）
#  3. 保存文件
#  4. 双击"安装脚本.bat"完成安装
#  5. 双击"启动图形界面.bat"开始使用
#
#  本文件由 scripts/gen_env_example.py 从 PRESET_PROVIDERS 自动生成；
#  新增/修改供应商后请重跑该脚本同步本文件。
#  支持的供应商（只需配置一个）见下方各小节。
# ============================================================

"""


def render_provider(p) -> str:
    name = p.name
    block = [f"# {name}"]
    if p.api_key_name:
        block.append(f"{p.api_key_name}=your_{p.id}_api_key_here")
    if p.base_url:
        block.append(f"# {p.api_key_name.replace('_API_KEY', '_BASE_URL')}={p.base_url}")
    if p.model:
        block.append(f"# {p.api_key_name.replace('_API_KEY', '_MODEL')}={p.model}")
    if p.json_model:
        block.append(f"# {p.api_key_name.replace('_API_KEY', '_JSON_MODEL')}={p.json_model}")
    return "\n".join(block) + "\n"


def main() -> None:
    out_path = ROOT / ".env.example"
    lines = [HEADER]
    for p in PRESET_PROVIDERS:
        lines.append(render_provider(p))
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gen_env_example] wrote {out_path} ({len(PRESET_PROVIDERS)} providers)")


if __name__ == "__main__":
    main()