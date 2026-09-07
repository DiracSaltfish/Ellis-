<template>
  <section class="table-section snapshot-guide-links">
    <div class="snapshot-guide-copy">
      <p class="eyebrow">说明入口</p>
      <h2>申赎与使用说明</h2>
    </div>
    <div class="snapshot-guide-actions">
      <button
        type="button"
        class="snapshot-guide-button"
        @click="$emit('open-contact')"
      >
        联系网站管理员
      </button>
      <button
        type="button"
        class="snapshot-guide-button primary"
        @click="$emit('open-redemption-guide')"
      >
        何时赎回？点我了解
      </button>
      <button
        type="button"
        class="snapshot-guide-button"
        @click="$emit('open-guide')"
      >
        使用说明 / 溢价率说明
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
defineEmits<{
  'open-redemption-guide': []
  'open-guide': []
  'open-contact': []
}>()
</script>

<style scoped>
.snapshot-guide-links {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-color: #cfd8e7;
  background: #f9fbff;
}

.snapshot-guide-copy {
  min-width: 0;
}

.snapshot-guide-copy h2 {
  margin: 0;
}

.snapshot-guide-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.snapshot-guide-button {
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

.snapshot-guide-button:hover {
  border-color: #7ca1df;
  background: #eef5ff;
  color: #1d4f9c;
}

.snapshot-guide-button.primary {
  border-color: #2462b8;
  background: #2462b8;
  color: white;
}

.snapshot-guide-button.primary:hover {
  border-color: #1b4e96;
  background: #1b4e96;
  color: white;
}

@media (max-width: 720px) {
  .snapshot-guide-links {
    align-items: stretch;
    flex-direction: column;
  }

  .snapshot-guide-actions {
    justify-content: stretch;
  }

  .snapshot-guide-button {
    flex: 1 1 180px;
    white-space: normal;
  }
}
</style>
