<script setup lang="ts">
import { computed } from 'vue'
import type { EffectiveRatioFitHistoryResponse, EffectiveRatioFitHistoryRow } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'

const props = defineProps<{ history: EffectiveRatioFitHistoryResponse }>()

defineEmits<{
  backToFund: [symbol: string]
}>()

function fmt(value: number, digits = 4) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(digits)
}

function fmtPct(value: number | null | undefined, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) return ''
  return `${fmt(value, digits)}%`
}

function fmtRatio(value: number | null | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value)) return ''
  return `${fmt(value * 100, 2)}%`
}

function pctClass(value: number | null | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value)) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

const latestRow = computed(() => props.history.rows[0] || null)
const latestEstimatedRow = computed(() => props.history.rows.find((row) => row.t2_estimated_nav !== null && row.t2_estimated_nav !== undefined) || null)
const latestClosePremiumRow = computed(() => props.history.rows.find((row) => row.closing_realtime_premium_pct !== null && row.closing_realtime_premium_pct !== undefined) || null)
const latestFittedRow = computed(() => props.history.rows.find((row) => row.fitted_effective_ratio !== null && row.fitted_effective_ratio !== undefined) || null)
const fittedRows = computed(() => props.history.rows.filter((row) => row.fitted_effective_ratio !== null && row.fitted_effective_ratio !== undefined))
const avgFittedRatio = computed(() => {
  if (!fittedRows.value.length) return null
  const total = fittedRows.value.reduce((sum, row) => sum + Number(row.fitted_effective_ratio || 0), 0)
  return total / fittedRows.value.length
})

function rowStatusLabel(row: EffectiveRatioFitHistoryRow) {
  switch (row.status) {
    case 'ok':
      return 'ok'
    case 'insufficient_window':
      return '窗口不足'
    case 'missing_base_nav':
      return '缺基准净值'
    case 'missing_context':
      return '缺估值上下文'
    case 'missing_window_context':
      return '窗口缺数据'
    case 'fit_failed':
      return '拟合失败'
    default:
      return row.status
  }
}
</script>

<template>
  <section class="section">
    <section class="detail-panel">
      <div class="detail-header-top">
        <p class="eyebrow">仓位跟踪</p>
        <div class="detail-header-actions">
          <button
            type="button"
            class="inline-link detail-history-link"
            @click="$emit('backToFund', history.symbol)"
          >
            返回标的详情
          </button>
          <span v-if="isValuationConfirmed(history.symbol)" class="confirmation-badge">
            估值方案已确认
          </span>
        </div>
      </div>
      <h2>{{ history.symbol }} {{ history.name || '' }}</h2>
      <p class="model-note">
        每日晚间在官方净值同步后重算；收盘实时溢价率取该交易日历史分时估值的最后一个分钟采样点。
      </p>
      <p class="model-note">
        本表中“官方净值”指目标日 T 的官方净值；“T日估值”指基于历史估值链路回推得到的目标日估值。
      </p>
    </section>

    <section class="summary-grid">
      <article class="metric-card">
        <span>最新目标日</span>
        <strong>{{ latestRow?.target_date || '-' }}</strong>
      </article>
      <article class="metric-card">
        <span>最新拟合仓位</span>
        <strong>{{ fmtRatio(latestFittedRow?.fitted_effective_ratio) || '-' }}</strong>
      </article>
      <article class="metric-card">
        <span>当前设定仓位</span>
        <strong>{{ fmtRatio(latestRow?.configured_effective_ratio) || '-' }}</strong>
      </article>
      <article class="metric-card">
        <span>最新窗口 MAPE</span>
        <strong>{{ fmtPct(latestFittedRow?.window_mape_pct) || '-' }}</strong>
      </article>
      <article class="metric-card">
        <span>最新偏差率</span>
        <strong :class="pctClass(latestEstimatedRow?.nav_error_pct)">
          {{ fmtPct(latestEstimatedRow?.nav_error_pct) || '-' }}
        </strong>
      </article>
      <article class="metric-card">
        <span>最新收盘溢价</span>
        <strong :class="pctClass(latestClosePremiumRow?.closing_realtime_premium_pct)">
          {{ fmtPct(latestClosePremiumRow?.closing_realtime_premium_pct) || '-' }}
        </strong>
      </article>
      <article class="metric-card">
        <span>区间平均拟合仓位</span>
        <strong>{{ fmtRatio(avgFittedRatio) || '-' }}</strong>
      </article>
    </section>

    <section class="table-section">
      <h2>滚动拟合明细</h2>
      <div class="table-wrap">
        <table class="ratio-history-table">
          <thead>
            <tr>
              <th class="sticky-col">目标日(T)</th>
              <th>官方净值(T)</th>
              <th>T日估值</th>
              <th>偏差率</th>
              <th>收盘溢价</th>
              <th>拟合仓位</th>
              <th>当前设定仓位</th>
              <th>窗口MAPE</th>
              <th>状态</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in history.rows" :key="`${row.symbol}-${row.target_date}-${row.window_size}`">
              <td class="sticky-col" data-label="目标日(T)">{{ row.target_date }}</td>
              <td data-label="官方净值(T)">{{ fmt(row.official_nav) }}</td>
              <td data-label="T日估值">{{ row.t2_estimated_nav !== null && row.t2_estimated_nav !== undefined ? fmt(row.t2_estimated_nav) : '-' }}</td>
              <td :class="pctClass(row.nav_error_pct)" data-label="偏差率">
                {{ fmtPct(row.nav_error_pct) || '-' }}
              </td>
              <td
                :class="pctClass(row.closing_realtime_premium_pct)"
                :title="row.closing_realtime_minute ? `采样点 ${row.closing_realtime_minute}` : ''"
                data-label="收盘溢价"
              >
                {{ fmtPct(row.closing_realtime_premium_pct) || '-' }}
              </td>
              <td data-label="拟合仓位">{{ fmtRatio(row.fitted_effective_ratio) || '-' }}</td>
              <td data-label="当前设定仓位">{{ fmtRatio(row.configured_effective_ratio) || '-' }}</td>
              <td data-label="窗口MAPE">{{ fmtPct(row.window_mape_pct) || '-' }}</td>
              <td data-label="状态">{{ rowStatusLabel(row) }}</td>
              <td class="debug-details" data-label="说明">{{ row.note || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
