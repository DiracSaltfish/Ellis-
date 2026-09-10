<script setup lang="ts">
import { computed } from 'vue'
import type { PrivateIndiaHistoryReviewResponse, PrivateIndiaHistoryReviewRow } from '../lib/privateTypes'

const props = defineProps<{
  history: PrivateIndiaHistoryReviewResponse
}>()

defineEmits<{
  backToFund: [symbol: string]
  openNiftyBridgeReview: [symbol: string]
}>()

const latestOfficialRow = computed(() => props.history.rows.find((row) => positive(row.official_nav)) || null)
const latestFittedRow = computed(() => props.history.rows.find((row) => fittedExposure(row) !== null) || null)
const latestDeviationRow = computed(() => props.history.rows.find((row) => deviationPct(row) !== null) || null)
const averageAbsoluteDeviation = computed(() => {
  const values = props.history.rows
    .map((row) => deviationPct(row))
    .filter((value): value is number => value !== null)
  if (!values.length) return null
  return values.reduce((sum, value) => sum + Math.abs(value), 0) / values.length
})

function positive(value: number | null | undefined) {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0
}

function deviationPct(row: PrivateIndiaHistoryReviewRow) {
  const value = row.final_deviation_pct
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null
}

function fittedExposure(row: PrivateIndiaHistoryReviewRow) {
  const value = row.fitted_exposure
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null
}

function windowMAPE(row: PrivateIndiaHistoryReviewRow) {
  const value = row.window_mape_pct
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null
}

function fmt(value: number | null | undefined, digits = 4) {
  return value !== null && value !== undefined && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function fmtPct(value: number | null | undefined, digits = 2) {
  return value !== null && value !== undefined && Number.isFinite(value) ? `${value.toFixed(digits)}%` : '—'
}

function fmtExposure(value: number | null | undefined) {
  return value !== null && value !== undefined && Number.isFinite(value) ? `${value.toFixed(3)}×` : '—'
}

function fmtRatio(value: number | null | undefined) {
  return value !== null && value !== undefined && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—'
}

function valueClass(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function statusLabel(row: PrivateIndiaHistoryReviewRow) {
  if (row.status === 'ok') return '已对照官方净值'
  if (row.status === 'pending_official_nav') return '待公布官方净值'
  if (row.status === 'missing_t2_base_nav') return '缺 T−2 基准'
  return row.status
}
</script>

<template>
  <section class="section private-india-review-page">
    <a :href="`/private/${history.symbol}`" class="private-detail-back" @click.prevent="$emit('backToFund', history.symbol)">← 返回 Private 标的详情</a>

    <section class="detail-panel private-detail-panel">
      <div class="detail-header-top">
        <p class="eyebrow">历史复盘 · 印度基金 LOF 最终净值</p>
        <div class="detail-header-actions">
          <button type="button" class="private-share-history-link private-history-review-link" @click="$emit('openNiftyBridgeReview', history.symbol)">NIFTY 桥接误差 →</button>
          <span class="confirmation-badge">{{ history.model_version }}</span>
        </div>
      </div>
      <h2><span class="detail-title-part">{{ history.symbol }}</span>{{ history.name }} 历史估值复盘</h2>
      <p class="model-note">{{ history.methodology_note }}</p>
      <p class="model-note private-review-separation-note">中国盘中的 NIFTY 代理桥 IOPV 继续在标的详情页展示；本页只用于收盘后最终净值误差复盘。</p>
    </section>

    <section class="summary-grid private-review-summary">
      <article class="metric-card"><span>复盘样本</span><strong>{{ history.rows.length }} 日</strong></article>
      <article class="metric-card"><span>最新官方净值</span><strong>{{ fmt(latestOfficialRow?.official_nav) }}</strong></article>
      <article class="metric-card"><span>最新最终估值</span><strong>{{ fmt(latestDeviationRow?.final_estimate_nav) }}</strong></article>
      <article class="metric-card"><span>最新估值偏差</span><strong :class="valueClass(latestDeviationRow ? deviationPct(latestDeviationRow) : null)">{{ fmtPct(latestDeviationRow ? deviationPct(latestDeviationRow) : null) }}</strong></article>
      <article class="metric-card"><span>滚动校准暴露</span><strong>{{ fmtExposure(latestFittedRow ? fittedExposure(latestFittedRow) : null) }}</strong></article>
      <article class="metric-card"><span>滚动窗口 MAPE</span><strong>{{ fmtPct(latestFittedRow ? windowMAPE(latestFittedRow) : null) }}</strong></article>
      <article class="metric-card"><span>区间平均绝对偏差</span><strong>{{ fmtPct(averageAbsoluteDeviation) }}</strong></article>
    </section>

    <section class="table-section private-review-table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>T−2 基准推 T 日四时点最终估值</h2>
          <p class="table-inline-note">加权锚点已包含日/港/欧/美对应收盘时间；拟合暴露仅是收盘复盘的收益率校准系数，不代表基金实际仓位或交易建议。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="private-review-table">
          <thead>
            <tr>
              <th class="sticky-col">目标日 (T)</th>
              <th>官方净值 (T)</th>
              <th>T−2 官方净值</th>
              <th>T 日最终估值</th>
              <th>估值偏差</th>
              <th>风险资产比例</th>
              <th>T−2 / T 加权锚点</th>
              <th>T−2 / T 汇率</th>
              <th>滚动校准暴露</th>
              <th>窗口 MAPE</th>
              <th>状态 / 说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in history.rows" :key="row.target_date">
              <td class="sticky-col" data-label="目标日 (T)">{{ row.target_date }}</td>
              <td data-label="官方净值 (T)">{{ fmt(row.official_nav) }}</td>
              <td data-label="T−2 官方净值">{{ fmt(row.base_nav) }}<small v-if="row.base_nav_date">{{ row.base_nav_date }}</small></td>
              <td data-label="T 日最终估值">{{ fmt(row.final_estimate_nav) }}</td>
              <td :class="valueClass(deviationPct(row))" data-label="估值偏差">{{ fmtPct(deviationPct(row)) }}</td>
              <td data-label="风险资产比例">{{ fmtRatio(row.investment_ratio) }}<small v-if="row.static_ratio">静态 {{ fmtRatio(row.static_ratio) }}</small></td>
              <td data-label="T−2 / T 加权锚点">{{ fmt(row.base_anchor_price) }} / {{ fmt(row.target_anchor_price) }}</td>
              <td data-label="T−2 / T 汇率">{{ fmt(row.base_fx, 4) }} / {{ fmt(row.target_fx, 4) }}</td>
              <td data-label="滚动校准暴露">{{ fmtExposure(fittedExposure(row)) }}<small v-if="row.fit_window_size">{{ row.fit_window_size }} 日窗口</small></td>
              <td data-label="窗口 MAPE">{{ fmtPct(windowMAPE(row)) }}</td>
              <td data-label="状态 / 说明">{{ statusLabel(row) }}<small v-if="row.note">{{ row.note }}</small><small v-else-if="row.source">{{ row.source }}</small></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
