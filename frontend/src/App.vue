<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <div class="app-background">
        <!-- 背景特效层 -->
        <div class="particle-bg">
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
          <div class="particle"></div>
        </div>
        <div class="grid-bg"></div>
        <div class="glow-orb glow-orb-1"></div>
        <div class="glow-orb glow-orb-2"></div>
        <div class="glow-orb glow-orb-3"></div>
        
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
        <GuideOverlay />
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import GuideOverlay from '@/components/GuideOverlay.vue'
import { useTheme } from '@/composables/useTheme'

const { currentTheme } = useTheme()

const themeOverrides = computed(() => ({
  common: {
    primaryColor: currentTheme.value.colors.primary,
    primaryColorHover: currentTheme.value.colors.primaryHover,
    primaryColorPressed: currentTheme.value.colors.primaryPressed,
    primaryColorSuppl: currentTheme.value.colors.primary,
    infoColor: currentTheme.value.colors.primary,
    successColor: currentTheme.value.colors.success,
    warningColor: currentTheme.value.colors.warning,
    errorColor: currentTheme.value.colors.error,
    textColorBase: currentTheme.value.colors.textPrimary,
    textColor1: currentTheme.value.colors.textPrimary,
    textColor2: currentTheme.value.colors.textSecondary,
    textColor3: currentTheme.value.colors.textMuted,
    textColorDisabled: currentTheme.value.colors.textMuted,
    borderColor: currentTheme.value.colors.border,
    borderColorHover: currentTheme.value.colors.border,
    borderColorPressed: currentTheme.value.colors.border,
    borderColorDisabled: currentTheme.value.colors.border,
    dividerColor: currentTheme.value.colors.border,
    backgroundColor: currentTheme.value.colors.background,
    backgroundColorHover: currentTheme.value.colors.surface,
    backgroundColorPressed: currentTheme.value.colors.surface,
    fillColor: currentTheme.value.colors.surface,
    fillColorHover: currentTheme.value.colors.surface,
    fillColorPressed: currentTheme.value.colors.surface,
    fillColorDisabled: currentTheme.value.colors.surface,
    borderRadius: currentTheme.value.components.borderRadius,
    fontFamily: currentTheme.value.typography.fontFamily,
    fontSizeSmall: currentTheme.value.typography.fontSizeSmall,
    fontSizeMedium: currentTheme.value.typography.fontSizeBase,
    fontSizeLarge: currentTheme.value.typography.fontSizeLarge,
    lineHeight: currentTheme.value.typography.lineHeight,
  },
  Button: {
    borderRadiusMedium: currentTheme.value.components.borderRadius,
    borderRadiusSmall: currentTheme.value.components.borderRadiusSm,
  },
  Card: {
    borderRadius: currentTheme.value.components.borderRadiusLg,
    color: currentTheme.value.colors.surface,
    textColor: currentTheme.value.colors.textPrimary,
    titleTextColor: currentTheme.value.colors.textPrimary,
    headerTextColor: currentTheme.value.colors.textPrimary,
  },
  Modal: {
    color: currentTheme.value.colors.surface,
    textColor: currentTheme.value.colors.textPrimary,
  },
  Input: {
    borderRadius: currentTheme.value.components.borderRadius,
    inputTextColor: currentTheme.value.colors.textPrimary,
    color: currentTheme.value.colors.surface,
    colorFocus: currentTheme.value.colors.surface,
    caretColor: currentTheme.value.colors.primary,
    placeholderColor: currentTheme.value.colors.textMuted,
    textColor: currentTheme.value.colors.textPrimary,
    border: `1px solid ${currentTheme.value.colors.border}`,
    borderHover: `1px solid ${currentTheme.value.colors.primary}`,
    borderFocus: `1px solid ${currentTheme.value.colors.primary}`,
    boxShadowFocus: `0 0 0 2px ${currentTheme.value.colors.primary}30`,
  },
  InputNumber: {
    borderRadius: currentTheme.value.components.borderRadius,
    textColor: currentTheme.value.colors.textPrimary,
    color: currentTheme.value.colors.surface,
  },
  Select: {
    peers: {
      InternalSelection: {
        textColor: currentTheme.value.colors.textPrimary,
        color: currentTheme.value.colors.surface,
        border: `1px solid ${currentTheme.value.colors.border}`,
        borderHover: `1px solid ${currentTheme.value.colors.primary}`,
        borderFocus: `1px solid ${currentTheme.value.colors.primary}`,
        boxShadowFocus: `0 0 0 2px ${currentTheme.value.colors.primary}30`,
      }
    }
  },
  Tag: {
    borderRadius: currentTheme.value.components.borderRadiusSm,
  },
  Modal: {
    color: currentTheme.value.colors.surface,
    textColor: currentTheme.value.colors.textPrimary,
  },
  Dialog: {
    color: currentTheme.value.colors.surface,
    textColor: currentTheme.value.colors.textPrimary,
  },
  Card: {
    color: currentTheme.value.colors.surface,
    textColor: currentTheme.value.colors.textPrimary,
  },
}))
</script>

