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
        <div class="stats-grid">
          <!-- 字数统计 -->
          <div class="stat-card">
            <div class="stat-label">总字数</div>
            <div class="stat-value">{{ wordCount.toLocaleString() }}</div>
          </div>
          <!-- 成本 -->
          <div class="stat-card">
            <div class="stat-label">预估成本</div>
            <div class="stat-value">¥{{ costData.estimated_cost_rmb || 0 }}</div>
          </div>
          <!-- 调用次数 -->
          <div class="stat-card">
            <div class="stat-label">LLM调用</div>
            <div class="stat-value">{{ costData.total_calls || 0 }}次</div>
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

const wordCount = computed(() => {
  const parts = workData.value.final_draft || workData.value.parts || {}
  return Object.values(parts).reduce((sum, text) => sum + (text?.length || 0), 0)
})

const avgScore = computed(() => {
  const report = workData.value.review_report
  if (!report) return '-'
  const logic = report.logic_score || 0
  const emotion = report.emotion_score || 0
  return ((logic + emotion) / 2).toFixed(1)
})

const scoreHistory = computed(() => {
  const report = workData.value.review_report
  if (!report || !report.parts) return []
  return report.parts.map(p => {
    const l = p.logic_score || 0
    const e = p.emotion_score || 0
    return Math.round((l + e) / 2 * 10) / 10
  })
})

const issues = computed(() => {
  const report = workData.value.review_report
  if (!report || !report.parts) return []
  const all = []
  for (const p of report.parts) {
    if (p.p0_issues) for (const i of p.p0_issues) all.push({ ...i, part: p.part, level: 'p0' })
    if (p.p1_issues) for (const i of p.p1_issues) all.push({ ...i, part: p.part, level: 'p1' })
  }
  return all
})

onMounted(async () => {
  workData.value = (await api.get(`/works/${workId}`)).data
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
