<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="page-title">创作配置</span>
        </div>
        <n-button type="primary" @click="startWriting" :loading="starting">开始创作</n-button>
      </div>

      <div class="content">
        <n-tabs type="line" animated>
          <!-- 创作模板 -->
          <n-tab-pane name="template" tab="创作模板">
            <div class="section">
              <div class="section-title">选择模板</div>
              <div class="template-grid">
                <div
                  v-for="t in templates"
                  :key="t.name"
                  class="template-card"
                  :class="{ active: selectedTemplate === t.name }"
                  @click="handleTemplateSelect(t)"
                >
                  <div class="tpl-name">{{ t.name }}</div>
                  <div class="tpl-info">{{ getTemplateDisplayInfo(t) }}</div>
                </div>
              </div>
              
              <!-- 自定义模板配置 -->
              <transition name="fade">
                <div v-if="selectedTemplate === '自定义'" key="custom-config" class="custom-template-config">
                  <div class="section-title" style="margin-top:24px">📝 自定义配置</div>
                  <n-form label-placement="left" label-width="100">
                    <n-form-item label="目标字数">
                      <n-input-number 
                        v-model:value="customTargetWords" 
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
                        v-model:value="customPartCount" 
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
                      预计每Part字数：{{ customTargetWords && customPartCount ? Math.round(customTargetWords / customPartCount).toLocaleString() : 0 }} 字
                    </n-alert>
                    <n-button type="primary" @click="saveCustomTemplate" block size="large">
                      💾 保存自定义配置
                    </n-button>
                  </n-form>
                </div>
              </transition>

              <!-- 当前选中模板信息 -->
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
          </n-tab-pane>

          <!-- LLM配置 -->
          <n-tab-pane name="llm" tab="LLM配置">
            <div class="section">
              <div class="section-title">选择LLM供应商</div>
              <n-alert v-if="!hasAnyApiKey" type="warning" style="margin-bottom:16px">
                尚未配置任何API Key，请先选择供应商并填入Key
              </n-alert>
              <div class="provider-grid">
                <div
                  v-for="p in providers"
                  :key="p.id"
                  class="provider-card"
                  :class="{ active: selectedProvider === p.id, 'has-key': p.has_api_key }"
                  @click="selectProvider(p)"
                >
                  <div class="p-name">{{ p.name }}</div>
                  <div class="p-status">
                    <span v-if="p.has_api_key" style="color:#52c41a">已配置 ✓</span>
                    <span v-else style="color:#ff4d4f">未配置</span>
                  </div>
                </div>
              </div>

              <template v-if="selectedProvider">
                <div class="section-title" style="margin-top:24px">
                  {{ selectedProviderName }} - API Key配置
                </div>
                <n-form label-placement="left" label-width="100">
                  <n-form-item :label="selectedProviderName + ' API Key'">
                    <n-input
                      v-model:value="currentApiKey"
                      type="password"
                      show-password-on="click"
                      :placeholder="'输入 ' + selectedProviderName + ' API Key'"
                    />
                  </n-form-item>
                  
                  <!-- 自定义LLM的额外配置 -->
                  <template v-if="selectedProvider === 'custom'">
                    <n-divider>自定义LLM配置（以下均可编辑）</n-divider>
                    <n-form-item label="Base URL">
                      <n-input
                        v-model:value="customBaseUrl"
                        placeholder="输入LLM API的Base URL，例如 https://api.openai.com/v1"
                        clearable
                      >
                        <template #prefix>🔗</template>
                      </n-input>
                    </n-form-item>
                    <n-form-item label="主模型">
                      <n-input
                        v-model:value="customModel"
                        placeholder="输入主模型名称，例如 gpt-4o-mini、claude-3-haiku等"
                        clearable
                      >
                        <template #prefix>🤖</template>
                      </n-input>
                      <div class="field-tip">用于小说写作的主要模型</div>
                    </n-form-item>
                    <n-form-item label="JSON模型">
                      <n-input
                        v-model:value="customJsonModel"
                        placeholder="输入JSON模型名称，例如 gpt-4o-mini"
                        clearable
                      >
                        <template #prefix>📋</template>
                      </n-input>
                      <div class="field-tip">用于结构化数据处理的模型</div>
                    </n-form-item>
                  </template>
                  
                  <!-- 所有供应商的模型配置（都可编辑） -->
                  <template v-else>
                    <n-divider>模型配置（可编辑）</n-divider>
                    <n-form-item label="主模型">
                      <n-input
                        v-model:value="editableModel"
                        clearable
                        placeholder="输入主模型名称"
                      >
                        <template #prefix>🤖</template>
                      </n-input>
                      <div class="field-tip">默认：{{ selectedProviderModel }}（可直接修改）</div>
                    </n-form-item>
                    <n-form-item label="JSON模型">
                      <n-input
                        v-model:value="editableJsonModel"
                        clearable
                        placeholder="输入JSON模型名称"
                      >
                        <template #prefix>📋</template>
                      </n-input>
                      <div class="field-tip">默认：{{ selectedProviderJsonModel }}（可直接修改）</div>
                    </n-form-item>
                  </template>
                  
                  <n-button 
                    type="primary" 
                    @click="saveProviderKey" 
                    :loading="savingLLM"
                    block
                    size="large"
                    style="margin-top:16px"
                  >
                    💾 保存 {{ selectedProviderName }} 配置
                  </n-button>
                </n-form>
              </template>

              <div class="section-title" style="margin-top:32px">全局参数</div>
              <n-form label-placement="left" label-width="120">
                <n-form-item label="创作温度">
                  <n-slider v-model:value="globalTemp" :min="0" :max="1" :step="0.05" />
                  <span style="margin-left:12px; font-weight:600; color:#E63946">{{ globalTemp }}</span>
                </n-form-item>
                <n-button type="primary" @click="saveLLMConfig">保存全局参数</n-button>
              </n-form>
            </div>
          </n-tab-pane>

          <!-- Agent配置 -->
          <n-tab-pane name="agents" tab="Agent配置">
            <div class="section">
              <div class="section-title">每个Agent独立配置</div>
              <n-collapse accordion>
                <n-collapse-item v-for="(cfg, name) in agentConfigs" :key="name" :title="agentLabel(name)" :name="name">
                  <n-form label-placement="left" label-width="90">
                    <n-form-item label="模型">
                      <n-input v-model:value="cfg.model" />
                    </n-form-item>
                    <n-form-item label="温度">
                      <n-slider v-model:value="cfg.temperature" :min="0" :max="1" :step="0.05" />
                      <span style="margin-left:12px">{{ cfg.temperature }}</span>
                    </n-form-item>
                    <n-form-item label="最大Token">
                      <n-input-number v-model:value="cfg.max_tokens" :min="1000" :max="32000" :step="500" />
                    </n-form-item>
                    <n-button size="small" type="primary" @click="saveAgent(name, cfg)">保存</n-button>
                  </n-form>
                </n-collapse-item>
              </n-collapse>
            </div>
          </n-tab-pane>

          <!-- R15: 滑动窗口配置（UI 手动调整） -->
          <!-- R23-P1-15: 提取到独立 sub-component，ConfigPanel 仅传 props + emit -->
          <n-tab-pane name="window" tab="滑动窗口">
            <WindowConfigTab
              :windowSize="windowConfig.window_size"
              :rollingEvery="windowConfig.rolling_every"
              :milestoneEvery="windowConfig.milestone_every"
              :defaults="windowConfig.defaults"
              :source="windowConfig.source"
              :saving="windowSaving"
              :lastResult="windowConfig.last_result"
              :lastOk="windowConfig.last_ok"
              @update:windowSize="v => windowConfig.window_size = v"
              @update:rollingEvery="v => windowConfig.rolling_every = v"
              @update:milestoneEvery="v => windowConfig.milestone_every = v"
              @save="saveWindowConfig"
              @reset="resetWindowConfig"
            />
          </n-tab-pane>

          <!-- 创作模式 -->
          <n-tab-pane name="mode" tab="创作模式">
            <div class="section">
              <div class="section-title">确认机制</div>
              <n-switch v-model:value="confirmMode" />
              <div class="mode-desc">{{ confirmMode ? '手动确认模式：低分/P0问题会弹窗确认' : '自动模式：自动处理所有情况' }}</div>
              <n-button type="primary" style="margin-top:16px" @click="saveAppConfig">保存设置</n-button>
            </div>
          </n-tab-pane>
        </n-tabs>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'
