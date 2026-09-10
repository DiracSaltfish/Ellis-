<script setup lang="ts">
import { ref } from 'vue'
import type { FundSnapshot, ValuationInput } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'
import { shouldHideValuationRawPrices } from '../lib/valuationRawPriceVisibility'
import FundDetailJumpLinks from './FundDetailJumpLinks.vue'
import OrderBookPremiumTable from './OrderBookPremiumTable.vue'
import QuoteTable from './QuoteTable.vue'
import ValuationMinuteChart from './ValuationMinuteChart.vue'

defineProps<{ fund: FundSnapshot }>()

defineEmits<{
  selectBranch: [key: string]
  selectFund: [symbol: string]
  showRatioHistory: [symbol: string]
  showShareHistory: [symbol: string]
}>()

const minuteChartSpan = ref<'1d' | '3d' | '5d'>('1d')

function fmt(value: number, digits = 4) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(digits)
}

function fmtOptional(value: number | undefined, digits = 4) {
  if (value === undefined || value === null || !Number.isFinite(value)) return ''
  return fmt(value, digits)
}

function fmtRatio(value: number | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value) || value === 0) return ''
  return `${fmt(value, 2)}%`
}

function fmtPct(value: number | null | undefined, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) return ''
  return `${fmt(value, digits)}%`
}

function fmtEffectiveRatio(value: number | null | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value) || value <= 0) return ''
  return `${fmt(value * 100, 2)}%`
}

