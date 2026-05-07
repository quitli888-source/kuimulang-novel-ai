<template>
  <div class="sidebar">
    <div class="sidebar-logo">
      <img src="@/assets/logo.svg" alt="奎木狼" class="logo-img" />
    </div>
    <nav class="sidebar-nav">
      <router-link to="/works" class="nav-item" :class="{ active: current === 'works' }" title="作品列表">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
        </svg>
        <span class="nav-label">作品</span>
      </router-link>
      <router-link to="/config" class="nav-item" :class="{ active: current === 'config' }" title="创作配置">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
          <path d="M2 17l10 5 10-5"></path>
          <path d="M2 12l10 5 10-5"></path>
        </svg>
        <span class="nav-label">配置</span>
      </router-link>
      <router-link to="/rewrite" class="nav-item" :class="{ active: current === 'rewrite' }" title="AI改写">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
        </svg>
        <span class="nav-label">改写</span>
      </router-link>
      <router-link to="/theme" class="nav-item" :class="{ active: current === 'theme' }" title="界面设置">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <circle cx="12" cy="12" r="4"></circle>
          <line x1="21.17" y1="8" x2="12" y2="8"></line>
          <line x1="3.95" y1="6.06" x2="8.54" y2="14"></line>
          <line x1="10.88" y1="21.94" x2="15.46" y2="14"></line>
        </svg>
        <span class="nav-label">界面</span>
      </router-link>
    </nav>
    <div class="sidebar-footer">
      <span class="version">V6.1.01</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

// 根据当前路由路径计算active状态
const current = computed(() => {
  const path = route.path
  if (path.startsWith('/config')) return 'config'
  if (path.startsWith('/writing')) return 'writing'
  if (path.startsWith('/preview')) return 'preview'
  if (path.startsWith('/report')) return 'report'
  if (path.startsWith('/rewrite')) return 'rewrite'
  if (path.startsWith('/theme')) return 'theme'
  if (path.startsWith('/works')) return 'works'
  return 'works' // 默认
})
</script>

<style scoped>
.sidebar {
  width: 72px;
  height: 100%;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 0;
  position: relative;
  z-index: 10;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}

.sidebar::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 1px;
  height: 100%;
  background: linear-gradient(
    180deg,
    transparent 0%,
    var(--color-primary) 20%,
    var(--color-primary) 80%,
    transparent 100%
  );
  opacity: 0.2;
}

.sidebar-logo {
  margin-bottom: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.sidebar-logo::before {
  content: '';
  position: absolute;
  width: 60px;
  height: 60px;
  background: radial-gradient(circle, var(--color-primary) 0%, transparent 70%);
  opacity: 0.15;
  border-radius: 50%;
  filter: blur(10px);
  animation: logo-glow 3s ease-in-out infinite;
}

@keyframes logo-glow {
  0%, 100% { opacity: 0.1; transform: scale(0.9); }
  50% { opacity: 0.2; transform: scale(1.1); }
}

.logo-img {
  width: 48px;
  height: 48px;
  object-fit: contain;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  z-index: 1;
}

/* 深色主题下SVG Logo增强可见性 */
[data-theme="dark"] .logo-img {
  filter: drop-shadow(0 0 10px rgba(0, 212, 255, 0.6));
}

[data-theme="dark"] .logo-img:hover {
  transform: scale(1.1);
  filter: drop-shadow(0 0 16px rgba(0, 212, 255, 0.8));
}

/* 浅色主题下SVG Logo保持清晰 */
[data-theme="light"] .logo-img {
  filter: drop-shadow(0 2px 6px rgba(0, 150, 200, 0.3));
}

[data-theme="light"] .logo-img:hover {
  transform: scale(1.1);
  filter: drop-shadow(0 4px 10px rgba(0, 150, 200, 0.4));
}

.sidebar-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  padding: 0 10px;
}

.nav-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 14px 8px;
  border-radius: 14px;
  cursor: pointer;
  text-decoration: none;
  color: var(--color-textSecondary);
  transition: all 0.2s ease;
  position: relative;
}

.nav-item:hover {
  color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 8%, var(--color-background));
}

.nav-item.active {
  color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 10%, var(--color-background));
  font-weight: 600;
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: -10px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 32px;
  background: linear-gradient(180deg, var(--color-primary), var(--color-accent));
  border-radius: 0 3px 3px 0;
  box-shadow: 0 0 12px var(--color-primary);
}

.nav-icon {
  width: 24px;
  height: 24px;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.2s ease;
}

.nav-item:hover .nav-icon {
  transform: scale(1.1);
}

.nav-item.active .nav-icon {
  filter: drop-shadow(0 0 6px var(--color-primary));
}

.nav-label {
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  letter-spacing: 0.3px;
}

.sidebar-footer {
  padding: 20px 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.sidebar-footer::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 40px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-border), transparent);
}

.version {
  font-size: 9px;
  font-weight: 500;
  color: var(--color-textSecondary);
  opacity: 0.5;
  letter-spacing: 0.5px;
}

@media (max-width: 768px) {
  .sidebar {
    width: 60px;
    padding: 16px 0;
  }
  
  .logo-img {
    width: 40px;
    height: 40px;
  }
  
  .nav-icon {
    width: 20px;
    height: 20px;
  }
  
  .nav-label {
    font-size: 10px;
  }
  
  .nav-item {
    padding: 12px 6px;
  }
}
</style>