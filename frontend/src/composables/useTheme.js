import { ref, computed, watch } from 'vue'

const themePresets = {
  modern: {
    id: 'modern',
    name: '现代简约风格',
    icon: '⚡',
    description: '极简专业，高效创作',
    colors: {
      primary: '#0ea5e9',
      primaryHover: '#38bdf8',
      primaryPressed: '#0284c7',
      secondary: '#64748b',
      accent: '#6366f1',
      background: '#f1f5f9',
      surface: '#ffffff',
      border: '#e2e8f0',
      textPrimary: '#0f172a',
      textSecondary: '#64748b',
      textMuted: '#94a3b8',
      success: '#22c55e',
      warning: '#f59e0b',
      error: '#ef4444',
    },
    typography: {
      fontFamily: "'Inter', -apple-system, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif",
      fontSizeBase: '14px',
      fontSizeSmall: '12px',
      fontSizeLarge: '16px',
      lineHeight: '1.5',
      fontWeightNormal: '400',
      fontWeightMedium: '500',
      fontWeightBold: '600',
    },
    components: {
      borderRadius: '8px',
      borderRadiusSm: '6px',
      borderRadiusLg: '12px',
      shadowSm: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
      shadowMd: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
      shadowLg: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
      spacingXs: '4px',
      spacingSm: '8px',
      spacingMd: '16px',
      spacingLg: '24px',
      spacingXl: '32px',
    },
    layout: {
      sidebarWidth: '72px',
      topbarHeight: '64px',
      contentPadding: '28px',
      cardGap: '20px',
    }
  },
  
  business: {
    id: 'business',
    name: '商务专业风格',
    icon: '💼',
    description: '稳重专业，适合正式场景',
    colors: {
      primary: '#1e40af',
      primaryHover: '#3b82f6',
      primaryPressed: '#1e3a8a',
      secondary: '#475569',
      accent: '#7c3aed',
      background: '#fafaf9',
      surface: '#ffffff',
      border: '#d6d3d1',
      textPrimary: '#1c1917',
      textSecondary: '#57534e',
      success: '#059669',
      warning: '#d97706',
      error: '#dc2626',
    },
    typography: {
      fontFamily: "'Segoe UI', 'Microsoft YaHei', sans-serif",
      fontSizeBase: '14px',
      fontSizeSmall: '12px',
      fontSizeLarge: '15px',
      lineHeight: '1.5',
      fontWeightNormal: '400',
      fontWeightMedium: '500',
      fontWeightBold: '600',
    },
    components: {
      borderRadius: '6px',
      borderRadiusSm: '4px',
      borderRadiusLg: '8px',
      shadowSm: '0 1px 3px rgba(0, 0, 0, 0.08)',
      shadowMd: '0 2px 4px rgba(0, 0, 0, 0.08)',
      shadowLg: '0 4px 12px rgba(0, 0, 0, 0.1)',
      spacingXs: '4px',
      spacingSm: '8px',
      spacingMd: '16px',
      spacingLg: '20px',
      spacingXl: '28px',
    },
    layout: {
      sidebarWidth: '68px',
      topbarHeight: '58px',
      contentPadding: '20px',
      cardGap: '14px',
    }
  },
  
  vibrant: {
    id: 'vibrant',
    name: '活力多彩风格',
    icon: '🌈',
    description: '活泼生动，充满创意与活力',
    colors: {
      primary: '#ec4899',
      primaryHover: '#f472b6',
      primaryPressed: '#db2777',
      secondary: '#8b5cf6',
      accent: '#f59e0b',
      background: '#fefce8',
      surface: '#ffffff',
      border: '#fde68a',
      textPrimary: '#1f2937',
      textSecondary: '#6b7280',
      success: '#22c55e',
      warning: '#eab308',
      error: '#f97316',
    },
    typography: {
      fontFamily: "'Nunito', 'Microsoft YaHei', 'PingFang SC', sans-serif",
      fontSizeBase: '14px',
      fontSizeSmall: '13px',
      fontSizeLarge: '17px',
      lineHeight: '1.65',
      fontWeightNormal: '500',
      fontWeightMedium: '600',
      fontWeightBold: '700',
    },
    components: {
      borderRadius: '12px',
      borderRadiusSm: '8px',
      borderRadiusLg: '16px',
      shadowSm: '0 2px 4px rgba(236, 72, 153, 0.1)',
      shadowMd: '0 4px 12px rgba(236, 72, 153, 0.15)',
      shadowLg: '0 8px 24px rgba(236, 72, 153, 0.2)',
      spacingXs: '6px',
      spacingSm: '10px',
      spacingMd: '18px',
      spacingLg: '26px',
      spacingXl: '36px',
    },
    layout: {
      sidebarWidth: '66px',
      topbarHeight: '62px',
      contentPadding: '26px',
      cardGap: '18px',
    }
  },
  
  dark: {
    id: 'dark',
    name: '深色主题风格',
    icon: '🌙',
    description: '护眼舒适，适合长时间使用',
    colors: {
      primary: '#60a5fa',
      primaryHover: '#93bbfd',
      primaryPressed: '#3b82f6',
      secondary: '#94a3b8',
      accent: '#a78bfa',
      background: '#0f172a',
      surface: '#1e293b',
      border: '#334155',
      textPrimary: '#f1f5f9',
      textSecondary: '#cbd5e1',
      textMuted: '#e2e8f0',
      success: '#4ade80',
      warning: '#fbbf24',
      error: '#f87171',
    },
    typography: {
      fontFamily: "'SF Pro Display', 'Microsoft YaHei', 'PingFang SC', sans-serif",
      fontSizeBase: '14px',
      fontSizeSmall: '12px',
      fontSizeLarge: '16px',
      lineHeight: '1.6',
      fontWeightNormal: '400',
      fontWeightMedium: '500',
      fontWeightBold: '600',
    },
    components: {
      borderRadius: '8px',
      borderRadiusSm: '6px',
      borderRadiusLg: '12px',
      shadowSm: '0 1px 2px rgba(0, 0, 0, 0.3)',
      shadowMd: '0 4px 6px rgba(0, 0, 0, 0.4)',
      shadowLg: '0 10px 15px rgba(0, 0, 0, 0.5)',
      spacingXs: '4px',
      spacingSm: '8px',
      spacingMd: '16px',
      spacingLg: '24px',
      spacingXl: '32px',
    },
    layout: {
      sidebarWidth: '64px',
      topbarHeight: '60px',
      contentPadding: '24px',
      cardGap: '16px',
    }
  }
}

