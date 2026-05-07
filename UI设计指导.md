# 奎木狼AI小说创作系统 V6 - UI设计指导

## 一、深色模式完善清单

### 1.1 当前已覆盖的样式

App.vue 中已有部分深色模式处理：
- 输入框文字颜色 (`#f1f5f9`)
- 占位符颜色 (`#94a3b8`)
- Collapse组件内输入框

global.css 中已覆盖：
- `.glow-effect` 文字发光
- `.card-glow` 卡片发光
- `.glow-orb` 光晕不透明度 (0.5)
- `.grid-bg` 网格背景透明度
- `.particle` 粒子颜色和透明度
- `.glass-nav` 玻璃态导航栏

### 1.2 需要补充覆盖的Naive UI组件CSS变量

#### 核心颜色变量（必须覆盖）
```css
/* Modal弹窗 */
[data-theme="dark"] .n-modal { background-color: #1e293b; }
[data-theme="dark"] .n-modal-mask { background-color: rgba(0, 0, 0, 0.7); }
[data-theme="dark"] .n-card { background-color: #1e293b; border-color: #334155; }

/* Message消息提示 */
[data-theme="dark"] .n-message { background-color: #1e293b; }
[data-theme="dark"] .n-message__content { color: #f1f5f9; }

/* Dropdown下拉菜单 */
[data-theme="dark"] .n-dropdown { background-color: #1e293b; border-color: #334155; }
[data-theme="dark"] .n-dropdown-menu { background-color: #1e293b; }
[data-theme="dark"] .n-dropdown-item { color: #f1f5f9; }
[data-theme="dark"] .n-dropdown-item:hover { background-color: #334155; }

/* Tooltip提示 */
[data-theme="dark"] .n-tooltip { background-color: #334155; }
[data-theme="dark"] .n-tooltip__content { color: #f1f5f9; }

/* Select选择器 */
[data-theme="dark"] .n-base-selection { background-color: #1e293b; border-color: #334155; }
[data-theme="dark"] .n-base-selection:hover { border-color: #60a5fa; }
[data-theme="dark"] .n-base-selection .n-base-selection-label { color: #f1f5f9; }

/* Checkbox & Radio */
[data-theme="dark"] .n-checkbox-box { background-color: #1e293b; border-color: #334155; }
[data-theme="dark"] .n-radio-box { background-color: #1e293b; border-color: #334155; }

/* Slider滑动条 */
[data-theme="dark"] .n-slider { background-color: #334155; }
[data-theme="dark"] .n-slider-handle { background-color: #60a5fa; }

/* Switch开关 */
[data-theme="dark"] .n-switch { background-color: #334155; }
[data-theme="dark"] .n-switch--active { background-color: #60a5fa; }

/* Tabs标签页 */
[data-theme="dark"] .n-tabs { background-color: transparent; }
[data-theme="dark"] .n-tab { color: #94a3b8; }
[data-theme="dark"] .n-tab--active { color: #60a5fa; }
[data-theme="dark"] .n-tabs-bar { background-color: #60a5fa; }

/* Steps步骤条 */
[data-theme="dark"] .n-step { color: #94a3b8; }
[data-theme="dark"] .n-step--finish { color: #f1f5f9; }
[data-theme="dark"] .n-step-indicator { background-color: #334155; border-color: #334155; }
[data-theme="dark"] .n-step--finish .n-step-indicator { background-color: #60a5fa; border-color: #60a5fa; }

/* Collapse折叠面板 */
[data-theme="dark"] .n-collapse { background-color: transparent; }
[data-theme="dark"] .n-collapse-item { background-color: #1e293b; border-color: #334155; }
[data-theme="dark"] .n-collapse-item__header { color: #f1f5f9; }

/* Table表格 */
[data-theme="dark"] .n-table { background-color: #1e293b; }
[data-theme="dark"] .n-td { background-color: #1e293b; color: #f1f5f9; border-color: #334155; }
[data-theme="dark"] .n-th { background-color: #0f172a; color: #f1f5f9; border-color: #334155; }

/* Pagination分页 */
[data-theme="dark"] .n-pagination { background-color: transparent; }
[data-theme="dark"] .n-pagination-item { background-color: #1e293b; border-color: #334155; color: #f1f5f9; }
[data-theme="dark"] .n-pagination-item--active { background-color: #60a5fa; border-color: #60a5fa; }

/* Spin加载 */
[data-theme="dark"] .n-spin { color: #60a5fa; }

/* Alert警告 */
[data-theme="dark"] .n-alert { background-color: #1e293b; border-color: #334155; }
[data-theme="dark"] .n-alert__content { color: #f1f5f9; }

/* Tag标签 */
[data-theme="dark"] .n-tag { background-color: #334155; color: #f1f5f9; border-color: #334155; }
[data-theme="dark"] .n-tag--success-type { background-color: rgba(74, 222, 128, 0.2); color: #4ade80; }
[data-theme="dark"] .n-tag--warning-type { background-color: rgba(251, 191, 36, 0.2); color: #fbbf24; }
[data-theme="dark"] .n-tag--error-type { background-color: rgba(248, 113, 113, 0.2); color: #f87171; }
[data-theme="dark"] .n-tag--info-type { background-color: rgba(96, 165, 250, 0.2); color: #60a5fa; }

/* Badge徽章 */
[data-theme="dark"] .n-badge { color: #f1f5f9; }

/* Progress进度条 */
[data-theme="dark"] .n-progress { color: #60a5fa; }
[data-theme="dark"] .n-progress-line .n-progress-line-rail { background-color: #334155; }

/* Timeline时间线 */
[data-theme="dark"] .n-timeline { color: #f1f5f9; }
[data-theme="dark"] .n-timeline-item-content { color: #f1f5f9; }
```

