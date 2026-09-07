<script setup lang="ts">
import { computed } from 'vue'
import type { PrivateBasketValuation, PrivateFundSnapshot, PrivateOrderBookValuation } from '../lib/privateTypes'

const props = defineProps<{
  snapshot: PrivateFundSnapshot
  valuation?: PrivateBasketValuation
  orderBook?: PrivateOrderBookValuation[]
  valuationLabel?: string
  hideValuationLabel?: boolean
}>()

const rows = computed(() => props.orderBook ?? props.snapshot.order_book ?? [])
const quote = computed(() => props.snapshot.domestic_quote)
const valuation = computed(() => props.valuation ?? props.snapshot.valuation)
const usesSilverSettlement = computed(() => (
  props.snapshot.valuation_kind === 'silver_settlement'
  || props.snapshot.silver_valuation !== undefined
  || props.snapshot.model_version.includes('.ag-settlement.')
))

function formatPrice(value: number) {
  return Number.isFinite(value) ? value.toFixed(3) : '—'
}

function formatVolume(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '—'
  return String(Math.round(value / 100))
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return '—'
  if (Math.abs(value) < 0.00005) return '0%'
  return `${(value * 100).toFixed(2)}%`
}

function rateClass(value: number) {
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function settlementPremium(price: number) {
  const settlementNAV = props.snapshot.silver_valuation?.settlement_nav
  if (!Number.isFinite(price) || !Number.isFinite(settlementNAV) || !settlementNAV || settlementNAV <= 0) {
    return Number.NaN
  }
  return price / settlementNAV - 1
}

function sideLabel(side: string, level: number) {
  return `${side === 'ask' ? '卖' : '买'}${level}`
}

function rowClass(side: string, level: number) {
  return {
    'private-orderbook-touch': level === 1,
    'private-orderbook-buy-touch': side === 'ask' && level === 1,
    'private-orderbook-sell-touch': side === 'bid' && level === 1,
  }
}
</script>

<template>
  <section v-if="valuation && rows.length" class="table-section orderbook-section private-orderbook-section">
    <h2 class="orderbook-title">
      <span class="detail-title-part">{{ snapshot.symbol }}</span>
      <span>当前5档交易</span>
      <span v-if="!hideValuationLabel" class="orderbook-title-meta">
        {{ valuationLabel || '相对于 Basket Bid / Ask NAV 的溢价' }}
      </span>
    </h2>
    <div class="table-wrap orderbook-wrap">
      <table
        class="orderbook-table private-orderbook-table"
        :class="{ 'private-silver-orderbook-table': usesSilverSettlement }"
      >
        <thead>
          <tr>
            <th class="sticky-col orderbook-col-side">交易</th>
            <th class="orderbook-col-price">价格</th>
            <th class="orderbook-col-volume">数量（手）</th>
            <th class="private-orderbook-bid-nav">{{ usesSilverSettlement ? 'Bid交易溢价' : 'Bid NAV 溢价' }}</th>
            <th class="private-orderbook-ask-nav">{{ usesSilverSettlement ? 'Ask交易溢价' : 'Ask NAV 溢价' }}</th>
            <th v-if="usesSilverSettlement" class="private-orderbook-settlement-nav">结算溢价率</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="`${row.side}-${row.level}`"
            :class="rowClass(row.side, row.level)"
          >
            <td class="sticky-col code orderbook-col-side" :class="row.side === 'ask' ? 'private-ask-label' : 'private-bid-label'">
              {{ sideLabel(row.side, row.level) }}
            </td>
            <td class="orderbook-price orderbook-col-price">{{ formatPrice(row.price) }}</td>
            <td class="orderbook-col-volume">{{ formatVolume(row.volume) }}</td>
            <td class="private-orderbook-bid-nav" :class="rateClass(row.premium_rate_vs_basket_bid_nav)">
              {{ formatPercent(row.premium_rate_vs_basket_bid_nav) }}
            </td>
            <td class="private-orderbook-ask-nav" :class="rateClass(row.premium_rate_vs_basket_ask_nav)">
              {{ formatPercent(row.premium_rate_vs_basket_ask_nav) }}
            </td>
            <td
              v-if="usesSilverSettlement"
              class="private-orderbook-settlement-nav"
              :class="rateClass(settlementPremium(row.price))"
            >
              {{ formatPercent(settlementPremium(row.price)) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="quote?.stale_reason || quote?.error" class="private-table-note">
      {{ quote.stale_reason || quote.error }}
    </p>
  </section>
  <section v-else class="table-section private-table-empty">
    Sina 五档或私有双侧 NAV 尚未齐备，暂不能展开盘口溢价表。
  </section>
</template>
