"""R4: 全流程 mock 验证 —— Phase1→4 全链路 + Phase4 新风格优化循环（不调真实 LLM）。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

results = []

def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), name, ('| ' + detail if detail else ''))

def run_full(tmp: Path):
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    work_id = 'r4_full_pipeline'
    work = {'id': work_id, 'title': 't', 'inspiration': '少年得到上古道种', 'phase': 'init',
            'parts': {}, 'part_summaries': {}, 'part_outline': [
                {'title': 'P1', 'core_event': '觉醒', 'emotion_target': '激动', 'key_dialogue': '这就是道种？', 'end_hook': '血月当空', 'pacing': '快', 'causality': '奇遇'},
                {'title': 'P2', 'core_event': '首战', 'emotion_target': '紧张', 'key_dialogue': '接我一剑', 'end_hook': '强敌现身', 'pacing': '快', 'causality': '报仇'},
                {'title': 'P3', 'core_event': '破境', 'emotion_target': '昂扬', 'key_dialogue': '即便如此', 'end_hook': '远行', 'pacing': '舒缓', 'causality': '成长'},
            ],
            'world_setting': '', 'characters': [], 'foreshadowing': [], 'cost_summary': None}
    (tmp / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')

    style_calls = {'n': 0}

    def mock_style(self, state, part_num, part_text, review_results=None, target_max=10000, target_min=0):
        style_calls['n'] += 1
        return part_text + '（已优化）'

    import core.config as config_mod
    import api.works as works_api
    with patch.object(config_mod, 'WORKS_DIR', tmp), patch.object(works_api, 'WORKS_DIR', tmp), \
         patch('core.agents.inspiration_agent.InspirationAgent.execute', return_value={'protagonist': {'identity': '林荒'}, 'core_conflict': '正邪之争', 'theme': '守护'}), \
         patch('core.agents.genre_agent.GenreAgent.execute', return_value={'genre_primary': '玄幻', 'genre_secondary': '东方玄幻'}), \
         patch('core.agents.plot_planner_agent.PlotPlannerAgent.execute', return_value={'world_setting': '九州', 'characters': [{'name': '林荒'}], 'part_outline': work['part_outline'], 'foreshadowing': []}), \
         patch('core.agents.part_writer_agent.PartWriterAgent.execute', return_value={'success': True, 'content': '正文内容。' * 200}), \
         patch('core.agents.logic_review_agent.LogicReviewAgent.execute', return_value={'score': 8, 'pass': True, 'issues': [], 'p0_count': 0, 'p1_count': 0, 'p2_count': 0, 'continuity_check': {}, 'strengths': [], 'verdict': 'ok'}), \
         patch('core.agents.emotion_review_agent.EmotionReviewAgent.execute', return_value={'emotion_score': 8, 'pass': True, 'highlights': [], 'weaknesses': [], 'enhancement_suggestions': [], 'verdict': 'ok'}), \
         patch('core.agents.consistency_review_agent.ConsistencyReviewAgent.execute', return_value={'overall_score': 8, 'pass': True, 'issues': [], 'character_states': {}, 'verdict': 'ok'}), \
         patch('core.agents.style_optimizer_agent.StyleOptimizerAgent.execute', mock_style):
        svc = WritingService(work_id, SSEEmitter())
        svc.cfg._part_count = 3
        svc.cfg.confirm_mode = False
        asyncio.run(svc.run())

    on_disk = json.loads((tmp / f'{work_id}.json').read_text(encoding='utf-8'))
    check('pipeline: 3 个 Part 全部落盘', len(on_disk.get('parts', {})) == 3, str(len(on_disk.get('parts', {}))))
    check('pipeline: phase=phase4', on_disk.get('phase') == 'phase4', str(on_disk.get('phase')))
    check('pipeline: part_outline 未被 resume 覆盖', len(on_disk.get('part_outline', [])) == 3)
    fd = on_disk.get('final_draft', {})
    check('pipeline: final_draft 有 3 个优化后 Part', len(fd) == 3, str(len(fd)))
    check('pipeline: StyleOptimizer 逐 Part 调用 3 次', style_calls['n'] == 3, f"style_calls={style_calls['n']}")
    check('pipeline: final_draft 含优化标记', all('（优化后）' not in v and v.endswith('。' * 200) or '已优化' in v for v in fd.values()) or any(v.endswith('（已优化）') for v in fd.values()))
    check('pipeline: review_report 生成', on_disk.get('review_report') is not None)

with tempfile.TemporaryDirectory() as td:
    run_full(Path(td))

failed = [r for r in results if not r[1]]
print()
print(f'==== {len(results) - len(failed)}/{len(results)} PASS ====')
sys.exit(1 if failed else 0)
