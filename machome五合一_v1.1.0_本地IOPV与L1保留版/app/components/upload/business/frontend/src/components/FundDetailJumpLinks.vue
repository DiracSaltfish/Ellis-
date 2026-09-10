<template>
  <section class="table-section fund-detail-jump-links">
    <div class="fund-detail-jump-copy">
      <p class="eyebrow">数据入口</p>
      <h2>历史份额与仓位跟踪</h2>
    </div>
    <div class="fund-detail-jump-actions">
      <button
        v-if="showShareHistory"
        type="button"
        class="fund-detail-jump-button"
        @click="$emit('open-share-history')"
      >
        查看历史份额
      </button>
      <button
        type="button"
        class="fund-detail-jump-button primary"
        @click="$emit('open-ratio-history')"
      >
        查看仓位跟踪
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  showShareHistory: boolean
}>()

defineEmits<{
  'open-ratio-history': []
  'open-share-history': []
}>()
</script>

<style scoped>
.fund-detail-jump-links {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-color: #cfd8e7;
  background: #f9fbff;
}

.fund-detail-jump-copy {
  min-width: 0;
}

.fund-detail-jump-copy h2 {
  margin: 0;
}

.fund-detail-jump-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.fund-detail-jump-button {
  min-height: 36px;
  border: 1px solid #b8c5d9;
  border-radius: 6px;
  padding: 7px 12px;
  background: white;
  color: #263449;
  cursor: pointer;
  font-size: 14px;
  line-height: 1.2;
  white-space: nowrap;
}

.fund-detail-jump-button:hover {
  border-color: #7ca1df;
  background: #eef5ff;
  color: #1d4f9c;
}

.fund-detail-jump-button.primary {
  border-color: #2462b8;
  background: #2462b8;
  color: white;
}

.fund-detail-jump-button.primary:hover {
  border-color: #1b4e96;
  background: #1b4e96;
  color: white;
}

@media (max-width: 720px) {
  .fund-detail-jump-links {
    align-items: stretch;
    flex-direction: column;
  }

  .fund-detail-jump-actions {
    justify-content: stretch;
  }

  .fund-detail-jump-button {
    flex: 1 1 180px;
    white-space: normal;
  }
}
</style>
