<script setup lang="ts">
import { computed } from 'vue'
import type { Branch } from '../lib/types'

interface ExtraTab {
  key: string
  label: string
  after_key?: string
}

const props = withDefaults(defineProps<{
  branches: Branch[]
  activeKey: string
  extraTabs?: ExtraTab[]
}>(), {
  extraTabs: () => [],
})

defineEmits<{
  select: [key: string]
}>()

const orderedTabs = computed(() => {
  const tabs: Array<{ key: string; label: string }> = []
  const inserted = new Set<string>()

  for (const branch of props.branches) {
    tabs.push({ key: branch.key, label: branch.name_cn })
    for (const extraTab of props.extraTabs) {
      if (extraTab.after_key !== branch.key || inserted.has(extraTab.key)) continue
      tabs.push({ key: extraTab.key, label: extraTab.label })
      inserted.add(extraTab.key)
    }
  }

  for (const extraTab of props.extraTabs) {
    if (inserted.has(extraTab.key)) continue
    tabs.push({ key: extraTab.key, label: extraTab.label })
  }

  return tabs
})
</script>

<template>
  <nav class="branch-tabs">
    <button
      v-for="tab in orderedTabs"
      :key="tab.key"
      :class="{ active: tab.key === activeKey }"
      type="button"
      @click="$emit('select', tab.key)"
    >
      {{ tab.label }}
    </button>
  </nav>
</template>
