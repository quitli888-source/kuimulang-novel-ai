<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="work-title">{{ workData.title }} - 创作报告</span>
        </div>
      </div>

      <div class="content">
        <!-- 评审报告占位提示（兼容空报告场景） -->
        <div v-if="!hasReport" class="empty-report">
          <div class="empty-report-icon">📋</div>
          <div class="empty-report-title">暂无评审报告</div>
          <div class="empty-report-sub">完成 Phase4 风格优化后，逻辑 / 情感 / 一致性评分会显示在这里</div>
        </div>

        <template v-else>
        <div class="stats-grid">
          <!-- 字数统计 -->
          <div class="stat-card">
            <div class="stat-label">总字数</div>
            <div class="stat-value">{{ wordCount.toLocaleString() }}</div>
          </div>
          <!-- 成本 -->
          <div class="stat-card">
            <div class="stat-label">预估成本</div>
            <div class="stat-value">
              <template v-if="costData && costData.total_calls > 0">¥{{ costData.estimated_cost_rmb || 0 }}</template>
              <template v-else><span class="muted">成本数据未记录</span></template>
            </div>
          </div>
          <!-- 调用次数 -->
          <div class="stat-card">
            <div class="stat-label">LLM调用</div>
            <div class="stat-value">
              <template v-if="costData && costData.total_calls > 0">{{ costData.total_calls || 0 }}次</template>
              <template v-else><span class="muted">-</span></template>
            </div>
          </div>
          <!-- 评分 -->
          <div class="stat-card">
            <div class="stat-label">综合评分</div>
            <div class="stat-value score">{{ avgScore }}/10</div>
          </div>
        </div>

        <!-- 评分曲线 -->
        <div class="chart-card">
          <div class="card-title">评分趋势</div>
          <div class="chart-placeholder">
            <div v-for="(score, idx) in scoreHistory" :key="idx" class="bar-wrap">
              <div class="bar" :style="{ height: (score / 10 * 100) + '%' }">{{ score }}</div>
              <div class="bar-label">P{{ idx + 1 }}</div>
            </div>
            <div v-if="!scoreHistory.length" class="empty-issues">暂无评分数据</div>
          </div>
        </div>

        <!-- R4-P1-4: 成本趋势（每 Part 成本柱状图，自绘 SVG 不引入新依赖） -->
        <div class="chart-card">
          <div class="card-title">
            <span>成本趋势</span>
            <span v-if="totalCostTrend > 0" class="chart-sub">合计 ¥{{ totalCostTrend.toFixed(4) }}</span>
          </div>
          <div v-if="!costPerPart.length" class="empty-issues">暂无成本数据（成本仅在 Phase3+ 阶段记录）</div>
          <div v-else class="cost-chart-wrap">
            <svg class="cost-svg" :viewBox="`0 0 ${costSvgWidth} ${costSvgHeight}`" preserveAspectRatio="none">
              <!-- 坐标轴 -->
              <line :x1="40" :y1="costSvgHeight - 20" :x2="costSvgWidth - 10" :y2="costSvgHeight - 20" stroke="#e2e8f0" stroke-width="1" />
              <line :x1="40" :y1="10" :x2="40" :y2="costSvgHeight - 20" stroke="#e2e8f0" stroke-width="1" />
              <!-- 折线 -->
              <polyline
                v-if="costPolylinePoints"
                :points="costPolylinePoints"
                fill="none"
                stroke="url(#costGradient)"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <!-- 面积 -->
              <polygon
                v-if="costAreaPoints"
                :points="costAreaPoints"
                fill="url(#costFillGradient)"
                opacity="0.18"
              />
              <!-- 数据点 -->
              <circle
                v-for="pt in costPoints"
                :key="`p${pt.part}`"
                :cx="pt.x"
                :cy="pt.y"
                r="3"
                fill="#2563eb"
              >
                <title>Part {{ pt.part }}: ¥{{ pt.cost.toFixed(4) }}</title>
              </circle>
              <!-- 定义 -->
              <defs>
                <linearGradient id="costGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stop-color="#06b6d4" />
                  <stop offset="100%" stop-color="#2563eb" />
                </linearGradient>
                <linearGradient id="costFillGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stop-color="#2563eb" stop-opacity="0.6" />
                  <stop offset="100%" stop-color="#2563eb" stop-opacity="0" />
                </linearGradient>
              </defs>
              <!-- X 轴标签（每隔 N 个显示） -->
              <text
                v-for="lbl in costXLabels"
                :key="`xl${lbl.part}`"
                :x="lbl.x"
                :y="costSvgHeight - 6"
                text-anchor="middle"
                fill="#94a3b8"
                font-size="10"
              >P{{ lbl.part }}</text>
              <!-- Y 轴标签（最大值的 50%/100%） -->
              <text :x="36" :y="14" text-anchor="end" fill="#94a3b8" font-size="10">¥{{ maxPartCost.toFixed(3) }}</text>
              <text :x="36" :y="costSvgHeight - 22" text-anchor="end" fill="#94a3b8" font-size="10">¥0</text>
            </svg>
          </div>
        </div>

        <!-- P0/P1问题 -->
        <div class="issues-card">
          <div class="card-title">问题列表</div>
          <div v-if="!issues.length" class="empty-issues">✅ 没有发现重大问题</div>
          <div v-for="issue in issues" :key="issue.id" class="issue-item" :class="issue.level">
            <div class="issue-badge">{{ issue.level.toUpperCase() }}</div>
            <div class="issue-content">{{ issue.description }}</div>
            <div class="issue-part">Part {{ issue.part }}</div>
          </div>
        </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const route = useRoute()
