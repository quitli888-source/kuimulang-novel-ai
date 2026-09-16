<template>
  <div class="section">
    <div class="section-title">滑动窗口与摘要触发参数</div>
    <div style="color:#888;font-size:13px;margin-bottom:16px;">
      控制 PartWriter 在创作时保留多少个最近 Part 的原文、每隔几个 Part 生成二级滚动摘要/三级里程碑摘要。
      当前配置来源：<b>{{ source || '加载中' }}</b>
    </div>
    <n-form label-placement="left" label-width="160">
      <n-form-item label="Window Size（最近 K 个 Part 原文）">
        <n-input-number :value="windowSize" @update:value="v => $emit('update:windowSize', v)" :min="2" :max="20" :step="1" />
        <span style="margin-left:12px;color:#888;">默认 {{ defaults?.window_size || 6 }}（范围 2~20）</span>
      </n-form-item>
      <n-form-item label="Rolling Every（每 N Part 生成二级摘要）">
        <n-input-number :value="rollingEvery" @update:value="v => $emit('update:rollingEvery', v)" :min="2" :max="10" :step="1" />
        <span style="margin-left:12px;color:#888;">默认 {{ defaults?.rolling_every || 3 }}（范围 2~10）</span>
      </n-form-item>
      <n-form-item label="Milestone Every（每 N Part 生成三级里程碑）">
        <n-input-number :value="milestoneEvery" @update:value="v => $emit('update:milestoneEvery', v)" :min="5" :max="100" :step="1" />
        <span style="margin-left:12px;color:#888;">默认 {{ defaults?.milestone_every || 20 }}（范围 5~100）</span>
      </n-form-item>
      <n-form-item>
        <n-button type="primary" @click="$emit('save')" :loading="saving">💾 保存滑动窗口配置</n-button>
        <n-button style="margin-left:12px" @click="$emit('reset')">重置为默认值</n-button>
      </n-form-item>
    </n-form>
    <div v-if="lastResult" :class="lastOk ? 'msg-ok' : 'msg-err'" style="margin-top:12px;padding:8px 12px;border-radius:6px;">
      {{ lastResult }}
    </div>
    <style scoped>
      .msg-ok { background: #f0f9ff; color: #1890ff; border: 1px solid #91d5ff; }
      .msg-err { background: #fff1f0; color: #f5222d; border: 1px solid #ffa39e; }
    </style>
  </div>
</template>

<script setup>
// R23-P1-15: WindowConfigTab 从 ConfigPanel.vue 拆出来的子组件
// 通过 props 传入状态，emit 事件让父组件处理保存/重置 API 调用
defineProps({
  windowSize: { type: Number, required: true },
  rollingEvery: { type: Number, required: true },
  milestoneEvery: { type: Number, required: true },
  defaults: { type: Object, default: () => ({ window_size: 6, rolling_every: 3, milestone_every: 20 }) },
  source: { type: String, default: 'loading' },
  saving: { type: Boolean, default: false },
  lastResult: { type: String, default: '' },
  lastOk: { type: Boolean, default: true },
})

defineEmits([
  'update:windowSize',
  'update:rollingEvery',
  'update:milestoneEvery',
  'save',
  'reset',
])
</script>
