<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <span class="page-title">AI改写与优化</span>
        </div>
      </div>

      <div class="content">
        <div class="rewrite-container">

          <!-- 模式选择 -->
          <div class="section">
            <div class="section-title">改写模式</div>
            <div class="mode-tabs">
              <div
                v-for="m in modes"
                :key="m.id"
                class="mode-tab"
                :class="{ active: selectedMode === m.id }"
                @click="selectedMode = m.id"
              >
                <div class="mode-icon">{{ m.icon }}</div>
                <div class="mode-name">{{ m.name }}</div>
                <div class="mode-desc">{{ m.desc }}</div>
              </div>
            </div>
          </div>

          <!-- 输入区 -->
          <div class="section">
            <div class="section-title">原文</div>
            <n-input
              v-model:value="inputText"
              type="textarea"
              placeholder="粘贴需要改写的文字..."
              :rows="8"
              :maxlength="10000"
              show-count
            />
          </div>

          <!-- 上下文（可选） -->
          <div class="section">
            <div class="section-title">上下文背景 <span class="optional">(可选)</span></div>
            <n-input
              v-model:value="context"
              type="textarea"
              placeholder="补充故事背景、人物设定等上下文，帮助AI更准确地改写..."
              :rows="3"
              :maxlength="2000"
              show-count
            />
          </div>

          <!-- 执行按钮 -->
          <div class="action-row">
            <n-button type="primary" size="large" @click="doRewrite" :loading="loading" :disabled="!inputText.trim()">
              {{ loading ? '改写中...' : '开始改写' }}
            </n-button>
            <n-button v-if="result" size="large" @click="copyResult" :disabled="!result">
              复制结果
            </n-button>
            <n-button v-if="result" size="large" @click="swapText" type="warning">
              原文←→结果
            </n-button>
          </div>

          <!-- 错误提示 -->
          <n-alert v-if="error" type="error" style="margin-top:16px">
            {{ error }}
          </n-alert>

          <!-- 结果展示 -->
          <div v-if="result" class="section result-section">
            <div class="section-title">
              改写结果
              <span class="result-stats">
                原文字数: {{ result.original_len || inputText.length }} →
                结果字数: {{ result.rewritten_len || result.rewritten?.length || 0 }}
              </span>
            </div>
            <div class="result-compare">
              <div class="result-block original">
                <div class="block-label">原文</div>
                <div class="block-text">{{ result.original || inputText }}</div>
              </div>
              <div class="result-block rewritten">
                <div class="block-label">{{ selectedModeLabel }}结果</div>
                <div class="block-text">{{ result.rewritten }}</div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const message = useMessage()

const inputText = ref('')
const context = ref('')
const selectedMode = ref('polish')
const loading = ref(false)
const error = ref('')
const result = ref(null)

const modes = [
  { id: 'polish', name: '润色', icon: '✨', desc: '优化文字，提升文学质感' },
  { id: 'expand', name: '扩写', icon: '📖', desc: '增加细节，扩充内容30%-50%' },
  { id: 'summarize', name: '缩写', icon: '✂️', desc: '精简冗余，压缩至50%-60%' },
]

const selectedModeLabel = computed(() => {
  return modes.find(m => m.id === selectedMode.value)?.name || '润色'
})

async function doRewrite() {
  if (!inputText.value.trim()) return
  loading.value = true
  error.value = ''
  result.value = null
  try {
    const res = await api.post('/rewrite/', {
      text: inputText.value,
      mode: selectedMode.value,
      context: context.value,
    })
    result.value = res.data
    message.success('改写完成')
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || '改写失败，请检查API配置'
  } finally {
    loading.value = false
  }
}