import WindowConfigTab from '@/components/config/WindowConfigTab.vue'

const router = useRouter()
const route = useRoute()

const workId = ref(route.params.workId || route.query.workId || '')
const templates = ref([])
const selectedTemplate = ref('')
const confirmMode = ref(true)
const llmConfig = ref({ api_key: '', base_url: '', model: '', json_model: '' })
const agentConfigs = ref({})
const starting = ref(false)
const savingLLM = ref(false)

// R15: 滑动窗口配置
const windowConfig = ref({
  window_size: 6,
  rolling_every: 3,
  milestone_every: 20,
  defaults: { window_size: 6, rolling_every: 3, milestone_every: 20 },
  source: 'loading',
  last_result: '',
  last_ok: true,
})
const windowSaving = ref(false)

// 多供应商支持
const providers = ref([])
const selectedProvider = ref('')
const currentApiKey = ref('')
const globalTemp = ref(0.7)

// 自定义LLM配置
const customBaseUrl = ref('')
const customModel = ref('gpt-4o-mini')
const customJsonModel = ref('gpt-4o-mini')

// 可编辑的模型配置（用于预设供应商）
const editableModel = ref('')
const editableJsonModel = ref('')

// 自定义模板配置
const customTargetWords = ref(20000)
const customPartCount = ref(4)

