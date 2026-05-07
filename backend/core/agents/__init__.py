"""
番茄小说AI创作系统 V5 - Agents模块
V5.1改动：添加Agent注册功能
"""
from .base_agent import BaseAgent, AgentRegistry
from .genre_agent import GenreAgent
from .inspiration_agent import InspirationAgent
from .plot_planner_agent import PlotPlannerAgent
from .part_writer_agent import PartWriterAgent
from .style_optimizer_agent import StyleOptimizerAgent
from .emotion_review_agent import EmotionReviewAgent
from .logic_review_agent import LogicReviewAgent
from .consistency_review_agent import ConsistencyReviewAgent

# 注册所有Agent
AgentRegistry.register(GenreAgent())
AgentRegistry.register(InspirationAgent())
AgentRegistry.register(PlotPlannerAgent())
AgentRegistry.register(PartWriterAgent())
AgentRegistry.register(StyleOptimizerAgent())
AgentRegistry.register(EmotionReviewAgent())
AgentRegistry.register(LogicReviewAgent())
AgentRegistry.register(ConsistencyReviewAgent())

__all__ = [
    "BaseAgent",
    "AgentRegistry",
    "GenreAgent",
    "InspirationAgent",
    "PlotPlannerAgent",
    "PartWriterAgent",
    "StyleOptimizerAgent",
    "EmotionReviewAgent",
    "LogicReviewAgent",
    "ConsistencyReviewAgent"
]