function pctClass(value: number) {
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function hasRealtimeEstimate(fund: FundSnapshot) {
  return fund.estimate?.realtime_est !== null && fund.estimate?.realtime_est !== undefined
}

function inputStatus(input: ValuationInput) {
  return input.realtime_status || (input.used ? 'used' : 'missing')
}

function inputStatusClass(input: ValuationInput) {
  const status = inputStatus(input)
  if (!input.used || status === 'stale' || status === 'unsupported' || status === 'missing') return 'warning'
  return 'ok'
}

function hideRawPriceDisplay(fund: FundSnapshot, input: ValuationInput) {
  return shouldHideValuationRawPrices(fund.symbol) && (
    input.role === '持仓标的' ||
    input.role === '连续价格尺度'
  )
}

function hasShareHistory(fund: FundSnapshot) {
  return fund.symbol.startsWith('SZ') || fund.symbol.startsWith('SH')
}

</script>

<template>
  <section class="section">
    <section class="detail-panel">
      <div>
        <div class="detail-header-top">
          <p class="eyebrow">标的详情</p>
          <div class="detail-header-actions">
            <span
              v-if="isValuationConfirmed(fund.symbol)"
              class="confirmation-badge"
            >
              估值方案已确认
            </span>
            <span
              v-if="fund.estimate?.effective_ratio"
              class="ratio-badge"
              :class="{ 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) }"
            >
              当前估值仓位 {{ fmtEffectiveRatio(fund.estimate.effective_ratio) }}
            </span>
          </div>
        </div>
        <h2>
          <span
            class="detail-title-part"
            :class="{ 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) }"
          >
            {{ fund.symbol }}
          </span>
          <span :class="{ 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) }">
            {{ fund.quote.name }}
          </span>
        </h2>
      </div>
      <div class="detail-metrics">
        <div>
          <span>现价</span>
          <strong>{{ fmt(fund.quote.price) }}</strong>
        </div>
        <div>
          <span>涨幅</span>
          <strong :class="pctClass(fund.quote.change_pct)">{{ fmt(fund.quote.change_pct, 2) }}%</strong>
        </div>
        <div v-if="fund.estimate">
          <span>{{ hasRealtimeEstimate(fund) ? '实时溢价' : 'T-1收盘时点溢价' }}</span>
          <strong
            :class="[
              pctClass(hasRealtimeEstimate(fund) ? (fund.estimate?.realtime_premium ?? 0) : fund.estimate.fair_premium),
              { 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) },
            ]"
          >
            {{
              hasRealtimeEstimate(fund)
                ? fmtPct(fund.estimate?.realtime_premium)
                : fmtPct(fund.estimate.fair_premium)
            }}
          </strong>
        </div>
        <div v-if="fund.estimate">
          <span>T-2官方公布净值</span>
          <strong>
            {{ fmt(fund.estimate.official_est) }}
          </strong>
        </div>
        <div v-if="fund.estimate">
          <span>T-2官方溢价</span>
          <strong :class="pctClass(fund.estimate.official_premium)">
            {{ fmt(fund.estimate.official_premium, 2) }}%
          </strong>
        </div>
        <div v-if="fund.estimate && !hasRealtimeEstimate(fund)">
          <span>T-1收盘时点估值</span>
          <strong :class="{ 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) }">
            {{ fmt(fund.estimate.fair_est) }}
          </strong>
        </div>
        <div v-if="fund.estimate && hasRealtimeEstimate(fund)">
          <span>实时估值</span>
          <strong :class="{ 'valuation-unconfirmed': !isValuationConfirmed(fund.symbol) }">
            {{ fmt(fund.estimate?.realtime_est ?? 0) }}
          </strong>
        </div>
        <div>
          <span>状态</span>
          <strong>{{ fund.quote.realtime_status || 'unknown' }}</strong>
        </div>
      </div>
      <p v-if="fund.estimate?.note" class="model-note">{{ fund.estimate.note }}</p>
    </section>

    <div
      v-if="fund.estimate"
      class="detail-secondary-grid"
      :class="{ 'detail-secondary-grid-expanded': minuteChartSpan !== '1d' }"
    >
      <OrderBookPremiumTable
        class="detail-secondary-orderbook"
        :quote="fund.quote"
        :estimate="fund.estimate"
        :reference-quote="fund.valuation_reference"
      />
      <ValuationMinuteChart
        v-if="fund.estimate"
        class="detail-secondary-chart"
        :fund="fund"
        :day-span="minuteChartSpan"
        @update:day-span="minuteChartSpan = $event"
      />
    </div>

    <FundDetailJumpLinks
      :show-share-history="hasShareHistory(fund)"
      @open-ratio-history="$emit('showRatioHistory', fund.symbol)"
      @open-share-history="$emit('showShareHistory', fund.symbol)"
    />

    <section v-if="fund.valuation_inputs?.length" class="table-section valuation-input-section">
      <h2>估值原始数据</h2>
      <div class="table-wrap valuation-input-wrap">
        <table class="valuation-input-table">
          <thead>
            <tr>
              <th class="sticky-col">类型</th>
              <th>代码</th>
              <th>名称</th>
              <th>比例</th>
              <th>仓位</th>
              <th>当前价</th>
              <th>官方价</th>
              <th>行情时间</th>
              <th>净值/基准日</th>
              <th>基准价</th>
              <th>基准来源</th>
              <th>状态</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="input in fund.valuation_inputs"
              :key="`${input.role}-${input.symbol}-${input.reference_symbol || ''}`"
              :class="{ 'valuation-input-unused': !input.used }"
            >
              <td class="sticky-col" data-label="类型">{{ input.role }}</td>
              <td class="code" data-label="代码">{{ input.symbol }}</td>
              <td class="name" data-label="名称">{{ input.name }}</td>
              <td data-label="比例">{{ fmtRatio(input.weight_ratio) }}</td>
              <td data-label="仓位">{{ fmtOptional(input.position, 4) }}</td>
              <td data-label="当前价">{{ hideRawPriceDisplay(fund, input) ? '--' : fmtOptional(input.current_price, 4) }}</td>
              <td data-label="官方价">{{ hideRawPriceDisplay(fund, input) ? '--' : fmtOptional(input.official_price, 4) }}</td>
              <td data-label="行情时间">{{ input.quote_date }} {{ input.quote_time }}</td>
              <td data-label="净值/基准日">{{ input.base_date || input.holding_date }}</td>
              <td data-label="基准价">{{ hideRawPriceDisplay(fund, input) ? '--' : fmtOptional(input.base_price, 4) }}</td>
              <td class="debug-details" data-label="基准来源">{{ input.base_source || input.source }}</td>
              <td :class="inputStatusClass(input)" data-label="状态">{{ inputStatus(input) }}</td>
              <td class="debug-details" data-label="说明">{{ input.note || input.stale_reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <QuoteTable
      :rows="[fund.quote]"
      title="当前行情"
      :mark-valuation-status="true"
      @select="$emit('selectFund', $event)"
    />
  </section>
</template>
