<script setup lang="ts">
import { computed } from 'vue'
import type { PrivateSilverCloseHistoryResponse, PrivateSilverCloseHistoryRow } from '../lib/privateTypes'

const props = defineProps<{
  history: PrivateSilverCloseHistoryResponse
}>()

defineEmits<{
  backToFund: [symbol: string]
}>()

const latestRow = computed(() => props.history.rows[0] ?? null)
const latestOfficialRow = computed(() => props.history.rows.find((row) => positive(row.official_nav)) ?? null)
const latestDeviationRow = computed(() => props.history.rows.find((row) => finite(row.settlement_deviation_rate)) ?? null)
const averageAbsoluteDeviation = computed(() => {
  const values = props.history.rows
    .map((row) => row.settlement_deviation_rate)
    .filter((value): value is number => finite(value))
  if (!values.length) return null
  return values.reduce((sum, value) => sum + Math.abs(value), 0) / values.length
})

const shanghaiCloseTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function finite(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value)
}

function positive(value: number | null | undefined) {
  return finite(value) && value > 0
}

function fmt(value: number | null | undefined, digits = 4) {
  if (!finite(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function fmtRate(value: number | null | undefined, digits = 3) {
  return finite(value) ? `${(value * 100).toFixed(digits)}%` : '—'
}

function valueClass(value: number | null | undefined) {
  if (!finite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function closeTime(row: PrivateSilverCloseHistoryRow) {
  const parsed = new Date(row.close_minute)
  return Number.isNaN(parsed.valueOf()) ? '—' : shanghaiCloseTime.format(parsed)
}
</script>

<template>
  <section class="section private-silver-close-history-page">
    <a
      :href="`/private/${history.symbol}`"
      class="private-detail-back"
      @click.prevent="$emit('backToFund', history.symbol)"
    >
      ← 返回 Private 标的详情
    </a>

    <section class="detail-panel private-detail-panel">
      <div class="detail-header-top">
        <p class="eyebrow">历史复盘 · 白银基金收盘时点</p>
        <span class="confirmation-badge">{{ history.model_version }}</span>
      </div>
      <h2><span class="detail-title-part">{{ history.symbol }}</span>{{ history.name }} 收盘估值历史</h2>
      <p class="model-note">{{ history.methodology_note }}</p>
    </section>

    <section class="summary-grid private-silver-close-summary">
      <article class="metric-card"><span>已保存收盘样本</span><strong>{{ history.rows.length }} 日</strong></article>
      <article class="metric-card"><span>最新交易日</span><strong>{{ latestRow?.trading_day || '—' }}</strong></article>
      <article class="metric-card"><span>最新官方净值</span><strong>{{ fmt(latestOfficialRow?.official_nav) }}</strong></article>
      <article class="metric-card"><span>最新结算估值</span><strong>{{ fmt(latestRow?.settlement_nav) }}</strong></article>
      <article class="metric-card"><span>结算估值溢价率</span><strong :class="valueClass(latestRow?.settlement_premium_rate)">{{ fmtRate(latestRow?.settlement_premium_rate) }}</strong></article>
      <article class="metric-card"><span>交易估值溢价率</span><strong :class="valueClass(latestRow?.trading_premium_rate)">{{ fmtRate(latestRow?.trading_premium_rate) }}</strong></article>
      <article class="metric-card"><span>最新结算估值偏差</span><strong :class="valueClass(latestDeviationRow?.settlement_deviation_rate)">{{ fmtRate(latestDeviationRow?.settlement_deviation_rate) }}</strong></article>
      <article class="metric-card"><span>平均绝对偏差</span><strong>{{ fmtRate(averageAbsoluteDeviation) }}</strong></article>
    </section>

    <section class="table-section private-review-table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>每日收盘时点明细</h2>
          <p class="table-inline-note">当日官方净值尚未发布时，净值与偏差暂显示“待公布”；结算、交易两套收盘估值保持原始历史值不变。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="private-silver-close-table">
          <thead>
            <tr>
              <th class="sticky-col">日期</th>
              <th>净值</th>
              <th>LOF 收盘价</th>
              <th>结算估值</th>
              <th>结算估值溢价率</th>
              <th>交易估值</th>
              <th>交易估值溢价率</th>
              <th>结算估值偏差（与净值）</th>
              <th>AG 合约</th>
              <th>AG 交易价 / 盘中均价</th>
              <th>收盘时点</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in history.rows" :key="`${row.trading_day}-${row.close_minute}`">
              <td class="sticky-col" data-label="日期">{{ row.trading_day }}</td>
              <td data-label="净值">
                <template v-if="positive(row.official_nav)">{{ fmt(row.official_nav) }}</template>
                <span v-else class="private-history-pending">待公布</span>
                <small v-if="row.official_nav_source">{{ row.official_nav_source }}</small>
              </td>
              <td data-label="LOF 收盘价">{{ fmt(row.market_price, 3) }}</td>
              <td data-label="结算估值">{{ fmt(row.settlement_nav) }}</td>
              <td :class="valueClass(row.settlement_premium_rate)" data-label="结算估值溢价率">{{ fmtRate(row.settlement_premium_rate) }}</td>
              <td data-label="交易估值">{{ fmt(row.trading_nav) }}</td>
              <td :class="valueClass(row.trading_premium_rate)" data-label="交易估值溢价率">{{ fmtRate(row.trading_premium_rate) }}</td>
              <td :class="valueClass(row.settlement_deviation_rate)" data-label="结算估值偏差（与净值）">{{ fmtRate(row.settlement_deviation_rate) }}</td>
              <td data-label="AG 合约">{{ row.active_contract || '—' }}</td>
              <td data-label="AG 交易价 / 盘中均价">{{ fmt(row.futures_price, 1) }} / {{ fmt(row.intraday_average, 1) }}</td>
              <td data-label="收盘时点">{{ closeTime(row) }}</td>
            </tr>
            <tr v-if="!history.rows.length">
              <td colspan="11" class="private-history-empty">尚无已保存的收盘估值点。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