async function copyResult() {
  if (!result.value?.rewritten) return
  try {
    await navigator.clipboard.writeText(result.value.rewritten)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

function swapText() {
  if (!result.value?.rewritten) return
  inputText.value = result.value.rewritten
  result.value = null
}
</script>

<style scoped>
.layout { display: flex; height: 100vh; background: var(--color-background, #f8fafc); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { 
  display: flex; 
  align-items: center; 
  padding: 14px 24px; 
  border-bottom: 1px solid var(--color-border, #eee); 
  background: var(--color-surface, #fff);
  backdrop-filter: blur(10px);
}
.topbar-left { display: flex; align-items: center; gap: 16px; }
.page-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary, #333); }
.content { 
  flex: 1; 
  overflow-y: auto; 
  padding: var(--content-padding, 24px);
  background: var(--color-background, #f8fafc);
}
.rewrite-container { max-width: 900px; }
.section { margin-bottom: 28px; }
.section-title { 
  font-size: 15px; 
  font-weight: 600; 
  color: var(--color-text-primary, #333); 
  margin-bottom: 14px; 
  display: flex; 
  align-items: center; 
  gap: 10px;
  letter-spacing: -0.2px;
}
.optional { font-size: 12px; color: var(--color-text-secondary, #aaa); font-weight: 400; }

.mode-tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.mode-tab {
  background: var(--color-surface, #fff); 
  border: 2px solid var(--color-border, #e2e8f0); 
  border-radius: var(--radius-lg, 12px);
  padding: 20px 16px; 
  cursor: pointer; 
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
  text-align: center;
  position: relative;
  overflow: hidden;
}
.mode-tab::before {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(37,99,235,0.05) 0%, transparent 70%);
  opacity: 0;
  transition: opacity 0.3s ease;
}
.mode-tab:hover::before {
  opacity: 1;
}
.mode-tab:hover { 
  border-color: var(--color-primary, #2563eb);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md, 0 4px 12px rgba(37,99,235,0.15));
}
.mode-tab.active { 
  border-color: var(--color-primary, #2563eb); 
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary, #2563eb) 5%, white), transparent);
  box-shadow: var(--shadow-md, 0 4px 16px rgba(37,99,235,0.2));
}
.mode-icon { 
  font-size: 32px; 
  margin-bottom: 10px;
  display: inline-block;
  transition: transform 0.3s ease;
}
.mode-tab:hover .mode-icon {
  transform: scale(1.15) rotate(5deg);
}
.mode-name { 
  font-size: 15px; 
  font-weight: 600; 
  color: var(--color-text-primary, #333); 
  margin-bottom: 4px; 
}
.mode-desc { font-size: 12px; color: var(--color-text-secondary, #888); line-height: 1.5; }

.action-row { display: flex; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }

.result-section { 
  margin-top: 32px; 
  border-top: 2px solid var(--color-border, #eee); 
  padding-top: 24px;
  animation: fadeInUp 0.4s ease-out;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.result-stats { 
  font-size: 12px; 
  color: var(--color-text-secondary, #888); 
  font-weight: 400; 
  margin-left: 8px;
  padding: 4px 10px;
  background: var(--color-background, #f8fafc);
  border-radius: 20px;
}
.result-compare { 
  display: grid; 
  grid-template-columns: 1fr 1fr; 
  gap: 16px;
  margin-top: 16px;
}
.result-block { 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 10px); 
  padding: 16px; 
  border: 2px solid var(--color-border, #e2e8f0);
  transition: all 0.25s ease;
  position: relative;
  overflow: hidden;
}
.result-block::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
}
.original::before {
  background: linear-gradient(90deg, var(--color-textSecondary, #94a3b8), transparent);
}
.rewritten::before {
  background: linear-gradient(90deg, var(--color-primary, #2563eb), transparent);
}

.result-block:hover {
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0,0,0,0.08));
  transform: translateY(-2px);
}

.block-label { 
  font-size: 12px; 
  font-weight: 600; 
  color: var(--color-text-secondary, #888); 
  margin-bottom: 12px; 
  text-transform: uppercase; 
  letter-spacing: 1px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.block-label::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}
.block-text { 
  font-size: 14px; 
  color: var(--color-textPrimary, #333); 
  line-height: 1.8; 
  white-space: pre-wrap;
  min-height: 120px;
}
.original { border-color: var(--color-border, #e2e8f0); }
.rewritten { 
  border-color: var(--color-primary, #2563eb); 
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary, #2563eb) 3%, white), transparent);
}

@media (max-width: 768px) {
  .topbar {
    padding: 12px 16px;
    flex-wrap: wrap;
    gap: 12px;
  }
  .content {
    padding: 16px;
  }
  .mode-tabs {
    grid-template-columns: 1fr;
  }
  .result-compare {
    grid-template-columns: 1fr;
  }
  .action-row {
    flex-direction: column;
  }
}

@media (max-width: 480px) {
  .mode-tab {
    padding: 16px 12px;
  }
  .mode-icon {
    font-size: 28px;
  }
  .result-block {
    padding: 12px;
  }
}
</style>
