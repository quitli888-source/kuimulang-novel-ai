<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <h1 class="page-title">奎木狼AI小说创作系统</h1>
        <n-button type="primary" @click="showCreate = true">
          <span class="btn-icon">+</span>
          新建作品
        </n-button>
      </div>

      <div class="content">
        <div v-if="store.loading" class="loading">
          <n-spin size="large" />
        </div>

        <div v-else-if="store.works.length === 0" class="empty">
          <div class="empty-illustration">
            <img src="@/assets/empty-works.png" alt="空状态" class="empty-img" />
          </div>
          <div class="empty-title">还没有作品</div>
          <div class="empty-desc">创建您的第一部小说，开启AI创作之旅</div>
          <n-button type="primary" size="large" @click="showCreate = true" class="create-btn">
            创建第一部作品
          </n-button>
        </div>

        <div v-else class="works-grid">
          <div v-for="work in store.works" :key="work.id" class="work-card" @click="openWork(work)">
            <div class="card-header">
              <span class="card-number">#{{ String(store.works.indexOf(work) + 1).padStart(2, '0') }}</span>
              <n-tag :type="phaseTag(work.phase)" size="small" :bordered="false">{{ phaseLabel(work.phase) }}</n-tag>
            </div>
            <div class="work-title">{{ work.title }}</div>
            <div class="work-inspiration">{{ work.inspiration }}</div>
            <div class="work-footer">
              <span class="work-date">{{ formatDate(work.updated_at) }}</span>
              <div class="work-actions" @click.stop>
                <n-button text size="small" @click="$router.push(`/writing/${work.id}`)">创作</n-button>
                <n-button text size="small" @click="$router.push(`/preview/${work.id}`)">预览</n-button>
                <n-button text size="small" quaternary @click="deleteWork(work.id)">删除</n-button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建作品弹窗 -->
    <n-modal v-model:show="showCreate" preset="card" title="新建作品" style="width: 480px;">
      <n-form :model="{ title: newTitle, inspiration: newInspiration }">
        <n-form-item label="作品标题" required>
          <n-input v-model:value="newTitle" placeholder="给作品起个名字" size="large" />
        </n-form-item>
        <n-form-item label="创作灵感">
          <n-input v-model:value="newInspiration" type="textarea" placeholder="描述你的故事灵感..." :rows="4" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" @click="createWork" :loading="creating">创建作品</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 删除确认弹窗 -->
    <n-modal v-model:show="showDeleteConfirm" preset="card" title="确认删除" style="width: 420px;">
      <div class="delete-confirm-content">
        <div class="delete-warning">确定要删除这个作品吗？</div>
        <div class="delete-hint">此操作不可恢复，所有创作内容将被永久删除。</div>
      </div>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="confirmDelete(false)">取消</n-button>
          <n-button type="error" @click="confirmDelete(true)" :loading="deleting">确认删除</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useWorksStore } from '@/stores/works'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const store = useWorksStore()
const router = useRouter()
const message = useMessage()

store.fetchWorks()

const showCreate = ref(false)
const newTitle = ref('')
const newInspiration = ref('')
const creating = ref(false)
const showDeleteConfirm = ref(false)
const pendingDeleteId = ref(null)
const deleting = ref(false)

async function createWork() {
  if (!newTitle.value.trim()) return
  creating.value = true
  try {
    const id = await store.createWork(newTitle.value, newInspiration.value)
    showCreate.value = false
    newTitle.value = ''
    newInspiration.value = ''
    router.push(`/config/${id}`)
  } finally {
    creating.value = false
  }
}

function openWork(work) {
  if (work.phase === 'init') {
    router.push(`/config/${work.id}`)
  } else {
    router.push(`/writing/${work.id}`)
  }
}

function deleteWork(id) {
  pendingDeleteId.value = id
  showDeleteConfirm.value = true
}

async function confirmDelete(confirmed) {
  showDeleteConfirm.value = false
  if (!confirmed || !pendingDeleteId.value) {
    pendingDeleteId.value = null
    return
  }
  const id = pendingDeleteId.value
  pendingDeleteId.value = null
  deleting.value = true
  try {
    await store.deleteWork(id)
    message.success('作品已删除')
  } catch (err) {
    message.error('删除失败: ' + (err.message || '未知错误'))
  } finally {
    deleting.value = false
  }
}

