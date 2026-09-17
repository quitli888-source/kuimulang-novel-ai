"""
番茄小说AI创作系统 - Prompt外部化加载器 V4.1

从 prompts/ 目录加载各Agent的系统提示词，支持不修改代码直接调优。
支持 {变量名} 占位符替换（从 config 读取变量值）。
如果外部文件不存在，回退到代码内嵌的默认Prompt（f-string已替换）。
"""
from core.config import PROMPT_DIR, TARGET_WORD_COUNT, PART_COUNT, PART_WORD_MIN, PART_WORD_MAX


# 可在 prompt 中使用的变量（key=占位符名, value=实际值）
_PROMPT_VARS = {
    "TARGET_WORD_COUNT": TARGET_WORD_COUNT,
    "PART_COUNT": PART_COUNT,
    "PART_WORD_MIN": PART_WORD_MIN,
    "PART_WORD_MAX": PART_WORD_MAX,
}


def load_prompt(prompt_name: str, default: str) -> str:
    """
    加载外部Prompt文件。

    Args:
        prompt_name: Prompt文件名（不含.txt后缀），如 "part_writer"
        default: 内嵌的默认Prompt（当外部文件不存在时使用）

    Returns:
        Prompt文本
    """
    prompt_path = PROMPT_DIR / f"{prompt_name}.txt"
    if prompt_path.exists():
        text = prompt_path.read_text(encoding="utf-8").strip()
        # 替换占位符变量（如 {PART_COUNT} → 6）
        for var_name, var_value in _PROMPT_VARS.items():
            text = text.replace("{" + var_name + "}", str(var_value))
        return text
    return default
