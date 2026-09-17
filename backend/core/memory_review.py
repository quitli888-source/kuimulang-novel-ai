"""
番茄小说AI创作系统 V5 - 记忆审查和管理功能
借鉴ClaudeCode的记忆审查设计
"""
import json
from typing import List, Dict

from core.memory_manager import memory_manager
from core.llm_client import call_llm


class MemoryReviewer:
    """记忆审查和管理工具"""
    
    def __init__(self):
        self.memory_manager = memory_manager
    
    def review_memory(self, auto_memory: List[str] = None) -> Dict:
        """
        审查记忆内容并生成报告
        
        Args:
            auto_memory: 自动记忆内容列表
            
        Returns:
            包含审查结果的字典
        """
        # 获取所有记忆内容
        managed_memory = self.memory_manager.get_managed_memory()
        user_memory = self.memory_manager.get_user_memory()
        project_memory = self.memory_manager.get_project_memory()
        local_memory = self.memory_manager.get_local_memory()
        
        # 生成审查提示
        prompt = self._generate_review_prompt(
            managed_memory, user_memory, project_memory, local_memory, auto_memory
        )
        
        # 调用LLM进行审查
        system_prompt = """
你是一个记忆管理专家，负责审查和优化小说创作系统的记忆内容。
你的任务是分析不同层级的记忆内容，识别重复、过时或冲突的条目，并提出优化建议。
        """
        
        response = call_llm(system_prompt, prompt, temperature=0.3, agent="memory_review")
        
        # 解析审查结果
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            # 如果LLM返回的不是JSON，返回错误信息
            result = {
                "error": "解析审查结果失败",
                "raw_response": response
            }
        
        return result
    
    def _generate_review_prompt(self, managed_memory: str, user_memory: str, project_memory: str, local_memory: str, auto_memory: List[str] = None) -> str:
        """生成记忆审查提示"""
        prompt = """
# 记忆审查任务

## 目标
审查小说创作系统的记忆内容，生成清晰的优化建议报告，按操作类型分组。不要直接应用更改，而是提出建议供用户批准。

## 记忆层级

### 1. 全局记忆（Managed Memory）
```
{managed_memory}
```

### 2. 用户记忆（User Memory）
```
{user_memory}
```

### 3. 项目记忆（Project Memory）
```
{project_memory}
```

### 4. 本地记忆（Local Memory）
```
{local_memory}
```

### 5. 自动记忆（Auto Memory）
{auto_memory_content}

## 审查步骤

### 1. 分类自动记忆条目
对于自动记忆中的每个实质性条目，确定最佳目的地：

| 目的地 | 适合内容 | 示例 |
|---|---|---|
| **全局记忆** | 适用于所有用户的通用写作规则和指南 | "使用标准中文标点符号"，"保持情节连贯性" |
| **用户记忆** | 特定用户的写作偏好和习惯 | "我喜欢简洁的叙述风格"，"避免使用过于复杂的句子结构" |
| **项目记忆** | 特定小说的设定和规则 | "主角是一名25岁的程序员"，"故事背景设定在2030年" |
| **本地记忆** | 个人创作笔记和草稿 | "需要修改第三章的结尾"，"考虑添加一个新角色" |
| **保留在自动记忆** | 临时上下文和工作笔记 | 会话特定的观察，不确定的模式 |

### 2. 识别清理机会
扫描所有层级的记忆，识别：
- **重复内容**：不同层级中重复的条目
- **过时内容**：与当前创作需求不符的条目
- **冲突内容**：不同层级之间相互矛盾的条目

### 3. 生成审查报告
输出结构化报告，按操作类型分组：
1. **提升建议** - 建议移动的条目，包括目的地和理由
2. **清理建议** - 重复、过时或冲突的条目
3. **歧义条目** - 需要用户输入的条目
4. **无需操作** - 应该保持不变的条目

## 输出格式
请以JSON格式输出审查结果，包含以下字段：
{
  "promotions": [
    {
      "content": "条目内容",
      "from": "来源记忆类型",
      "to": "目标记忆类型",
      "reason": "建议理由"
    }
  ],
  "cleanup": [
    {
      "content": "条目内容",
      "type": "重复/过时/冲突",
      "location": "记忆类型",
      "reason": "清理理由"
    }
  ],
  "ambiguous": [
    {
      "content": "条目内容",
      "current_location": "当前位置",
      "possible_locations": ["可能的位置1", "可能的位置2"],
      "reason": "歧义原因"
    }
  ],
  "no_action": [
    {
      "content": "条目内容",
      "location": "记忆类型",
      "reason": "无需操作的理由"
    }
  ]
}

请确保输出是有效的JSON格式，不要包含任何额外的解释文字。
        """
        
        auto_memory_content = "\n".join([f"- {item}" for item in auto_memory]) if auto_memory else "无"
        
        return prompt.format(
            managed_memory=managed_memory or "无",
            user_memory=user_memory or "无",
            project_memory=project_memory or "无",
            local_memory=local_memory or "无",
            auto_memory_content=auto_memory_content
        )
    
    def apply_memory_changes(self, changes: Dict) -> Dict:
        """
        应用记忆更改
        
        Args:
            changes: 包含更改建议的字典
            
        Returns:
            应用结果
        """
        results = {
            "promotions": [],
            "cleanup": [],
            "errors": []
        }
        
        # 应用提升建议
        for promotion in changes.get("promotions", []):
            try:
                self._promote_memory(promotion)
                results["promotions"].append(promotion)
            except Exception as e:
                results["errors"].append({
                    "type": "promotion",
                    "item": promotion,
                    "error": str(e)
                })
        
        # 应用清理建议
        for cleanup in changes.get("cleanup", []):
            try:
                self._cleanup_memory(cleanup)
                results["cleanup"].append(cleanup)
            except Exception as e:
                results["errors"].append({
                    "type": "cleanup",
                    "item": cleanup,
                    "error": str(e)
                })
        
        return results
    
    def _promote_memory(self, promotion: Dict):
        """R21-P2-27: 提升记忆条目 —— 暂未实现，调用方必须 catch NotImplementedError。
        之前是空 pass，会让 apply_memory_changes '成功' 但什么都不做，造成 silent 数据丢失。
        """
        raise NotImplementedError(
            "_promote_memory 尚未实现。请在 MemoryManager 中实现 promote 操作"
            "（将临时/局部记忆提升到全局记忆）后调用此方法。"
        )

    def _cleanup_memory(self, cleanup: Dict):
        """R21-P2-27: 清理记忆条目 —— 暂未实现。
        """
        raise NotImplementedError(
            "_cleanup_memory 尚未实现。请在 MemoryManager 中实现 cleanup 操作"
            "（删除过期/废弃的记忆条目）后调用此方法。"
        )


# 全局记忆审查器实例
memory_reviewer = MemoryReviewer()


def review_memory(auto_memory: List[str] = None):
    """审查记忆内容"""
    return memory_reviewer.review_memory(auto_memory)


def apply_memory_changes(changes: Dict):
    """应用记忆更改"""
    return memory_reviewer.apply_memory_changes(changes)
