"""
番茄小说AI创作系统 V5 - 多层级记忆管理系统
借鉴ClaudeCode的记忆管理设计
"""
import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Set, Any
from functools import lru_cache

from core.config import get_app_config

# 记忆类型定义
class MemoryType:
    MANAGED = "managed"  # 全局记忆
    USER = "user"        # 用户记忆
    PROJECT = "project"  # 项目记忆
    LOCAL = "local"      # 本地记忆

# 记忆文件信息
MemoryFileInfo = Dict[str, Any]

# 记忆指令提示
MEMORY_INSTRUCTION_PROMPT = """小说创作系统的记忆内容如下。请严格遵循这些指令，它们会覆盖任何默认行为。"""

# 最大记忆文件字符数
MAX_MEMORY_CHARACTER_COUNT = 40000

# 允许的文件扩展名
TEXT_FILE_EXTENSIONS = {
    '.md', '.txt', '.text',
    '.json', '.yaml', '.yml'
}


class MemoryManager:
    """多层级记忆管理系统"""
    
    def __init__(self):
        self.config = get_app_config()
        # 使用getattr获取memory_dir属性，如果不存在则使用默认值
        memory_dir = getattr(self.config, 'memory_dir', None)
        # 使用当前工作目录作为默认的记忆目录，避免权限问题
        self.memory_dir = Path(memory_dir or Path.cwd() / ".tomato_novel" / "memory")
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[MemoryManager] 创建记忆目录失败: {e}")
            # 如果创建失败，使用临时目录
            import tempfile
            self.memory_dir = Path(tempfile.gettempdir()) / ".tomato_novel" / "memory"
            self.memory_dir.mkdir(parents=True, exist_ok=True)
        
    def get_memory_files(self, force_include_external: bool = False) -> List[MemoryFileInfo]:
        """获取所有记忆文件"""
        result = []
        processed_paths = set()
        
        # 1. 加载Managed记忆（全局指令）
        managed_path = self.memory_dir / "managed" / "MEMORY.md"
        result.extend(self._process_memory_file(managed_path, MemoryType.MANAGED, processed_paths, force_include_external))
        
        # 2. 加载User记忆（用户全局指令）
        user_path = self.memory_dir / "user" / "MEMORY.md"
        result.extend(self._process_memory_file(user_path, MemoryType.USER, processed_paths, True))
        
        # 3. 加载Project记忆（项目指令）
        project_dir = Path.cwd()
        while project_dir != project_dir.parent:
            # 检查CLAUDE.md
            project_path = project_dir / "CLAUDE.md"
            result.extend(self._process_memory_file(project_path, MemoryType.PROJECT, processed_paths, force_include_external))
            
            # 检查.claude/CLAUDE.md
            dot_claude_path = project_dir / ".claude" / "CLAUDE.md"
            result.extend(self._process_memory_file(dot_claude_path, MemoryType.PROJECT, processed_paths, force_include_external))
            
            # 检查.claude/rules/*.md
            rules_dir = project_dir / ".claude" / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for md_file in rules_dir.glob("*.md"):
                    result.extend(self._process_memory_file(md_file, MemoryType.PROJECT, processed_paths, force_include_external))
            
            project_dir = project_dir.parent
        
        # 4. 加载Local记忆（本地项目指令）
        local_dir = Path.cwd()
        while local_dir != local_dir.parent:
            local_path = local_dir / "CLAUDE.local.md"
            result.extend(self._process_memory_file(local_path, MemoryType.LOCAL, processed_paths, force_include_external))
            local_dir = local_dir.parent
        
        return result
    
    def _process_memory_file(self, path: Path, memory_type: str, processed_paths: Set[str], include_external: bool) -> List[MemoryFileInfo]:
        """处理单个记忆文件"""
        result = []
        
        # 检查文件是否存在
        if not path.exists() or not path.is_file():
            return result
        
        # 检查文件扩展名
        if path.suffix not in TEXT_FILE_EXTENSIONS:
            return result
        
        # 检查是否已经处理过
        path_str = str(path)
        if path_str in processed_paths:
            return result
        processed_paths.add(path_str)
        
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            
            # 处理@include指令
            included_files = self._process_include_directives(content, path.parent, processed_paths, include_external)
            result.extend(included_files)
            
            # 添加当前文件
            result.append({
                "path": str(path),
                "type": memory_type,
                "content": content
            })
        except Exception as e:
            print(f"[MemoryManager] 处理文件 {path} 时出错: {e}")
        
        return result
    
    def _process_include_directives(self, content: str, base_dir: Path, processed_paths: Set[str], include_external: bool) -> List[MemoryFileInfo]:
        """处理@include指令"""
        result = []
        
        # 匹配@include指令
        include_pattern = r'@([^\s]+)'
        matches = re.findall(include_pattern, content)
        
        for match in matches:
            include_path = match
            
            # 处理路径
            if include_path.startswith("~"):
                # 用户主目录
                include_path = include_path.replace("~", str(Path.home()), 1)
            elif not include_path.startswith("/"):
                # 相对路径
                include_path = str(base_dir / include_path)
            
            # 处理文件
            include_file = Path(include_path)
            if include_file.exists() and include_file.is_file():
                result.extend(self._process_memory_file(include_file, MemoryType.PROJECT, processed_paths, include_external))
        
        return result
    
    def get_memory_content(self, memory_files: List[MemoryFileInfo], filter_type: Optional[str] = None) -> str:
        """获取记忆内容"""
        memories = []
        
        for file in memory_files:
            if filter_type and file["type"] != filter_type:
                continue
            
            if file["content"]:
                content = file["content"].strip()
                if content:
                    description = self._get_memory_description(file["type"])
                    memories.append(f"Contents of {file['path']}{description}:\n\n{content}")
        
        if not memories:
            return ""
        
        return f"{MEMORY_INSTRUCTION_PROMPT}\n\n{chr(10).join(memories)}"
    
    def _get_memory_description(self, memory_type: str) -> str:
        """获取记忆类型的描述"""
        descriptions = {
            MemoryType.MANAGED: " (全局指令，适用于所有用户)",
            MemoryType.USER: " (用户私有全局指令，适用于所有项目)",
            MemoryType.PROJECT: " (项目指令，签入代码库)",
            MemoryType.LOCAL: " (本地项目指令，不签入代码库)"
        }
        return descriptions.get(memory_type, "")
    
    def get_managed_memory(self) -> str:
        """获取全局记忆"""
        files = self.get_memory_files()
        return self.get_memory_content(files, MemoryType.MANAGED)
    
    def get_user_memory(self) -> str:
        """获取用户记忆"""
        files = self.get_memory_files()
        return self.get_memory_content(files, MemoryType.USER)
    
    def get_project_memory(self) -> str:
        """获取项目记忆"""
        files = self.get_memory_files()
        return self.get_memory_content(files, MemoryType.PROJECT)
    
    def get_local_memory(self) -> str:
        """获取本地记忆"""
        files = self.get_memory_files()
        return self.get_memory_content(files, MemoryType.LOCAL)
    
    def get_all_memory(self) -> str:
        """获取所有记忆"""
        files = self.get_memory_files()
        return self.get_memory_content(files)


# 全局记忆管理器实例
memory_manager = MemoryManager()


# 缓存记忆文件加载
@lru_cache(maxsize=1)
def get_memory_files():
    """获取记忆文件（缓存）"""
    return memory_manager.get_memory_files()


def get_all_memory():
    """获取所有记忆内容"""
    files = get_memory_files()
    return memory_manager.get_memory_content(files)


def get_memory_by_type(memory_type: str):
    """按类型获取记忆内容"""
    files = get_memory_files()
    return memory_manager.get_memory_content(files, memory_type)