const workId = route.params.workId

const workData = ref({ title: '', parts: {}, review_report: null, final_draft: {} })
const costData = ref({})
const costPerPart = ref([])  // R4-P1-4: 后端注入的 [{part, total_tokens, calls, estimated_cost_rmb}, ...]

// R4-P1-4: 成本曲线图计算
const costSvgWidth = 720
const costSvgHeight = 180
const chartLeftPad = 40
const chartRightPad = 10
const chartTopPad = 10
const chartBottomPad = 20

const maxPartCost = computed(() => {
  if (!costPerPart.value.length) return 0
  return Math.max(...costPerPart.value.map(p => p.estimated_cost_rmb || 0), 0.01)
})

const totalCostTrend = computed(() => {
  return costPerPart.value.reduce((s, p) => s + (p.estimated_cost_rmb || 0), 0)
})

const costPoints = computed(() => {
  if (!costPerPart.value.length) return []
  const innerW = costSvgWidth - chartLeftPad - chartRightPad
  const innerH = costSvgHeight - chartTopPad - chartBottomPad
  const max = maxPartCost.value
  const step = costPerPart.value.length > 1 ? innerW / (costPerPart.value.length - 1) : 0
  return costPerPart.value.map((p, idx) => ({
    part: p.part,
    cost: p.estimated_cost_rmb || 0,
    x: chartLeftPad + idx * step,
    y: chartTopPad + innerH - ((p.estimated_cost_rmb || 0) / max) * innerH,
  }))
})

const costPolylinePoints = computed(() => {
  return costPoints.value.map(pt => `${pt.x},${pt.y}`).join(' ')
})

const costAreaPoints = computed(() => {
  if (!costPoints.value.length) return ''
  const innerH = costSvgHeight - chartTopPad - chartBottomPad
  const baseY = chartTopPad + innerH
  const first = costPoints.value[0]
  const last = costPoints.value[costPoints.value.length - 1]
  const pts = costPoints.value.map(pt => `${pt.x},${pt.y}`).join(' ')
  return `${first.x},${baseY} ${pts} ${last.x},${baseY}`
})

const costXLabels = computed(() => {
  if (!costPerPart.value.length) return []
  const n = costPerPart.value.length
  // 每隔最多 10 个显示一个标签
  const step = Math.max(1, Math.floor(n / 10))
  return costPoints.value.filter((_, idx) => idx % step === 0 || idx === n - 1)
})

