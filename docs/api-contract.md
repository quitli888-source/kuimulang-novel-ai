# API Contract — 奎木狼 AI 小说创作系统

> 维护人：Round 1 Optimizer（基于 review round 1 生成）
> 目的：把"前端 → 后端"的 HTTP 路径、Method、Body shape 1:1 对齐，
> 任何修改都需同步更新本表。

**约定**：
- 全部路径前缀 `/api`（FastAPI 注册于 `backend/main.py`）。
- 路径前缀 `/api/works` ←→ `backend/api/works.py`
- 路径前缀 `/api/writing` ←→ `backend/api/writing.py`
- 路径前缀 `/api/config` ←→ `backend/api/config_api.py`
- 路径前缀 `/api/rewrite` ←→ `backend/api/rewrite.py`
- 路径前缀 `/api/sse` ←→ `backend/api/sse.py`
- 路由常量以源文件 `@router.<METHOD>("<sub-path>")` 为准。

---

## 1. 创作控制（writing）

| Method | Path                       | Body                                          | 后端入口                                | 备注                                       |
| ------ | -------------------------- | --------------------------------------------- | --------------------------------------- | ------------------------------------------ |
| POST   | `/api/writing/start`       | `{ "work_id": "..." }`                        | `backend/api/writing.py:21`             | 异步启动 Phase1→4                          |
| POST   | `/api/writing/pause`       | `{ "work_id": "..." }`                        | `backend/api/writing.py:55`             | 触发暂停信号                                |
| POST   | `/api/writing/resume/{id}` | (空，路径参数 work_id)                        | `backend/api/writing.py:65`             | 恢复（当前仍跑完整 Phase1→4，TODO 下一轮） |
| GET    | `/api/writing/status/{id}` | (空，路径参数 work_id)                        | `backend/api/writing.py:85`             | 查询写作状态                                |
| POST   | `/api/writing/rewrite-part/{id}/{n}` | (空，路径参数)                       | `backend/api/writing.py:94`             | 重写指定 Part                               |
| POST   | `/api/writing/confirm`     | `{ "work_id": "...", "choice": "proceed" \| "cancel" }` | `backend/api/writing.py` (V6.1 新增)   | 手动确认模式下的响应                         |

> V6.1 起 `/api/writing/confirm` 为正式路径；旧的 `/api/works/writing/confirm`
> 已下线。`/writing/confirm`（无 `/api` 前缀）由 `main.py` 307 重定向至新路径。

## 2. 作品管理（works）

| Method | Path                                | Body                                | 后端入口                       | 备注             |
| ------ | ----------------------------------- | ----------------------------------- | ------------------------------ | ---------------- |
| GET    | `/api/works/`                       | (空)                                | `works.py:75`                  | 列出作品         |
| POST   | `/api/works/`                       | `{ "title": "...", "inspiration": "..." }` | `works.py:80`           | 新建作品         |
| GET    | `/api/works/{work_id}`              | (空)                                | `works.py:111`                 | 获取作品详情     |
| PATCH  | `/api/works/{work_id}`              | `{ "title"?: "...", "inspiration"?: "..." }` | `works.py:120`       | 更新标题/灵感    |
| DELETE | `/api/works/{work_id}`              | (空)                                | `works.py:139`                 | 删除作品         |
| GET    | `/api/works/sessions/list`          | (空)                                | `works.py:153`                 | 会话列表（TODO 接入前端） |
| POST   | `/api/works/sessions/create`        | `{ "inspiration": "...", "title"?: "..." }` | `works.py:160`         | 创建会话         |
| GET    | `/api/works/sessions/{session_id}`  | (空)                                | `works.py:168`                 | 会话详情         |
| PATCH  | `/api/works/sessions/{session_id}`  | `{ "title": "..." }`                | `works.py:177`                 | 更新会话标题     |
| DELETE | `/api/works/sessions/{session_id}`  | (空)                                | `works.py:186`                 | 删除会话         |
| POST   | `/api/works/sessions/{session_id}/load` | (空)                            | `works.py:195`                 | 加载会话         |