const STORAGE_KEY = 'kuimulang-theme-preference'
const currentThemeId = ref(localStorage.getItem(STORAGE_KEY) || 'modern')
const currentTheme = computed(() => themePresets[currentThemeId.value] || themePresets.modern)

function setTheme(themeId) {
  if (themePresets[themeId]) {
    currentThemeId.value = themeId
    localStorage.setItem(STORAGE_KEY, themeId)
    applyThemeToDocument(themePresets[themeId])
  }
}

function applyThemeToDocument(theme) {
  const root = document.documentElement
  
  // 设置 data-theme 属性用于深色模式样式选择
  root.setAttribute('data-theme', theme.id)
  
  Object.entries(theme.colors).forEach(([key, value]) => {
    root.style.setProperty(`--color-${key}`, value)
  })
  
  root.style.setProperty('--font-family', theme.typography.fontFamily)
  root.style.setProperty('--font-size-base', theme.typography.fontSizeBase)
  root.style.setProperty('--font-size-small', theme.typography.fontSizeSmall)
  root.style.setProperty('--font-size-large', theme.typography.fontSizeLarge)
  root.style.setProperty('--line-height', theme.typography.lineHeight)
  root.style.setProperty('--font-weight-normal', theme.typography.fontWeightNormal)
  root.style.setProperty('--font-weight-medium', theme.typography.fontWeightMedium)
  root.style.setProperty('--font-weight-bold', theme.typography.fontWeightBold)
  
  root.style.setProperty('--radius-sm', theme.components.borderRadiusSm)
  root.style.setProperty('--radius-md', theme.components.borderRadius)
  root.style.setProperty('--radius-lg', theme.components.borderRadiusLg)
  root.style.setProperty('--shadow-sm', theme.components.shadowSm)
  root.style.setProperty('--shadow-md', theme.components.shadowMd)
  root.style.setProperty('--shadow-lg', theme.components.shadowLg)
  root.style.setProperty('--spacing-xs', theme.components.spacingXs)
  root.style.setProperty('--spacing-sm', theme.components.spacingSm)
  root.style.setProperty('--spacing-md', theme.components.spacingMd)
  root.style.setProperty('--spacing-lg', theme.components.spacingLg)
  root.style.setProperty('--spacing-xl', theme.components.spacingXl)
  
  root.style.setProperty('--sidebar-width', theme.layout.sidebarWidth)
  root.style.setProperty('--topbar-height', theme.layout.topbarHeight)
  root.style.setProperty('--content-padding', theme.layout.contentPadding)
  root.style.setProperty('--card-gap', theme.layout.cardGap)
}

watch(currentThemeId, (newThemeId) => {
  if (themePresets[newThemeId]) {
    applyThemeToDocument(themePresets[newThemeId])
  }
}, { immediate: true })

export function useTheme() {
  return {
    currentTheme,
    currentThemeId,
    themePresets,
    setTheme,
    applyThemeToDocument
  }
}