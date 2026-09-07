<script setup lang="ts">
import { computed } from 'vue'
import type { DebugStatus, NAVSyncStatus, NAVSyncSymbolResult, RuntimeTaskStatus, SystemEvent } from '../lib/types'

const props = defineProps<{
  status: DebugStatus
}>()

const navSync = computed(() => props.status.nav_sync_status)
const runtimeTasks = computed(() => props.status.runtime_status || [])
const runtimeSummary = computed(() => {
  const tasks = runtimeTasks.value
  return {
    total: tasks.length,
    running: tasks.filter((task) => task.running).length,
    error: tasks.filter((task) => task.status === 'error').length,
    disabled: tasks.filter((task) => !task.enabled || task.status === 'disabled').length,
  }
})

function fmtDate(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function fmtDuration(seconds?: number) {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${rest}s`
}

function navStateLabel(status?: NAVSyncStatus) {
  if (!status) return 'unknown'
  if (!status.enabled) return 'disabled'
  if (status.running) return 'running'
  return status.last_success ? 'ok' : 'idle'
}

function navStateClass(status?: NAVSyncStatus) {
  if (!status || !status.enabled) return 'flat'
  if (status.running) return 'flat'
  return status.last_error ? 'warning' : 'ok'
}

function navProgress(status: NAVSyncStatus) {
  if (!status.total_symbols) return ''
  if (status.running && status.current_index > 0) {
    return `${status.current_index} / ${status.total_symbols}`
  }
  return `0 / ${status.total_symbols}`
}

function levelClass(event: SystemEvent) {
  return {
    warning: event.level === 'warn' || event.level === 'error',
    ok: event.level === 'info',
  }
}

function resultClass(result: NAVSyncSymbolResult) {
  return {
    warning: result.status.includes('failed'),
    ok: result.status === 'synced' || result.status === 'current',
    flat: result.status === 'no_rows',
  }
}

function taskStateLabel(task: RuntimeTaskStatus) {
  if (!task.enabled || task.status === 'disabled') return 'disabled'
  if (task.running || task.status === 'running') return 'running'
  if (task.status === 'error') return 'error'
  if (task.status === 'ok') return 'ok'
  return task.status || 'idle'
}

function taskStateClass(task: RuntimeTaskStatus) {
  return {
    warning: task.status === 'error',
    ok: task.status === 'ok',
    flat: !task.enabled || task.status === 'disabled' || task.status === 'idle',
  }
}
</script>

<template>
  <section class="section debug-page">
    <section class="summary-grid">
      <div class="metric-card">
        <span>服务器时间</span>
        <strong>{{ fmtDate(status.server_time) }}</strong>
      </div>
      <div class="metric-card">
        <span>上传覆盖</span>
        <strong :class="status.upload_status.enabled ? 'up' : 'down'">
          {{ status.upload_status.enabled ? 'enabled' : 'disabled' }}
        </strong>
      </div>
      <div class="metric-card">
        <span>上传缓存</span>
        <strong>{{ status.upload_status.count }}</strong>
      </div>
      <div class="metric-card">
        <span>需要行情</span>
        <strong>{{ status.required.count }}</strong>
      </div>
      <div class="metric-card">
        <span>实时 / 占位</span>
        <strong>{{ status.freshness.realtime }} / {{ status.freshness.demo }}</strong>
      </div>
    </section>

    <section v-if="navSync" class="nav-sync-section">
      <div class="debug-section-title">
        <h2>净值拉取状态</h2>
        <span :class="navStateClass(navSync)">{{ navStateLabel(navSync) }}</span>
      </div>
      <section class="summary-grid nav-sync-grid">
        <div class="metric-card">
          <span>当前进度</span>
          <strong>{{ navProgress(navSync) }}</strong>
        </div>
        <div class="metric-card">
          <span>当前标的</span>
          <strong>{{ navSync.current_symbol || '-' }}</strong>
        </div>
        <div class="metric-card">
          <span>请求 / 写入</span>
          <strong>{{ navSync.requests }} / {{ navSync.written }}</strong>
        </div>
        <div class="metric-card">
          <span>跳过 / 失败</span>
          <strong>{{ navSync.skipped }} / {{ navSync.failed }}</strong>
        </div>
        <div class="metric-card">
          <span>最近开始</span>
          <strong>{{ fmtDate(navSync.last_started_at) || '-' }}</strong>
        </div>
        <div class="metric-card">
          <span>最近结束</span>
          <strong>{{ fmtDate(navSync.last_finished_at) || '-' }}</strong>
        </div>
        <div class="metric-card">
          <span>耗时</span>
          <strong>{{ fmtDuration(navSync.last_duration_seconds) || '-' }}</strong>
        </div>
        <div class="metric-card">
          <span>下次计划</span>
          <strong>{{ fmtDate(navSync.next_run_at) || '-' }}</strong>
        </div>
      </section>
      <p v-if="navSync.last_error" class="notice error nav-sync-error">
        {{ navSync.last_error }}
      </p>
      <p v-else-if="navSync.last_written_symbol" class="notice nav-sync-note">
        最近写入 {{ navSync.last_written_symbol }}，净值日期 {{ navSync.last_written_date }}，{{ navSync.last_written_rows }} 行
      </p>
      <div class="table-wrap compact">
        <table class="nav-sync-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>标的</th>
              <th>状态</th>
              <th>区间</th>
              <th>行数</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in navSync.recent_symbol_results" :key="item.at + item.symbol + item.status">
              <td>{{ fmtDate(item.at) }}</td>
              <td class="code">{{ item.symbol }}</td>
              <td :class="resultClass(item)">{{ item.status }}</td>
              <td>{{ item.start_date || '-' }} - {{ item.end_date || '-' }}</td>
              <td>{{ item.rows || 0 }}</td>
              <td class="debug-error">{{ item.error }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="runtimeTasks.length" class="nav-sync-section">
      <div class="debug-section-title">
        <h2>后台时间节点</h2>
        <span :class="runtimeSummary.error ? 'warning' : 'ok'">
          {{ runtimeSummary.running }} running / {{ runtimeSummary.error }} errors
        </span>
      </div>
      <section class="summary-grid runtime-summary-grid">
        <div class="metric-card">
          <span>任务数</span>
          <strong>{{ runtimeSummary.total }}</strong>
        </div>
        <div class="metric-card">
          <span>运行中</span>
          <strong>{{ runtimeSummary.running }}</strong>
        </div>
        <div class="metric-card">
          <span>失败</span>
          <strong :class="runtimeSummary.error ? 'warning' : 'ok'">{{ runtimeSummary.error }}</strong>
        </div>
        <div class="metric-card">
          <span>禁用</span>
          <strong>{{ runtimeSummary.disabled }}</strong>
        </div>
      </section>
      <div class="table-wrap compact">
        <table class="runtime-task-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>状态</th>
              <th>计划</th>
              <th>下次</th>
              <th>最近开始</th>
              <th>最近结束</th>
              <th>耗时</th>
              <th>成功 / 失败</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in runtimeTasks" :key="task.key">
              <td>{{ task.name || task.key }}</td>
              <td :class="taskStateClass(task)">{{ taskStateLabel(task) }}</td>
              <td>{{ task.schedule || '-' }}</td>
              <td>{{ fmtDate(task.next_run_at) || '-' }}</td>
              <td>{{ fmtDate(task.last_started_at) || '-' }}</td>
              <td>{{ fmtDate(task.last_finished_at) || '-' }}</td>
              <td>{{ fmtDuration(task.last_duration_seconds) || '-' }}</td>
              <td>{{ task.success_count }} / {{ task.failure_count }}</td>
              <td class="debug-error">{{ task.last_error }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="table-section">
      <h2>WS 客户端</h2>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr>
              <th>来源</th>
              <th>状态</th>
              <th>连接时间</th>
              <th>最近心跳</th>
              <th>最近上传</th>
              <th>上传次数</th>
              <th>行情数</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="client in status.ws_clients" :key="client.id">
              <td>{{ client.source }}</td>
              <td :class="client.active ? 'up' : 'flat'">{{ client.active ? 'active' : 'closed' }}</td>
              <td>{{ fmtDate(client.connected_at) }}</td>
              <td>{{ fmtDate(client.last_seen_at) }}</td>
              <td>{{ fmtDate(client.last_upload_at) }}</td>
              <td>{{ client.upload_count }}</td>
              <td>{{ client.quote_count }}</td>
              <td class="debug-error">{{ client.last_error }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="debug-grid">
      <section class="table-section">
        <h2>上传来源</h2>
        <div class="table-wrap compact">
          <table>
            <thead>
              <tr>
                <th>来源</th>
                <th>最近上传</th>
                <th>次数</th>
                <th>累计行情</th>
                <th>最近接受</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="source in status.upload_sources" :key="source.source">
                <td>{{ source.source }}</td>
                <td>{{ fmtDate(source.last_upload_at) }}</td>
                <td>{{ source.upload_count }}</td>
                <td>{{ source.quote_count }}</td>
                <td>{{ source.last_accepted }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="table-section">
        <h2>行情来源</h2>
        <div class="table-wrap compact">
          <table>
            <thead>
              <tr>
                <th>来源</th>
                <th>数量</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="source in status.quote_sources" :key="source.source">
                <td>{{ source.source }}</td>
                <td>{{ source.count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </section>

    <section class="table-section">
      <h2>最近事件</h2>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>级别</th>
              <th>来源</th>
              <th>消息</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="event in status.recent_events" :key="event.at + event.message">
              <td>{{ fmtDate(event.at) }}</td>
              <td :class="levelClass(event)">{{ event.level }}</td>
              <td>{{ event.source }}</td>
              <td>{{ event.message }}</td>
              <td class="debug-details">{{ event.details ? JSON.stringify(event.details) : '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
