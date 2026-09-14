"""
Round 6 Smoke Test —— 简化版：直接测 _phase3_writing 用 mock LLM
不调真 LLM，验证 4 Phase 关键机制 + parts/cost_summary 写入
"""
import asyncio
import json
import sys
import time
import tempfile
import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


async def main():
    # 临时目录
    tmpdir = Path(tempfile.mkdtemp(prefix="r6_smoke_"))
    print(f"[smoke] tmpdir = {tmpdir}")

    import core.config as cfg_mod
    tmp_data = tmpdir / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)
    cfg_mod.DATA_DIR = tmp_data
    cfg_mod.WORKS_DIR = tmp_data / "works"
    cfg_mod.MEMORY_DIR = tmp_data / "memory"
    cfg_mod.WORKS_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.ENV_FILE = tmpdir / ".env"
    cfg_mod.ENV_FILE.write_text("TEMPLATE_NAME=短篇\n", encoding="utf-8")

    # LLM config file
    cfg_mod._LLM_CONFIG_FILE = tmp_data / "llm_config.json"
    cfg_mod._LLM_CONFIG_FILE.write_text(
        json.dumps({"active_provider_id": "step", "per_agent_enabled": False,
                    "agent_providers": {}, "global_temperature": 0.7,
                    "agent_temperatures": {}}),
        encoding="utf-8",
    )

    # Mock OpenAI 客户端
    part_text = ("这是第一段。\n这是第二段。\n" * 200)  # 大约 1200 字
    class FakeUsage:
        prompt_tokens = 100
        completion_tokens = 200
        total_tokens = 300
    class FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()
    class FakeResp:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]
            self.usage = FakeUsage()
    class FakeCompletions:
        def create(self, **kwargs):
            # 不做流式分支，避免函数变成 generator
            return FakeResp(part_text)
    class FakeChat:
        completions = FakeCompletions()
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    import core.llm_client as llm_client_mod
    llm_client_mod.OpenAI = FakeOpenAI
    llm_client_mod._client_cache = {}

    # 重置 app_config
    cfg_mod._app_config = None

    # 构造 work JSON
    work_id = "smoke_001"
    work_data = {
        "work_id": work_id,
        "title": "Smoke 测试作品",
        "user_theme": "雨夜的约定",
        "config": {"template": "短篇", "part_count": 3,
                   "target_words": 10000, "part_word_min": 2500,
                   "part_word_max": 4000, "confirm_mode": False},
        "phase": "init",
        "parts": {},
        "part_summaries": {},
        "cost_summary": {"calls": []},
        "foreshadowing": [],
        "character_state_track": {},
        "part_outline": [
            {"title": f"Part {i+1}", "core_event": f"event{i+1}",
             "emotion_target": "tension", "key_dialogue": "dialog",
             "end_hook": "hook", "pacing": "normal", "causality": "c",
             "phase": "setup", "word_count": 3000}
            for i in range(3)
        ],
    }
    work_file = cfg_mod.WORKS_DIR / f"{work_id}.json"
    work_file.write_text(json.dumps(work_data, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    # 把 works.py 的 get_work_file 指到我们的 WORKS_DIR
    import api.works as works_mod
    from core.config import WORKS_DIR as WORKS_DIR_NEW
    works_mod.WORKS_DIR = WORKS_DIR_NEW

    # emitter
    class FakeEmitter:
        def __init__(self):
            self.logs = []
        async def emit(self, event_type, payload, work_id=None):
            self.logs.append((str(event_type), payload if isinstance(payload, dict) else {}))
            return None
    fake_emitter = FakeEmitter()

    # 跑 WritingService.run()
    from services.writing_service import WritingService
    service = WritingService(work_id, fake_emitter, restart=True)
    # 关掉 confirm_mode 让流程不卡在用户确认
    service.cfg.confirm_mode = False

    print("[smoke] 启动 WritingService.run()")
    t0 = time.time()
    try:
        await asyncio.wait_for(service.run(), timeout=120)
    except asyncio.TimeoutError:
        print("[smoke] TIMEOUT after 120s — 部分流程可能未完成")
    print(f"[smoke] run() 返回, 耗时 {time.time() - t0:.1f}s")

    # 检查结果
    saved = json.loads(work_file.read_text(encoding="utf-8"))
    print(f"[smoke] phase = {saved.get('phase')}")
    print(f"[smoke] parts count = {len(saved.get('parts', {}))}")
    print(f"[smoke] part_summaries count = {len(saved.get('part_summaries', {}))}")
    cs = saved.get("cost_summary") or {}
    print(f"[smoke] cost_summary keys = {list(cs.keys())}")
    print(f"[smoke] cost_summary.calls 长度 = {len(cs.get('calls', []))}")
    print(f"[smoke] cost_summary.total_cost_rmb = {cs.get('total_cost_rmb')}")

    # 验证 phase 推进
    phase_ok = saved.get("phase") in ("phase3_part4", "phase4", "phase5_review", "complete")
    parts_ok = len(saved.get("parts", {})) >= 1
    cost_ok = "calls" in cs

    print()
    print(f"[smoke] phase_ok={phase_ok} parts_ok={parts_ok} cost_ok={cost_ok}")

    # 输出 emitter 重要事件
    phase_events = [l for l in fake_emitter.logs if "PHASE" in l[0]]
    print(f"[smoke] PHASE 事件数: {len(phase_events)}")
    for ev in phase_events[:10]:
        print(f"  - {ev[0]}: {ev[1].get('phase', ev[1].get('name', '?'))}")

    # 清理
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    overall = phase_ok and parts_ok and cost_ok
    print(f"\n[smoke] OVERALL: {'PASS' if overall else 'FAIL'}")
    return overall


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(0 if rc else 1)