// 当前选中的模板信息（用于显示）
const currentTemplateInfo = computed(() => {
  const t = templates.value.find(x => x.name === selectedTemplate.value)
  return t || { name: '短篇', target_words: 10000, part_count: 3 }
})

const selectedProviderName = computed(() => {
  const p = providers.value.find(x => x.id === selectedProvider.value)
  return p ? p.name : ''
})
const selectedProviderModel = computed(() => {
  const p = providers.value.find(x => x.id === selectedProvider.value)
  return p ? p.model : ''
})
const selectedProviderJsonModel = computed(() => {
  const p = providers.value.find(x => x.id === selectedProvider.value)
  return p ? p.json_model : ''
})
const hasAnyApiKey = computed(() => providers.value.some(x => x.has_api_key))

const agentLabels = {
  plot_planner: '情节规划Agent',
  part_writer: 'Part写作Agent',
  style_optimizer: '风格优化Agent',
  emotion_review: '情感审核Agent',
  logic_review: '逻辑审核Agent',
  consistency_review: '一致性审核Agent',
}

function agentLabel(name) { return agentLabels[name] || name }

// 获取模板显示信息
function getTemplateDisplayInfo(t) {
  if (t.name === '自定义') {
    return '完全自定义 ✏️'
  }
  if (t.target_words != null) {
    return `${Number(t.target_words).toLocaleString()}字 / ${t.part_count}Part`
  }
  return '完全自定义'
}

// 处理模板选择
function handleTemplateSelect(t) {
  console.log('🎯 选择模板:', t.name, '字数:', t.target_words, 'Part:', t.part_count)
  
  // 立即更新前端状态
  selectedTemplate.value = t.name
  
  // 如果是自定义模板，设置默认值并确保配置区域显示
  if (t.name === '自定义') {
    console.log('✅ 选择自定义模板，显示配置区域')
    // 使用已加载的自定义配置值，如果没有则使用默认值
    if (!customTargetWords.value || customTargetWords.value <= 0) {
      customTargetWords.value = 20000
    }
    if (!customPartCount.value || customPartCount.value <= 0) {
      customPartCount.value = 4
    }
    console.log('📊 自定义模板配置:', { target_words: customTargetWords.value, part_count: customPartCount.value })
    
    // 不立即发送API请求，等待用户填写后手动保存
    console.log('⏳ 等待用户填写自定义配置...')
    return
  }
  
  // 对于预设模板，立即发送API请求保存
  console.log('💾 保存预设模板配置:', t.name)
  api.put('/config/app', { 
    confirm_mode: confirmMode.value, 
    template_name: t.name,
    custom_target_words: t.target_words,
    custom_part_count: t.part_count
  }).then(() => {
    console.log('✅ 模板配置保存成功:', t.name)
  }).catch(error => {
    console.error('❌ 模板配置保存失败:', error)
  })
}

