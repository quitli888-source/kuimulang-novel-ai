import { defineStore } from 'pinia'
import api from '@/api'
import { safeStorage, throttledPersist, flushPersist } from '@/utils/safeStorage'

// =====================================================================
// R4-P0-1 SSE 二合一（2026-09-14）：
//   删除 handleSSEEvent / initSSE / closeSSE / reconnect 等冗余 SSE 处理逻辑。
//   WritingProgress.vue 直接订阅 EventSource，调本 store 暴露的 7 个 action。
//   本文件仅保留：state + 持久化 + 7 个 SSE 事件 action + 状态查询。
// =====================================================================

// SSE连接状态枚举
export const SSE_STATUS = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  ERROR: 'error',
}

// 创作阶段定义
export const WRITING_PHASES = [
  { id: 'phase1', name: '灵感解析', order: 0 },
  { id: 'phase2', name: '情节规划', order: 1 },
  { id: 'phase3', name: '章节创作', order: 2 },
  { id: 'phase4', name: '风格优化', order: 3 },
]

const STORAGE_KEY = 'kuimulang-writing-store'

// P2-107: 统一日志 trim 阈值（运行时 500→300，持久化 100→保留运行时 300）
const LOG_TRIM_THRESHOLD = 500
const LOG_TRIM_KEEP = 300
const LOG_PERSIST_KEEP = 300

// P1-54 + P2-62: 用 safeStorage 替代直接 localStorage；持久化走 1s 节流

function loadFromStorage() {
  return safeStorage.get(STORAGE_KEY, null)
}

const persist = throttledPersist(STORAGE_KEY, 1000)

// P1-54: 取代旧的 saveToStorage —— 只持久化关键状态字段，写盘走节流
function saveToStorage(state) {
  const toSave = {
    currentWorkId: state.currentWorkId,
    currentPhase: state.currentPhase,
    currentPart: state.currentPart,
    completedParts: state.completedParts,
    logs: (state.logs || []).slice(-LOG_PERSIST_KEEP),
  }
  persist(toSave)
}

// 立即同步落盘（路由切换 / 页面 unload 时调用）
function saveToStorageNow(state) {
  const toSave = {
    currentWorkId: state.currentWorkId,
    currentPhase: state.currentPhase,
    currentPart: state.currentPart,
    completedParts: state.completedParts,
    logs: (state.logs || []).slice(-LOG_PERSIST_KEEP),
  }
  flushPersist(STORAGE_KEY)
  safeStorage.set(STORAGE_KEY, toSave)
}

