<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="work-title">{{ workTitle || '会话管理' }}</span>
        </div>
        <div class="topbar-actions">
          <n-button type="primary" @click="showCreate = true">
            <span class="btn-icon">+</span>
            新建会话
          </n-button>
        </div>
      </div>

      <div class="content">
        <div v-if="store.loading" class="loading">
          <n-spin size="large" />
        </div>

        <div v-else-if="!store.sessions.length" class="empty">
          <div class="empty-icon">📁</div>
          <div class="empty-title">还没有会话</div>
          <div class="empty-desc">创建一个新会话，开启一段独立创作</div>
          <n-button type="primary" size="large" @click="showCreate = true">创建第一个会话</n-button>
        </div>

        <div v-else class="session-grid">
          <div v-for="session in store.sessions" :key="session.session_id" class="session-card">
            <div class="card-header">
              <span class="session-title">{{ session.title || '未命名会话' }}</span>
              <n-tag :type="statusTag(session.status)" size="small" :bordered="false">
                {{ statusLabel(session.status) }}
              </n-tag>
            </div>
            <div class="session-meta">
              <div class="meta-row">
                <span class="meta-label">阶段</span>
                <span class="meta-value">{{ phaseLabel(session.phase) }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">创建</span>
                <span class="meta-value">{{ formatDateTime(session.created_at) }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">更新</span>
                <span class="meta-value">{{ formatDateTime(session.updated_at) }}</span>
              </div>
              <div v-if="session.inspiration" class="meta-row inspiration">
                <span class="meta-label">灵感</span>
                <span class="meta-value ellipsis">{{ session.inspiration }}</span>
              </div>
            </div>
            <div class="card-footer">
              <n-button size="small" type="primary" @click="handleSwitch(session)">切换</n-button>
              <n-button size="small" @click="handleRename(session)">重命名</n-button>
              <n-button size="small" quaternary type="error" @click="handleDelete(session)">删除</n-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建会话弹窗 -->
    <n-modal v-model:show="showCreate" preset="card" title="新建会话" style="width: 480px;">
      <n-form :model="{ title: newTitle, inspiration: newInspiration }">
        <n-form-item label="会话标题">
          <n-input v-model:value="newTitle" placeholder="为新会话命名（可选）" />
        </n-form-item>
        <n-form-item label="创作灵感" required>
          <n-input
            v-model:value="newInspiration"
            type="textarea"
            placeholder="描述故事灵感..."
            :rows="4"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" @click="handleCreate" :loading="creating">创建</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 删除确认弹窗 -->
    <n-modal v-model:show="showDeleteConfirm" preset="card" title="确认删除" style="width: 420px;">
      <div class="delete-confirm">
        <div class="warning">确定要删除会话 "{{ pendingDelete?.title }}" 吗？</div>
        <div class="hint">此操作不可恢复。</div>
      </div>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="showDeleteConfirm = false">取消</n-button>
          <n-button type="error" @click="confirmDelete" :loading="deleting">确认删除</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 重命名弹窗 -->
    <n-modal v-model:show="showRename" preset="card" title="重命名会话" style="width: 420px;">
      <n-form>
        <n-form-item label="新标题" required>
          <n-input v-model:value="renameTitle" placeholder="输入新标题" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="showRename = false">取消</n-button>
          <n-button type="primary" @click="confirmRename" :loading="renaming">保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 切换结果提示 -->
    <n-modal v-model:show="showSwitchResult" preset="card" title="会话切换" style="width: 420px;">
      <div>{{ switchResultMsg }}</div>
      <template #footer>
        <n-button type="primary" @click="closeSwitchResult">我知道了</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useSessionsStore } from '@/stores/sessions'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const store = useSessionsStore()

const workId = route.params.workId
const workTitle = ref('')

// 弹窗状态
const showCreate = ref(false)
const newTitle = ref('')
const newInspiration = ref('')
const creating = ref(false)

const showDeleteConfirm = ref(false)
const pendingDelete = ref(null)
const deleting = ref(false)

const showRename = ref(false)
const renameTitle = ref('')
const pendingRename = ref(null)
const renaming = ref(false)

const showSwitchResult = ref(false)
const switchResultMsg = ref('')

onMounted(async () => {
  await store.loadSessions(workId)
  // 尝试拿作品标题
  // P1-96: 改走统一 api 客户端，自动走 AbortController / 重试 / 401/500 错误处理
  try {
    const data = (await api.get(`/works/${workId}`)).data
    workTitle.value = data?.title || '会话管理'
  } catch {
    workTitle.value = '会话管理'
  }
})

async function handleCreate() {
  if (!newInspiration.value.trim()) {
    message.warning('请输入创作灵感')
    return
  }
  creating.value = true
  try {
    await store.createSession(workId, newTitle.value, newInspiration.value)
    showCreate.value = false
    newTitle.value = ''
    newInspiration.value = ''
    message.success('会话已创建')
  } catch (err) {
    message.error('创建失败: ' + (err.message || '未知错误'))
  } finally {
    creating.value = false
  }
}

function handleDelete(session) {
  pendingDelete.value = session
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  try {
    await store.deleteSession(pendingDelete.value.session_id)
    message.success('会话已删除')
  } catch (err) {
    message.error('删除失败: ' + (err.message || '未知错误'))
  } finally {
    deleting.value = false
    showDeleteConfirm.value = false
    pendingDelete.value = null
  }
}

function handleRename(session) {
  pendingRename.value = session
  renameTitle.value = session.title || ''
  showRename.value = true
}

async function confirmRename() {
  if (!pendingRename.value || !renameTitle.value.trim()) {
    message.warning('请输入标题')
    return
  }
  renaming.value = true
  try {
    await store.renameSession(pendingRename.value.session_id, renameTitle.value.trim())
    message.success('已重命名')
    showRename.value = false
  } catch (err) {
    message.error('重命名失败: ' + (err.message || '未知错误'))
  } finally {
    renaming.value = false
  }
}

async function handleSwitch(session) {
  try {
    const result = await store.switchSession(session.session_id)
    switchResultMsg.value = `已切换到会话 "${session.title}"（当前阶段：${phaseLabel(result?.phase || session.phase)}）`
    showSwitchResult.value = true
  } catch (err) {
    message.error('切换失败: ' + (err.message || '未知错误'))
  }
}

function closeSwitchResult() {
  showSwitchResult.value = false
  // 切换后跳回作品列表（用户可以从那里进入新激活的会话）
  router.push('/works')
}

function statusLabel(status) {
  const map = { active: '进行中', paused: '已暂停', completed: '已完成' }
  return map[status] || status || '未知'
}

function statusTag(status) {
  const map = { active: 'primary', paused: 'warning', completed: 'success' }
  return map[status] || 'default'
}

function phaseLabel(phase) {
  const map = { init: '未开始', phase1: '灵感解析', phase2: '情节规划', phase3: '章节创作', phase4: '风格优化' }
  return map[phase] || phase || '未知'
}

function formatDateTime(d) {
  if (!d) return ''
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return d
  return dt.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
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
.btn-icon { font-size: 18px; line-height: 1; margin-right: 4px; }

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
}

.session-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 18px;
}

.session-card {
  background: var(--color-surface, #fff);
  border-radius: var(--radius-lg, 12px);
  padding: 18px;
  border: 1px solid var(--color-border, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: all 0.25s ease;
}

.session-card:hover {
  box-shadow: var(--shadow-md, 0 4px 6px rgba(0,0,0,0.08));
  transform: translateY(-2px);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary, #333);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 0;
  border-top: 1px solid var(--color-border, #f1f5f9);
  border-bottom: 1px solid var(--color-border, #f1f5f9);
}

.meta-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  line-height: 1.6;
}

.meta-label {
  color: var(--color-text-secondary, #888);
  flex-shrink: 0;
}

.meta-value {
  color: var(--color-text-primary, #333);
  text-align: right;
  max-width: 70%;
}

.meta-value.ellipsis {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-footer {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.delete-confirm {
  padding: 8px 0;
}

.warning {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary, #333);
  margin-bottom: 8px;
}

.hint {
  font-size: 13px;
  color: var(--color-text-secondary, #666);
}

@media (max-width: 768px) {
  .session-grid {
    grid-template-columns: 1fr;
  }
  .content {
    padding: 16px;
  }
}
</style>