onMounted(async () => {
  try {
    console.log('🚀 开始加载配置...')
    const [tplRes, llmRes, agentRes, appRes, providersRes, llmCfgRes, winCfgRes] = await Promise.all([
      api.get('/config/templates'),
      api.get('/config/llm'),
      api.get('/config/agents'),
      api.get('/config/app'),
      api.get('/config/providers'),
      api.get('/config/llm-config'),
      api.get('/config/sliding-window'),
    ])
    // R15: 加载滑动窗口配置
    if (winCfgRes?.data) {
      windowConfig.value = {
        ...windowConfig.value,
        window_size: winCfgRes.data.window_size,
        rolling_every: winCfgRes.data.rolling_every,
        milestone_every: winCfgRes.data.milestone_every,
        defaults: winCfgRes.data.defaults || windowConfig.value.defaults,
        source: winCfgRes.data.source || 'unknown',
      }
      console.log('📐 滑动窗口配置加载完成:', windowConfig.value)
    }
    
    console.log('📦 配置数据加载完成')
    
    templates.value = tplRes.data
    llmConfig.value = llmRes.data
    agentConfigs.value = agentRes.data
    confirmMode.value = Boolean(appRes.data.confirm_mode)
    selectedTemplate.value = appRes.data.template?.name || '短篇'
    
    console.log('📋 应用配置:', appRes.data)

    // 加载自定义模板配置（无论当前是否选中自定义模板，都加载配置以备切换时使用）
    if (appRes.data.template) {
      customTargetWords.value = appRes.data.template.target_words || 20000
      customPartCount.value = appRes.data.template.part_count || 4
      console.log('📊 自定义模板配置已加载:', { target_words: customTargetWords.value, part_count: customPartCount.value })
    }

    // 加载供应商
    providers.value = providersRes.data
    const cfg = llmCfgRes.data
    selectedProvider.value = cfg.active_provider_id || 'minimax'
    globalTemp.value = cfg.global_temperature || 0.7
    
    console.log('🔌 供应商配置:', { active_provider_id: selectedProvider.value, providers_count: providers.value.length })

    // 设置当前provider的key和配置
    updateCurrentApiKey()
    
    console.log('✅ 配置初始化完成')
  } catch (error) {
    console.error('❌ 加载配置失败:', error)
  }
})

// 监听供应商变化，更新可编辑的模型配置
watch(selectedProvider, (newProvider) => {
  if (newProvider && newProvider !== 'custom') {
    const p = providers.value.find(x => x.id === newProvider)
    if (p) {
      editableModel.value = p.model || ''
      editableJsonModel.value = p.json_model || ''
      console.log('🔄 更新可编辑模型配置:', { model: editableModel.value, json_model: editableJsonModel.value })
    }
  }
}, { immediate: true })

function updateCurrentApiKey() {
  const p = providers.value.find(x => x.id === selectedProvider.value)
  currentApiKey.value = p?.api_key || ''
  
  // 如果是自定义LLM，加载已保存的配置字段
  if (selectedProvider.value === 'custom') {
    customBaseUrl.value = p?.base_url || ''
    customModel.value = p?.model || 'gpt-4o-mini'
    customJsonModel.value = p?.json_model || 'gpt-4o-mini'
    console.log('🎨 更新自定义LLM配置:', { 
      base_url: customBaseUrl.value, 
      model: customModel.value, 
      json_model: customJsonModel.value 
    })
  }
  
  console.log('🔑 更新API Key:', { provider: selectedProvider.value, has_key: !!currentApiKey.value })
}

function selectProvider(p) {
  console.log('🔄 选择供应商:', p.id, p.name)
  selectedProvider.value = p.id
  updateCurrentApiKey()
}

