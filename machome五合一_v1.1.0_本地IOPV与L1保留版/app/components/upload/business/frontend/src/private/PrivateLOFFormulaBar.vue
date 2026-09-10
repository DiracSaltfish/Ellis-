<script setup lang="ts">
import { computed } from 'vue'
import type {
  PrivateBasketValuation,
  PrivateIBQuoteInput,
  PrivateLOFWeightedAnchorInput,
} from '../lib/privateTypes'

const props = defineProps<{
  lof: PrivateLOFWeightedAnchorInput
  xopQuote: PrivateIBQuoteInput
  valuation: PrivateBasketValuation
  domesticBid?: number
  domesticAsk?: number
}>()

const staticRatio = computed(() => 1 - props.lof.effective_ratio.value)
const fxRatio = computed(() => ratio(props.lof.current_fx.rate, props.lof.base_fx.rate))

function validNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0
}

function ratio(numerator: number | null | undefined, denominator: number | null | undefined) {
  return validNumber(numerator) && validNumber(denominator) ? numerator / denominator : undefined
}

function plainNumber(value: number | null | undefined, digits: number) {
  return validNumber(value) ? value.toFixed(digits) : '—'
}

function percent(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function sourceTitle(parts: Array<string | undefined>) {
  return parts.filter(Boolean).join(' · ')
}
</script>

<template>
  <aside class="lof-formula-bar" aria-label="XOP 收盘锚点 LOF 估值公式与实时带入值">
    <div class="lof-formula-line">
      <span class="formula-name">估值 NAV Bid</span>
      <span>=</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_nav_date, lof.base_nav_source])">
        <span class="formula-token-label">基准净值</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_nav, 6) }}</span>
      </span>
      <span>×［</span>
      <span class="formula-token" tabindex="0" :title="lof.effective_ratio.source">
        <span class="formula-token-label">静态占比</span>
        <span class="formula-token-value">{{ percent(staticRatio) }}</span>
      </span>
      <span>＋</span>
      <span class="formula-token" tabindex="0" :title="lof.effective_ratio.source">
        <span class="formula-token-label">有效 XOP 占比</span>
        <span class="formula-token-value">{{ percent(lof.effective_ratio.value) }}</span>
      </span>
      <span>×</span>
      <span class="formula-token formula-token-accent" tabindex="0" :title="sourceTitle([xopQuote.source, xopQuote.market_data_type, xopQuote.observed_at])">
        <span class="formula-token-label">当前 XOP Bid</span>
        <span class="formula-token-value">{{ plainNumber(xopQuote.bid, 4) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_reference.price_basis, lof.base_reference.observed_at, lof.base_reference.source])">
        <span class="formula-token-label">基准 XOP 收盘价</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_reference.price, 4) }}</span>
      </span>
      <span>×</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.current_fx.trading_day, lof.current_fx.source])">
        <span class="formula-token-label">T日中间价</span>
        <span class="formula-token-value">{{ plainNumber(lof.current_fx.rate, 6) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_fx.trading_day, lof.base_fx.source])">
        <span class="formula-token-label">基准日中间价</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_fx.rate, 6) }}</span>
      </span>
      <span>］</span>
      <span class="formula-result">= {{ plainNumber(valuation.nav_bid, 6) }}</span>
    </div>

    <div class="lof-formula-line">
      <span class="formula-name">估值 NAV Ask</span>
      <span>=</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_nav_date, lof.base_nav_source])">
        <span class="formula-token-label">基准净值</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_nav, 6) }}</span>
      </span>
      <span>×［</span>
      <span class="formula-token" tabindex="0" :title="lof.effective_ratio.source">
        <span class="formula-token-label">静态占比</span>
        <span class="formula-token-value">{{ percent(staticRatio) }}</span>
      </span>
      <span>＋</span>
      <span class="formula-token" tabindex="0" :title="lof.effective_ratio.source">
        <span class="formula-token-label">有效 XOP 占比</span>
        <span class="formula-token-value">{{ percent(lof.effective_ratio.value) }}</span>
      </span>
      <span>×</span>
      <span class="formula-token formula-token-accent" tabindex="0" :title="sourceTitle([xopQuote.source, xopQuote.market_data_type, xopQuote.observed_at])">
        <span class="formula-token-label">当前 XOP Ask</span>
        <span class="formula-token-value">{{ plainNumber(xopQuote.ask, 4) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_reference.price_basis, lof.base_reference.observed_at, lof.base_reference.source])">
        <span class="formula-token-label">基准 XOP 收盘价</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_reference.price, 4) }}</span>
      </span>
      <span>×</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.current_fx.trading_day, lof.current_fx.source])">
        <span class="formula-token-label">T日中间价</span>
        <span class="formula-token-value">{{ plainNumber(lof.current_fx.rate, 6) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0" :title="sourceTitle([lof.base_fx.trading_day, lof.base_fx.source])">
        <span class="formula-token-label">基准日中间价</span>
        <span class="formula-token-value">{{ plainNumber(lof.base_fx.rate, 6) }}</span>
      </span>
      <span>］</span>
      <span class="formula-result">= {{ plainNumber(valuation.nav_ask, 6) }}</span>
    </div>

    <div class="lof-formula-line lof-premium-line">
      <span class="formula-name">开仓 / 平仓溢价</span>
      <span>= 国内卖一÷NAV Bid−1 / 国内买一÷NAV Ask−1</span>
      <span class="formula-substitution">
        {{ plainNumber(domesticAsk, 3) }}÷{{ plainNumber(valuation.nav_bid, 6) }}−1 /
        {{ plainNumber(domesticBid, 3) }}÷{{ plainNumber(valuation.nav_ask, 6) }}−1
      </span>
      <span class="formula-result">
        = {{ percent(valuation.buy_direction_premium_rate, 4) }} / {{ percent(valuation.sell_direction_premium_rate, 4) }}
      </span>
    </div>
  </aside>
</template>

<style scoped>
.lof-formula-bar {
  min-width: 0;
  padding: 9px 11px;
  border: 1px solid #d9e4f3;
  border-radius: 8px;
  background: linear-gradient(135deg, #f7fbff 0%, #fff 68%);
  color: #334155;
  font-size: 11px;
  line-height: 1.5;
}

.lof-formula-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px;
}

.lof-formula-line + .lof-formula-line {
  margin-top: 4px;
}

.formula-name {
  color: #172033;
  font-weight: 700;
}

.formula-token {
  display: inline-grid;
  min-width: 58px;
  min-height: 20px;
  place-items: center;
  padding: 1px 6px;
  border: 1px solid #d8e0ec;
  border-radius: 5px;
  background: #fff;
  color: #56647a;
  cursor: help;
  font-variant-numeric: tabular-nums;
  outline: none;
}

.formula-token-accent {
  border-color: #eab86f;
  background: #fffaf0;
  color: #985711;
}

.formula-token-value {
  display: none;
  color: #0f766e;
  font-weight: 700;
}

.formula-token:hover .formula-token-label,
.formula-token:focus .formula-token-label {
  display: none;
}

.formula-token:hover .formula-token-value,
.formula-token:focus .formula-token-value {
  display: inline;
}

.formula-result {
  color: #0f766e;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
}

.formula-substitution {
  color: #64748b;
  font-variant-numeric: tabular-nums;
}

.lof-premium-line {
  color: #56647a;
}
</style>
