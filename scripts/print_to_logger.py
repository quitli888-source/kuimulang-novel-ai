#!/usr/bin/env python3
"""R22-P1-13: 用 AST 重建方式批量把 backend/ 下的 print() 替换为 logger 调用。

正确做法（不依赖脆弱的字符串切片）：
1. ast.parse(file)
2. 对每个 ast.Call(func=Name('print'))，用 ast.unparse(node) 拿到完整源码
3. 从 unparsed 提取 args（去掉 "print(" 和最后的 ")"）
4. 启发式分类 info/warning/error
5. 用 ast.unparse 把 Call 节点的 func 替换为 Name(f"logger.{level}")
   → ast.unparse 给出 "logger.info(args)"
6. 整文件用 ast.unparse(tree) 输出（保留缩进）
"""
import ast
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "backend"
SKIP_FILES = {"logger.py", "launcher.py", "main.py", "console.py", "__init__.py"}
LOGGER_IMPORT = "from core.logger import get_logger"


def classify_level(msg: str) -> str:
    """按 print 内容启发式分类日志级别。"""
    low = msg.lower() if msg else ""
    if any(kw in low for kw in ["fail", "error", "exception", "traceback", "❌", "异常"]):
        return "error"
    if any(kw in low for kw in ["warn", "⚠", "warning", "fallback"]):
        return "warning"
    if any(kw in low for kw in ["debug", "trace"]):
        return "debug"
    return "info"


def has_logger_module(tree: ast.Module) -> bool:
    """检查文件是否已经导入 get_logger。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.logger":
            for alias in node.names:
                if alias.name == "get_logger":
                    return True
    return False


def has_logger_instance(tree: ast.Module) -> bool:
    """检查文件是否已经有 logger = get_logger(...) 实例化。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "logger":
                    return True
    return False


def add_logger_setup(tree: ast.Module, module_name: str) -> None:
    """在所有 import 之后插入 logger 配置。"""
    # 检查是否已有
    if has_logger_module(tree) and has_logger_instance(tree):
        return

    # 找到最后一个 import 节点
    last_import_idx = -1
    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_idx = i

    # 构造 logger 配置节点（必须 fix_missing_locations 否则 ast.unparse 失败）
    import_node = ast.ImportFrom(module="core.logger", names=[ast.alias(name="get_logger", asname=None)], level=0)
    call_node = ast.Call(
        func=ast.Name(id="get_logger"),
        args=[ast.Constant(value=module_name)],
        keywords=[],
    )
    assign_node = ast.Assign(
        targets=[ast.Name(id="logger")],
        value=call_node,
    )
    # R22-P1-13: 新建 AST 节点默认没 lineno，ast.unparse 会失败；用 fix_missing_locations 补
    ast.fix_missing_locations(import_node)
    ast.fix_missing_locations(call_node)
    ast.fix_missing_locations(assign_node)

    # 插入到 last_import_idx 之后
    insert_idx = last_import_idx + 1 if last_import_idx >= 0 else 0
    tree.body.insert(insert_idx, assign_node)
    tree.body.insert(insert_idx, import_node)


def rename_print_calls(tree: ast.Module) -> int:
    """把所有 print(...) 调用替换为 logger.<level>(...) 调用。

    返回替换数量。
    """
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue

        # 提取 msg 用于分类
        msg_literal = ""
        if node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                msg_literal = a0.value

        level = classify_level(msg_literal)
        # 替换 func 为 logger.<level>
        node.func = ast.Attribute(
            value=ast.Name(id="logger"),
            attr=level,
            ctx=ast.Load(),
        )
        # 由于 ast.unparse 会输出 "logger.info(...)" —— 注意：print() 无返回，但我们替换的是表达式位置
        # print(...) 在 Python 中表达式求值为 None，logger.<level>(...) 返回 None —— 语义等价
        n += 1
    return n


def transform_file(path: Path) -> tuple[bool, int]:
    """返回 (modified, n_replacements)。"""
    try:
        src = path.read_text(encoding="utf-8")
    except Exception:
        return False, 0

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"  SKIP {path.relative_to(ROOT.parent)}: syntax error ({e})")
        return False, 0

    n = rename_print_calls(tree)
    if n == 0:
        return False, 0

    add_logger_setup(tree, module_name=path.stem)

    # 输出（ast.unparse 保证语法正确 + 缩进正确）
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            new_src = ast.unparse(tree)
    except Exception as e:
        print(f"  FAIL {path.relative_to(ROOT.parent)}: unparse failed ({e})")
        return False, 0

    if new_src != src:
        path.write_text(new_src, encoding="utf-8")
        return True, n
    return False, 0


def main():
    if not ROOT.exists():
        print(f"Backend dir not found: {ROOT}")
        sys.exit(1)
    total_files = 0
    total_repl = 0
    for py_file in sorted(ROOT.rglob("*.py")):
        if py_file.name in SKIP_FILES:
            continue
        if "__pycache__" in py_file.parts:
            continue
        modified, n = transform_file(py_file)
        if modified:
            total_files += 1
            total_repl += n
            print(f"  {py_file.relative_to(ROOT.parent)}: {n}")
    print(f"\nTotal: {total_files} files, {total_repl} print() -> logger calls")


if __name__ == "__main__":
    main()
