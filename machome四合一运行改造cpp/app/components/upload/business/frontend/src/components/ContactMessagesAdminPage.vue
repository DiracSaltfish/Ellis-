<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getContactMessages } from '../lib/api'
import type { ContactMessage } from '../lib/types'

const rows = ref<ContactMessage[]>([])
const loading = ref(false)
const error = ref('')

function formatDate(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const payload = await getContactMessages(200)
    rows.value = payload.rows
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="section contact-page">
    <div class="detail-panel contact-panel">
      <div class="contact-header">
        <div>
          <p class="eyebrow">留言后台</p>
          <h2>网站管理员留言收件箱</h2>
          <p class="model-note">
            该页面现在可通过 /debug 二级页访问；旧隐藏入口和原有管理员 token 方式仍兼容保留。
          </p>
        </div>
        <button type="button" class="secondary-action" :disabled="loading" @click="refresh">
          {{ loading ? '刷新中...' : '刷新列表' }}
        </button>
      </div>

      <p v-if="error" class="notice error">{{ error }}</p>
      <p v-else-if="loading" class="notice">正在加载留言...</p>
      <p v-else-if="rows.length === 0" class="notice">暂时还没有收到留言。</p>

      <div v-else class="contact-message-list">
        <article v-for="row in rows" :key="row.id" class="contact-message-card">
          <div class="contact-message-meta">
            <strong>#{{ row.id }}</strong>
            <span>{{ row.email }}</span>
            <time :datetime="row.created_at">{{ formatDate(row.created_at) }}</time>
          </div>
          <p class="contact-message-body">{{ row.message }}</p>
        </article>
      </div>
    </div>
  </section>
</template>