<style>
#app {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}

.app-background {
  width: 100%;
  height: 100%;
  position: relative;
  background: var(--color-background);
}

.app-background::before {
  content: '';
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(
    135deg,
    var(--color-background) 0%,
    var(--bg-gradient-mid, color-mix(in srgb, var(--color-primary) 8%, var(--color-background))) 50%,
    var(--color-background) 100%
  );
  pointer-events: none;
  z-index: 0;
}

/* ========== 背景粒子特效 ========== */
.particle-bg {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.particle {
  position: absolute;
  width: 6px;
  height: 6px;
  background: var(--color-primary);
  border-radius: 50%;
  opacity: 0.15;
  animation: float 15s infinite ease-in-out;
}

.particle:nth-child(1) { left: 10%; top: 20%; animation-delay: 0s; animation-duration: 18s; }
.particle:nth-child(2) { left: 20%; top: 80%; animation-delay: 2s; animation-duration: 20s; }
.particle:nth-child(3) { left: 30%; top: 40%; animation-delay: 4s; animation-duration: 16s; }
.particle:nth-child(4) { left: 40%; top: 60%; animation-delay: 1s; animation-duration: 22s; }
.particle:nth-child(5) { left: 50%; top: 30%; animation-delay: 3s; animation-duration: 19s; }
.particle:nth-child(6) { left: 60%; top: 70%; animation-delay: 5s; animation-duration: 17s; }
.particle:nth-child(7) { left: 70%; top: 10%; animation-delay: 2.5s; animation-duration: 21s; }
.particle:nth-child(8) { left: 80%; top: 50%; animation-delay: 0.5s; animation-duration: 18s; }
.particle:nth-child(9) { left: 90%; top: 85%; animation-delay: 3.5s; animation-duration: 20s; }
.particle:nth-child(10) { left: 15%; top: 55%; animation-delay: 1.5s; animation-duration: 23s; }

@keyframes float {
  0%, 100% { transform: translateY(0) translateX(0) scale(1); opacity: 0.15; }
  25% { transform: translateY(-30px) translateX(15px) scale(1.1); opacity: 0.25; }
  50% { transform: translateY(-15px) translateX(-10px) scale(0.9); opacity: 0.18; }
  75% { transform: translateY(-40px) translateX(20px) scale(1.05); opacity: 0.22; }
}

/* ========== 网格背景 ========== */
.grid-bg {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(14, 165, 233, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(14, 165, 233, 0.03) 1px, transparent 1px);
  background-size: 50px 50px;
}

/* ========== 光晕特效 ========== */
.glow-orb {
  position: fixed;
  border-radius: 50%;
  pointer-events: none;
  z-index: 0;
  filter: blur(80px);
  opacity: 0.4;
}

.glow-orb-1 {
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, var(--color-primary) 0%, transparent 70%);
  top: -100px;
  right: -100px;
  animation: pulse-glow 8s ease-in-out infinite;
}

.glow-orb-2 {
  width: 300px;
  height: 300px;
  background: radial-gradient(circle, var(--color-accent) 0%, transparent 70%);
  bottom: -50px;
  left: -50px;
  animation: pulse-glow 10s ease-in-out infinite reverse;
}

.glow-orb-3 {
  width: 250px;
  height: 250px;
  background: radial-gradient(circle, var(--color-secondary) 0%, transparent 70%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation: pulse-glow 12s ease-in-out infinite;
  opacity: 0.2;
}

@keyframes pulse-glow {
  0%, 100% { opacity: 0.3; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.1); }
}

/* 路由过渡动画 */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

* {
  transition-property: background-color, border-color, color, box-shadow;
  transition-duration: 0.25s;
  transition-timing-function: ease;
}

/* 深色主题增强 - 确保所有输入框文字清晰可见 */
[data-theme="dark"] .n-input .n-input__input-el,
[data-theme="dark"] .n-input .n-input__textarea-el,
[data-theme="dark"] .n-input-wrapper .n-input__input-el,
[data-theme="dark"] .n-input-wrapper .n-input__textarea-el {
  color: #f1f5f9 !important;
  -webkit-text-fill-color: #f1f5f9 !important;
}

[data-theme="dark"] .n-input::placeholder {
  color: #cbd5e1 !important;
  -webkit-text-fill-color: #cbd5e1 !important;
}

/* 确保Collapse内的输入框在深色模式下也清晰 */
[data-theme="dark"] .n-collapse-item .n-input .n-input__input-el,
[data-theme="dark"] .n-collapse-item .n-input .n-input__textarea-el {
  color: #f1f5f9 !important;
  -webkit-text-fill-color: #f1f5f9 !important;
}

[data-theme="dark"] .n-collapse-item .n-input__placeholder {
  color: #cbd5e1 !important;
}

/* Form相关输入框文字 */
[data-theme="dark"] .n-form-item .n-input .n-input__input-el,
[data-theme="dark"] .n-form-item .n-input .n-input__textarea-el {
  color: #f1f5f9 !important;
}
</style>