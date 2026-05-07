<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="work-title">{{ workData.title || '加载中...' }}</span>
          <span class="sse-status" :class="sseStatus">
            <span class="status-dot"></span>
            <span class="status-text">{{ sseStatusText }}</span>
          </span>
        </div>
        <div class="topbar-actions">
          <n-button v-if="writingStatus.running && !writingStatus.paused" @click="pauseWriting">暂停</n-button>
          <n-button v-if="writingStatus.paused" type="primary" @click="resumeWriting">继续</n-button>
          <n-button v-if="!writingStatus.running" type="primary" @click="startWriting">开始创作</n-button>
        </div>
      </div>

      <div class="content">
        <!-- 实时进度条 -->
        <ProgressBar :title="'创作进度'" :progress="currentProgress" :message="progressMessage" />

        <!-- 创作阶段进度 -->
        <div class="phase-progress">
          <div
            v-for="(phase, idx) in phases"
            :key="phase.id"
            class="phase-item"
            :class="{ active: currentPhase === phase.id, done: phaseDone(phase.id) }"
          >
            <div class="phase-dot">
              <span v-if="phaseDone(phase.id)">✓</span>
              <span v-else>{{ idx + 1 }}</span>
            </div>
            <div class="phase-label">{{ phase.name }}</div>
          </div>
        </div>

        <!-- Part进度 -->
        <div v-if="workData.part_outline && workData.part_outline.length" class="parts-progress">
          <div class="parts-title">章节进度</div>
          <div class="parts-list">
            <div
              v-for="(p, idx) in workData.part_outline"
              :key="idx"
              class="part-item"
              :class="{ active: currentPart === idx + 1, done: completedParts.includes(idx + 1) }"
            >
              <span>Part {{ idx + 1 }}</span>
              <span class="part-theme">{{ p.title || p.theme || '章节' + (idx + 1) }}</span>
              <span v-if="completedParts.includes(idx + 1)" class="part-done">✓</span>
            </div>
          </div>
        </div>

        <!-- 实时日志 -->
        <div class="log-panel">
          <div class="log-title">实时日志</div>
          <div class="log-list" ref="logList">
            <div v-for="(log, idx) in logs" :key="idx" class="log-item" :class="log.type">
              <span class="log-time">{{ log.time }}</span>
              <span class="log-msg">{{ log.msg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 确认弹窗 -->
    <n-modal v-model:show="showConfirm" preset="card" title="⚠️ 需要确认" style="width:500px">
      <div>{{ confirmMsg }}</div>
      <template #footer>
        <n-button @click="confirmAction('cancel')">取消</n-button>
        <n-button type="primary" @click="confirmAction('proceed')">确认继续</n-button>
      </template>
    </n-modal>

    <!-- 错误提示弹窗 -->
    <n-modal v-model:show="showError" preset="card" title="❌ 创作错误" style="width:500px">
      <div class="error-content">
        <div class="error-message">{{ errorMessage }}</div>
        <div v-if="errorSuggestion" class="error-suggestion">
          <div class="error-suggestion-title">建议：</div>
          <div class="error-suggestion-text">{{ errorSuggestion }}</div>
        </div>
      </div>
      <template #footer>
        <n-button type="primary" @click="showError = false">我知道了</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'
import ProgressBar from '@/components/ProgressBar.vue'

const route = useRoute()
const router = useRouter()
const workId = route.params.workId

const workData = ref({ title: '', parts: {}, part_outline: [] })
const writingStatus = ref({ running: false, paused: false, phase: '', current_part: 0 })
const logs = ref([])
const currentPhase = ref('')
const currentPart = ref(0)
const completedParts = ref([])
const showConfirm = ref(false)
const confirmMsg = ref('')
const currentProgress = ref(0)
const progressMessage = ref('准备开始创作')
const showError = ref(false)
const errorMessage = ref('')
const errorSuggestion = ref('')
const sseStatus = ref('disconnected') // connecting | connected | disconnected | error
let eventSource = null

const phases = [
  { id: 'phase1', name: '灵感解析' },
  { id: 'phase2', name: '情节规划' },
  { id: 'phase3', name: '章节创作' },
  { id: 'phase4', name: '风格优化' },
]

const sseStatusText = computed(() => {
  const map = {
    connecting: '连接中',
    connected: '已连接',
    disconnected: '已断开',
    error: '连接异常',
  }
  return map[sseStatus.value] || '未知'
})

function phaseDone(id) {
  const order = ['phase1', 'phase2', 'phase3', 'phase4']
  return order.indexOf(id) < order.indexOf(currentPhase.value)
}

onMounted(async () => {
  // 加载作品
  workData.value = (await api.get(`/works/${workId}`)).data

  // 加载状态
  try {
    const s = (await api.get(`/writing/status/${workId}`)).data
    writingStatus.value = s
  } catch {}

  // 连接SSE（按workId隔离）
  sseStatus.value = 'connecting'
  eventSource = new EventSource(`/api/sse/stream?work_id=${workId}`)
  eventSource.onopen = () => {
    sseStatus.value = 'connected'
  }
  eventSource.onerror = () => {
    sseStatus.value = 'error'
  }
  eventSource.onmessage = (e) => {
    const ev = JSON.parse(e.data)
    handleEvent(ev)
  }
})

onUnmounted(() => {
  if (eventSource) eventSource.close()
})

function handleEvent(ev) {
  if (ev.type === 'log') {
    logs.value.push({ time: new Date().toLocaleTimeString('zh-CN'), msg: ev.data.message, type: 'info' })
    nextTick(() => scrollLog())
  } else if (ev.type === 'phase') {
    currentPhase.value = ev.data.phase
  } else if (ev.type === 'part_complete') {
    completedParts.value.push(ev.data.part)
  } else if (ev.type === 'agent_call') {
    if (ev.data.status === 'start') {
      currentPart.value = ev.data.part
      logs.value.push({ time: new Date().toLocaleTimeString('zh-CN'), msg: `🤖 ${ev.data.message}`, type: 'agent' })
    } else {
      logs.value.push({ time: new Date().toLocaleTimeString('zh-CN'), msg: `✅ ${ev.data.message}`, type: 'done' })
    }
    nextTick(() => scrollLog())
  } else if (ev.type === 'confirm') {
    showConfirm.value = true
    confirmMsg.value = ev.data.message
  } else if (ev.type === 'error') {
    logs.value.push({ time: new Date().toLocaleTimeString('zh-CN'), msg: `❌ ${ev.data.message}`, type: 'error' })
    errorMessage.value = ev.data.message
    errorSuggestion.value = ev.data.recovery_suggestion || ''
    showError.value = true
  } else if (ev.type === 'final') {
    router.push(`/report/${workId}`)
  } else if (ev.type === 'progress') {
    currentProgress.value = ev.data.progress
    progressMessage.value = ev.data.message
  }
}

function scrollLog() {
  const el = document.querySelector('.log-list')
  if (el) el.scrollTop = el.scrollHeight
}

async function startWriting() {
  console.log('[Frontend] 点击开始创作，准备调用API')
  try {
    const res = await api.post('/writing/start', { work_id: workId })
    console.log('[Frontend] API调用成功:', res.data)
    writingStatus.value.running = true
  } catch (err) {
    console.error('[Frontend] API调用失败:', err)
    logs.value.push({ 
      time: new Date().toLocaleTimeString('zh-CN'), 
      msg: `❌ 启动创作失败: ${err.message}`, 
      type: 'error' 
    })
    errorMessage.value = `启动创作失败: ${err.message}`
    showError.value = true
  }
}

async function pauseWriting() {
  try {
    await api.post('/writing/pause', { work_id: workId })
    writingStatus.value.paused = true
  } catch (err) {
    console.error('[Frontend] 暂停创作失败:', err)
    logs.value.push({ 
      time: new Date().toLocaleTimeString('zh-CN'), 
      msg: `❌ 暂停创作失败: ${err.message}`, 
      type: 'error' 
    })
  }
}

async function resumeWriting() {
  try {
    await api.post(`/writing/resume/${workId}`)
    writingStatus.value.paused = false
  } catch (err) {
    console.error('[Frontend] 继续创作失败:', err)
    logs.value.push({ 
      time: new Date().toLocaleTimeString('zh-CN'), 
      msg: `❌ 继续创作失败: ${err.message}`, 
      type: 'error' 
    })
  }
}

async function confirmAction(choice) {
  showConfirm.value = false
  try {
    await api.post('/works/writing/confirm', { work_id: workId, choice })
    console.log(`[Frontend] 确认选择已发送: ${choice}`)
  } catch (err) {
    console.error('[Frontend] 发送确认选择失败:', err)
    logs.value.push({ 
      time: new Date().toLocaleTimeString('zh-CN'), 
      msg: `❌ 发送确认失败: ${err.message}`, 
      type: 'error' 
    })
  }
}
</script>

<style scoped>
.layout { display: flex; height: 100vh; background: var(--color-background, #f8fafc); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { 
  display: flex; 
  align-items: center; 
  justify-content: space-between; 
  padding: 14px 24px; 
  border-bottom: 1px solid var(--color-border, #eee); 
  background: var(--color-surface, #fff);
  backdrop-filter: blur(10px);
}
.topbar-left { display: flex; align-items: center; gap: 16px; }
.work-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary, #333); }
.topbar-actions { display: flex; gap: 8px; }

.sse-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  background: var(--color-background);
  border: 1px solid var(--color-border);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-muted);
  transition: background 0.3s ease;
}

.sse-status.connecting .status-dot {
  background: var(--color-warning, #f59e0b);
  animation: pulse 1.5s ease-in-out infinite;
}

.sse-status.connected .status-dot {
  background: var(--color-success, #22c55e);
}

.sse-status.disconnected .status-dot {
  background: var(--color-text-muted);
}

.sse-status.error .status-dot {
  background: var(--color-error, #ef4444);
  animation: pulse 1.5s ease-in-out infinite;
}

.status-text {
  color: var(--color-text-secondary);
}

.sse-status.connected .status-text {
  color: var(--color-success);
}

.sse-status.error .status-text {
  color: var(--color-error);
}
.content { 
  flex: 1; 
  overflow-y: auto; 
  padding: var(--content-padding, 24px); 
  display: flex; 
  flex-direction: column; 
  gap: 24px;
  background: var(--color-background, #f8fafc);
}
.phase-progress { 
  display: flex; 
  gap: 0; 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 24px 20px; 
  border: 1px solid var(--color-border, #e2e8f0);
  box-shadow: var(--shadow-md);
}
.phase-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 10px; position: relative; }
.phase-item:not(:last-child)::after { content: ''; position: absolute; top: 16px; left: 60%; width: 80%; height: 3px; background: var(--color-border, #eee); transition: background 0.4s ease; }
.phase-item.done::after { background: linear-gradient(90deg, var(--color-primary), var(--color-accent)); }
.phase-dot { 
  width: 32px; 
  height: 32px; 
  border-radius: 50%; 
  background: var(--color-background);
  border: 2px solid var(--color-border);
  color: var(--color-text-secondary, #888); 
  display: flex; 
  align-items: center; 
  justify-content: center; 
  font-size: 13px; 
  font-weight: 600; 
  z-index: 1;
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}
.phase-item.done .phase-dot { 
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent)); 
  border-color: var(--color-primary);
  color: #fff; 
}
.phase-item.active .phase-dot { 
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent)); 
  border-color: var(--color-primary);
  color: #fff; 
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--color-primary) 20%, transparent);
  animation: phase-pulse 2s ease-in-out infinite;
}
.phase-label { font-size: 12px; color: var(--color-text-secondary, #888); transition: color 0.3s ease; font-weight: 500; }
.phase-item.done .phase-label, .phase-item.active .phase-label { 
  color: var(--color-primary); 
  font-weight: 600; 
}

@keyframes phase-pulse {
  0%, 100% { box-shadow: 0 0 0 5px color-mix(in srgb, var(--color-primary) 20%, transparent); }
  50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--color-primary) 10%, transparent); }
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 4px rgba(37,99,235,0.2); }
  50% { box-shadow: 0 0 0 6px rgba(37,99,235,0.15); }
}

.parts-progress { 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 20px; 
  border: 1px solid var(--color-border, #e2e8f0);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
}
.parts-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--color-text-primary, #333); }
.parts-list { display: flex; flex-wrap: wrap; gap: 8px; }
.part-item { 
  padding: 6px 14px; 
  border-radius: 20px; 
  background: var(--color-background, #f5f5f5); 
  font-size: 13px; 
  display: flex; 
  align-items: center; 
  gap: 8px; 
  border: 1px solid transparent;
  transition: all 0.25s ease;
}
.part-item:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
}
.part-item.active { 
  border-color: var(--color-primary, #2563eb); 
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary, #2563eb) 10%, white), transparent);
  color: var(--color-primary, #2563eb);
  font-weight: 500;
}
.part-item.done { background: linear-gradient(135deg, #f0fdf4, #dcfce7); color: var(--color-success, #16a34a); }
.part-theme { color: var(--color-text-secondary, #888); font-size: 12px; }
.part-done { color: var(--color-success, #16a34a); font-weight: bold; }
.log-panel { 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 20px; 
  border: 1px solid var(--color-border, #e2e8f0); 
  flex: 1; 
  display: flex; 
  flex-direction: column; 
  min-height: 300px;
  box-shadow: var(--shadow-md);
  position: relative;
  overflow: hidden;
}
.log-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-primary), transparent);
  opacity: 0.5;
}
.log-title { 
  font-size: 14px; 
  font-weight: 600; 
  margin-bottom: 12px; 
  color: var(--color-text-primary, #333);
  display: flex;
  align-items: center;
  gap: 8px;
}
.log-title::before {
  content: '';
  width: 4px;
  height: 16px;
  background: linear-gradient(180deg, var(--color-primary), var(--color-accent));
  border-radius: 2px;
}
.log-list { 
  flex: 1; 
  overflow-y: auto; 
  display: flex; 
  flex-direction: column; 
  gap: 2px; 
  font-family: 'Consolas', 'Monaco', monospace; 
  padding-right: 4px;
}
.log-item { 
  display: flex; 
  gap: 12px; 
  font-size: 13px; 
  line-height: 1.7; 
  padding: 6px 10px; 
  border-radius: 6px; 
  transition: all 0.2s ease;
  animation: log-fade-in 0.3s ease-out;
}
@keyframes log-fade-in {
  from { opacity: 0; transform: translateX(-10px); }
  to { opacity: 1; transform: translateX(0); }
}
.log-item:hover { 
  background: var(--color-background, #f8fafc); 
}
.log-time {
  color: var(--color-text-muted, #cbd5e1);
  flex-shrink: 0;
  font-size: 12px;
}
.log-msg { color: var(--color-text-primary, #444); }
.log-item.done .log-msg { color: var(--color-success, #22c55e); font-weight: 500; }
.log-item.error .log-msg { color: var(--color-error, #ef4444); font-weight: 500; }
.log-item.agent .log-msg { 
  color: var(--color-primary, #0ea5e9); 
  font-weight: 500;
}

.error-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.error-message {
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-primary, #333);
}

.error-suggestion {
  background: var(--color-background, #f5f5f5);
  padding: 12px;
  border-radius: var(--radius-md, 8px);
  font-size: 13px;
  line-height: 1.5;
  border-left: 3px solid var(--color-warning, #f59e0b);
}

.error-suggestion-title {
  font-weight: 600;
  color: var(--color-warning, #f59e0b);
  margin-bottom: 4px;
}

.error-suggestion-text {
  color: var(--color-text-secondary, #555);
}

@media (max-width: 768px) {
  .phase-progress {
    overflow-x: auto;
    padding: 16px;
  }
  .phase-item {
    min-width: 80px;
  }
  .content {
    padding: 16px;
    gap: 16px;
  }
  .parts-list {
    flex-direction: column;
  }
}
</style>
