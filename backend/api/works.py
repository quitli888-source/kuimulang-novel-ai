"""
番茄小说AI创作系统 V5 - 作品管理API
V5.1改动：添加会话管理功能
"""
import json
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import WORKS_DIR
from core.session_manager import session_manager

router = APIRouter()


class CreateWorkRequest(BaseModel):
    title: str
    inspiration: str


class UpdateMetaRequest(BaseModel):
    title: Optional[str] = None
    inspiration: Optional[str] = None
    # R4-P1-5: 角色档案编辑
    characters: Optional[list] = None


class CreateSessionRequest(BaseModel):
    inspiration: str
    title: Optional[str] = None


class UpdateSessionRequest(BaseModel):
    title: str


# V6.1: /writing/confirm 已迁移至 api/writing.py（与 start/pause/resume 同侧）。
# 旧的 /api/works/writing/confirm 路由保留为向后兼容薄壳（重定向至新路径）。


# ---- 作品数据文件 ----
def get_work_file(work_id: str) -> Path:
    return WORKS_DIR / f"{work_id}.json"


def list_works() -> list[dict]:
    """列出所有作品"""
    works = []
    for f in WORKS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            works.append({
                "id": data.get("id", f.stem),
                "title": data.get("title", "未命名"),
                "inspiration": data.get("inspiration", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "phase": data.get("phase", "init"),
                "word_count": data.get("word_count", 0),
            })
        except:
            continue
    works.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return works


@router.get("/")
def get_works():
    return {"works": list_works()}


@router.post("/")
def create_work(req: CreateWorkRequest):
    import uuid
    from datetime import datetime

    work_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()

    data = {
        "id": work_id,
        "title": req.title,
        "inspiration": req.inspiration,
        "created_at": now,
        "updated_at": now,
        "phase": "init",
        "word_count": 0,
        "parts": {},
        "part_outline": [],
        "review_report": None,
        "final_draft": {},
        "title_options": None,
        "tags": None,
        "character_state_track": {},
    }

    path = get_work_file(work_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"id": work_id, "title": req.title}


@router.get("/{work_id}")
def get_work(work_id: str):
    path = get_work_file(work_id)
    if not path.exists():
        raise HTTPException(404, "作品不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    # R3-P0-1: 把 in-memory cost_tracker 摘要注入响应，供 Report.vue 渲染
    # 成本 / 调用次数卡（costData 永远空 修复）
    # R5-P0-3: 先 attach_work(work_id, data.get('cost_summary')) 把 work JSON 里的
    # 历史 calls 叠加到 tracker.calls，再 get_summary() —— 双轨合并避免重启清零。
    try:
        from core.cost_tracker import get_tracker
        tracker = get_tracker()
        tracker.attach_work(work_id, data.get("cost_summary"))
        data["cost_summary"] = tracker.get_summary()
        # R4-P1-4: 按 Part 成本明细供 Report.vue 成本曲线渲染
        try:
            total_parts = max(
                len(data.get("parts", {}) or {}),
                len(data.get("part_outline", []) or []),
                0,
            )
            per_part = []
            for part_num in range(1, total_parts + 1):
                per_part.append({
                    "part": part_num,
                    **tracker.get_per_part_summary(part_num),
                })
            data["cost_per_part"] = per_part
        except Exception as per_part_err:
            data["cost_per_part"] = []
            print(f"[works.get_work] cost_per_part 聚合失败（不影响主流程）: {per_part_err}")
    except Exception:
        # tracker 不可用时给空 summary，前端兜底为 0
        data["cost_summary"] = {
            "total_calls": 0,
            "total_tokens": 0,
            "estimated_cost_rmb": 0.0,
            "calls": [],
        }
        data["cost_per_part"] = []
    return data


@router.patch("/{work_id}")
def update_work(work_id: str, req: UpdateMetaRequest):
    path = get_work_file(work_id)
    if not path.exists():
        raise HTTPException(404, "作品不存在")

    data = json.loads(path.read_text(encoding="utf-8"))
    from datetime import datetime
    data["updated_at"] = datetime.now().isoformat()

    if req.title is not None:
        data["title"] = req.title
    if req.inspiration is not None:
        data["inspiration"] = req.inspiration
    # R4-P1-5: 角色档案写回（前端 CharacterEdit.vue 提交）
    if req.characters is not None:
        # 简单校验：必须是 list
        if isinstance(req.characters, list):
            data["characters"] = req.characters
        else:
            raise HTTPException(400, "characters 字段必须是 list")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@router.delete("/{work_id}")
def delete_work(work_id: str):
    path = get_work_file(work_id)
    if not path.exists():
        raise HTTPException(404, "作品不存在")
    path.unlink()
    # 清理output目录
    output_dir = WORKS_DIR / work_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    return {"ok": True}


# ---- 会话管理API ----
@router.get("/sessions/list")
def list_sessions():
    """列出所有会话"""
    sessions = session_manager.list_sessions()
    return {"sessions": sessions}


@router.post("/sessions/create")
def create_session(req: CreateSessionRequest):
    """创建新会话"""
    session_id = session_manager.create_session(req.inspiration, req.title)
    session_info = session_manager.get_session_info(session_id)
    return {"session_id": session_id, "session_info": session_info}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """获取会话信息"""
    session_info = session_manager.get_session_info(session_id)
    if not session_info:
        raise HTTPException(404, "会话不存在")
    return {"session_info": session_info}


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, req: UpdateSessionRequest):
    """更新会话标题"""
    success = session_manager.update_session_title(session_id, req.title)
    if not success:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话"""
    success = session_manager.delete_session(session_id)
    if not success:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.post("/sessions/{session_id}/load")
def load_session(session_id: str):
    """加载会话"""
    story_state = session_manager.load_session(session_id)
    if not story_state:
        raise HTTPException(404, "会话不存在或加载失败")
    return {
        "ok": True,
        "session_id": session_id,
        "phase": story_state.phase
    }