const wordCount = computed(() => {
  // R4-P2-x: final_draft 为空对象时（Phase4 未跑完）`{} || parts` 恒取 final_draft，
  // 导致总字数显示 0。空 final_draft 回退 parts。
  const fd = workData.value.final_draft
  const parts = (fd && Object.keys(fd).length) ? fd : (workData.value.parts || {})
  return Object.values(parts).reduce((sum, text) => sum + (text?.length || 0), 0)
})

const hasReport = computed(() => {
  const r = workData.value.review_report
  return !!(r && (r.parts || r.logic || r.emotion || r.consistency))
})

// 综合评分：优先读顶层聚合（report.logic.avg_score / report.emotion.avg_score），
// 兜底从 parts 求均值。
const avgScore = computed(() => {
  const report = workData.value.review_report
  if (!report) return '-'
  let logicAvg = 0
  let emotionAvg = 0
  if (report.logic && typeof report.logic.avg_score === 'number') {
    logicAvg = report.logic.avg_score
  } else if (Array.isArray(report.parts) && report.parts.length) {
    logicAvg = report.parts.reduce((s, p) => s + (p.logic_score || 0), 0) / report.parts.length
  }
  if (report.emotion && typeof report.emotion.avg_score === 'number') {
    emotionAvg = report.emotion.avg_score
  } else if (Array.isArray(report.parts) && report.parts.length) {
    emotionAvg = report.parts.reduce((s, p) => s + (p.emotion_score || 0), 0) / report.parts.length
  }
  if (!logicAvg && !emotionAvg) return '-'
  return ((logicAvg + emotionAvg) / 2).toFixed(1)
})

// 评分曲线：每个 Part 的 (logic_score + emotion_score) / 2
const scoreHistory = computed(() => {
  const report = workData.value.review_report
  if (!report || !Array.isArray(report.parts)) return []
  return report.parts.map(p => {
    const l = p.logic_score || 0
    const e = p.emotion_score || 0
    return Math.round((l + e) / 2 * 10) / 10
  })
})

const issues = computed(() => {
  const report = workData.value.review_report
  if (!report || !Array.isArray(report.parts)) return []
  const all = []
  for (const p of report.parts) {
    if (p.p0_issues) for (const i of p.p0_issues) all.push({ ...i, part: p.part, level: 'p0' })
    if (p.p1_issues) for (const i of p.p1_issues) all.push({ ...i, part: p.part, level: 'p1' })
  }
  return all
})

onMounted(async () => {
  workData.value = (await api.get(`/works/${workId}`)).data
  // R3-P0-1: 把后端注入的 cost_summary 同步到 costData（成本卡数据通路修复）
  costData.value = workData.value.cost_summary || {}
  // R4-P1-4: 加载按 Part 成本明细
  costPerPart.value = workData.value.cost_per_part || []
})
</script>

