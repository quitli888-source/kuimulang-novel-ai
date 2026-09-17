/**
 * 统一API管理 - Axios拦截器封装
 * 统一管理所有API请求，包含请求/响应拦截器、重试机制、AbortController支持
 */
import axios from 'axios'

// 创建Axios实例
const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 存储message实例
let messageInstance = null

// 设置message实例（需在Vue应用中调用）
export function setMessage(msg) {
  messageInstance = msg
}

// 重试配置
const RETRY_CONFIG = {
  maxRetries: 3,
  retryDelay: 1000,
  retryableStatuses: [408, 429, 500, 502, 503, 504],
}

// 判断是否应该重试
function shouldRetry(error) {
  // 网络错误可以重试
  if (!error.response) {
    return true
  }
  // 服务器错误可以重试
  const status = error.response.status
  return RETRY_CONFIG.retryableStatuses.includes(status)
}

// 显示错误提示
function showErrorToast(message) {
  if (messageInstance) {
    messageInstance.error(message, { duration: 3000 })
  } else {
    console.warn('[API Error]', message)
  }
}

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    // 生成唯一请求ID
    config.requestId = `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

    // 添加AbortController
    const abortController = new AbortController()
    config.abortController = abortController
    config.signal = abortController.signal
    // P1-14: 注册到全局 pending 映射，cancelRequest 真正能 abort
    _pendingControllers.set(config.requestId, abortController)

    // 添加时间戳防止缓存（GET请求）
    if (config.method === 'get') {
      config.params = {
        ...config.params,
        _t: Date.now(),
      }
    }

    // 重试计数
    config.retryCount = config.retryCount || 0

    // 记录请求日志
    console.log(`[API Request] ${config.method?.toUpperCase()} ${config.url}`, {
      requestId: config.requestId,
      params: config.params,
      data: config.data,
    })

    return config
  },
  (error) => {
    console.error('[API Request Error]', error)
    return Promise.reject(error)
  }
)

// 带重试的请求执行
async function executeRequestWithRetry(requestConfig) {
  const { config, resolve, reject } = requestConfig
  const { maxRetries, retryDelay } = RETRY_CONFIG

  try {
    const response = await api.request(config)
    resolve(response)
    return  // P1-14: resolve 后必须 return，避免下方 catch 仍执行
  } catch (error) {
    // 检查是否应该重试
    if (shouldRetry(error) && config.retryCount < maxRetries) {
      config.retryCount++

      console.log(`[API Retry] ${config.url} (${config.retryCount}/${maxRetries})`)

      // 延迟后重试
      await new Promise(resolve => setTimeout(resolve, retryDelay * config.retryCount))

      // 递归重试
      return executeRequestWithRetry({ config, resolve, reject })
    }

    reject(error)
  }
}

// 封装带重试的请求方法
function requestWithRetry(config) {
  return new Promise((resolve, reject) => {
    executeRequestWithRetry({ config, resolve, reject })
  })
}

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    // 记录响应日志
    console.log(`[API Response] ${response.config.url}`, {
      requestId: response.config.requestId,
      status: response.status,
      data: response.data,
    })

    _pendingControllers.delete(response.config.requestId)
    return response
  },
  (error) => {
    // P1-14: 请求结束（成功或失败）清理 pending 映射
    if (error.config?.requestId) {
      _pendingControllers.delete(error.config.requestId)
    }
    // 如果是取消的请求，不显示错误
    if (axios.isCancel(error)) {
      console.log(`[API Cancelled] ${error.config?.url}`)
      return Promise.reject(error)
    }

    // 解析错误
    const status = error.response?.status
    const detail = error.response?.data?.detail
    const message = error.message || '网络请求失败'

    // 记录错误日志
    console.error(`[API Error] ${error.config?.url}`, {
      requestId: error.config?.requestId,
      status,
      detail,
      message,
      retryCount: error.config?.retryCount,
    })

    // 处理特定错误码
    let errorMsg = message
    if (status === 401) {
      errorMsg = '登录已过期，请重新登录'
    } else if (status === 403) {
      errorMsg = '没有权限执行此操作'
    } else if (status === 404) {
      errorMsg = '请求的资源不存在'
    } else if (status === 500) {
      errorMsg = '服务器内部错误，请稍后再试'
    } else if (status === 502) {
      errorMsg = '网关错误，请稍后再试'
    } else if (status === 503) {
      errorMsg = '服务暂不可用，请稍后再试'
    } else if (status === 504) {
      errorMsg = '网关超时，请稍后再试'
    } else if (detail) {
      errorMsg = typeof detail === 'string' ? detail : JSON.stringify(detail)
    }

    // 显示错误提示（可选，通过配置控制）
    if (error.config?.showError !== false) {
      showErrorToast(errorMsg)
    }

    // 创建错误对象包含原始信息
    const enhancedError = new Error(errorMsg)
    enhancedError.originalError = error
    enhancedError.status = status
    enhancedError.detail = detail
    enhancedError.code = error.code
    enhancedError.requestId = error.config?.requestId

    return Promise.reject(enhancedError)
  }
)

// P1-14: 维护 pending requestId → AbortController 映射，真正可取消
const _pendingControllers = new Map()

// 取消请求的方法
export function cancelRequest(requestId) {
  const ctrl = _pendingControllers.get(requestId)
  if (ctrl) {
    ctrl.abort()
    _pendingControllers.delete(requestId)
    console.log(`[API Cancel] Request aborted: ${requestId}`)
  } else {
    console.log(`[API Cancel] No pending request for id: ${requestId}`)
  }
}

// 取消所有请求
export function cancelAllRequests() {
  let cancelled = 0
  for (const [id, ctrl] of _pendingControllers.entries()) {
    ctrl.abort()
    _pendingControllers.delete(id)
    cancelled++
  }
  console.log(`[API Cancel] Aborted ${cancelled} pending requests`)
}

export default api

// 便捷的API方法导出（带重试）
export const get = (url, params, config) => {
  const finalConfig = {
    ...config,
    method: 'get',
    url,
    params,
    showError: config?.showError !== false,
  }
  return requestWithRetry(finalConfig)
}

export const post = (url, data, config) => {
  const finalConfig = {
    ...config,
    method: 'post',
    url,
    data,
    showError: config?.showError !== false,
  }
  return requestWithRetry(finalConfig)
}

export const put = (url, data, config) => {
  const finalConfig = {
    ...config,
    method: 'put',
    url,
    data,
    showError: config?.showError !== false,
  }
  return requestWithRetry(finalConfig)
}

export const patch = (url, data, config) => {
  const finalConfig = {
    ...config,
    method: 'patch',
    url,
    data,
    showError: config?.showError !== false,
  }
  return requestWithRetry(finalConfig)
}

export const del = (url, params, config) => {
  const finalConfig = {
    ...config,
    method: 'delete',
    url,
    params,
    showError: config?.showError !== false,
  }
  return requestWithRetry(finalConfig)
}

// SSE专用方法（不受重试机制影响）
export const sseGet = (url, params, config) => {
  return api.get(url, {
    ...config,
    params,
    timeout: 0, // SSE不设置超时
    showError: false,
  })
}