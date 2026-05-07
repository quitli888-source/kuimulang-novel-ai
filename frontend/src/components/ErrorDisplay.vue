<template>
  <div class="error-display" v-if="show">
    <div class="error-content">
      <!-- 错误图标 -->
      <div class="error-icon" :class="errorType">
        <span class="error-icon-emoji">{{ errorIcon }}</span>
      </div>

      <!-- 错误标题 -->
      <h3 class="error-title">{{ errorTitle }}</h3>

      <!-- 错误描述 -->
      <p class="error-message">{{ errorMessage }}</p>

      <!-- 建议区域 -->
      <div class="error-suggestion" v-if="suggestions.length > 0">
        <div class="suggestion-header">
          <span class="suggestion-icon">💡</span>
          <span class="suggestion-label">建议</span>
        </div>
        <ul class="suggestion-list">
          <li v-for="(suggestion, index) in suggestions" :key="index" class="suggestion-item">
            {{ suggestion }}
          </li>
        </ul>
      </div>

      <!-- 操作按钮 -->
      <div class="error-actions">
        <n-button
          v-for="action in actions"
          :key="action.label"
          :type="action.primary ? 'primary' : 'default'"
          @click="handleAction(action.handler)"
        >
          {{ action.label }}
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  show: {
    type: Boolean,
    default: false
  },
  type: {
    type: String,
    default: 'network',
    validator: (value) => ['network', 'api', 'memory', 'storage', 'auth', 'unknown'].includes(value)
  },
  title: {
    type: String,
    default: ''
  },
  message: {
    type: String,
    default: ''
  },
  suggestions: {
    type: Array,
    default: () => []
  },
  actions: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['close', 'action'])

const errorConfig = {
  network: {
    icon: '📡',
    title: '网络连接中断',
    message: '创作过程中与服务器的连接意外断开。您的创作进度已自动保存。',
    suggestions: [
      '检查网络连接后，点击「重试连接」',
      '如需紧急保存，可点击「导出当前进度」'
    ],
    actions: [
      { label: '导出进度', handler: 'export', primary: false },
      { label: '重试连接', handler: 'retry', primary: true }
    ]
  },
  api: {
    icon: '⏱️',
    title: 'AI响应超时',
    message: 'AI创作服务暂时无法响应。系统已缓存您的操作。',
    suggestions: [
      '点击「重试」继续创作',
      '或稍后手动恢复创作'
    ],
    actions: [
      { label: '取消', handler: 'cancel', primary: false },
      { label: '重试', handler: 'retry', primary: true }
    ]
  },
  memory: {
    icon: '💾',
    title: '内存使用警告',
    message: '检测到系统内存不足，可能影响创作流畅度。',
    suggestions: [
      '建议关闭其他程序释放内存',
      '点击「继续创作」尝试保存当前进度'
    ],
    actions: [
      { label: '继续创作', handler: 'continue', primary: true }
    ]
  },
  storage: {
    icon: '💿',
    title: '保存遇到问题',
    message: '作品保存失败，请检查存储空间。',
    suggestions: [
      '确认磁盘空间充足',
      '点击「重试保存」',
      '或联系技术支持'
    ],
    actions: [
      { label: '取消', handler: 'cancel', primary: false },
      { label: '重试保存', handler: 'retry', primary: true }
    ]
  },
  auth: {
    icon: '🔐',
    title: '会话已过期',
    message: '登录状态已过期，请重新登录。',
    suggestions: [
      '点击「重新登录」'
    ],
    actions: [
      { label: '重新登录', handler: 'login', primary: true }
    ]
  },
  unknown: {
    icon: '⚠️',
    title: '创作异常',
    message: '发生了意外的错误。错误信息已记录。',
    suggestions: [
      '点击「重新开始」',
      '或联系技术支持获取帮助'
    ],
    actions: [
      { label: '重新开始', handler: 'restart', primary: true }
    ]
  }
}

const errorType = computed(() => props.type)

