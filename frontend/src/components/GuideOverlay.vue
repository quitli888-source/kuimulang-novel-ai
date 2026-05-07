<template>
  <n-modal v-model:show="visible" preset="card" title="新手引导" style="width:600px" :mask-closable="false">
    <div class="guide-step" v-if="step === 1">
      <div class="guide-icon">
        <svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="12" y="8" width="40" height="48" rx="4" stroke="#0ea5e9"/>
          <line x1="20" y1="20" x2="44" y2="20" stroke="#0ea5e9"/>
          <line x1="20" y1="28" x2="44" y2="28" stroke="#e2e8f0"/>
          <line x1="20" y1="36" x2="36" y2="36" stroke="#e2e8f0"/>
          <circle cx="32" cy="46" r="6" fill="#0ea5e9" stroke="none"/>
          <line x1="32" y1="43" x2="32" y2="46" stroke="white" stroke-width="1.5"/>
          <line x1="29.5" y1="46" x2="32" y2="46" stroke="white" stroke-width="1.5"/>
        </svg>
      </div>
      <h3>第一步：创建作品</h3>
      <p>点击「新建作品」，输入你的故事灵感。</p>
      <p class="tip">灵感越具体，AI创作质量越高。例如：「一个能看见死亡倒计时的女人」</p>
    </div>
    <div class="guide-step" v-if="step === 2">
      <div class="guide-icon">
        <svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke-width="2">
          <circle cx="32" cy="32" r="20" stroke="#0ea5e9"/>
          <circle cx="32" cy="32" r="8" fill="#0ea5e9" stroke="none"/>
          <line x1="32" y1="8" x2="32" y2="16" stroke="#0ea5e9"/>
          <line x1="32" y1="48" x2="32" y2="56" stroke="#0ea5e9"/>
          <line x1="8" y1="32" x2="16" y2="32" stroke="#0ea5e9"/>
          <line x1="48" y1="32" x2="56" y2="32" stroke="#0ea5e9"/>
          <circle cx="32" cy="32" r="3" fill="white" stroke="none"/>
        </svg>
      </div>
      <h3>第二步：配置创作</h3>
      <p>在「配置」页面选择创作模板，设置每个Agent的模型和参数。</p>
      <p class="tip">建议新手使用「短篇」模板，3个Part，约1万字。</p>
    </div>
    <div class="guide-step" v-if="step === 3">
      <div class="guide-icon">
        <svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke-width="2">
          <path d="M32 8 L56 32 L32 56 L8 32 Z" stroke="#0ea5e9"/>
          <path d="M32 18 L46 32 L32 46 L18 32 Z" fill="#0ea5e9" fill-opacity="0.2" stroke="#0ea5e9"/>
          <line x1="32" y1="8" x2="32" y2="56" stroke="#e2e8f0"/>
          <line x1="8" y1="32" x2="56" y2="32" stroke="#e2e8f0"/>
        </svg>
      </div>
      <h3>第三步：开始创作</h3>
      <p>点击「开始创作」，AI将自动完成灵感解析→情节规划→章节创作→风格优化的全流程。</p>
      <p class="tip">创作过程中可随时暂停，进度自动保存。</p>
    </div>
    <div class="guide-step" v-if="step === 4">
      <div class="guide-icon">
        <svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke-width="2">
          <rect x="8" y="12" width="48" height="40" rx="4" stroke="#0ea5e9"/>
          <line x1="8" y1="24" x2="56" y2="24" stroke="#0ea5e9"/>
          <line x1="24" y1="12" x2="24" y2="52" stroke="#e2e8f0"/>
          <line x1="32" y1="24" x2="32" y2="52" stroke="#e2e8f0"/>
          <line x1="40" y1="24" x2="40" y2="52" stroke="#e2e8f0"/>
        </svg>
      </div>
      <h3>第四步：预览与修改</h3>
      <p>在「预览」页面查看并编辑每个章节，选中文字可AI改写润色。</p>
      <p class="tip">使用「润色/扩写/缩写」功能优化细节。</p>
    </div>
    <template #footer>
      <div class="guide-footer">
        <div class="dots">
          <span v-for="s in 4" :key="s" :class="{ active: step === s }">●</span>
        </div>
        <div>
          <n-button v-if="step > 1" @click="step--">← 上一步</n-button>
          <n-button v-if="step < 4" type="primary" @click="step++">下一步 →</n-button>
          <n-button v-if="step === 4" type="primary" @click="finish">开始使用</n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const visible = ref(false)
const step = ref(1)
const FIRST_KEY = 'kuimulang_v6_guide_done'

onMounted(() => {
  const done = localStorage.getItem(FIRST_KEY)
  if (!done) visible.value = true
})

function finish() {
  localStorage.setItem(FIRST_KEY, '1')
  visible.value = false
}
</script>

<style scoped>
.guide-step { text-align: center; padding: 20px 0; }
.guide-icon { margin-bottom: 16px; display: flex; justify-content: center; }
.guide-step h3 { margin-bottom: 12px; color: #0f172a; font-weight: 600; }
.guide-step p { color: #64748b; line-height: 1.8; font-size: 14px; }
.tip { background: #f0f9ff; border-radius: 8px; padding: 10px; margin-top: 12px; color: #0284c7 !important; font-size: 13px; border: 1px solid #e0f2fe; }
.guide-footer { display: flex; align-items: center; justify-content: space-between; }
.dots { color: #e2e8f0; font-size: 12px; }
.dots span.active { color: #0ea5e9; }
</style>
