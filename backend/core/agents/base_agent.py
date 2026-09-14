"""
番茄小说AI创作系统 V5 - Agent基类
V5.1改动：实现标准化的Agent接口，支持进度跟踪、错误处理和会话管理
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable


class BaseAgent(ABC):
    """Agent基类 - 所有Agent继承此类"""

    name: str = "未命名Agent"
    description: str = ""
    version: str = "1.0.0"

    def __init__(self):
        self.system_prompt = ""
        self.progress_callback: Optional[Callable[[int, str], None]] = None
        # R7-P1-5: chunk 级 checkpoint 回调（PartWriterAgent 每完成一个 chunk 触发）
        # 签名：callable(part_num: int, chunk_idx: int, accumulated_text: str) -> None
        self.checkpoint_callback: Optional[Callable[[int, int, str], None]] = None

    @abstractmethod
    def execute(self, state, **kwargs) -> Dict[str, Any]:
        """
        执行Agent任务
        Args:
            state: StoryState对象
            **kwargs: 额外参数
        Returns:
            Dict[str, Any]: 该Agent的输出结果，将写入state
        """
        pass

    def set_progress_callback(self, callback: Callable[[int, str], None]):
        """
        设置进度回调函数
        Args:
            callback: 进度回调函数，参数为(progress: int, message: str)
        """
        self.progress_callback = callback

    def set_checkpoint_callback(self, callback: Callable[[int, int, str], None]):
        """R7-P1-5: 设置 chunk 级 checkpoint 回调。
        Args:
            callback: 签名 (part_num, chunk_idx, accumulated_text) -> None
        """
        self.checkpoint_callback = callback

    def update_progress(self, progress: int, message: str):
        """
        更新进度
        Args:
            progress: 进度百分比 (0-100)
            message: 进度消息
        """
        if self.progress_callback:
            try:
                self.progress_callback(progress, message)
            except Exception as e:
                self.log_error(f"更新进度失败: {e}")
        print(f"[Agent] {self.name} - {progress}%: {message}")

    def log_start(self):
        print(f"[Agent] {self.name} 开始执行")
        self.update_progress(0, "开始执行")

    def log_done(self, summary: str):
        print(f"[Agent] {self.name} 完成 - {summary}")
        self.update_progress(100, f"执行完成: {summary}")

    def log_error(self, error: str):
        print(f"[Agent] {self.name} 错误: {error}")
        self.update_progress(100, f"执行失败: {error}")

    def validate_input(self, state, **kwargs) -> bool:
        """
        验证输入
        Args:
            state: StoryState对象
            **kwargs: 额外参数
        Returns:
            bool: 输入是否有效
        """
        return True

    def get_info(self) -> Dict[str, Any]:
        """
        获取Agent信息
        Returns:
            Dict[str, Any]: Agent信息
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version
        }


class AgentRegistry:
    """
    Agent注册中心
    """
    _agents: Dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, agent: BaseAgent):
        """
        注册Agent
        Args:
            agent: Agent实例
        """
        cls._agents[agent.name] = agent

    @classmethod
    def get_agent(cls, name: str) -> Optional[BaseAgent]:
        """
        获取Agent
        Args:
            name: Agent名称
        Returns:
            Optional[BaseAgent]: Agent实例
        """
        return cls._agents.get(name)

    @classmethod
    def get_all_agents(cls) -> Dict[str, BaseAgent]:
        """
        获取所有Agent
        Returns:
            Dict[str, BaseAgent]: 所有Agent
        """
        return cls._agents

    @classmethod
    def get_agent_info(cls) -> Dict[str, Dict[str, Any]]:
        """
        获取所有Agent信息
        Returns:
            Dict[str, Dict[str, Any]]: 所有Agent信息
        """
        return {name: agent.get_info() for name, agent in cls._agents.items()}
