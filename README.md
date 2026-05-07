# 奎木狼AI小说创作系统

基于 Vue3 + FastAPI 的智能小说生成工具，支持多LLM供应商、实时进度追踪和可视化创作流程。

## 功能特性

- **多阶段创作流程**：灵感解析 → 情节规划 → 章节创作 → 风格优化
- **多Agent协作**：8个专业Agent协同工作，确保创作质量
- **多LLM支持**：MiniMax、OpenAI、Claude等主流LLM供应商
- **实时进度追踪**：SSE事件流实时推送创作进度
- **可视化配置**：支持自定义模板、Part数量、创作温度等参数
- **手动/自动双模式**：可选择每个阶段的确认机制

## 技术栈

### 前端
- Vue 3 + Composition API
- Naive UI 组件库
- Pinia 状态管理
- Vite 构建工具

### 后端
- FastAPI 异步框架
- Python 3.10+
- SSE 实时通信
- 多供应商LLM集成

## 快速开始

### 环境要求

- Node.js >= 18
- Python >= 3.10
- LLM API Key（MiniMax / OpenAI / 等）

### 安装

```bash
# 克隆项目
git clone https://github.com/YOUR_USERNAME/kuimulang-novel-ai.git
cd kuimulang-novel-ai

# 前端安装依赖
cd frontend
npm install

# 后端安装依赖
cd ../backend
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

### 启动

```bash
# 启动后端（端口8000）
cd backend
python -m uvicorn main:app --reload --port 8000

# 启动前端（端口5173，新开终端）
cd frontend
npm run dev
```

访问 http://localhost:5173 即可使用。

## 项目结构

```
kuimulang-novel-ai/
├── frontend/          # Vue3 前端项目
│   ├── src/
│   │   ├── api/       # API 封装
│   │   ├── components/# 公共组件
│   │   ├── views/     # 页面组件
│   │   ├── stores/    # Pinia 状态管理
│   │   └── styles/    # 全局样式
│   └── dist/          # 构建产物
├── backend/           # FastAPI 后端项目
│   ├── api/           # API 路由
│   ├── core/          # 核心模块（Agent、LLM等）
│   ├── services/      # 业务服务层
│   └── main.py        # 入口文件
├── prompts/           # Agent Prompt 模板
├── data/              # 数据存储目录
│   ├── works/         # 作品数据
│   └── memory/         # 记忆数据
└── .env               # 环境配置（不提交）
```

## 创作流程

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: 灵感解析 (0-25%)                                   │
│  InspirationAgent → 提取核心要素                             │
│  GenreAgent → 判断题材分类                                    │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: 情节规划 (25-50%)                                  │
│  PlotPlannerAgent → 生成世界观/角色/伏笔/Part规划             │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: 逐Part创作 (50-80%)                                │
│  PartWriterAgent → 逐章生成正文                              │
├─────────────────────────────────────────────────────────────┤
│  Phase 4: 风格优化 (80-100%)                                 │
│  LogicReview + EmotionReview + ConsistencyReview → 审核        │
│  StyleOptimizer → 最终优化                                   │
└─────────────────────────────────────────────────────────────┘
```

## 配置说明

### LLM供应商配置

系统支持多种LLM供应商，可在 `配置 → LLM配置` 中切换：

| 供应商 | 模型 | 适用场景 |
|--------|------|---------|
| MiniMax | MiniMax-Text-01 | 创作主力 |
| OpenAI | GPT-4 / GPT-4o-mini | 备用 |
| 自定义 | 任意OpenAI兼容API | 高级用户 |

### Agent独立配置

每个Agent可独立设置模型、温度、最大Token：

- `plot_planner` - 情节规划，温度0.3
- `part_writer` - Part写作，温度0.8
- `style_optimizer` - 风格优化，温度0.5
- `logic/emotion/consistency_review` - 审核Agent，温度0.3

### 创作模板

| 模板 | 目标字数 | Part数量 | 每Part范围 |
|------|---------|---------|-----------|
| 短篇 | 10,000 | 3 | 2,500-4,000 |
| 中篇 | 30,000 | 6 | 4,000-6,000 |
| 长篇 | 50,000 | 10 | 4,000-6,000 |
| 超长篇 | 100,000 | 20 | 4,000-6,000 |
| 自定义 | 自定义 | 自定义 | 自动计算 |

## License

MIT License