#### 数据属性选择器建议
为确保深色模式覆盖全面，建议在 `useTheme.js` 的 `applyThemeToDocument` 函数中添加：
```javascript
// 文档级别深色模式标识（已有）
root.setAttribute('data-theme', theme.id)

// 为body添加主题类名（建议添加）
root.classList.remove('theme-light', 'theme-dark')
root.classList.add(theme.id === 'dark' ? 'theme-dark' : 'theme-light')
```

### 1.3 自定义组件深色模式检查清单

| 组件文件 | 检查项 | 深色模式建议 |
|---------|--------|-------------|
| ProgressBar.vue | 进度条背景 | 使用 `var(--color-background)` |
| WritingProgress.vue | 阶段进度dot | 深色模式下考虑增加边框 |
| WritingProgress.vue | 日志面板 | 确保日志文字在深色背景下可读 |
| Sidebar.vue | Logo发光效果 | 已有 `#60a5fa` 深色适配 |
| WorksList.vue | 空状态插图 | 考虑深色版本或透明背景 |

---

## 二、错误状态设计规范

### 2.1 错误提示设计原则

1. **层次分明**：错误信息分为「发生了什么」和「如何解决」两个层次
2. **视觉区分**：使用错误色 `var(--color-error)` 作为主标识
3. **可操作性**：提供明确的操作按钮或建议

### 2.2 WritingProgress错误状态UI设计

#### 当前问题
错误弹窗（n-modal）已存在，但缺少：
- 错误图标系统
- 错误分类（网络错误/业务错误/系统错误）
- 恢复操作的可视化引导

#### 建议优化布局

```
┌─────────────────────────────────────────────┐
│  ⚠️ 网络连接中断                              │
│                                              │
│  创作过程中与服务器的连接意外断开。            │
│  您的创作进度已自动保存。                     │
│                                              │
│  ┌─────────────────────────────────────┐    │
│  │ 💡 建议：                              │    │
│  │ 1. 检查网络连接后，点击「重试连接」    │    │
│  │ 2. 如需紧急保存，可点击「导出当前进度」 │    │
│  └─────────────────────────────────────┘    │
│                                              │
│           [导出进度]      [重试连接]          │
└─────────────────────────────────────────────┘
```

#### 错误文案模板库

| 错误类型 | 标题模板 | 内容模板 | 建议模板 |
|---------|---------|---------|---------|
| 网络断开 | 网络连接中断 | 创作过程中与服务器的连接意外断开。您的创作进度已自动保存。 | 1. 检查网络连接后，点击「重试连接」<br>2. 如需紧急保存，可点击「导出当前进度」 |
| API调用失败 | AI响应超时 | AI创作服务暂时无法响应。系统已缓存您的操作。 | 1. 点击「重试」继续创作<br>2. 或稍后手动恢复创作 |
| 内存不足 | 内存使用警告 | 检测到系统内存不足，可能影响创作流畅度。 | 1. 建议关闭其他程序释放内存<br>2. 点击「继续创作」尝试保存当前进度 |
| 文件保存失败 | 保存遇到问题 | 作品保存失败，请检查存储空间。 | 1. 确认磁盘空间充足<br>2. 点击「重试保存」<br>3. 或联系技术支持 |
| 认证过期 | 会话已过期 | 登录状态已过期，请重新登录。 | 1. 点击「重新登录」<br>2. 您的作品数据已安全保存 |
| 未知错误 | 创作异常 | 发生了意外的错误。错误信息已记录。 | 1. 点击「重新开始」<br>2. 或联系技术支持获取帮助 |

#### 错误弹窗样式规范