function phaseLabel(phase) {
  const map = { init: '未开始', phase1: '灵感解析', phase2: '情节规划', phase3: '创作中', phase4: '优化完成' }
  return map[phase] || phase
}

function phaseTag(phase) {
  const map = { init: 'default', phase1: 'info', phase2: 'warning', phase3: 'primary', phase4: 'success' }
  return map[phase] || 'default'
}

function formatDate(d) {
  if (!d) return ''
  return new Date(d).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
</script>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  background: var(--color-background);
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  z-index: 1;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 28px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  position: relative;
}

.topbar::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--color-border) 20%,
    var(--color-primary) 50%,
    var(--color-border) 80%,
    transparent 100%
  );
}

.page-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-textPrimary);
  letter-spacing: -0.3px;
  background: linear-gradient(135deg, var(--color-textPrimary) 0%, var(--color-primary) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.btn-icon {
  font-size: 20px;
  line-height: 1;
  margin-right: 6px;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
  position: relative;
  z-index: 1;
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

.empty-illustration {
  width: 320px;
  height: 240px;
  margin-bottom: 24px;
  opacity: 0.95;
  animation: float 4s ease-in-out infinite;
}

.empty-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  filter: drop-shadow(0 12px 32px rgba(99, 102, 241, 0.2));
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-12px); }
}

.empty-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-textPrimary);
  margin-bottom: 8px;
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.empty-desc {
  font-size: 14px;
  color: var(--color-textSecondary);
  margin-bottom: 28px;
  max-width: 360px;
  text-align: center;
  line-height: 1.6;
}

.create-btn {
  padding: 0 36px;
  height: 48px;
  font-weight: 600;
  font-size: 15px;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
  box-shadow: 0 4px 20px rgba(14, 165, 233, 0.4);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.create-btn:hover {
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 8px 28px rgba(14, 165, 233, 0.5);
}

.works-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 24px;
}

.work-card {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  padding: 24px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  border: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.work-card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--color-primary) 40%, transparent),
    color-mix(in srgb, var(--color-accent) 40%, transparent),
    transparent
  );
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.work-card::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--color-primary), var(--color-accent));
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 0.3s ease;
}

.work-card:hover {
  transform: translateY(-6px);
  box-shadow: 
    0 24px 48px -12px rgba(0, 0, 0, 0.15),
    0 0 0 1px color-mix(in srgb, var(--color-primary) 15%, transparent);
}

.work-card:hover::before {
  opacity: 1;
}

.work-card:hover::after {
  transform: scaleX(1);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.card-number {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-textMuted);
  letter-spacing: 0.5px;
}

.work-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--color-textPrimary);
  margin-bottom: 10px;
  line-height: 1.4;
}

.work-inspiration {
  font-size: 13px;
  color: var(--color-textSecondary);
  line-height: 1.7;
  margin-bottom: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  flex: 1;
}

.work-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
}

.work-date {
  font-size: 12px;
  color: var(--color-textMuted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.work-date::before {
  content: '';
  width: 6px;
  height: 6px;
  background: var(--color-success);
  border-radius: 50%;
  opacity: 0.6;
}

.work-actions {
  display: flex;
  gap: 6px;
}

.work-actions :deep(.n-button) {
  font-size: 12px;
  font-weight: 500;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.delete-confirm-content {
  padding: 8px 0;
}

.delete-warning {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-textPrimary);
  margin-bottom: 10px;
}

.delete-hint {
  font-size: 13px;
  color: var(--color-textSecondary);
  line-height: 1.5;
}

@media (max-width: 768px) {
  .works-grid {
    grid-template-columns: 1fr;
    gap: 16px;
  }
  
  .topbar {
    padding: 14px 16px;
  }
  
  .page-title {
    font-size: 16px;
  }
  
  .content {
    padding: 16px;
  }
  
  .empty-illustration {
    width: 240px;
    height: 180px;
  }
}
</style>