async function saveProviderKey() {
  savingLLM.value = true
  try {
    // 构建请求数据
    const requestData = {
      api_key: currentApiKey.value
    }
    
    // 如果是自定义LLM，添加额外的配置字段
    if (selectedProvider.value === 'custom') {
      requestData.base_url = customBaseUrl.value
      requestData.model = customModel.value
      requestData.json_model = customJsonModel.value
      console.log('💾 保存自定义LLM配置:', requestData)
    } else {
      // 对于预设供应商，也保存用户修改的模型配置
      requestData.model = editableModel.value
      requestData.json_model = editableJsonModel.value
      console.log('💾 保存供应商模型配置:', { provider: selectedProvider.value, ...requestData })
    }
    
    await api.put(`/config/providers/${selectedProvider.value}`, requestData)
    console.log('✅ 供应商配置保存成功')
    
    // 激活该供应商
    await api.put('/config/llm-config', {
      active_provider_id: selectedProvider.value,
      per_agent_enabled: false,
      global_temperature: globalTemp.value,
    })
    
    // 刷新供应商列表
    const res = await api.get('/config/providers')
    providers.value = res.data
    console.log('🔄 供应商列表刷新成功')
    
    alert(`${selectedProviderName.value} 配置保存成功！`)
  } catch (error) {
    console.error('❌ 保存供应商配置失败:', error)
    alert('保存失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    savingLLM.value = false
  }
}

async function saveLLMConfig() {
  try {
    await api.put('/config/llm-config', {
      active_provider_id: selectedProvider.value,
      per_agent_enabled: false,
      global_temperature: globalTemp.value,
    })
    alert('全局参数保存成功')
  } catch (error) {
    console.error('保存全局参数失败:', error)
    alert('保存失败')
  }
}

async function saveLLM() {
  savingLLM.value = true
  try {
    await api.put('/config/llm', llmConfig.value)
    alert('LLM配置保存成功')
  } catch (error) {
    console.error('保存LLM配置失败:', error)
    alert('保存失败')
  } finally {
    savingLLM.value = false
  }
}

async function saveAgent(name, cfg) {
  try {
    await api.put(`/config/agents/${name}`, cfg)
    alert(`${agentLabel(name)} 配置保存成功`)
  } catch (error) {
    console.error('保存Agent配置失败:', error)
    alert('保存失败')
  }
}

async function saveAppConfig() {
  try {
    await api.put('/config/app', { 
      confirm_mode: confirmMode.value, 
      template_name: selectedTemplate.value,
      custom_target_words: customTargetWords.value,
      custom_part_count: customPartCount.value
    })
    alert('设置保存成功')
  } catch (error) {
    console.error('保存设置失败:', error)
    alert('保存失败')
  }
}

async function saveCustomTemplate() {
  try {
    // 验证输入值
    if (!customTargetWords.value || customTargetWords.value < 1000) {
      alert('请输入有效的目标字数（至少1000字）')
      return
    }
    if (!customPartCount.value || customPartCount.value < 1) {
      alert('请输入有效的Part数量（至少1个）')
      return
    }
    
    console.log('💾 保存自定义模板:', { 
      target_words: customTargetWords.value, 
      part_count: customPartCount.value,
      avg_per_part: Math.round(customTargetWords.value / customPartCount.value)
    })
    
    await api.put('/config/app', {
      confirm_mode: confirmMode.value,
      template_name: '自定义',
      custom_target_words: customTargetWords.value,
      custom_part_count: customPartCount.value
    })
    
    alert(`✅ 自定义模板配置保存成功！\n\n目标字数：${customTargetWords.value.toLocaleString()}字\nPart数量：${customPartCount.value}个\n每Part约：${Math.round(customTargetWords.value / customPartCount.value).toLocaleString()}字`)
  } catch (error) {
    console.error('❌ 保存自定义模板失败:', error)
    alert('保存自定义模板失败: ' + (error.response?.data?.detail || error.message))
  }
}

// R15: 滑动窗口配置 保存 / 重置
async function saveWindowConfig() {
  windowSaving.value = true
  try {
    const resp = await api.put('/config/sliding-window', {
      window_size: Number(windowConfig.value.window_size),
      rolling_every: Number(windowConfig.value.rolling_every),
      milestone_every: Number(windowConfig.value.milestone_every),
    })
    windowConfig.value.last_ok = true
    windowConfig.value.last_result = `✅ 保存成功：window_size=${resp.data.config.window_size}, rolling_every=${resp.data.config.rolling_every}, milestone_every=${resp.data.config.milestone_every}`
    windowConfig.value.source = 'user_file'
    console.log('💾 滑动窗口配置保存:', resp.data.config)
    alert(`✅ 滑动窗口配置已保存！\n下次启动创作时生效。`)
  } catch (error) {
    windowConfig.value.last_ok = false
    windowConfig.value.last_result = `❌ 保存失败: ${error.response?.data?.detail || error.message}`
    console.error('❌ 滑动窗口配置保存失败:', error)
    alert('保存失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    windowSaving.value = false
  }
}

async function resetWindowConfig() {
  if (!confirm('确认重置为默认值？（删除 data/window_config.json）')) return
  windowSaving.value = true
  try {
    await api.post('/config/sliding-window/reset', {})
    // 重置后重新加载
    const resp = await api.get('/config/sliding-window')
    windowConfig.value = {
      ...windowConfig.value,
      ...resp.data,
      last_ok: true,
      last_result: '✅ 已重置为默认值',
    }
    console.log('🔄 滑动窗口配置已重置')
  } catch (error) {
    windowConfig.value.last_ok = false
    windowConfig.value.last_result = '❌ 重置失败: ' + (error.response?.data?.detail || error.message)
    alert('重置失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    windowSaving.value = false
  }
}

async function startWriting() {
  starting.value = true
  try {
    if (workId.value) {
      await api.post('/writing/start', { work_id: workId.value })
      router.push(`/writing/${workId.value}`)
    } else {
      alert('请先选择或创建作品')
    }
  } catch (error) {
    console.error('开始创作失败:', error)
    alert('开始创作失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    starting.value = false
  }
}
</script>

<style scoped>
.layout { display: flex; height: 100vh; background: var(--color-background, #f8fafc); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { 
  display: flex; 
  align-items: center; 
  justify-content: space-between; 
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
.section { max-width: 700px; }
.section-title { 
  font-size: 15px; 
  font-weight: 600; 
  color: var(--color-text-primary, #333); 
  margin-bottom: 16px;
  letter-spacing: -0.2px;
}
.template-grid { 
  display: grid; 
  grid-template-columns: repeat(3, 1fr); 
  gap: 12px; 
}
.template-card { 
  background: var(--color-surface, #fff); 
  border: 2px solid var(--color-border, #e2e8f0); 
  border-radius: var(--radius-lg, 10px); 
  padding: 16px; 
  cursor: pointer; 
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
  position: relative; 
  overflow: hidden;
}
.template-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
  transition: left 0.5s ease;
}
.template-card:hover::before {
  left: 100%;
}
.template-card:hover { 
  border-color: var(--color-primary, #2563eb); 
  transform: translateY(-2px); 
  box-shadow: var(--shadow-md, 0 4px 12px rgba(37,99,235,0.15)); 
}
.template-card.active { 
  border-color: var(--color-primary, #2563eb); 
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary, #2563eb) 8%, white), color-mix(in srgb, var(--color-primary, #2563eb) 12%, white));
  box-shadow: var(--shadow-md, 0 4px 16px rgba(37,99,235,0.2)); 
}
.tpl-name { font-size: 14px; font-weight: 600; margin-bottom: 4px; color: var(--color-text-primary, #333); }
.tpl-info { font-size: 12px; color: var(--color-text-secondary, #888); }
.custom-template-config { 
  margin-top: 20px; 
  padding: 24px; 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  border: 2px solid var(--color-primary, #2563eb); 
  box-shadow: var(--shadow-md, 0 4px 12px rgba(37,99,235,0.1)); 
  animation: slideIn 0.3s ease-out; 
}
.current-template-info { margin-top: 16px; }
.info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.info-item { text-align: center; }
.info-label { display: block; font-size: 11px; color: var(--color-text-secondary, #888); margin-bottom: 4px; }
.info-value { display: block; font-size: 16px; font-weight: 600; color: var(--color-primary, #2563eb); }
.mode-desc { margin-top: 8px; font-size: 13px; color: var(--color-text-secondary, #666); line-height: 1.5; }
.provider-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.provider-card { 
  background: var(--color-surface, #fff); 
  border: 2px solid var(--color-border, #e2e8f0); 
  border-radius: var(--radius-lg, 10px); 
  padding: 16px; 
  cursor: pointer; 
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}
.provider-card:hover { 
  border-color: var(--color-primary, #2563eb); 
  transform: translateY(-2px); 
  box-shadow: var(--shadow-md, 0 4px 12px rgba(37,99,235,0.15)); 
}
.provider-card.active { 
  border-color: var(--color-primary, #2563eb); 
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); 
  box-shadow: var(--shadow-md, 0 4px 16px rgba(59,130,246,0.2)); 
}
.provider-card.has-key { border-color: var(--color-success, #52c41a); }
.p-name { font-size: 14px; font-weight: 600; margin-bottom: 6px; color: var(--color-text-primary, #333); }
.p-status { font-size: 12px; }
.field-tip { font-size: 11px; color: var(--color-text-secondary, #999); margin-top: 4px; }

/* 动画效果 */
.fade-enter-active, .fade-leave-active { transition: all 0.3s ease; }
.fade-enter-from { opacity: 0; transform: translateY(-10px); }
.fade-leave-to { opacity: 0; transform: translateY(10px); }

@keyframes slideIn {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 响应式设计 */
@media (max-width: 1024px) {
  .template-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .provider-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .topbar {
    padding: 12px 16px;
    flex-wrap: wrap;
    gap: 12px;
  }
  .page-title {
    font-size: 16px;
  }
  .content {
    padding: 16px;
  }
  .template-grid {
    grid-template-columns: 1fr;
  }
  .info-grid {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}

@media (max-width: 480px) {
  .section {
    max-width: 100%;
  }
  .custom-template-config {
    padding: 16px;
  }
}
</style>
