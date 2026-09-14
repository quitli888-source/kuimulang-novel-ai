import { defineStore } from 'pinia'
import api from '@/api'

// =====================================================================
// R4-P1-3: 会话管理 store（2026-09-14）
//   暴露 sessions 列表 + loadSessions/createSession/deleteSession/switchSession。
//   后端 6 个端点已在 backend/api/works.py:157-209 就位。
// =====================================================================

export const useSessionsStore = defineStore('sessions', {
  state: () => ({
    // 全局会话列表（跨作品）
    sessions: [],
    loading: false,
    error: null,
  }),

  getters: {
    // 按状态过滤
    activeSessions: (state) => state.sessions.filter(s => s.status === 'active'),
    completedSessions: (state) => state.sessions.filter(s => s.status === 'completed'),
    pausedSessions: (state) => state.sessions.filter(s => s.status === 'paused'),
  },

  actions: {
    // 拉取所有会话（全局）
    async loadSessions(_workId) {
      // 后端 /api/works/sessions/list 返回全局会话，与作品独立
      // workId 参数保留以便未来按 work_id 隔离
      this.loading = true
      this.error = null
      try {
        const res = await api.get('/works/sessions/list')
        this.sessions = res.data.sessions || []
      } catch (e) {
        this.error = e.message || '加载会话失败'
        this.sessions = []
      } finally {
        this.loading = false
      }
    },

    // 创建新会话
    async createSession(workId, title = '', inspiration = '') {
      // 后端 works.py:164 create_session 接受 inspiration + 可选 title
      // 这里传 workId 占位（后端暂不感知 workId；前端用作命名/路由）
      const finalTitle = title || `会话-${new Date().toLocaleString('zh-CN')}`
      const finalInsp = inspiration || `从作品 ${workId || '未知'} 派生的会话`
      try {
        const res = await api.post('/works/sessions/create', {
          inspiration: finalInsp,
          title: finalTitle,
        })
        await this.loadSessions(workId)
        return res.data?.session_id
      } catch (e) {
        this.error = e.message || '创建会话失败'
        throw e
      }
    },

    // 切换会话（载入）
    async switchSession(sessionId) {
      try {
        const res = await api.post(`/works/sessions/${sessionId}/load`)
        return res.data
      } catch (e) {
        this.error = e.message || '切换会话失败'
        throw e
      }
    },

    // 删除会话
    async deleteSession(sessionId) {
      try {
        await api.delete(`/works/sessions/${sessionId}`)
        this.sessions = this.sessions.filter(s => s.session_id !== sessionId)
        return true
      } catch (e) {
        this.error = e.message || '删除会话失败'
        throw e
      }
    },

    // 重命名会话
    async renameSession(sessionId, title) {
      try {
        await api.patch(`/works/sessions/${sessionId}`, { title })
        await this.loadSessions()
        return true
      } catch (e) {
        this.error = e.message || '重命名失败'
        throw e
      }
    },

    // 清空错误
    clearError() {
      this.error = null
    },
  },
})