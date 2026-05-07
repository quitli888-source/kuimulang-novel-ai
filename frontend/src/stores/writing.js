import { defineStore } from 'pinia'
import api from '@/api'

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

// 从localStorage加载持久化状态
function loadFromStorage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.warn('[WritingStore] Failed to load from storage:', e)
  }
  return null
}

// 保存到localStorage
function saveToStorage(state) {
  try {
    // 只持久化关键状态
    const toSave = {
      currentWorkId: state.currentWorkId,
      currentPhase: state.currentPhase,
      currentPart: state.currentPart,
      completedParts: state.completedParts,
      logs: state.logs.slice(-100), // 只保留最近100条日志
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
  } catch (e) {
    console.warn('[WritingStore] Failed to save to storage:', e)
  }
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

      // EventSource实例
      eventSource: null,
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
    // 持久化状态
    persistState() {
      saveToStorage(this.$state)
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

    // 处理SSE事件
    handleSSEEvent(ev) {
      switch (ev.type) {
        case 'log':
          this.addLog(ev.data.message, 'info')
          break
        case 'phase':
          this.currentPhase = ev.data.phase
          this.persistState()
          break
        case 'part':
          this.currentPart = ev.data.part
          break
        case 'part_complete':
          if (!this.completedParts.includes(ev.data.part)) {
            this.completedParts.push(ev.data.part)
            this.persistState()
          }
          break
        case 'progress':
          this.progress = ev.data.progress
          this.progressMessage = ev.data.message
          break
        case 'error':
          this.showErrorDialog(ev.data.message, ev.data.suggestion)
          break
        case 'confirm':
          this.showConfirmDialog(ev.data.message)
          break
        case 'complete':
          this.writingStatus.running = false
          this.addLog('创作完成！', 'success')
          break
      }
    },

    // 添加日志
    addLog(msg, type = 'info') {
      const logEntry = {
        time: new Date().toLocaleTimeString('zh-CN'),
        msg,
        type,
      }
      this.logs.push(logEntry)
      // 保持日志数量限制
      if (this.logs.length > 500) {
        this.logs = this.logs.slice(-300)
      }
      this.persistState()
    },

    // 显示确认弹窗
    showConfirmDialog(msg) {
      this.confirmMsg = msg
      this.showConfirm = true
    },

    // 显示错误弹窗
    showErrorDialog(message, suggestion = '') {
      this.errorMessage = message
      this.errorSuggestion = suggestion
      this.showError = true
    },

    // 确认操作
    confirmAction(proceed) {
      this.showConfirm = false
      // 通过API通知后端
      if (proceed && this.currentWorkId) {
        api.post(`/writing/confirm/${this.currentWorkId}`, { action: 'proceed' }).catch(() => {})
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

    // 初始化SSE连接
    initSSE(workId) {
      // 关闭已有连接
      this.closeSSE()

      this.setSSEStatus(SSE_STATUS.CONNECTING)
      this.currentWorkId = workId

      const eventSource = new EventSource(`/api/sse/stream?work_id=${workId}`)
      this.eventSource = eventSource

      eventSource.onopen = () => {
        this.setSSEStatus(SSE_STATUS.CONNECTED)
        this.addLog('SSE连接已建立', 'info')
      }

      eventSource.onerror = () => {
        this.setSSEStatus(SSE_STATUS.ERROR)
        this.addLog('SSE连接错误', 'error')
      }

      eventSource.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          this.handleSSEEvent(ev)
        } catch (err) {
          console.error('[WritingStore] Failed to parse SSE event:', err)
        }
      }

      return eventSource
    },

    // 关闭SSE连接
    closeSSE() {
      if (this.eventSource) {
        this.eventSource.close()
        this.eventSource = null
        this.setSSEStatus(SSE_STATUS.DISCONNECTED)
      }
    },

    // 重置状态
    reset() {
      this.closeSSE()
      this.workData = { title: '', parts: {}, part_outline: [] }
      this.currentPhase = ''
      this.currentPart = 0
      this.completedParts = []
      this.logs = []
      this.progress = 0
      this.progressMessage = '准备开始创作'
      this.showConfirm = false
      this.showError = false
      this.errorMessage = ''
      this.errorSuggestion = ''
    },
  },
})