```css
.error-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.error-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.error-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.1), rgba(239, 68, 68, 0.2));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}

.error-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-error);
}

.error-message {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-textPrimary);
  padding: 16px;
  background: var(--color-background);
  border-radius: var(--radius-md);
  border-left: 4px solid var(--color-error);
}

.error-suggestion {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.08), rgba(245, 158, 11, 0.04));
  padding: 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.6;
  border: 1px solid rgba(245, 158, 11, 0.2);
}

.error-suggestion-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--color-warning);
  margin-bottom: 12px;
}

.error-suggestion-text {
  color: var(--color-textSecondary);
}

.error-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 8px;
}
```

---

## 三、动效使用规范

### 3.1 动效分类与使用场景

| 动效类别 | 名称 | 时长 | 缓动函数 | 适用场景 |
|---------|------|------|---------|---------|
| 微交互 | 悬停反馈 | 150ms | cubic-bezier(0.4, 0, 0.2, 1) | 按钮、卡片悬停 |
| 状态切换 | 展开/收起 | 250ms | cubic-bezier(0.4, 0, 0.2, 1) | 下拉菜单、折叠面板 |
| 页面过渡 | 路由切换 | 250ms | ease | 页面跳转 |
| 加载状态 | 骨架屏 | 1500ms | ease-in-out | 内容加载中 |
| 强调动画 | 脉冲发光 | 2000ms | ease-in-out | 进度指示、状态提醒 |
| 列表动画 | 交错进入 | 400ms | ease-out | 列表项依次显示 |

### 3.2 全局动效变量

在 `global.css` 中已定义的变量：

```css
--transition-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1);
--transition-normal: 250ms cubic-bezier(0.4, 0, 0.2, 1);
--transition-slow: 350ms cubic-bezier(0.4, 0, 0.2, 1);
```

### 3.3 组件动效规范

#### 卡片组件
```css
/* 卡片悬浮 */
.card {
  transition: transform var(--transition-normal),
              box-shadow var(--transition-normal);
}
.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--lift-shadow);
}

/* 禁用：卡片悬浮时禁止使用 */
.card:hover {
  /* ❌ 不要使用 scale 变换 */
  transform: scale(1.02); /* 错误 */
}
```

#### 按钮组件
```css
/* 按钮点击反馈 */
.btn {
  transition: all var(--transition-fast);
}

.btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.btn:active {
  transform: translateY(0);
  box-shadow: none;
}

/* 禁用：按钮悬浮时禁止使用 */
.btn:hover {
  /* ❌ 不要使用 color-mix 复杂变换 */
  /* ❌ 不要使用 filter 变换 */
}
```

#### 进度条组件
```css
/* 进度条填充动画 */
.progress-fill {
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 进度条 shimmer 效果 */
.progress-fill::after {
  animation: shimmer 1.8s ease-in-out infinite;
}
```

#### 列表项交错动画
```css
/* 列表项依次进入 */
.stagger-item {
  opacity: 0;
  animation: stagger-in 0.4s ease-out forwards;
}

.stagger-item:nth-child(1) { animation-delay: 0.05s; }
.stagger-item:nth-child(2) { animation-delay: 0.1s; }
.stagger-item:nth-child(3) { animation-delay: 0.15s; }
.stagger-item:nth-child(4) { animation-delay: 0.2s; }
.stagger-item:nth-child(5) { animation-delay: 0.25s; }
.stagger-item:nth-child(6) { animation-delay: 0.3s; }
/* 建议最多6个有交错效果 */
```

### 3.4 动效性能优化

1. **使用transform/opacity**：动画仅触发这两类属性时，可以利用GPU加速
2. **避免动画属性**：
   - `width`, `height` (使用transform: scale)
   - `margin`, `padding`
   - `left`, `top` (使用transform: translate)
3. **动画帧率**：保持在60fps，避免低于30fps
4. **减少重排**：批量读取和写入DOM属性

### 3.5 动效禁用场景

以下场景应禁用或减少动效：
- 用户设置了 `prefers-reduced-motion` 媒体查询
- 页面正在执行大量计算时
- 用户明确选择简化模式时

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 四、响应式布局确认

### 4.1 办公室场景分析

根据用户画像（办公室桌面环境业余爱好者）：
- **主要屏幕分辨率**：1920x1080, 1440x900, 1366x768
- **最小支持分辨率**：1366x768
- **触控设备**：非必需

### 4.2 响应式断点建议

```css
/* 常规桌面（默认） */
.container {
  max-width: 1400px;
  padding: 0 24px;
}

/* 小屏幕笔记本 */
@media (max-width: 1366px) {
  :root {
    --content-padding: 20px;
    --card-gap: 16px;
  }
}

/* 平板横屏（可选支持） */
@media (max-width: 1024px) {
  .sidebar {
    width: 60px;
  }
}

/* 平板竖屏（不建议支持） */
@media (max-width: 768px) {
  /* 考虑显示"请使用电脑访问"提示 */
}
```

### 4.3 结论

**确认结论**：办公室场景下，1366px以下屏幕无需专门支持。