## 3. 配置（config）

| Method | Path                                | Body                                | 后端入口                       | 备注             |
| ------ | ----------------------------------- | ----------------------------------- | ------------------------------ | ---------------- |
| GET    | `/api/config/providers`             | (空)                                | `config_api.py:26`             | 列出所有供应商 + API Key 状态 |
| PUT    | `/api/config/providers/{provider_id}` | `{ api_key?, base_url?, model?, json_model? }` | `config_api.py:74` | provider_id ∈ `step / minimax / deepseek / openai / siliconflow / custom` |
| GET    | `/api/config/llm-config`            | (空)                                | `config_api.py:148`            | 当前激活的供应商配置 |
| PUT    | `/api/config/llm-config`            | `{ active_provider_id, per_agent_enabled, agent_providers, global_temperature, agent_temperatures }` | `config_api.py:163` | 切换供应商 |
| GET    | `/api/config/llm`                   | (空)                                | `config_api.py:205`            | 兼容旧接口       |
| PUT    | `/api/config/llm`                   | `{ api_key?, base_url?, model?, json_model? }` | `config_api.py:225`    | 兼容旧接口       |
| GET    | `/api/config/agents`                | (空)                                | `config_api.py:267`            | Agent 配置       |
| GET    | `/api/config/templates`             | (空)                                | `config_api.py:273`            | 创作模板         |
| GET    | `/api/config/app`                   | (空)                                | `config_api.py:282`            | 当前 App 配置    |
| PUT    | `/api/config/agents/{agent_name}`   | `{ model, temperature, max_tokens }` | `config_api.py:350`            | 更新 Agent 配置  |
| PUT    | `/api/config/app`                   | `{ confirm_mode, template_name, custom_target_words?, custom_part_count? }` | `config_api.py:371` | 切换模板       |

## 4. AI 改写（rewrite）

| Method | Path                | Body                                | 后端入口                  | 备注             |
| ------ | ------------------- | ----------------------------------- | ------------------------- | ---------------- |
| POST   | `/api/rewrite/`     | `{ work_id, part_num, mode, instruction? }` | `rewrite.py:16`     | mode ∈ `polish / expand / summarize / custom` |

## 5. SSE 实时事件（sse）

| Method | Path                                                | 后端入口              | 备注                       |
| ------ | --------------------------------------------------- | --------------------- | -------------------------- |
| GET    | `/api/sse/stream?work_id={id}`                      | `sse.py:136`          | 推送 phase/log/agent_call/part_complete/confirm/error/final |

## 6. 系统

| Method | Path              | 后端入口      | 备注       |
| ------ | ----------------- | ------------- | ---------- |
| GET    | `/api/health`     | `main.py:163` | 健康检查   |

---

## 7. review_report 数据契约（双形态 V6.1）

`review_report` 字段同时输出两种形态，确保前端新旧代码都能消费：

```json
{
  "logic": { /* LogicReviewAgent 原始输出 */ },
  "emotion": { /* EmotionReviewAgent 原始输出 */ },
  "consistency": { /* ConsistencyReviewAgent 原始输出 */ },
  "parts": [
    {
      "part": 1,
      "logic_score": 8.0,
      "emotion_score": 7.5,
      "consistency_score": 9.0,
      "p0_issues": [{ "id": "...", "description": "..." }],
      "p1_issues": [{ "id": "...", "description": "..." }]
    }
  ]
}
```

- 顶层 `logic / emotion / consistency` 摘要保留向后兼容。
- `parts[]` 由 `WritingService._build_review_parts_list` 从三个 agent 输出归一化，
  字段命名兼容 `per_part / parts / part_results / by_part` 等多形态。

---

## 8. 下一轮 TODO

- `POST /api/writing/resume/{work_id}`：实现真正的断点续写（见 review B.4）。
- `GET /api/works/{id}/review-report`：可选独立端点，避免每次拉整部作品数据。
- `POST /api/writing/cost-alert/{work_id}`：成本熔断（见 review D.1）。