<style scoped>
.layout { display: flex; height: 100vh; background: var(--color-background, #f8fafc); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { 
  display: flex; 
  align-items: center; 
  padding: 14px 24px; 
  border-bottom: 1px solid var(--color-border, #eee); 
  background: var(--color-surface, #fff);
  backdrop-filter: blur(10px);
}
.topbar-left { display: flex; align-items: center; gap: 16px; }
.work-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary, #333); }
.content { 
  flex: 1; 
  overflow-y: auto; 
  padding: var(--content-padding, 24px); 
  display: flex; 
  flex-direction: column; 
  gap: 16px;
  background: var(--color-background, #f8fafc);
}
.stats-grid { 
  display: grid; 
  grid-template-columns: repeat(4, 1fr); 
  gap: 16px;
}
.stat-card { 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 20px; 
  border: 1px solid var(--color-border, #e2e8f0); 
  text-align: center;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--color-primary, #2563eb), var(--color-accent, #06b6d4));
  opacity: 0;
  transition: opacity 0.25s ease;
}
.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-md, 0 4px 6px rgba(37,99,235,0.15));
}
.stat-card:hover::before {
  opacity: 1;
}
.stat-label { font-size: 12px; color: var(--color-text-secondary, #888); margin-bottom: 8px; font-weight: 500; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--color-text-primary, #333); }
.stat-value.score { color: var(--color-primary, #2563eb); }
.muted { color: var(--color-text-secondary, #aaa); font-size: 14px; font-weight: 500; }

.chart-card, .issues-card { 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 20px; 
  border: 1px solid var(--color-border, #e2e8f0);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  transition: all 0.25s ease;
}
.chart-card:hover, .issues-card:hover {
  box-shadow: var(--shadow-md, 0 4px 6px rgba(0,0,0,0.08));
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--color-text-primary, #333);
  display: flex;
  align-items: center;
  gap: 8px;
}
.chart-sub {
  font-size: 12px;
  color: var(--color-text-secondary, #888);
  font-weight: 500;
  margin-left: auto;
}

/* R4-P1-4: 成本曲线 */
.cost-chart-wrap {
  width: 100%;
  background: linear-gradient(180deg, transparent, rgba(37, 99, 235, 0.02));
  border-radius: var(--radius-md, 8px);
  padding: 4px;
}
.cost-svg {
  width: 100%;
  height: 180px;
  display: block;
}
.chart-placeholder { 
  display: flex; 
  align-items: flex-end; 
  gap: 12px; 
  height: 150px;
  padding: 10px 0;
}
.bar-wrap { 
  flex: 1; 
  display: flex; 
  flex-direction: column; 
  align-items: center; 
  gap: 4px; 
  height: 100%; 
  justify-content: flex-end;
}
.bar { 
  width: 100%; 
  max-width: 50px; 
  background: linear-gradient(to top, var(--color-primary, #2563eb), var(--color-primaryHover, #3b82f6)); 
  border-radius: 4px 4px 0 0; 
  display: flex; 
  align-items: flex-end; 
  justify-content: center; 
  font-size: 12px; 
  color: #fff; 
  font-weight: 600; 
  min-height: 10px;
  transition: height 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}
.bar:hover {
  filter: brightness(1.1);
  transform: scaleX(1.05);
}
.bar-label { font-size: 11px; color: var(--color-text-secondary, #aaa); }
.empty-issues { 
  color: var(--color-success, #52c41a); 
  font-size: 14px; 
  padding: 16px 0;
  text-align: center;
  font-weight: 500;
}
.issue-item { 
  display: flex; 
  align-items: flex-start; 
  gap: 10px; 
  padding: 12px 0; 
  border-bottom: 1px solid var(--color-background, #f5f5f5);
  transition: background 0.2s ease;
  border-radius: var(--radius-sm, 6px);
}
.issue-item:hover {
  background: var(--color-background, #f8fafc);
  padding-left: 8px;
  padding-right: 8px;
}
.issue-badge { 
  padding: 3px 8px; 
  border-radius: var(--radius-sm, 4px); 
  font-size: 11px; 
  font-weight: 700; 
  flex-shrink: 0;
  letter-spacing: 0.5px;
}
.p0 .issue-badge { 
  background: linear-gradient(135deg, #fef2f2, #fee2e2); 
  color: var(--color-error, #dc2626);
  border: 1px solid #fecaca;
}
.p1 .issue-badge { 
  background: linear-gradient(135deg, #fffbeb, #fef3c7); 
  color: var(--color-warning, #d97706);
  border: 1px solid #fde68a;
}
.issue-content {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-primary, #444);
  line-height: 1.5;
}
.issue-part {
  font-size: 12px;
  color: var(--color-text-secondary, #aaa);
  flex-shrink: 0;
  font-weight: 500;
}

.empty-report {
  background: var(--color-surface, #fff);
  border: 1px dashed var(--color-border, #e2e8f0);
  border-radius: var(--radius-lg, 12px);
  padding: 60px 24px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.empty-report-icon {
  font-size: 48px;
  line-height: 1;
}
.empty-report-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text-primary, #333);
}
.empty-report-sub {
  font-size: 13px;
  color: var(--color-text-secondary, #888);
  max-width: 480px;
  line-height: 1.6;
}

@media (max-width: 1024px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .topbar {
    padding: 12px 16px;
    flex-wrap: wrap;
    gap: 12px;
  }
  .content {
    padding: 16px;
  }
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }
  .stat-card {
    padding: 16px;
  }
  .stat-value {
    font-size: 24px;
  }
}

@media (max-width: 480px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .chart-placeholder {
    overflow-x: auto;
  }
}
</style>
