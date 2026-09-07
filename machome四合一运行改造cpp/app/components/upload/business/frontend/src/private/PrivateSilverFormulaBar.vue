<script setup lang="ts">
import type { PrivateSilverSettlementInput, PrivateSilverSettlementValuation } from '../lib/privateTypes'

defineProps<{
  silver: PrivateSilverSettlementInput
  valuation: PrivateSilverSettlementValuation
  marketPrice?: number
}>()

function number(value: number | null | undefined, digits: number) {
  return value !== null && value !== undefined && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function percent(value: number | null | undefined) {
  return value !== null && value !== undefined && Number.isFinite(value) ? `${(value * 100).toFixed(4)}%` : '—'
}
</script>

<template>
  <aside class="silver-formula-bar" aria-label="白银基金结算与交易双估值公式">
    <div class="silver-formula-line">
      <strong>基金净值估值</strong>
      <span>=</span>
      <span class="token" :title="`${silver.base_nav_date} · ${silver.base_nav_source}`">昨日净值 {{ number(silver.base_nav, 6) }}</span>
      <span>÷</span>
      <span class="token" :title="silver.previous_settlement_source">{{ silver.contract }} 昨结 {{ number(silver.previous_settlement, 3) }}</span>
      <span>×</span>
      <span class="token accent" :title="`${silver.intraday_average_basis} · ${silver.observed_at}`">盘中均价 {{ number(silver.intraday_average, 3) }}</span>
      <span class="result">= {{ number(valuation.settlement_nav, 6) }}</span>
      <span class="premium">LOF现价折溢价 {{ percent(valuation.settlement_premium_rate) }}</span>
    </div>
    <div class="silver-formula-line">
      <strong>基金盘中交易估值</strong>
      <span>=</span>
      <span class="token">昨日净值 {{ number(silver.base_nav, 6) }}</span>
      <span>÷</span>
      <span class="token">{{ silver.contract }} 昨结 {{ number(silver.previous_settlement, 3) }}</span>
      <span>×</span>
      <span class="token accent" :title="`${silver.source} · ${silver.observed_at}`">当前交易价 {{ number(silver.futures_price, 3) }}</span>
      <span class="result">= {{ number(valuation.trading_nav, 6) }}</span>
      <span class="premium">LOF现价 {{ number(marketPrice, 4) }} · 折溢价 {{ percent(valuation.trading_premium_rate) }}</span>
    </div>
  </aside>
</template>

<style scoped>
.silver-formula-bar {
  min-width: 0;
  padding: 9px 11px;
  border: 1px solid #e1d7ef;
  border-radius: 8px;
  background: linear-gradient(135deg, #fbf8ff 0%, #fff 68%);
  color: #475569;
  font-size: 11px;
  line-height: 1.5;
}

.silver-formula-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.silver-formula-line + .silver-formula-line { margin-top: 5px; }
.silver-formula-line strong { color: #312e81; }
.token { padding: 1px 6px; border: 1px solid #ddd6e8; border-radius: 5px; background: #fff; font-variant-numeric: tabular-nums; }
.token.accent { border-color: #c4b5fd; background: #faf5ff; color: #6d28d9; }
.result { color: #0f766e; font-weight: 750; font-variant-numeric: tabular-nums; }
.premium { color: #92400e; font-variant-numeric: tabular-nums; }
</style>
