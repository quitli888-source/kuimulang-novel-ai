// P1-50/P1-54 + P2-62 通用工具：safeStorage + debounced persist
//
// 用法：
//   import { safeStorage, debouncedPersist } from '@/utils/safeStorage'
//   safeStorage.get('key', defaultVal)  // 容错读，SSR/test 安全
//   safeStorage.set('key', value)        // 容错写
//   const persist = debouncedPersist('key', 1000)  // 1s 内多次调用只写一次
//   persist(stateObj)                     // 触发节流持久化

const isBrowser = typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

export const safeStorage = {
  /**
   * 安全读取 localStorage，失败/缺失/非法 JSON 时返回 fallback。
   */
  get(key, fallback = null) {
    if (!isBrowser) return fallback
    try {
      const raw = window.localStorage.getItem(key)
      if (raw === null || raw === undefined) return fallback
      return JSON.parse(raw)
    } catch (e) {
      console.warn(`[safeStorage.get] ${key}:`, e)
      return fallback
    }
  },

  /**
   * 安全写入 localStorage；失败仅 warn，不抛异常。
   */
  set(key, value) {
    if (!isBrowser) return false
    try {
      window.localStorage.setItem(key, JSON.stringify(value))
      return true
    } catch (e) {
      console.warn(`[safeStorage.set] ${key}:`, e)
      return false
    }
  },

  /**
   * 删除 localStorage 项
   */
  remove(key) {
    if (!isBrowser) return
    try {
      window.localStorage.removeItem(key)
    } catch (e) {
      console.warn(`[safeStorage.remove] ${key}:`, e)
    }
  },
}

const _pendingTimers = new Map()

/**
 * P1-50/P1-54: 节流持久化 —— 同一 key 的多次调用合并为 1 次写入。
 * - delayMs 内连续 persist() 只触发最后 1 次实际写盘
 * - flush(key) 立即同步触发未执行的写盘
 *
 * @param {string} key localStorage key
 * @param {number} delayMs 节流窗口（默认 1000ms）
 * @returns {(value: any) => void} 触发函数
 */
export function debouncedPersist(key, delayMs = 1000) {
  return (value) => {
    if (!isBrowser) return
    const existing = _pendingTimers.get(key)
    if (existing) {
      clearTimeout(existing.timer)
    }
    const timer = setTimeout(() => {
      safeStorage.set(key, value)
      _pendingTimers.delete(key)
    }, delayMs)
    _pendingTimers.set(key, { timer, value })
  }
}

/**
 * P1-50/P1-54: 立即 flush 节流缓冲（用于路由切换前 / 页面 unload 前）
 */
export function flushPersist(key) {
  const pending = _pendingTimers.get(key)
  if (!pending) return
  clearTimeout(pending.timer)
  safeStorage.set(key, pending.value)
  _pendingTimers.delete(key)
}

const _throttledFlags = new Set()

/**
 * P1-97: 前缘节流（leading-edge throttle）—— 高频事件流下避免"每次都 reset 定时器导致
 * 最后一次写入永远被丢掉"的问题。
 *
 * 与 debouncedPersist 的区别：
 *   - debouncedPersist：每次调用都 clearTimeout + 重置定时器 → SSE 高频事件下
 *     每次新事件都把"原本该 1s 后写"的定时器清掉，最后一条状态永远不写。
 *   - throttledPersist：第一次调用 schedule，1s 内后续调用都 no-op；1s 到点 flush 一次；
 *     flush 后解除标记，下一批第一次调用重新 schedule。保证窗口内最新状态写盘。
 *
 * 用法：throttledPersist('foo', 1000)({ a: 1 })
 */
export function throttledPersist(key, delayMs = 1000) {
  return (value) => {
    if (!isBrowser) return
    if (_throttledFlags.has(key)) {
      // 窗口内：仅更新待写值（避免 reset 定时器），到点时一次性 flush 最新值
      const existing = _pendingTimers.get(key)
      if (existing) existing.value = value
      return
    }
    _throttledFlags.add(key)
    const timer = setTimeout(() => {
      const pending = _pendingTimers.get(key)
      if (pending) {
        safeStorage.set(key, pending.value)
        _pendingTimers.delete(key)
      }
      _throttledFlags.delete(key)
    }, delayMs)
    _pendingTimers.set(key, { timer, value })
  }
}