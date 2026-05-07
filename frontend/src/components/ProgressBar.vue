<template>
  <div class="progress-container">
    <div class="progress-header">
      <span class="progress-title">{{ title }}</span>
      <span class="progress-percentage">{{ progress }}%</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" :style="{ width: progress + '%' }"></div>
    </div>
    <div class="progress-message">{{ message }}</div>
  </div>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: '创作进度'
  },
  progress: {
    type: Number,
    default: 0
  },
  message: {
    type: String,
    default: ''
  }
})
</script>

<style scoped>
.progress-container {
  background: var(--color-surface);
  border-radius: 12px;
  padding: 20px;
  border: 1px solid var(--color-border);
  margin-bottom: 24px;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}

.progress-container:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.progress-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-textPrimary);
}

.progress-percentage {
  font-size: 16px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.progress-bar {
  width: 100%;
  height: 10px;
  background: var(--color-background);
  border-radius: 5px;
  overflow: hidden;
  margin-bottom: 8px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-accent));
  border-radius: 5px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.progress-fill::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.4) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.8s ease-in-out infinite;
}

@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.progress-message {
  font-size: 12px;
  color: var(--color-textSecondary);
  margin-top: 4px;
}
</style>