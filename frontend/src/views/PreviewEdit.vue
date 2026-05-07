<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="work-title">{{ workData.title }}</span>
        </div>
        <div class="topbar-actions">
          <n-button @click="showReport = true">查看报告</n-button>
        </div>
      </div>

      <div class="content">
        <div class="preview-layout">
          <!-- Part选择 -->
          <div class="part-selector">
            <div class="selector-title">章节</div>
            <div
              v-for="(outline, idx) in workData.part_outline"
              :key="idx"
              class="part-tab"
              :class="{ active: selectedPart === idx + 1 }"
              @click="selectPart(idx + 1)"
            >
              Part {{ idx + 1 }}: {{ outline.title || outline.theme || '' }}
            </div>
          </div>

          <!-- 文本编辑器 -->
          <div class="editor-area">
            <div class="editor-toolbar">
              <n-button size="small" type="primary" @click="showRewriteMenu = true">AI改写</n-button>
              <n-button size="small" type="warning" @click="reOptimize">重新优化</n-button>
            </div>
            <div class="editor-content" contenteditable="true" ref="editorRef" @mouseup="handleSelection">
              {{ currentText || '暂无内容' }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- AI改写浮动菜单 -->
    <div v-if="showRewriteMenu && selectedText" class="rewrite-menu">
      <div class="rewrite-title">选中文本改写</div>
      <div class="rewrite-btns">
        <n-button size="small" @click="doRewrite('polish')">润色</n-button>
        <n-button size="small" @click="doRewrite('expand')">扩写</n-button>
        <n-button size="small" @click="doRewrite('summarize')">缩写</n-button>
      </div>
      <n-button size="small" text @click="showRewriteMenu = false">取消</n-button>
    </div>

    <!-- 报告弹窗 -->
    <n-modal v-model:show="showReport" preset="card" title="创作报告" style="width:700px">
      <report-view :work-id="workId" />
    </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const route = useRoute()
const router = useRouter()
const workId = route.params.workId

const workData = ref({ title: '', parts: {}, part_outline: [] })
const selectedPart = ref(1)
const showRewriteMenu = ref(false)
const selectedText = ref('')
const showReport = ref(false)
const editorRef = ref(null)

const currentText = computed(() => workData.value.parts?.[String(selectedPart.value)] || '')

onMounted(async () => {
  workData.value = (await api.get(`/works/${workId}`)).data
  if (!workData.value.part_outline?.length) {
    const count = workData.value.part_outline?.length || Object.keys(workData.value.parts || {}).length || 1
    workData.value.part_outline = Array.from({ length: count }, (_, i) => ({ title: `章节${i + 1}` }))
  }
})

function selectPart(n) {
  selectedPart.value = n
  showRewriteMenu.value = false
}

function handleSelection() {
  const sel = window.getSelection()
  if (sel && sel.toString().trim()) {
    selectedText.value = sel.toString()
    showRewriteMenu.value = true
  }
}

async function doRewrite(mode) {
  showRewriteMenu.value = false
  if (!selectedText.value) return

  try {
    const res = await api.post('/rewrite/', {
      text: selectedText.value,
      mode,
      context: currentText.value,
    })

    const rewritten = res.data.rewritten
    if (rewritten && editorRef.value) {
      // 使用DOM操作替换选中文本
      const sel = window.getSelection()
      if (sel && sel.rangeCount > 0) {
        const range = sel.getRangeAt(0)
        range.deleteContents()
        range.insertNode(document.createTextNode(rewritten))
        // 更新workData中的文本
        const originalText = currentText.value
        const newText = originalText.replace(selectedText.value, rewritten)
        workData.value.parts[String(selectedPart.value)] = newText
      }
    }
  } catch (err) {
    console.error('改写失败:', err)
  }
}

async function reOptimize() {
  try {
    const res = await api.post(`/writing/rewrite-part/${workId}/${selectedPart.value}`)
    // 等待重写完成，刷新内容
    const updated = (await api.get(`/works/${workId}`)).data
    workData.value = updated
  } catch (err) {
    console.error('重优化失败:', err)
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
.work-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary, #333); }
.topbar-actions { display: flex; gap: 8px; }
.content { 
  flex: 1; 
  overflow: hidden; 
  padding: 16px 24px;
  background: var(--color-background, #f8fafc);
}
.preview-layout { 
  display: flex; 
  gap: 16px; 
  height: 100%; 
}
.part-selector { 
  width: 200px; 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 16px; 
  border: 1px solid var(--color-border, #e2e8f0);
  overflow-y: auto;
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  transition: all 0.25s ease;
}
.part-selector:hover {
  box-shadow: var(--shadow-md, 0 4px 6px rgba(0,0,0,0.1));
}
.selector-title { 
  font-size: 13px; 
  font-weight: 600; 
  color: var(--color-text-secondary, #888); 
  margin-bottom: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.part-tab { 
  padding: 10px 12px; 
  border-radius: var(--radius-sm, 6px); 
  font-size: 13px; 
  cursor: pointer; 
  color: var(--color-text-secondary, #555); 
  margin-bottom: 6px; 
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); 
  overflow: hidden; 
  text-overflow: ellipsis; 
  white-space: nowrap;
  border: 1px solid transparent;
}
.part-tab:hover { 
  background: var(--color-background, #f5f5f5);
  transform: translateX(4px);
}
.part-tab.active { 
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary, #2563eb) 10%, white), transparent);
  color: var(--color-primary, #2563eb); 
  font-weight: 600;
  border-color: var(--color-primary, #2563eb);
}
.editor-area { 
  flex: 1; 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 12px); 
  padding: 20px; 
  border: 1px solid var(--color-border, #e2e8f0); 
  display: flex; 
  flex-direction: column; 
  overflow: hidden;
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  transition: all 0.25s ease;
}
.editor-area:focus-within {
  box-shadow: var(--shadow-md, 0 4px 6px rgba(37,99,235,0.1));
  border-color: var(--color-primary, #2563eb);
}
.editor-toolbar { 
  display: flex; 
  gap: 8px; 
  margin-bottom: 16px; 
  padding-bottom: 16px; 
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}
.editor-content { 
  flex: 1; 
  overflow-y: auto; 
  font-size: 15px; 
  line-height: 1.8; 
  color: var(--color-text-primary, #333); 
  white-space: pre-wrap; 
  outline: none;
  padding: 12px;
  border-radius: var(--radius-md, 8px);
  transition: background 0.2s ease;
}
.editor-content:hover {
  background: var(--color-background, #fafafa);
}
.rewrite-menu { 
  position: fixed; 
  background: var(--color-surface, #fff); 
  border-radius: var(--radius-lg, 10px); 
  padding: 16px; 
  box-shadow: var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.15)); 
  z-index: 1000; 
  min-width: 220px;
  border: 1px solid var(--color-border, #e2e8f0);
  animation: slideUp 0.2s ease-out;
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.rewrite-title { 
  font-size: 13px; 
  color: var(--color-text-secondary, #888); 
  margin-bottom: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.rewrite-btns { 
  display: flex; 
  gap: 8px; 
  margin-bottom: 12px;
  flex-wrap: wrap;
}

@media (max-width: 1024px) {
  .preview-layout {
    flex-direction: column;
  }
  .part-selector {
    width: 100%;
    max-height: 150px;
  }
}

@media (max-width: 768px) {
  .topbar {
    padding: 12px 16px;
    flex-wrap: wrap;
    gap: 12px;
  }
  .content {
    padding: 12px 16px;
  }
  .editor-area {
    padding: 16px;
  }
}
</style>
