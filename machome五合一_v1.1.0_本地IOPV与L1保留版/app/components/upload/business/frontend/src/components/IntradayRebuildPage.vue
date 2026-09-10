<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getIntradayRebuildStatus, postIntradayRebuildJob } from '../lib/api'
import type { IntradayRebuildJob, IntradayRebuildState, IntradayRebuildStatus } from '../lib/types'

const status = ref<IntradayRebuildStatus | null>(null)
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const message = ref('')
const date = ref(shanghaiDay())
const symbols = ref('SZ159518')
let refreshTimer: number | undefined

const agentLabel = computed(() => {
  if (!status.value?.agent.connected) return '离线'
  return `在线：${status.value.agent.agent_id || 'Mac-home'}`
})

function shanghaiDay() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

function fmtDate(value?: string) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function stateLabel(state: IntradayRebuildState) {
  const labels: Record<IntradayRebuildState, string> = {
    queued: '等待 Agent', dispatched: '已下发', capturing: '回补中', awaiting_fx: '等待当日汇率', completed: '已完成', failed: '失败', cancelled: '已取消',
  }
  return labels[state]
}

function parseSymbols() {
  return Array.from(new Set(symbols.value.split(/[,，\s]+/).map((value) => value.trim().toUpperCase()).filter(Boolean)))
}

async function load(silent = false) {
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    status.value = await getIntradayRebuildStatus()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function submit() {
  const selected = parseSymbols()
  if (!date.value) {
    error.value = '请选择交易日'
    return
  }
  if (selected.length === 0) {
    error.value = '请至少填写一个私有估值标的；避免误触发全量回补。'
    return
  }
  if (!window.confirm(`确认以 CFETS 当日中间价重建 ${date.value} 的 ${selected.join(', ')} 分时估值？`)) return
  submitting.value = true
  error.value = ''
  message.value = ''
  try {
    const job = await postIntradayRebuildJob({ trade_date: date.value, symbols: selected, fx_policy: 'final_cfets' })
    message.value = `任务 ${job.id} 已创建。`
    await load(true)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await load()
  refreshTimer = window.setInterval(() => void load(true), 5000)
})

onBeforeUnmount(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <section class="detail-panel intraday-rebuild-panel">
    <div class="intraday-rebuild-heading">
      <div>
        <p class="eyebrow">Private valuation</p>
        <h3>盘中回补</h3>
        <p class="model-note">任务经专用 WSS 下发至 Mac-home，由本地 TWS 拉取后调用既有历史导入接口重建。只允许在 CFETS 当日中间价可用时写入，避免以昨收或猜测汇率污染历史。</p>
      </div>
      <span class="intraday-agent" :class="{ offline: !status?.agent.connected }">{{ agentLabel }}</span>
    </div>

    <form class="intraday-rebuild-form" @submit.prevent="submit">
      <label>
        <span>交易日</span>
        <input v-model="date" type="date" required />
      </label>
      <label class="intraday-symbol-input">
        <span>标的（逗号分隔）</span>
        <input v-model="symbols" placeholder="例如 SZ159518,SH513350" required />
      </label>
      <button type="submit" :disabled="submitting || !status?.agent.connected">
        {{ submitting ? '创建中...' : '下发 TWS 回补' }}
      </button>
    </form>
    <p class="model-note">当前版本要求指定标的；SH513220 仍需独立的 QMT 境内 BID/ASK 审计文件，不能仅由 TWS 重建。</p>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="message" class="notice">{{ message }}</p>
    <p v-else-if="loading" class="notice">正在加载任务状态...</p>

    <div class="intraday-jobs">
      <p v-if="status && status.jobs.length === 0" class="model-note">暂无盘中回补任务。</p>
      <article v-for="job in status?.jobs || []" :key="job.id" class="intraday-job">
        <div class="intraday-job-title">
          <strong>{{ job.trade_date }} · {{ job.symbols.join(', ') }}</strong>
          <span :class="['intraday-state', job.state]">{{ stateLabel(job.state) }}</span>
        </div>
        <div class="intraday-progress"><span :style="{ width: `${job.progress}%` }" /></div>
        <p>{{ job.message || '—' }}</p>
        <p v-if="job.error" class="notice error">{{ job.error }}</p>
        <small>{{ job.agent_id || '未分配 Agent' }} · 更新于 {{ fmtDate(job.updated_at) }}</small>
      </article>
    </div>
  </section>
</template>

<style scoped>
.intraday-rebuild-panel { margin-top: 1rem; }
.intraday-rebuild-heading, .intraday-job-title { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
.intraday-agent, .intraday-state { border-radius: 999px; padding: .28rem .62rem; background: var(--panel-soft, #eef4ee); color: #266443; white-space: nowrap; }
.intraday-agent.offline, .intraday-state.failed, .intraday-state.cancelled { background: #fff0ef; color: #9b332c; }
.intraday-state.awaiting_fx { background: #fff8e6; color: #84610d; }
.intraday-rebuild-form { display: grid; grid-template-columns: 11rem minmax(14rem, 1fr) auto; gap: .75rem; align-items: end; margin-top: 1rem; }
.intraday-rebuild-form label { display: grid; gap: .35rem; }
.intraday-rebuild-form input { min-width: 0; }
.intraday-jobs { display: grid; gap: .75rem; margin-top: 1rem; }
.intraday-job { border: 1px solid var(--line, #dce3dc); border-radius: .7rem; padding: .8rem; }
.intraday-job p { margin: .55rem 0 .35rem; }
.intraday-job small { color: var(--muted, #68726a); }
.intraday-progress { height: .35rem; background: #edf0ed; border-radius: 999px; overflow: hidden; }
.intraday-progress span { display: block; height: 100%; background: #2f7b51; transition: width .25s ease; }
@media (max-width: 720px) { .intraday-rebuild-form { grid-template-columns: 1fr; } .intraday-rebuild-heading, .intraday-job-title { flex-direction: column; } }
</style>