const errorIcon = computed(() => {
  if (props.title) return errorConfig.unknown.icon
  return errorConfig[props.type]?.icon || errorConfig.unknown.icon
})

const errorTitle = computed(() => {
  if (props.title) return props.title
  return errorConfig[props.type]?.title || errorConfig.unknown.title
})

const errorMessage = computed(() => {
  if (props.message) return props.message
  return errorConfig[props.type]?.message || errorConfig.unknown.message
})

const suggestions = computed(() => {
  if (props.suggestions && props.suggestions.length > 0) return props.suggestions
  return errorConfig[props.type]?.suggestions || errorConfig.unknown.suggestions
})

const actions = computed(() => {
  if (props.actions && props.actions.length > 0) return props.actions
  return errorConfig[props.type]?.actions || errorConfig.unknown.actions
})

function handleAction(handler) {
  emit('action', handler)
  if (handler === 'retry' || handler === 'continue' || handler === 'login' || handler === 'restart') {
    emit('close')
  }
}
</script>

<style scoped>
.error-display {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.error-content {
  background: var(--color-surface, #ffffff);
  border-radius: 16px;
  padding: 32px;
  max-width: 480px;
  width: 90%;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
  animation: error-fade-in 0.3s ease-out;
}

@keyframes error-fade-in {
  from {
    opacity: 0;
    transform: scale(0.95) translateY(10px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

/* 错误图标 */
.error-icon {
  width: 72px;
  height: 72px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36px;
}

.error-icon.network {
  background: linear-gradient(135deg, rgba(14, 165, 233, 0.15), rgba(14, 165, 233, 0.25));
}

.error-icon.api {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.25));
}

.error-icon.memory {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(99, 102, 241, 0.25));
}

.error-icon.storage {
  background: linear-gradient(135deg, rgba(236, 72, 153, 0.15), rgba(236, 72, 153, 0.25));
}

.error-icon.auth {
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.15), rgba(168, 85, 247, 0.25));
}

.error-icon.unknown {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.15), rgba(239, 68, 68, 0.25));
}

.error-icon-emoji {
  font-size: 36px;
  line-height: 1;
}

/* 错误标题 */
.error-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-textPrimary, #0f172a);
  margin: 0;
}

/* 错误消息 */
.error-message {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-textSecondary, #64748b);
  margin: 0;
  max-width: 380px;
}

/* 建议区域 */
.error-suggestion {
  background: linear-gradient(
    135deg,
    rgba(245, 158, 11, 0.08),
    rgba(245, 158, 11, 0.04)
  );
  padding: 16px 20px;
  border-radius: 12px;
  width: 100%;
  text-align: left;
  border: 1px solid rgba(245, 158, 11, 0.2);
}

.suggestion-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.suggestion-icon {
  font-size: 16px;
}

.suggestion-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-warning, #f59e0b);
}

.suggestion-list {
  margin: 0;
  padding-left: 20px;
  list-style: none;
}

.suggestion-item {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-textSecondary, #64748b);
  margin-bottom: 6px;
}

.suggestion-item:last-child {
  margin-bottom: 0;
}

.suggestion-item::before {
  content: '•';
  color: var(--color-warning, #f59e0b);
  margin-right: 8px;
}

/* 操作按钮 */
.error-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}

.error-actions :deep(.n-button) {
  min-width: 100px;
}

/* 深色模式适配 */
[data-theme="dark"] .error-content {
  background: #1e293b;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
}

[data-theme="dark"] .error-title {
  color: #f1f5f9;
}

[data-theme="dark"] .error-message {
  color: #cbd5e1;
}

[data-theme="dark"] .suggestion-item {
  color: #cbd5e1;
}

[data-theme="dark"] .error-suggestion {
  background: linear-gradient(
    135deg,
    rgba(251, 191, 36, 0.1),
    rgba(251, 191, 36, 0.05)
  );
  border-color: rgba(251, 191, 36, 0.2);
}
</style>
