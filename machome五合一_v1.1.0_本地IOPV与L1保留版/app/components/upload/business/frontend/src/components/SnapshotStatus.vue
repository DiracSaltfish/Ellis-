<script setup lang="ts">
const props = defineProps<{
  asOf: string
  ttlSeconds: number
  warnings: string[]
}>()

function ageText() {
  const ms = Date.now() - new Date(props.asOf).getTime()
  if (!Number.isFinite(ms)) return '未知'
  return `${Math.max(0, Math.round(ms / 1000))} 秒前`
}
</script>

<template>
  <div class="snapshot-status">
    <span>快照时间：{{ new Date(props.asOf).toLocaleString() }}</span>
    <span>年龄：{{ ageText() }}</span>
    <span>TTL：{{ props.ttlSeconds }} 秒</span>
    <span v-if="props.warnings.length" class="warning">警告 {{ props.warnings.length }}</span>
  </div>
</template>
