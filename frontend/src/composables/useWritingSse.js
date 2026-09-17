// P0-43: useWritingSse composable —— 把 EventSource + reconnect 逻辑封装到一处，
// 生命周期绑定组件 scope（onScopeDispose 自动清理 setTimeout + EventSource），
// 避免原写法 let eventSource = null 在 script setup 顶层每次组件 setup 都重置引用，
// 旧实例的 setTimeout 回调可能引用过期的 EventSource 导致 ghost reconnect。
import { onScopeDispose } from 'vue'

export const SSE_STATUS = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  ERROR: 'error',
}

const DEFAULT_OPTS = {
  baseDelayMs: 1000,
  maxDelayMs: 30000,
  onStatusChange: () => {},
  onError: () => {},
  onReconnectScheduled: () => {},
}

/**
 * @param {string} workId 作品 ID
 * @param {(ev: object) => void} handleEvent 业务事件分发
 * @param {Partial<typeof DEFAULT_OPTS>} [opts]
 */
export function useWritingSse(workId, handleEvent, opts = {}) {
  const config = { ...DEFAULT_OPTS, ...opts }
  let eventSource = null
  const reconnectState = {
    attempts: 0,
    timer: null,
    stopped: false,
  }

  function clearReconnectTimer() {
    if (reconnectState.timer) {
      clearTimeout(reconnectState.timer)
      reconnectState.timer = null
    }
  }

  function connectSSE() {
    if (reconnectState.stopped) return
    if (eventSource) {
      try { eventSource.close() } catch (e) { /* noop */ }
      eventSource = null
    }
    config.onStatusChange(SSE_STATUS.CONNECTING)
    const es = new EventSource(`/api/sse/stream?work_id=${workId}`)
    eventSource = es

    es.onopen = () => {
      reconnectState.attempts = 0
      config.onStatusChange(SSE_STATUS.CONNECTED)
    }
    es.onerror = () => {
      if (reconnectState.stopped) return
      config.onStatusChange(SSE_STATUS.ERROR)
      config.onError('SSE连接错误，准备重连...')
      try { es.close() } catch (e) { /* noop */ }
      if (eventSource === es) eventSource = null
      reconnectState.attempts += 1
      const delay = Math.min(
        config.maxDelayMs,
        config.baseDelayMs * Math.pow(2, reconnectState.attempts - 1),
      )
      config.onReconnectScheduled(reconnectState.attempts, delay)
      reconnectState.timer = setTimeout(connectSSE, delay)
    }
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data)
        handleEvent(ev)
      } catch (err) {
        console.error('[useWritingSse] Failed to parse SSE event:', err)
      }
    }
  }

  function closeSSE() {
    reconnectState.stopped = true
    clearReconnectTimer()
    if (eventSource) {
      try { eventSource.close() } catch (e) { /* noop */ }
      eventSource = null
    }
    config.onStatusChange(SSE_STATUS.DISCONNECTED)
  }

  // P0-43: 自动 cleanup —— 组件 unmount 时关 EventSource + 清 timer
  onScopeDispose(closeSSE)

  return { connect: connectSSE, close: closeSSE, getEventSource: () => eventSource }
}