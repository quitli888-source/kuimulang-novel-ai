import { defineStore } from 'pinia'
import api from '@/api'
import { safeStorage, debouncedPersist, flushPersist } from '@/utils/safeStorage'

const STORAGE_KEY = 'kuimulang-works-store'

// P1-50 + P2-62: 用 safeStorage 替代直接 localStorage 调用 + 节流持久化
function loadFromStorage() {
  const saved = safeStorage.get(STORAGE_KEY, null)
  if (saved && typeof saved === 'object') {
    return {
      works: Array.isArray(saved.works) ? saved.works : [],
      currentWork: saved.currentWork || null,
    }
  }
  return { works: [], currentWork: null }
}

const persist = debouncedPersist(STORAGE_KEY, 1000)

export const useWorksStore = defineStore('works', {
  state: () => {
    const saved = loadFromStorage()
    return {
      works: saved.works,
      currentWork: saved.currentWork,
      loading: false,
    }
  },

  getters: {
    totalWorks: (state) => state.works.length,
    activeWorks: (state) => state.works.filter(w => w.status === 'active'),
    getWorkById: (state) => (id) => state.works.find(w => w.id === id),
  },

  actions: {
    // P1-50: persistState() 走 1s 节流；多次连续调用合并为 1 次 localStorage 写
    persistState() {
      persist({ works: this.works, currentWork: this.currentWork })
    },
    // P2 兼容：路由切换前立即同步写
    persistStateNow() {
      flushPersist(STORAGE_KEY)
      safeStorage.set(STORAGE_KEY, { works: this.works, currentWork: this.currentWork })
    },

    async fetchWorks() {
      this.loading = true
      try {
        const res = await api.get('/works/')
        this.works = res.data.works || []
        this.persistState()
      } finally {
        this.loading = false
      }
    },

    async createWork(title, inspiration) {
      const res = await api.post('/works/', { title, inspiration })
      await this.fetchWorks()
      return res.data.id
    },

    async loadWork(workId) {
      const res = await api.get(`/works/${workId}`)
      this.currentWork = res.data
      this.persistState()
      return res.data
    },

    async deleteWork(workId) {
      await api.delete(`/works/${workId}`)
      if (this.currentWork?.id === workId) {
        this.currentWork = null
      }
      await this.fetchWorks()
    },

    clearAll() {
      this.works = []
      this.currentWork = null
      this.persistState()
    },
  },
})