理由：
1. 目标用户为办公室桌面环境
2. 当前设计已适配1366px+
3. App.vue 中的 768px 断点可保留用于未来扩展
4. 无需为小屏幕优化浪费开发资源

---

## 五、视觉细节检查清单

### 5.1 玻璃态效果检查

#### 当前实现
```css
.glass-card {
  background: var(--glass-bg); /* rgba(255, 255, 255, 0.7) */
  backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border); /* rgba(255, 255, 255, 0.3) */
}
```

#### 深色模式适配建议
```css
[data-theme="dark"] .glass-card {
  background: rgba(30, 41, 59, 0.8);
  border-color: rgba(255, 255, 255, 0.1);
}
```

#### 使用场景建议
| 场景 | 是否使用玻璃态 | 原因 |
|-----|--------------|------|
| 侧边栏 | ✅ 使用 | 毛玻璃效果现代感强 |
| 顶部导航栏 | ✅ 使用 | 保持内容区域可读 |
| 弹窗背景 | ✅ 使用 | 突出内容层级 |
| 卡片组件 | ❌ 建议不使用 | 办公室环境光线复杂，玻璃态可能影响可读性 |

### 5.2 阴影层次统一

#### 当前阴影系统
```css
--shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
--shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
--shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
--shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
--shadow-glow: 0 0 20px rgba(14, 165, 233, 0.3);
--lift-shadow: 0 20px 40px -12px rgba(0, 0, 0, 0.15);
```

#### 阴影使用规范
| 元素类型 | 默认阴影 | 悬浮阴影 | 说明 |
|---------|---------|---------|------|
| 页面级卡片 | shadow-sm | shadow-md | 主要内容卡片 |
| 悬浮卡片 | shadow-md | shadow-lg | 可交互卡片 |
| 模态弹窗 | shadow-xl | - | 最高层级 |
| 按钮 | 无 | shadow-sm | 微交互反馈 |

#### 深色模式阴影适配
```css
[data-theme="dark"] {
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.5);
  --shadow-glow: 0 0 20px rgba(96, 165, 250, 0.4);
}
```

### 5.3 配色方案微调建议

#### 深色主题文字对比度检查
当前深色主题配色（建议对比度达标WCAG AA）：
```css
--color-textPrimary: #f1f5f9;  /* ✅ 约 92% 对比度 */
--color-textSecondary: #94a3b8; /* ⚠️ 约 60% 对比度，建议调整为 #cbd5e1 */
--color-textMuted: #64748b;     /* ❌ 约 40% 对比度过低 */
```

**建议微调**：
```css
dark: {
  colors: {
    textPrimary: '#f1f5f9',
    textSecondary: '#cbd5e1',  /* 提升对比度 */
    textMuted: '#94a3b8',       /* 可保留，标题/正文不常用 */
  }
}
```

#### 状态色检查
```css
/* Success - 深色模式表现良好 */
--color-success: #4ade80;  /* ✅ 绿色在深色背景可读 */

/* Warning - 深色模式需注意 */
--color-warning: #fbbf24;  /* ✅ 琥珀色可读 */

/* Error - 深色模式表现良好 */
--color-error: #f87171;    /* ✅ 红色在深色背景可读 */
```

### 5.4 边框与分割线检查

```css
/* 当前实现 */
.divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-border), transparent);
  margin: var(--spacing-4) 0;
}

/* 深色模式适配 */
[data-theme="dark"] .divider {
  background: linear-gradient(90deg, transparent, #334155, transparent);
}
```

---

## 六、实施优先级建议

### P0 - 必须修复（影响核心体验）
1. **深色模式覆盖**：Modal、Dropdown、Tooltip组件
2. **错误状态优化**：WritingProgress错误提示UI

### P1 - 重要优化
1. **动效规范统一**：统一组件动效时长和缓动函数
2. **阴影系统统一**：确保深色模式阴影层次

### P2 - 体验提升
1. **玻璃态效果调整**：评估并优化
2. **配色微调**：文字对比度优化

---

## 七、附录：前端开发检查要点

### 深色模式自检清单
- [ ] 打开深色主题
- [ ] 检查所有弹窗（Modal）背景和文字
- [ ] 检查所有下拉菜单（Dropdown）
- [ ] 检查工具提示（Tooltip）
- [ ] 检查输入框（Input/Textarea）文字和边框
- [ ] 检查日志面板文字可读性
- [ ] 检查卡片背景和边框
- [ ] 检查阴影层次
- [ ] 检查状态色（success/warning/error）在深色背景表现

### 动效自检清单
- [ ] 路由切换动画流畅
- [ ] 卡片悬浮动效协调统一
- [ ] 列表项交错动画无卡顿
- [ ] 进度条动画流畅
- [ ] 按钮点击反馈及时

