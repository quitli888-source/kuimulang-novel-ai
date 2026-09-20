<template>
  <div class="section">
    <div class="section-title">选择模板</div>
    <div class="template-grid">
      <div
        v-for="t in templates"
        :key="t.name"
        class="template-card"
        :class="{ active: selectedTemplate === t.name }"
        @click="emit('select', t)"
      >
        <div class="tpl-name">{{ t.name }}</div>
        <div class="tpl-info">{{ getDisplayInfo(t) }}</div>
      </div>
    </div>

    <transition name="fade">
      <div v-if="selectedTemplate === '自定义'" key="custom-config" class="custom-template-config">
        <div class="section-title" style="margin-top:24px">📝 自定义配置</div>
        <n-form label-placement="left" label-width="100">
          <n-form-item label="目标字数">
            <n-input-number
              :value="localTargetWords"
              @update:value="onTargetWordsChange"
              :min="1000"
              :max="500000"
              :step="1000"
              placeholder="请输入目标字数（建议10,000-100,000）"
              clearable
              size="large"
              style="width: 100%"
            />
          </n-form-item>
          <n-form-item label="Part数量">
            <n-input-number
              :value="localPartCount"
              @update:value="onPartCountChange"
              :min="1"
              :max="50"
              :step="1"
              placeholder="请输入Part数量（建议3-20）"
              clearable
              size="large"
              style="width: 100%"
            />
          </n-form-item>
          <n-alert type="info" style="margin-bottom:16px">
            预计每Part字数：{{ localTargetWords && localPartCount ? Math.round(localTargetWords / localPartCount).toLocaleString() : 0 }} 字
          </n-alert>
          <n-button type="primary" @click="emit('saveCustom')" block size="large">
            💾 保存自定义配置
          </n-button>
        </n-form>
      </div>
    </transition>

    <div v-if="selectedTemplate && selectedTemplate !== '自定义'" class="current-template-info">
      <n-card size="small" :bordered="false" style="margin-top:20px; background:#f0f9ff;">
        <template #header>
          <span style="font-weight:600">✅ 当前选择：{{ selectedTemplate }}</span>
        </template>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">目标字数</span>
            <span class="info-value">{{ currentTemplateInfo.target_words?.toLocaleString() || '0' }} 字</span>
          </div>
          <div class="info-item">
            <span class="info-label">Part数量</span>
            <span class="info-value">{{ currentTemplateInfo.part_count || 0 }} Part</span>
          </div>
          <div class="info-item">
            <span class="info-label">每Part范围</span>
            <span class="info-value">{{ currentTemplateInfo.part_word_min || 0 }}-{{ currentTemplateInfo.part_word_max || 0 }} 字</span>
          </div>
        </div>
      </n-card>
    </div>
  </div>
</template>

<script setup>
// P1-15: TemplateSection.vue —— 从 ConfigPanel.vue 拆出 (lines 16-94)
import { computed, ref, watch } from 'vue'

const props = defineProps({
  templates: { type: Array, required: true },
  selectedTemplate: { type: String, default: '' },
  customTargetWords: { type: Number, default: null },
  customPartCount: { type: Number, default: null },
  currentTemplateInfo: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['select', 'saveCustom', 'update:customTargetWords', 'update:customPartCount'])

// R4-P1-x: v-model:value 此前直接绑 props（只读），用户输入被静默丢弃且保存时提交旧值。
// 改为本地 ref + emit update，父组件负责回写。
const localTargetWords = ref(props.customTargetWords)
const localPartCount = ref(props.customPartCount)
watch(() => props.customTargetWords, (v) => { localTargetWords.value = v })
watch(() => props.customPartCount, (v) => { localPartCount.value = v })

function onTargetWordsChange(v) {
  localTargetWords.value = v
  emit('update:customTargetWords', v)
}
function onPartCountChange(v) {
  localPartCount.value = v
  emit('update:customPartCount', v)
}

function getDisplayInfo(t) {
  return `${t.target_words?.toLocaleString() || 0} 字 · ${t.part_count} Part`
}
</script>

<style scoped>
.template-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }
.template-card { padding: 16px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; transition: all 0.2s; }
.template-card.active { border-color: #2563eb; background: #eff6ff; box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
.tpl-name { font-weight: 600; font-size: 16px; margin-bottom: 8px; }
.tpl-info { font-size: 13px; color: #6b7280; }
.current-template-info { margin-top: 16px; }
.info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.info-label { color: #6b7280; font-size: 13px; }
.info-value { display: block; font-weight: 600; font-size: 16px; margin-top: 4px; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>