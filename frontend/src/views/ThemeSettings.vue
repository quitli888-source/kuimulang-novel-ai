<template>
  <div class="layout">
    <LayoutSidebar />
    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <n-button text @click="$router.push('/works')">← 作品列表</n-button>
          <span class="page-title">界面设置</span>
        </div>
      </div>

      <div class="content">
        <div class="theme-header">
          <h2>选择您喜欢的界面风格</h2>
          <p class="subtitle">实时预览效果，个性化您的创作体验</p>
        </div>

        <div class="theme-grid">
          <div
            v-for="(theme, id) in themePresets"
            :key="id"
            class="theme-card"
            :class="{ active: currentThemeId === id }"
            @click="selectTheme(id)"
          >
            <div class="theme-info">
              <span class="theme-icon">{{ theme.icon }}</span>
              <div class="theme-details">
                <h3>{{ theme.name }}</h3>
                <p>{{ theme.description }}</p>
              </div>
              <div v-if="currentThemeId === id" class="active-badge">✓ 当前使用</div>
            </div>
            
            <button 
              v-if="currentThemeId !== id" 
              class="apply-btn"
              @click.stop="selectTheme(id)"
            >
              应用此风格
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useTheme } from '@/composables/useTheme'
import LayoutSidebar from '@/components/Layout/Sidebar.vue'

const { currentThemeId, themePresets, setTheme } = useTheme()

function selectTheme(themeId) {
  setTheme(themeId)
}
</script>

<style scoped>
.layout { display: flex; height: 100vh; background: var(--color-background); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 14px 24px; border-bottom: 1px solid var(--color-border); background: var(--color-surface); }
.topbar-left { display: flex; align-items: center; gap: 16px; }
.page-title { font-size: 18px; font-weight: 600; color: var(--color-text-primary); }
.content { flex: 1; overflow-y: auto; padding: 24px; }

.theme-header { margin-bottom: 32px; }
.theme-header h2 { font-size: 24px; font-weight: 700; color: var(--color-text-primary); margin-bottom: 8px; }
.subtitle { font-size: 14px; color: var(--color-text-secondary); }

.theme-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }

.theme-card {
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 20px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.theme-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--color-primary);
}

.theme-card.active {
  border-color: var(--color-primary);
  box-shadow: 0 8px 24px rgba(37, 99, 235, 0.15);
}

.theme-info {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.theme-icon { font-size: 28px; }

.theme-details { flex: 1; }
.theme-details h3 { font-size: 16px; font-weight: 600; color: var(--color-text-primary); margin-bottom: 4px; }
.theme-details p { font-size: 13px; color: var(--color-text-secondary); }

.active-badge {
  background: var(--color-primary);
  color: #fff;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

.apply-btn {
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--color-primary);
  color: #fff;
}

.apply-btn:hover {
  background: var(--color-primaryHover);
}
</style>