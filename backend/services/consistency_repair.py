"""
R1-J: P0 定向修复回路 —— Phase 4 评审检出 P0 后系统内自愈。

背景：评审发现 P0 后此前仅记录进 review_report，全管线无重写路径，
G4（logic+consistency P0 总数 = 0）只能靠"预防全对"，容错为零。

设计（Reviewer 批准缩窄版）：
- 某 Part 聚合后 p0_count > 0 时，用该 Part 的 issues + 相关 established_facts
  生成 revision brief，调用 PartWriterAgent 带更强约束重写**一次**
- 仅重审该 Part 的 Logic + Consistency 两个 agent（Emotion 不受影响）
- 仍不过则**保留原文**，并在 review_report 对应 Part 标注
  revision_attempted / revision_passed；修订痕迹写入 s.data['revision_log']
- 重写走与 Phase 3 相同的 _save_chunk_progress 落盘路径
- 不静默丢内容；最多 1 轮（20 Part 墙钟约束，不做 2 轮）
"""
import asyncio
import time

from api.sse import EventType
from core.established_facts import EstablishedFacts
from core.logger import get_logger
from core.text_utils import truncate

logger = get_logger('consistency_repair')


class _RevisionStateProxy:
    """R1-J: 包一层 state，把修订指令追加到 PartWriterAgent 看到的上下文末尾。

    不修改 PartWriterAgent 本身（其 prompt/schema 不在本轮批准边界内），
    其余属性全部委托给内层 TempStoryState。
    """

    def __init__(self, inner, brief: str):
        self._inner = inner
        self._brief = brief

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def get_part_context(self, part_num):
        base = self._inner.get_part_context(part_num) or ''
        if self._brief:
            base += (
                '\n\n## ⚠ 修订指令（上一轮评审检出 P0 问题，本次重写必须逐条修正，'
                '其余设定/剧情推进不变）\n' + self._brief + '\n'
            )
        return base


def count_p0(logic_result: dict, consistency_result: dict) -> int:
    """聚合 Logic + Consistency 的 P0 计数。

    降级结果（agent 调用失败）不计入 —— 重写治不了基础设施故障，
    且降级结果的 P0 是"未检查"标记而非真实矛盾。
    """
    lr = logic_result or {}
    cr = consistency_result or {}
    if lr.get('_fallback') or str(cr.get('verdict', '')).startswith('检查失败'):
        return 0
    logic_p0 = 0
    try:
        logic_p0 = int(lr.get('p0_count', 0) or 0)
    except (TypeError, ValueError):
        logic_p0 = 0
    cons_issues = [i for i in (cr.get('issues') or [])
                   if isinstance(i, dict) and i.get('level') == 'P0']
    return logic_p0 + len(cons_issues)


