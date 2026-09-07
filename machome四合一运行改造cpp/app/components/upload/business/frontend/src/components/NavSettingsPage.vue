<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  getNavSettingsStatus,
  postNavSettingsDeleteMinuteHistoryDay,
  postNavSettingsRecomputeToday,
  postNavSettingsRefresh,
  postNavSettingsValuationPosition,
} from '../lib/api'
import type {
  MinuteHistoryDeleteDayResult,
  MinuteHistoryRecomputeResult,
  NavSettingsRatioStatus,
  NavSettingsStatus,
} from '../lib/types'

const status = ref<NavSettingsStatus | null>(null)
const loading = ref(false)
const actionLoading = ref('')
const rowLoading = ref('')
const actionError = ref('')
const actionMessage = ref('')
const recomputeResult = ref<MinuteHistoryRecomputeResult | null>(null)
const deleteResult = ref<MinuteHistoryDeleteDayResult | null>(null)
const deleteDate = ref('')
const drafts = ref<Record<string, string>>({})

function fmtRatio(value?: number | null) {
  if (!Number.isFinite(value) || value === null || value === undefined || value <= 0) return ''
  return `${(Number(value) * 100).toFixed(2)}%`
}

function fmtDate(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function fmtDay(value?: string) {
  if (!value) return ''
  const clean = value.trim()
  if (/^\d{8}$/.test(clean)) {
    return `${clean.slice(0, 4)}-${clean.slice(4, 6)}-${clean.slice(6, 8)}`
  }
  return clean
}

function normalizeDateInput(value: string) {
  const clean = value.trim()
  return /^\d{4}-\d{2}-\d{2}$/.test(clean) ? clean : ''
}

function ratioInputValue(row: NavSettingsRatioStatus) {
  const value = row.manual_override_ratio ?? row.effective_ratio
  if (!Number.isFinite(value) || value <= 0) return ''
  return Number(value).toFixed(4)
}

function syncDrafts(nextStatus: NavSettingsStatus | null) {
  if (!nextStatus) {
    drafts.value = {}
    return
  }
  const next: Record<string, string> = {}
  for (const row of nextStatus.ratios) {
    next[row.symbol] = drafts.value[row.symbol] && rowLoading.value === row.symbol
      ? drafts.value[row.symbol]
      : ratioInputValue(row)
  }
  drafts.value = next
}

function syncStatusLabel(row: NavSettingsRatioStatus) {
  switch (row.uploader_sync_status) {
    case 'default':
      return '默认值'
    case 'synced':
      return '已同步'
    case 'missing_local':
      return '本地未落地'
    case 'mismatch':
      return '前后端不一致'
    case 'drift':
      return '本地有漂移'
    default:
      return '未知'
  }
}

async function loadStatus() {
  loading.value = true
  actionError.value = ''
  try {
    status.value = await getNavSettingsStatus()
    syncDrafts(status.value)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function refreshSnapshots() {
  actionLoading.value = 'refresh'
  actionError.value = ''
  actionMessage.value = ''
  try {
    const payload = await postNavSettingsRefresh()
    if (!payload.ok) {
      throw new Error(payload.error || 'refresh failed')
    }
    actionMessage.value = '快照已刷新。'
    await loadStatus()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    actionLoading.value = ''
  }
}

async function recomputeToday() {
  actionLoading.value = 'recompute'
  actionError.value = ''
  actionMessage.value = ''
  try {
    const payload = await postNavSettingsRecomputeToday()
    recomputeResult.value = payload
    actionMessage.value = payload.ok
      ? `已重算 ${payload.updated_rows} 条今日分时记录。`
      : '今日分时记录未发生更新。'
    await loadStatus()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    actionLoading.value = ''
  }
}

function fillDeleteDateWithCurrentLogicDay() {
  const currentDay = fmtDay(status.value?.minute_history_day)
  if (!currentDay) {
    actionError.value = '当前状态里没有可用的分时逻辑日'
    actionMessage.value = ''
    return
  }
  deleteDate.value = currentDay
}

async function deleteMinuteHistoryDay() {
  const targetDate = normalizeDateInput(deleteDate.value)
  if (!targetDate) {
    actionError.value = '请选择要删除的日期'
    actionMessage.value = ''
    return
  }
  if (!window.confirm(`确认删除 ${targetDate} 的全部历史分时估值？该操作只影响这一天的历史分时估值，且无法撤销。`)) {
    return
  }

  actionLoading.value = 'delete-day'
  actionError.value = ''
  actionMessage.value = ''
  try {
    deleteResult.value = await postNavSettingsDeleteMinuteHistoryDay({ date: targetDate })
    actionMessage.value = deleteResult.value.message || `${targetDate} 的历史分时估值已处理。`
    await loadStatus()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    actionLoading.value = ''
  }
}

async function applyRatio(row: NavSettingsRatioStatus) {
  const raw = drafts.value[row.symbol] || ''
  const ratio = Number(raw)
  if (!Number.isFinite(ratio) || ratio <= 0 || ratio > 1.2) {
    actionError.value = `${row.symbol} 的仓位请输入 (0, 1.2] 内的数字`
    actionMessage.value = ''
    return
  }
  rowLoading.value = row.symbol
  actionError.value = ''
  actionMessage.value = ''
  try {
    const payload = await postNavSettingsValuationPosition({ symbol: row.symbol, ratio })
    if (!payload.ok) {
      throw new Error(payload.error || 'update failed')
    }
    actionMessage.value = payload.refresh_error
      ? `${payload.message || '仓位已更新'}；站内快照刷新失败，请手工点一次“刷新快照”。`
      : (payload.message || '仓位已更新。')
    if (payload.status) {
      status.value = payload.status
      syncDrafts(status.value)
    } else {
      await loadStatus()
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    rowLoading.value = ''
  }
}

onMounted(() => {
  void loadStatus()
})
</script>

<template>
  <section class="section navsettings-section">
    <section class="detail-panel">
      <div class="navsettings-header">
        <div>
          <p class="eyebrow">调试页面</p>
          <h2>Valuation Controls</h2>
          <p class="model-note">这里可以查看当前采用仓位，并通过已鉴权的 debug 接口下发手工 override。</p>
        </div>
        <div class="navsettings-actions">
          <button
            type="button"
            :disabled="actionLoading === 'refresh'"
            @click="refreshSnapshots"
          >
            {{ actionLoading === 'refresh' ? '刷新中...' : '刷新快照' }}
          </button>
          <button
            type="button"
            :disabled="actionLoading === 'recompute'"
            @click="recomputeToday"
          >
            {{ actionLoading === 'recompute' ? '重算中...' : '重算今日分时估值' }}
          </button>
        </div>
      </div>

      <p v-if="actionMessage" class="notice navsettings-notice success">{{ actionMessage }}</p>
      <p v-if="actionError" class="notice error">{{ actionError }}</p>
      <p v-else-if="loading" class="notice">正在加载设置状态...</p>

      <div v-if="status" class="detail-metrics">
        <div>
          <span>服务时间</span>
          <strong>{{ status.server_time }}</strong>
        </div>
        <div>
          <span>分时逻辑日</span>
          <strong>{{ status.minute_history_day }}</strong>
        </div>
        <div>
          <span>Uploader 连接</span>
          <strong>{{ status.uploader.connected ? '已连接' : '未连接' }}</strong>
        </div>
        <div>
          <span>Uploader 来源</span>
          <strong>{{ status.uploader.source || '-' }}</strong>
        </div>
      </div>

      <div v-if="status" class="detail-metrics navsettings-secondary-metrics">
        <div>
          <span>最近心跳</span>
          <strong>{{ fmtDate(status.uploader.last_seen_at) || '-' }}</strong>
        </div>
        <div>
          <span>本地已知 override</span>
          <strong>{{ status.uploader.known_states }}</strong>
        </div>
        <div>
          <span>可用操作数</span>
          <strong>{{ status.operations.length }}</strong>
        </div>
      </div>

      <div v-if="recomputeResult" class="navsettings-summary">
        <span>最近一次重算:</span>
        <strong>{{ recomputeResult.updated_rows }}</strong>
        <span>条更新，</span>
        <strong>{{ recomputeResult.skipped_rows }}</strong>
        <span>条跳过</span>
      </div>

      <section class="navsettings-danger-zone">
        <div class="navsettings-danger-header">
          <div>
            <p class="eyebrow">危险操作</p>
            <h3>删除指定日期历史分时估值</h3>
            <p class="model-note">
              仅删除该日期所有标的的历史分时估值文件，不会改动份额、昨日赎回榜、收盘拟合或其他日期数据；点击删除后浏览器会再弹一次确认框。
            </p>
          </div>
          <button type="button" class="secondary-action" @click="fillDeleteDateWithCurrentLogicDay">
            填入当前逻辑日
          </button>
        </div>

        <div class="navsettings-danger-grid">
          <label class="navsettings-field">
            <span>删除日期</span>
            <input v-model="deleteDate" type="date" />
          </label>
        </div>

        <div class="navsettings-danger-actions">
          <button
            type="button"
            class="danger-action"
            :disabled="actionLoading === 'delete-day'"
            @click="deleteMinuteHistoryDay"
          >
            {{ actionLoading === 'delete-day' ? '删除中...' : '删除该日期全部历史分时估值' }}
          </button>
          <span class="model-note">当前分时逻辑日：{{ fmtDay(status?.minute_history_day) || '-' }}</span>
        </div>

        <div v-if="deleteResult" class="navsettings-delete-result">
          <span>最近一次删除结果：</span>
          <strong>{{ fmtDay(deleteResult.day) || '-' }}</strong>
          <template v-if="deleteResult.deleted">
            <span>已删除</span>
            <strong>{{ deleteResult.symbol_count }}</strong>
            <span>个标的 /</span>
            <strong>{{ deleteResult.row_count }}</strong>
            <span>条记录</span>
          </template>
          <template v-else>
            <span>未发现可删除文件</span>
          </template>
        </div>
        <p v-if="deleteResult?.warnings?.length" class="model-note">
          {{ deleteResult.warnings.join('；') }}
        </p>
      </section>
    </section>

    <section v-if="status?.ratios?.length" class="table-section">
      <h2>估值仓位控制</h2>
      <div class="table-wrap compact">
        <table class="navsettings-ratio-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>模型</th>
              <th>参考标的</th>
              <th>当前采用</th>
              <th>默认设定</th>
              <th>手工 override</th>
              <th>upload 本地</th>
              <th>同步状态</th>
              <th>本地更新时间</th>
              <th>修改为</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in status.ratios" :key="row.symbol">
              <td class="code" data-label="代码">{{ row.symbol }}</td>
              <td class="name" data-label="名称">{{ row.name }}</td>
              <td data-label="模型">{{ row.model_version }}</td>
              <td data-label="参考标的">{{ row.reference_symbol || '-' }}</td>
              <td data-label="当前采用">{{ fmtRatio(row.effective_ratio) }}</td>
              <td data-label="默认设定">{{ fmtRatio(row.default_effective_ratio) }}</td>
              <td data-label="手工 override">{{ fmtRatio(row.manual_override_ratio) || '-' }}</td>
              <td data-label="upload 本地">{{ fmtRatio(row.uploader_local_ratio) || '-' }}</td>
              <td data-label="同步状态">{{ syncStatusLabel(row) }}</td>
              <td data-label="本地更新时间">{{ fmtDate(row.uploader_local_updated_at) || '-' }}</td>
              <td data-label="修改为" class="navsettings-input-cell">
                <input
                  v-model="drafts[row.symbol]"
                  class="navsettings-ratio-input"
                  inputmode="decimal"
                  placeholder="0.8000"
                />
              </td>
              <td data-label="操作" class="navsettings-action-cell">
                <button
                  type="button"
                  class="secondary-action"
                  :disabled="rowLoading === row.symbol || !status.uploader.connected"
                  @click="applyRatio(row)"
                >
                  {{ rowLoading === row.symbol ? '提交中...' : '修改' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
