import { defineStore } from 'pinia'
import api from '@/api'

const STORAGE_KEY = 'kuimulang-works-store'

// 从localStorage加载持久化状态
function loadFromStorage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved)
      return {
        works: parsed.works || [],
        currentWork: parsed.currentWork || null,
      }
    }
  } catch (e) {
    console.warn('[WorksStore] Failed to load from storage:', e)
  }
  return {
    works: [],
    currentWork: null,
  }
}

// 保存到localStorage
function saveToStorage(state) {
  try {
    const toSave = {
      works: state.works,
      currentWork: state.currentWork,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
  } catch (e) {
    console.warn('[WorksStore] Failed to save to storage:', e)
  }
}

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
    // 获取作品总数
    totalWorks: (state) => state.works.length,

    // 获取进行中的作品
    activeWorks: (state) => state.works.filter(w => w.status === 'active'),

    // 根据ID获取作品
    getWorkById: (state) => (id) => {
      return state.works.find(w => w.id === id)
    },
  },

  actions: {
    // 保存状态到localStorage
    persistState() {
      saveToStorage(this.$state)
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

    // 清空所有作品数据
    clearAll() {
      this.works = []
      this.currentWork = null
      this.persistState()
    },
  },
})