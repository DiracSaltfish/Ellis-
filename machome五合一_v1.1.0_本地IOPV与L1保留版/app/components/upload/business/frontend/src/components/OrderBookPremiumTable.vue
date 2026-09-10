<script setup lang="ts">
import { computed } from 'vue'
import type { EstimateRow, Quote, QuoteLevel } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'

const props = defineProps<{
  quote: Quote
  estimate: EstimateRow
  referenceQuote?: Quote
}>()

type OrderBookRow = {
  key: string
  label: string
  price: number
  volume?: number
  tone: 'limit' | 'touch' | 'normal'
}

const bidLevels = computed(() => cleanLevels(props.quote.bid_levels))
const askLevels = computed(() => cleanLevels(props.quote.ask_levels))
const hasOrderBook = computed(() => bidLevels.value.length > 0 || askLevels.value.length > 0)
const hasRealtimeEst = computed(() => props.estimate.realtime_est !== null && props.estimate.realtime_est !== undefined)
const unconfirmedEstimate = computed(() => !isValuationConfirmed(props.quote.symbol))

const estimateValues = computed(() => {
  const currentValue =
    hasRealtimeEst.value && props.estimate.realtime_est ? props.estimate.realtime_est : props.estimate.fair_est
  return {
    official: fmtPrice(props.estimate.official_est),
    current: fmtPrice(currentValue),
  }
})

const rows = computed<OrderBookRow[]>(() => {
  if (!hasOrderBook.value) return []
  const out: OrderBookRow[] = []
  const limitUp = props.quote.limit_up || limitPrice(1.1)
  if (limitUp > 0) {
    out.push({ key: 'limit-up', label: '涨停', price: limitUp, tone: 'limit' })
  }
  for (const level of [...askLevels.value].sort((a, b) => b.level - a.level)) {
    out.push({
      key: `ask-${level.level}`,
      label: `卖${level.level}`,
      price: level.price,
      volume: level.volume,
      tone: level.level === 1 ? 'touch' : 'normal',
    })
  }
  for (const level of bidLevels.value) {
    out.push({
      key: `bid-${level.level}`,
      label: `买${level.level}`,
      price: level.price,
      volume: level.volume,
      tone: level.level === 1 ? 'touch' : 'normal',
    })
  }
  const limitDown = props.quote.limit_down || limitPrice(0.9)
  if (limitDown > 0) {
    out.push({ key: 'limit-down', label: '跌停', price: limitDown, tone: 'limit' })
  }
  return out
})

function cleanLevels(levels: QuoteLevel[] | undefined) {
  return (levels || [])
    .filter((level) => level.price > 0 || level.volume > 0)
    .sort((a, b) => a.level - b.level)
}

function limitPrice(multiplier: number) {
  if (!props.quote.prev_close) return 0
  return Math.round(props.quote.prev_close * multiplier * 1000) / 1000
}

function premium(price: number, estimate: number | null | undefined) {
  if (!estimate || estimate <= 0) return null
  return (price / estimate - 1) * 100
}

function impliedReferencePrice(price: number) {
  if (!props.referenceQuote || props.referenceQuote.price <= 0 || props.estimate.fair_est <= 0) return null
  return (price / props.estimate.fair_est) * props.referenceQuote.price
}

function pctClass(value: number | null) {
  if (value === null) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function rowClass(row: OrderBookRow) {
  return {
    'orderbook-row-limit': row.tone === 'limit',
    'orderbook-row-touch': row.tone === 'touch',
  }
}

function fmtPrice(value: number | null | undefined, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  return value.toFixed(digits)
}

function fmtCompact(value: number | null | undefined, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  return value.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')
}

function fmtReference(value: number | null) {
  if (value === null || !Number.isFinite(value)) return ''
  const digits = Math.abs(value) >= 100 ? 2 : 3
  return fmtPrice(value, digits)
}

function fmtPct(value: number | null) {
  if (value === null || !Number.isFinite(value)) return ''
  if (Math.abs(value) < 0.005) return '0'
  return `${fmtCompact(value, 2)}%`
}

function fmtVolume(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value) || value <= 0) return ''
  return String(Math.round(value / 100))
}
</script>

<template>
  <section v-if="hasOrderBook" class="table-section orderbook-section">
    <h2 class="orderbook-title">
      <span :class="{ 'valuation-unconfirmed': !isValuationConfirmed(quote.symbol) }">{{ quote.symbol }}</span>
      <span>当前5档交易</span>
      <span class="orderbook-title-meta">
        相对于 EST
        <span>{{ estimateValues.official }}</span>
        <span>|</span>
        <span :class="{ 'valuation-unconfirmed': unconfirmedEstimate }">{{ estimateValues.current }}</span>
        的溢价
      </span>
    </h2>
    <div class="table-wrap orderbook-wrap">
      <table class="orderbook-table">
        <thead>
          <tr>
            <th class="sticky-col orderbook-col-side">交易</th>
            <th class="orderbook-col-price">价格</th>
            <th class="orderbook-col-volume">数量（手）</th>
            <th class="orderbook-col-official">T-2官方溢价</th>
            <th v-if="!hasRealtimeEst">T-1收盘时点溢价</th>
            <th v-if="hasRealtimeEst" class="orderbook-col-realtime">实时溢价</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key" :class="rowClass(row)">
            <td class="sticky-col code orderbook-col-side">{{ row.label }}</td>
            <td class="orderbook-price orderbook-col-price">{{ fmtPrice(row.price) }}</td>
            <td class="orderbook-col-volume">{{ fmtVolume(row.volume) }}</td>
            <td
              class="orderbook-col-official"
              :class="pctClass(premium(row.price, estimate.official_est))"
            >
              {{ fmtPct(premium(row.price, estimate.official_est)) }}
            </td>
            <td
              v-if="!hasRealtimeEst"
              :class="[
                pctClass(premium(row.price, estimate.fair_est)),
                { 'valuation-unconfirmed': unconfirmedEstimate },
              ]"
            >
              {{ fmtPct(premium(row.price, estimate.fair_est)) }}
            </td>
            <td
              v-if="hasRealtimeEst"
              class="orderbook-col-realtime"
              :class="[
                pctClass(premium(row.price, estimate.realtime_est)),
                { 'valuation-unconfirmed': unconfirmedEstimate },
              ]"
            >
              {{ fmtPct(premium(row.price, estimate.realtime_est)) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