class ConsistencyRepairer:
    """R1-J: 单 Part 的 P0 定向修复（最多 1 轮重写 + 重审）。"""

    def __init__(self, service, logic_agent, consistency_agent):
        self.service = service
        self.logic_agent = logic_agent
        self.consistency_agent = consistency_agent

    async def maybe_repair_part(self, part_num: int, part_text: str,
                                logic_result: dict, consistency_result: dict,
                                state_mock) -> dict:
        """p0_count > 0 时启动修复；返回 {} 表示未触发（行为与改前一致）。

        触发时返回标注 dict（revision_attempted/revision_passed，重审通过时
        附带替换用的 logic_result/consistency_result），由调用方合并进
        per_part_results 与 review_report。
        """
        s = self.service
        p0 = count_p0(logic_result, consistency_result)
        if p0 <= 0:
            return {}
        logger.info(f'[ConsistencyRepairer] Part {part_num} 检出 {p0} 个 P0，启动定向修复（最多 1 轮重写）')
        await s.emitter.emit(EventType.LOG, {
            'message': f'🔧 Part {part_num} 检出 {p0} 个 P0 问题，启动定向重写修复...',
            'work_id': s.work_id}, work_id=s.work_id)

        brief = self._build_revision_brief(part_num, logic_result, consistency_result)
        new_text = ''
        rewrite_error = ''
        try:
            from services.writing_service import TempStoryState
            from core.memory_manager import get_all_memory
            from core.agents.part_writer_agent import PartWriterAgent
            temp_state = TempStoryState(s.data, get_all_memory(), vector_store=s.vector_store)
            proxy = _RevisionStateProxy(temp_state, brief)
            writer = PartWriterAgent()
            writer.set_progress_callback(s.progress_callback)
            result = await asyncio.to_thread(writer.execute, proxy, part_num)
            if isinstance(result, dict) and result.get('success'):
                new_text = result.get('content', '') or ''
        except Exception as e:
            rewrite_error = str(e)[:200]
            logger.info(f'[ConsistencyRepairer] Part {part_num} 重写失败（保留原文）: {e}')

        # 重写产物不可用（空/过短/异常）—— 保留原文，仅留痕
        min_acceptable = max(500, len(part_text) // 3)
        if not new_text or len(new_text) < min_acceptable:
            note = {'revision_attempted': True, 'revision_passed': False,
                    'revision_error': rewrite_error or 'rewrite_empty_or_too_short'}
            self._append_revision_log(part_num, p0, note)
            await s.emitter.emit(EventType.LOG, {
                'message': f'↩️ Part {part_num} 重写产物不可用，保留原文',
                'work_id': s.work_id}, work_id=s.work_id)
            return note

        # 重审 Logic + Consistency（Emotion 不参与，不影响其评审结果）
        new_logic = await self._re_review(self.logic_agent, 'logic', part_num, new_text, state_mock)
        new_cons = await self._re_review(self.consistency_agent, 'consistency', part_num, new_text, state_mock)
        residual_p0 = count_p0(new_logic, new_cons)

        if residual_p0 <= 0:
            # 通过：落盘修订稿（与 Phase 3 相同的 checkpoint 路径）
            summary = truncate(new_text, n=200, suffix='...')
            s._save_chunk_progress(part_num, new_text, summary)
            part_key = str(part_num)
            state_mock.parts[part_key] = new_text
            state_mock.final_draft[part_key] = new_text
            note = {'revision_attempted': True, 'revision_passed': True,
                    'logic_result': new_logic, 'consistency_result': new_cons}
            self._append_revision_log(part_num, p0, note)
            await s.emitter.emit(EventType.LOG, {
                'message': f'✅ Part {part_num} 重写修复完成（重审 P0 归零，{len(new_text)} 字）',
                'work_id': s.work_id}, work_id=s.work_id)
            return note

        # 仍不过：保留原文（恢复落盘），仅留痕
        s._save_chunk_progress(part_num, part_text, truncate(part_text, n=200, suffix='...'))
        note = {'revision_attempted': True, 'revision_passed': False, 'residual_p0': residual_p0}
        self._append_revision_log(part_num, p0, note)
        await s.emitter.emit(EventType.LOG, {
            'message': f'↩️ Part {part_num} 重写后仍有 {residual_p0} 个 P0，保留原文',
            'work_id': s.work_id}, work_id=s.work_id)
        return note

    async def _re_review(self, agent, kind: str, part_num: int, part_text: str, state_mock) -> dict:
        try:
            return await asyncio.to_thread(agent.execute, state_mock, part_num, part_text)
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] 重审 {kind} Part {part_num} 失败: {e}')
            return self.service._review_failure(kind, part_num, e)

    def _build_revision_brief(self, part_num: int, logic_result: dict, consistency_result: dict) -> str:
        """用 issues + 相关 established_facts 生成 revision brief。"""
        l_p0 = [i for i in ((logic_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0']
        c_p0 = [i for i in ((consistency_result or {}).get('issues') or [])
                if isinstance(i, dict) and i.get('level') == 'P0']
        lines = []
        lr = logic_result or {}
        if lr.get('p0_count') and not l_p0:
            lines.append(f"- 逻辑审查判定 P0 共 {lr.get('p0_count')} 处；结论: {(lr.get('verdict') or '')[:120]}")
        for i in l_p0 + c_p0:
            desc = (i.get('description') or '').strip()
            sugg = (i.get('suggestion') or '').strip()
            loc = (i.get('location') or f'Part {part_num}').strip()
            if not desc:
                continue
            lines.append(f"- [{loc}] {desc}" + (f' → 修正建议: {sugg}' if sugg else ''))
        brief = '\n'.join(lines) if lines else '- 评审检出 P0 一致性问题，请对照前文事实清单全面自查。'
        facts_block = self._facts_block(part_num)
        if facts_block:
            brief += '\n\n' + facts_block
        return brief

    def _facts_block(self, part_num: int) -> str:
        """渲染 Part 1..N-1 的已确立事实（修订的权威基线）。"""
        facts = EstablishedFacts()
        raw = self.service.data.get('established_facts')
        if isinstance(raw, dict):
            try:
                facts.from_dict(raw)
            except Exception:
                return ''
        try:
            block = facts.render_for_prompt(before_part_num=part_num)
        except Exception:
            return ''
        if not block:
            return ''
        return '【前文已确立事实清单——重写内容不得与本表矛盾】\n' + block

    def _append_revision_log(self, part_num: int, p0_before: int, note: dict) -> None:
        """修订痕迹落盘（s.data['revision_log']，resume 可查）。"""
        entry = {
            'part': part_num,
            'p0_before': p0_before,
            'revision_attempted': bool(note.get('revision_attempted')),
            'revision_passed': bool(note.get('revision_passed')),
            'residual_p0': note.get('residual_p0'),
            'revision_error': note.get('revision_error'),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        try:
            log = list(self.service.data.get('revision_log') or [])
            log.append(entry)
            self.service.data['revision_log'] = log
            self.service._save()
        except Exception as e:
            logger.info(f'[ConsistencyRepairer] revision_log 落盘失败（不影响主流程）: {e}')
