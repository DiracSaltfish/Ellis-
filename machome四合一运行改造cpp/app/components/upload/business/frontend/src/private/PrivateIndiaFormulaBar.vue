<script setup lang="ts">
import { computed } from 'vue'
import type {
  PrivateBasketValuation,
  PrivateIBQuoteInput,
  PrivateIndiaT2Input,
} from '../lib/privateTypes'

const props = defineProps<{
  variantKey: string
  india: PrivateIndiaT2Input
  indaQuote: PrivateIBQuoteInput
  valuation: PrivateBasketValuation
  domesticBid?: number
  domesticAsk?: number
}>()

const isNiftyBridge = computed(() => props.variantKey === 'nifty_bridge' && Boolean(props.india.nifty_bridge))
const anchorPrice = computed(() => props.india.anchors.reduce((total, anchor) => total + anchor.weight * anchor.price, 0))
const bridge = computed(() => props.india.nifty_bridge)

const proxyPrice = computed(() => {
  const niftyBridge = bridge.value
  if (!isNiftyBridge.value || !niftyBridge) {
    return { bid: props.indaQuote.bid, ask: props.indaQuote.ask }
  }
  const bid = validNumber(niftyBridge.inda_reference.bid)
    && validNumber(niftyBridge.nifty.bid)
    && validNumber(niftyBridge.nifty_reference.ask)
    ? niftyBridge.inda_reference.bid! * Math.pow(niftyBridge.nifty.bid! / niftyBridge.nifty_reference.ask!, niftyBridge.beta)
    : undefined
  const ask = validNumber(niftyBridge.inda_reference.ask)
    && validNumber(niftyBridge.nifty.ask)
    && validNumber(niftyBridge.nifty_reference.bid)
    ? niftyBridge.inda_reference.ask! * Math.pow(niftyBridge.nifty.ask! / niftyBridge.nifty_reference.bid!, niftyBridge.beta)
    : undefined
  return { bid, ask }
})

const fxRatio = computed(() => {
  const current = props.india.current_fx.rate
  const base = props.india.base_fx.rate
  return validNumber(current) && validNumber(base) ? current! / base! : undefined
})

function validNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0
}

function plainNumber(value: number | null | undefined, digits: number) {
  if (!validNumber(value)) return '—'
  return value.toFixed(digits)
}

function percent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(2)}%`
}
</script>

<template>
  <aside class="india-formula-bar" aria-label="当前估值公式与实时带入值">
    <div class="india-formula-title">
      <span>实时估值公式</span>
      <small>悬停变量查看带入值</small>
    </div>

    <div v-if="isNiftyBridge && bridge" class="india-formula-line">
      <span class="formula-name">合成 INDA 买价/卖价</span>
      <span>=</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">桥接 INDA 价</span>
        <span class="formula-token-value">{{ plainNumber(bridge.inda_reference.bid, 4) }} / {{ plainNumber(bridge.inda_reference.ask, 4) }}</span>
      </span>
      <span>×（</span>
      <span class="formula-token formula-token-accent" tabindex="0">
        <span class="formula-token-label">NIFTY 价</span>
        <span class="formula-token-value">{{ plainNumber(bridge.nifty.bid, 1) }} / {{ plainNumber(bridge.nifty.ask, 1) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">桥接 NIFTY 反向价</span>
        <span class="formula-token-value">{{ plainNumber(bridge.nifty_reference.ask, 1) }} / {{ plainNumber(bridge.nifty_reference.bid, 1) }}</span>
      </span>
      <span>）<sup>{{ plainNumber(bridge.beta, 0) }}</sup></span>
      <span class="formula-result">= {{ plainNumber(proxyPrice.bid, 4) }} / {{ plainNumber(proxyPrice.ask, 4) }}</span>
    </div>

    <div class="india-formula-line">
      <span class="formula-name">单位净值买价/卖价</span>
      <span>=</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">T−2 净值</span>
        <span class="formula-token-value">{{ plainNumber(india.base_nav, 6) }}</span>
      </span>
      <span>×［</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">静态占比</span>
        <span class="formula-token-value">{{ percent(india.static_ratio) }}</span>
      </span>
      <span>＋</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">风险占比</span>
        <span class="formula-token-value">{{ percent(india.investment_ratio) }}</span>
      </span>
      <span>×（</span>
      <span class="formula-token formula-token-accent" tabindex="0">
        <span class="formula-token-label">{{ isNiftyBridge ? '合成 INDA 价' : 'INDA 价' }}</span>
        <span class="formula-token-value">{{ plainNumber(proxyPrice.bid, 4) }} / {{ plainNumber(proxyPrice.ask, 4) }}</span>
      </span>
      <span>÷</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">T−2 加权锚点</span>
        <span class="formula-token-value">{{ plainNumber(anchorPrice, 6) }}</span>
      </span>
      <span>）×</span>
      <span class="formula-token" tabindex="0">
        <span class="formula-token-label">汇率比</span>
        <span class="formula-token-value">{{ plainNumber(fxRatio, 6) }}</span>
      </span>
      <span>］</span>
      <span class="formula-result">= {{ plainNumber(valuation.nav_bid, 4) }} / {{ plainNumber(valuation.nav_ask, 4) }}</span>
    </div>

    <div class="india-formula-line india-premium-line">
      <span class="formula-name">买入/卖出折溢价</span>
      <span>= ETF 卖一÷净值买价−1 / ETF 买一÷净值卖价−1</span>
      <span class="formula-substitution">
        {{ plainNumber(domesticAsk, 3) }}÷{{ plainNumber(valuation.nav_bid, 4) }}−1 /
        {{ plainNumber(domesticBid, 3) }}÷{{ plainNumber(valuation.nav_ask, 4) }}−1
      </span>
      <span class="formula-result">= {{ percent(valuation.buy_direction_premium_rate) }} / {{ percent(valuation.sell_direction_premium_rate) }}</span>
    </div>
  </aside>
</template>

<style scoped>
.india-formula-bar {
  flex: 1 1 720px;
  min-width: 0;
  padding: 8px 11px;
  border: 1px solid #d9e4f3;
  border-radius: 8px;
  background: linear-gradient(135deg, #f8fbff 0%, #fff 68%);
  color: #334155;
  font-size: 11px;
  line-height: 1.45;
}

.india-formula-title,
.india-formula-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px;
}

.india-formula-title {
  justify-content: space-between;
  margin-bottom: 3px;
  color: #24446f;
  font-weight: 700;
}

.india-formula-title small {
  color: #8290a6;
  font-size: 10px;
  font-weight: 500;
}

.india-formula-line + .india-formula-line {
  margin-top: 3px;
}

.formula-name {
  color: #172033;
  font-weight: 650;
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
  border-color: #f0b760;
  background: #fffaf0;
  color: #9a5713;
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

.india-premium-line {
  color: #56647a;
}

@media (max-width: 900px) {
  .india-formula-bar {
    flex-basis: 100%;
  }
}
</style>
