<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="work-title">{{ workTitle || '角色档案' }}</span>
        </div>
        <div class="topbar-actions">
          <n-button @click="addCharacter">+ 添加角色</n-button>
          <n-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</n-button>
        </div>
      </div>

      <div class="content">
        <div v-if="loading" class="loading">
          <n-spin size="large" />
        </div>

        <div v-else-if="!characters.length" class="empty">
          <div class="empty-icon">🎭</div>
          <div class="empty-title">还没有角色</div>
          <div class="empty-desc">
            {{ hasPlot ?
              '点击"添加角色"开始，或前往 Plot 规划重新生成角色。' :
              '当前作品未运行 Plot 规划，请先在配置阶段生成情节蓝图。' }}
          </div>
          <n-button type="primary" size="large" @click="addCharacter">添加第一个角色</n-button>
        </div>

        <div v-else class="character-list">
          <div v-for="(char, idx) in characters" :key="idx" class="character-card">
            <div class="card-header">
              <span class="char-index">#{{ idx + 1 }}</span>
              <div class="header-fields">
                <n-input v-model:value="char.name" placeholder="姓名" class="name-input" />
                <n-select
                  v-model:value="char.role"
                  :options="roleOptions"
                  placeholder="角色定位"
                  style="width: 140px;"
                />
              </div>
              <n-button text type="error" @click="removeCharacter(idx)">删除</n-button>
            </div>
            <div class="card-body">
              <div class="field">
                <label>身份</label>
                <n-input v-model:value="char.identity" type="textarea" :rows="2" placeholder="角色身份描述" />
              </div>
              <div class="field-row">
                <div class="field">
                  <label>核心特质</label>
                  <n-input v-model:value="char.core_trait" placeholder="如：冷静、执着" />
                </div>
                <div class="field">
                  <label>动机</label>
                  <n-input v-model:value="char.motivation" placeholder="驱动目标" />
                </div>
              </div>
              <div class="field-row">
                <div class="field">
                  <label>秘密</label>
                  <n-input v-model:value="char.secret" placeholder="角色隐藏信息" />
                </div>
                <div class="field">
                  <label>角色弧</label>
                  <n-input v-model:value="char.arc" placeholder="人物成长轨迹" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const route = useRoute()
const message = useMessage()
const workId = route.params.workId

const workTitle = ref('')
const characters = ref([])
const originalJson = ref('')
const loading = ref(true)
const saving = ref(false)
const hasPlot = ref(false)

const roleOptions = [
  { label: '主角', value: '主角' },
  { label: '配角', value: '配角' },
  { label: '反派', value: '反派' },
  { label: '导师', value: '导师' },
  { label: '助手', value: '助手' },
  { label: '其他', value: '其他' },
]

const dirty = computed(() => JSON.stringify(characters.value) !== originalJson.value)

onMounted(async () => {
  loading.value = true
  try {
    const res = await api.get(`/works/${workId}`)
    workTitle.value = res.data.title || '角色档案'
    characters.value = Array.isArray(res.data.characters) ? JSON.parse(JSON.stringify(res.data.characters)) : []
    originalJson.value = JSON.stringify(characters.value)
    hasPlot.value = !!(res.data.part_outline && res.data.part_outline.length)
  } catch (err) {
    message.error('加载失败: ' + (err.message || '未知错误'))
    characters.value = []
    originalJson.value = '[]'
  } finally {
    loading.value = false
  }
})

function addCharacter() {
  characters.value.push({
    name: '',
    role: '主角',
    identity: '',
    core_trait: '',
    motivation: '',
    secret: '',
    arc: '',
  })
}

function removeCharacter(idx) {
  characters.value.splice(idx, 1)
}

async function save() {
  saving.value = true
  try {
    // 清理空名字的角色
    const cleaned = characters.value
      .filter(c => (c.name || '').trim())
      .map(c => ({
        name: c.name || '',
        role: c.role || '其他',
        identity: c.identity || '',
        core_trait: c.core_trait || '',
        motivation: c.motivation || '',
        secret: c.secret || '',
        arc: c.arc || '',
      }))
    await api.patch(`/works/${workId}`, { characters: cleaned })
    originalJson.value = JSON.stringify(cleaned)
    characters.value = JSON.parse(originalJson.value)
    message.success(`已保存 ${cleaned.length} 个角色`)
  } catch (err) {
    message.error('保存失败: ' + (err.message || '未知错误'))
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  background: var(--color-background, #f8fafc);
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-bottom: 1px solid var(--color-border, #eee);
  background: var(--color-surface, #fff);
}
.topbar-left { display: flex; align-items: center; gap: 16px; }
.work-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary, #333); }
.topbar-actions { display: flex; gap: 8px; }

.content {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
}

.loading,
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  gap: 12px;
}

.empty-icon {
  font-size: 48px;
}
.empty-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary, #333);
}
.empty-desc {
  font-size: 14px;
  color: var(--color-text-secondary, #888);
  margin-bottom: 16px;
  max-width: 480px;
  text-align: center;
  line-height: 1.6;
}

.character-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 920px;
}

.character-card {
  background: var(--color-surface, #fff);
  border-radius: var(--radius-lg, 12px);
  padding: 18px;
  border: 1px solid var(--color-border, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: box-shadow 0.25s ease;
}
.character-card:hover {
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
}

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border, #f1f5f9);
}

.char-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-muted, #94a3b8);
  letter-spacing: 0.5px;
}

.header-fields {
  flex: 1;
  display: flex;
  gap: 8px;
  align-items: center;
}

.name-input {
  flex: 1;
  font-weight: 600;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary, #888);
}

.field-row {
  display: flex;
  gap: 12px;
}

@media (max-width: 768px) {
  .field-row {
    flex-direction: column;
  }
  .header-fields {
    flex-direction: column;
    align-items: stretch;
  }
  .content {
    padding: 16px;
  }
}
</style>