export const useWritingStore = defineStore('writing', {
  state: () => {
    const saved = loadFromStorage()
    return {
      // 当前作品ID
      currentWorkId: saved?.currentWorkId || null,

      // 作品数据
      workData: {
        title: '',
        parts: {},
        part_outline: [],
      },

      // SSE连接状态
      sseStatus: SSE_STATUS.DISCONNECTED,

      // 创作状态
      writingStatus: {
        running: false,
        paused: false,
        phase: '',
        current_part: 0,
      },

      // 当前阶段
      currentPhase: saved?.currentPhase || '',

      // 当前Part
      currentPart: saved?.currentPart || 0,

      // 已完成的Parts
      completedParts: saved?.completedParts || [],

      // 日志
      logs: saved?.logs || [],

      // 进度
      progress: 0,
      progressMessage: '准备开始创作',

      // 确认弹窗
      showConfirm: false,
      confirmMsg: '',

      // 错误弹窗
      showError: false,
      errorMessage: '',
      errorSuggestion: '',
    }
  },

  getters: {
    // 阶段完成状态
    isPhaseDone: (state) => (phaseId) => {
      const currentIdx = WRITING_PHASES.findIndex(p => p.id === state.currentPhase)
      const phaseIdx = WRITING_PHASES.findIndex(p => p.id === phaseId)
      return phaseIdx < currentIdx
    },

    // 是否创作中
    isRunning: (state) => state.writingStatus?.running && !state.writingStatus?.paused,

    // 是否暂停
    isPaused: (state) => state.writingStatus?.paused,

    // SSE是否已连接
    isSSEConnected: (state) => state.sseStatus === SSE_STATUS.CONNECTED,
  },

  actions: {
    // P1-54: 节流持久化（1s 内多次调用合并为 1 次 localStorage 写）
    persistState() {
      saveToStorage(this.$state)
    },
    // 立即同步落盘（路由切换 / 页面 unload 时调用，绕过节流）
    persistStateNow() {
      saveToStorageNow(this.$state)
    },

    // 设置当前作品
    setCurrentWork(workId) {
      this.currentWorkId = workId
      this.persistState()
    },

    // 设置作品数据
    setWorkData(data) {
      this.workData = data
    },

    // 更新创作状态
    setWritingStatus(status) {
      this.writingStatus = status
    },

    // 设置SSE状态
    setSSEStatus(status) {
      this.sseStatus = status
    },

    // ---- R4-P0-1: SSE 事件 action（view 调这 7 个方法即可） ----

    // 1. 添加日志（R4-P0-1: view.handleEvent('log') 调）
    addLog(log) {
      // 兼容入参是字符串（直接消息）或对象（{msg, type}）
      let entry
      if (typeof log === 'string') {
        entry = { time: new Date().toLocaleTimeString('zh-CN'), msg: log, type: 'info' }
      } else {
        entry = {
          time: new Date().toLocaleTimeString('zh-CN'),
          msg: log.msg || log.message || '',
          type: log.type || 'info',
        }
      }

      // P3-112: SSE 重连可能重复发同一事件 —— 500ms 窗口内同 msg+type 重复 push 跳过，
      // 避免日志流刷屏（"✅ Part X 创作完成"被重发 3 次）。
      const now = Date.now()
      const last = this._lastAddLog
      if (last && now - last.ts < 500 && last.msg === entry.msg && last.type === entry.type) {
        return
      }
      this._lastAddLog = { ts: now, msg: entry.msg, type: entry.type }

      this.logs.push(entry)
      if (this.logs.length > LOG_TRIM_THRESHOLD) {
        this.logs = this.logs.slice(-LOG_TRIM_KEEP)
      }
      this.persistState()
    },

    // 2. 标记 Part 完成（R4-P0-1: view.handleEvent('part_complete') 调）
    markPartComplete(part) {
      const partNum = typeof part === 'number' ? part : part?.part
      if (!partNum) return
      if (!this.completedParts.includes(partNum)) {
        this.completedParts.push(partNum)
        this.persistState()
      }
    },

    // 3. 设置阶段（R4-P0-1: view.handleEvent('phase') 调）
    setPhase(phase) {
      this.currentPhase = phase
      this.persistState()
    },

    // 4. 设置进度（R4-P0-1: view.handleEvent('progress') 调）
    // 入参兼容：setProgress(progress, message) 或 setProgress({progress, message})
    setProgress(p, msg) {
      if (typeof p === 'object' && p !== null) {
        this.progress = p.progress ?? p.value ?? 0
        this.progressMessage = p.message ?? this.progressMessage
      } else {
        this.progress = p ?? 0
        if (msg !== undefined) this.progressMessage = msg
      }
    },

    // 5. 设置确认弹窗（R4-P0-1: view.handleEvent('confirm') 调）
    setConfirm(msg) {
      this.confirmMsg = typeof msg === 'string' ? msg : msg?.message || ''
      this.showConfirm = true
    },

    // 6. 设置错误弹窗（R4-P0-1: view.handleEvent('error') 调）
    // 入参兼容：setError(msg, suggestion) 或 setError({message, recovery_suggestion})
    setError(msg, suggestion) {
      if (typeof msg === 'object' && msg !== null) {
        this.errorMessage = msg.message || ''
        this.errorSuggestion = msg.recovery_suggestion || msg.suggestion || ''
      } else {
        this.errorMessage = msg || ''
        this.errorSuggestion = suggestion || ''
      }
      this.showError = true
      // 同步追加到日志流
      this.addLog({ msg: `❌ ${this.errorMessage}`, type: 'error' })
    },

    // 7. finalize（R4-P0-1: view.handleEvent('final') 调）
    finalize() {
      this.writingStatus.running = false
      this.addLog({ msg: '创作完成！', type: 'success' })
    },

    // ---- 保留方法（供 view / 其他模块沿用） ----

    // 显示确认弹窗（保留兼容旧调用）
    showConfirmDialog(msg) {
      this.setConfirm(msg)
    },

    // 显示错误弹窗（保留兼容旧调用）
    showErrorDialog(message, suggestion = '') {
      this.setError(message, suggestion)
    },

    // 关闭错误弹窗
    dismissError() {
      this.showError = false
    },

    // 关闭确认弹窗
    dismissConfirm() {
      this.showConfirm = false
    },

    // 确认操作（发后端 /api/writing/confirm）
    confirmAction(proceed) {
      this.dismissConfirm()
      if (proceed && this.currentWorkId) {
        api.post(`/writing/confirm`, { work_id: this.currentWorkId, choice: 'proceed' }).catch(() => {})
      }
    },

    // 加载作品状态
    async loadWorkStatus(workId) {
      try {
        const s = (await api.get(`/writing/status/${workId}`)).data
        this.setWritingStatus(s)
        this.setCurrentWork(workId)
      } catch (e) {
        console.error('[WritingStore] Failed to load work status:', e)
      }
    },

    // 重置状态
    reset() {
      this.workData = { title: '', parts: {}, part_outline: [] }
      this.currentPhase = ''
      this.completedParts = []
      this.logs = []
      this.progress = 0
      this.progressMessage = '准备开始创作'
      this.showConfirm = false
      this.showError = false
      this.errorMessage = ''
      this.errorSuggestion = ''
      this.sseStatus = SSE_STATUS.DISCONNECTED
      this.setCurrentPart(0)  // P0-42: 走 action 走封装（最后清 part，避免与 completedParts 顺序耦合）
      this.persistState()
    },

    // P0-42: 集中 setter —— 维护 store 内部不变式（如清空 completedParts）
    setCurrentPart(n) {
      this.currentPart = n
    },
  },
})