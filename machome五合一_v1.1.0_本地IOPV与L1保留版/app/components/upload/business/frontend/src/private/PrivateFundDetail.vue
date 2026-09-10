<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PrivateFundSnapshot, PrivateLevel } from '../lib/privateTypes'
import PrivateIndiaFormulaBar from './PrivateIndiaFormulaBar.vue'
import PrivateLOFFormulaBar from './PrivateLOFFormulaBar.vue'
import PrivateSilverFormulaBar from './PrivateSilverFormulaBar.vue'
import PrivateOrderBookPremiumTable from './PrivateOrderBookPremiumTable.vue'
import PrivateValuationMinuteChart from './PrivateValuationMinuteChart.vue'

const props = defineProps<{
  snapshot: PrivateFundSnapshot
}>()

defineEmits<{
  back: []
	'open-india-history-review': [symbol: string]
  'open-india-nifty-history-review': [symbol: string]
  'open-silver-close-history': [symbol: string]
}>()

const valuation = computed(() => props.snapshot.valuation)
const input = computed(() => props.snapshot.input)
const quote = computed(() => props.snapshot.domestic_quote)
const components = computed(() => props.snapshot.components ?? [])
const india = computed(() => input.value?.india)
const lof = computed(() => input.value?.lof)
const silver = computed(() => input.value?.silver)
const silverValuation = computed(() => props.snapshot.silver_valuation)
const indiaValuations = computed(() => props.snapshot.india_valuations)
const usesLOFWeightedAnchor = computed(() => (
  props.snapshot.valuation_kind === 'lof_weighted_anchor'
  || lof.value !== undefined
  || props.snapshot.model_version === 'private.weighted-nav.xop-safe.us-close.v1'
))
const lofDataFresh = computed(() => (
  usesLOFWeightedAnchor.value
  && props.snapshot.ready
  && props.snapshot.warnings.length === 0
))
const usesSilverSettlement = computed(() => (
  props.snapshot.valuation_kind === 'silver_settlement'
  || silver.value !== undefined
  || props.snapshot.model_version.includes('.ag-settlement.')
))
const usesIndiaT2 = computed(() => india.value !== undefined || props.snapshot.model_version.includes('.t2-multimarket.inda-'))
const usesMultiMarket = computed(() => !usesIndiaT2.value && !usesLOFWeightedAnchor.value && !usesSilverSettlement.value && (components.value.length > 0 || props.snapshot.model_version.includes('full-cash-substitution')))
const referenceSymbol = computed(() => usesSilverSettlement.value ? (silver.value?.contract || 'AG') : (usesIndiaT2.value ? 'INDA' : (input.value?.ib.symbol || lof.value?.base_reference.symbol || 'XOP')))
const fxLabel = computed(() => {
  const fx = input.value?.fx
  if (fx?.source === 'CFETS_PREOPEN_FALLBACK') return `${fx.pair} 前一有效交易日即期回退（仅展示、禁止执行）`
  if (fx?.source === 'CFETS_SPOT_RATE') return `${fx.pair} 即期 BID/ASK 中间价`
  return `${fx?.pair || 'USD/CNY'} 小时参考价`
})
const historicalCalibration = ref<{ pcfTradingDay: string; xopEquivalentShares: number; minute: string } | null>(null)
const pcfDerivedXOPEquivalent = computed(() => {
  if (props.snapshot.symbol !== 'SH513350' || usesMultiMarket.value) return null
  const liveShares = valuation.value?.xop_equivalent_shares
  if (liveShares && Number.isFinite(liveShares) && liveShares > 0) {
    return {
      shares: liveShares,
      pcfTradingDay: input.value?.pcf.trading_day || '',
      historical: false,
      minute: '',
    }
  }
  const historical = historicalCalibration.value
  if (!historical || !Number.isFinite(historical.xopEquivalentShares) || historical.xopEquivalentShares <= 0) return null
  return {
    shares: historical.xopEquivalentShares,
    pcfTradingDay: historical.pcfTradingDay,
    historical: true,
    minute: historical.minute,
  }
})
const usesPCFDerivedXOPEquivalent = computed(() => pcfDerivedXOPEquivalent.value !== null)
const bestAsk = computed(() => bestLevel(quote.value?.ask_levels))
const bestBid = computed(() => bestLevel(quote.value?.bid_levels))
const minuteChartSpan = ref<1 | 3 | 5>(1)
const selectedIndiaVariantKey = ref('nifty_bridge')
const indiaVariantInitialized = ref(false)
const selectedIndiaVariant = computed(() => {
  const variants = indiaValuations.value
  if (!variants) return undefined
  if (selectedIndiaVariantKey.value === 'nifty_bridge' && variants.nifty_bridge) return variants.nifty_bridge
  return variants.direct_inda
})
const chartReferenceLabel = computed(() => {
  if (usesIndiaT2.value) return selectedIndiaVariant.value?.quote_symbol || 'INDA'
  if (usesLOFWeightedAnchor.value) return 'XOP'
  if (usesSilverSettlement.value) return silver.value?.contract || 'AG'
  if (usesMultiMarket.value) return '成分篮子'
  return referenceSymbol.value
})
const displayValuation = computed(() => selectedIndiaVariant.value?.valuation ?? valuation.value)
const displayOrderBook = computed(() => selectedIndiaVariant.value?.order_book ?? props.snapshot.order_book)
const lofStaticRatio = computed(() => lof.value ? 1 - lof.value.effective_ratio.value : undefined)
const lofFXRatio = computed(() => {
  const base = lof.value?.base_fx.rate
  const current = lof.value?.current_fx.rate
  return base && current && Number.isFinite(base) && Number.isFinite(current) ? current / base : undefined
})
const lofXOPBidRatio = computed(() => {
  const base = lof.value?.base_reference.price
  const current = input.value?.ib.bid
  return base && current && Number.isFinite(base) && Number.isFinite(current) ? current / base : undefined
})
const lofXOPAskRatio = computed(() => {
  const base = lof.value?.base_reference.price
  const current = input.value?.ib.ask
  return base && current && Number.isFinite(base) && Number.isFinite(current) ? current / base : undefined
})
const lofStaticContribution = computed(() => (
  lof.value && lofStaticRatio.value !== undefined
    ? lof.value.base_nav * lofStaticRatio.value
    : undefined
))
const lofRiskBidContribution = computed(() => (
  lof.value && lofFXRatio.value !== undefined && lofXOPBidRatio.value !== undefined
    ? lof.value.base_nav * lof.value.effective_ratio.value * lofXOPBidRatio.value * lofFXRatio.value
    : undefined
))
const lofRiskAskContribution = computed(() => (
  lof.value && lofFXRatio.value !== undefined && lofXOPAskRatio.value !== undefined
    ? lof.value.base_nav * lof.value.effective_ratio.value * lofXOPAskRatio.value * lofFXRatio.value
    : undefined
))
const selectedIndiaReferenceQuote = computed(() => {
  if (!usesIndiaT2.value) return undefined
  if (selectedIndiaVariantKey.value === 'nifty_bridge' && india.value?.nifty_bridge) {
    return {
      label: 'NIFTY 参考价 Bid / Ask',
      bid: india.value.nifty_bridge.nifty.bid,
      ask: india.value.nifty_bridge.nifty.ask,
      digits: 1,
    }
  }
  return {
    label: 'INDA 参考价 Bid / Ask',
    bid: input.value?.ib.bid,
    ask: input.value?.ib.ask,
    digits: 4,
  }
})

watch(indiaValuations, (variants) => {
  if (!variants) {
    selectedIndiaVariantKey.value = 'direct_inda'
    indiaVariantInitialized.value = false
    return
  }
  const selectedExists = selectedIndiaVariantKey.value === 'direct_inda' || Boolean(variants.nifty_bridge)
  if (!indiaVariantInitialized.value || !selectedExists) {
    selectedIndiaVariantKey.value = variants.nifty_bridge ? 'nifty_bridge' : 'direct_inda'
    indiaVariantInitialized.value = true
  }
}, { immediate: true })

const shanghaiDateTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const safePCFSourceURL = computed(() => {
  if (usesIndiaT2.value || usesLOFWeightedAnchor.value || usesSilverSettlement.value) return ''
  const raw = input.value?.pcf.source_url?.trim()
  if (!raw) return ''
  try {
    const parsed = new URL(raw)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : ''
  } catch {
    return ''
  }
})

const modelNote = computed(() => {
  const currentInput = input.value
  if (!currentInput) {
    const historical = pcfDerivedXOPEquivalent.value
    if (historical?.historical) {
      return `当前实时输入未就绪；展示 PCF ${historical.pcfTradingDay} 的最近审计历史校准（不可操作）`
    }
    return props.snapshot.model_version
  }
  if (usesLOFWeightedAnchor.value && lof.value) {
    return [
      `基准 NAV ${formatNumber(lof.value.base_nav, 6)}（${lof.value.base_nav_date}）`,
      `XOP 美东常规盘收盘锚点 ${formatNumber(lof.value.base_reference.price, 4)}（目标 ${formatDateTime(lof.value.base_reference.target_at)} / 实际 ${formatDateTime(lof.value.base_reference.observed_at)}）`,
      `当前 XOP ${formatNumber(currentInput.ib.bid, 4)} / ${formatNumber(currentInput.ib.ask, 4)}（${currentInput.ib.market_data_type || 'unknown'}）`,
      `SAFE USD/CNY ${formatNumber(lof.value.current_fx.rate, 6)} / 基准 ${formatNumber(lof.value.base_fx.rate, 6)}`,
      `有效 XOP 占比 ${formatPercent(lof.value.effective_ratio.value)} / 静态占比 ${formatPercent(lofStaticRatio.value)}`,
      `模型 ${props.snapshot.model_version}`,
    ].join('，')
  }
  if (usesSilverSettlement.value && silver.value) {
    return [
      `基准 NAV ${formatNumber(silver.value.base_nav, 6)}（${silver.value.base_nav_date}）`,
      `${silver.value.contract} 昨结 ${formatNumber(silver.value.previous_settlement, 3)}`,
      `盘中均价 ${formatNumber(silver.value.intraday_average, 3)} / 交易价 ${formatNumber(silver.value.futures_price, 3)}`,
      `换月规则：每个偶数月 10 日切换下一合约，不做基差调整`,
      `模型 ${props.snapshot.model_version}`,
    ].join('，')
  }
  if (usesIndiaT2.value && india.value) {
    const anchorSummary = india.value.anchors
      .map((anchor) => `${anchor.label} ${(anchor.weight * 100).toFixed(2)}%`)
      .join(' / ')
    const bridge = india.value.nifty_bridge
    return [
      `T−2 NAV ${formatNumber(india.value.base_nav, 6)}（${india.value.base_nav_date}）`,
      `SAFE USD/CNY ${formatNumber(india.value.current_fx.rate, 6)} / 锚点 ${formatNumber(india.value.base_fx.rate, 6)}`,
      `INDA ${formatNumber(currentInput.ib.bid, 4)} / ${formatNumber(currentInput.ib.ask, 4)}（${currentInput.ib.market_data_type || 'unknown'}）`,
      bridge
        ? `NIFTY ${formatNumber(bridge.nifty.bid, 1)} / ${formatNumber(bridge.nifty.ask, 1)}，桥接 ${formatDateTime(bridge.reference_at)}，β=${formatNumber(bridge.beta, 2)}，${bridge.roll_adjustment ? `换月 ${bridge.roll_adjustment.old_contract}→${bridge.roll_adjustment.new_contract} ${bridge.roll_adjustment.direction}` : '最后一个周二起使用下月合约'}`
        : 'NIFTY 桥接参考尚未采集（仅显示直接 INDA）',
      anchorSummary,
      `模型 ${props.snapshot.model_version}`,
    ].join('，')
  }
  if (usesMultiMarket.value) {
    const fxRates = (currentInput.fx_rates ?? [])
      .map((rate) => `${rate.pair} ${formatNumber(rate.rate, 6)}`)
      .join(' / ')
    return [
      `PCF ${currentInput.pcf.trading_day}`,
      `${currentInput.pcf.component_count ?? 0} 只 PCF 成分（港股/ADR）`,
      fxRates || '双 FX 未就绪',
      `实时双边价 ${currentInput.market_quotes?.length ?? 0} 只`,
      `模型 ${props.snapshot.model_version}`,
    ].join('，')
  }
  return [
    `PCF ${currentInput.pcf.trading_day}`,
    `CFETS ${currentInput.fx.pair || 'USD/CNY'} ${formatNumber(currentInput.fx.rate, 6)}（${currentInput.fx.quote_time}）`,
    `IB ${currentInput.ib.symbol || 'XOP'} ${formatNumber(currentInput.ib.bid, 4)} / ${formatNumber(currentInput.ib.ask, 4)}（${currentInput.ib.market_data_type || 'unknown'}）`,
    `模型 ${props.snapshot.model_version}`,
  ].join('，')
})

function bestLevel(levels: PrivateLevel[] | undefined) {
  if (!levels?.length) return undefined
  return levels.find((level) => level.level === 1 && level.price > 0) ?? levels.find((level) => level.price > 0)
}

function formatNumber(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatPercent(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function formatDateTime(value: string | undefined) {
  const parsed = new Date(value ?? '')
  if (!value || Number.isNaN(parsed.valueOf()) || parsed.getUTCFullYear() < 2000) return '—'
  return shanghaiDateTime.format(parsed)
}

function hasDateTime(value: string | undefined) {
  const parsed = new Date(value ?? '')
  return Boolean(value) && !Number.isNaN(parsed.valueOf()) && parsed.getUTCFullYear() >= 2000
}

function componentInputSummary() {
  if (usesLOFWeightedAnchor.value && lof.value) {
    return `XOP Bid/Ask ÷ 美东常规盘收盘锚点 ${formatNumber(lof.value.base_reference.price, 4)}`
  }
  if (usesIndiaT2.value) {
    const variant = selectedIndiaVariant.value
    const proxy = variant?.key === 'nifty_bridge' ? '合成 INDA（NIFTY 桥接）' : 'INDA'
    return `${proxy} ÷ T−2 四市场锚点 ${formatNumber(displayValuation.value?.xop_equivalent_shares, 6)}`
  }
  if (!usesMultiMarket.value) return formatNumber(input.value?.ib.bid, 4)
  const hk = components.value.filter((component) => component.market === 'HK').length
  const us = components.value.filter((component) => component.market === 'US').length
  return `${components.value.length} 只（港股 ${hk} / ADR ${us}）`
}

function rateClass(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}
</script>

<template>
  <section class="section private-detail-page">
    <a href="/private" class="private-detail-back" @click.prevent="$emit('back')">← 返回独立估值列表</a>

    <section class="detail-panel private-detail-panel">
      <div>
        <div class="detail-header-top">
          <h2 class="private-detail-heading">{{ snapshot.symbol }}{{ snapshot.name }}</h2>
          <div class="detail-header-actions">
            <a :href="`/funds/${snapshot.symbol}/share-history`" class="private-share-history-link">查看历史份额 →</a>
            <button v-if="usesSilverSettlement" type="button" class="private-share-history-link private-history-review-link" @click="$emit('open-silver-close-history', snapshot.symbol)">收盘估值历史 →</button>
            <button v-if="usesIndiaT2" type="button" class="private-share-history-link private-history-review-link" @click="$emit('open-india-history-review', snapshot.symbol)">历史估值复盘 →</button>
            <button v-if="usesIndiaT2" type="button" class="private-share-history-link private-history-review-link" @click="$emit('open-india-nifty-history-review', snapshot.symbol)">NIFTY 桥接误差 →</button>
            <span class="confirmation-badge">{{ snapshot.model_version }}</span>
            <span class="ratio-badge" :class="(usesLOFWeightedAnchor ? lofDataFresh : snapshot.actionable) ? 'private-actionable-badge' : 'private-blocked-badge'">
              {{ usesLOFWeightedAnchor ? (lofDataFresh ? '估值实时 · 非篮子套利' : (snapshot.ready ? '估值仅展示 · 数据需核对' : '估值未就绪 · 非篮子套利')) : (snapshot.actionable ? '当前可操作' : '当前禁止操作') }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="usesIndiaT2 && indiaValuations" class="private-valuation-controls">
        <div class="minute-chart-span-toggle private-variant-toggle" role="group" aria-label="切换 164824 估值口径">
          <button
            type="button"
            class="minute-chart-toggle"
            :class="{ active: selectedIndiaVariantKey === 'direct_inda' }"
            :aria-pressed="selectedIndiaVariantKey === 'direct_inda'"
            @click="selectedIndiaVariantKey = 'direct_inda'"
          >最终 NAV · INDA</button>
          <button
            v-if="indiaValuations.nifty_bridge"
            type="button"
            class="minute-chart-toggle"
            :class="{ active: selectedIndiaVariantKey === 'nifty_bridge' }"
            :aria-pressed="selectedIndiaVariantKey === 'nifty_bridge'"
            @click="selectedIndiaVariantKey = 'nifty_bridge'"
          >中国盘中 · NIFTY 桥接</button>
        </div>
        <PrivateIndiaFormulaBar
          v-if="india && input?.ib && displayValuation"
          :variant-key="selectedIndiaVariantKey"
          :india="india"
          :inda-quote="input.ib"
          :valuation="displayValuation"
          :domestic-bid="bestBid?.price"
          :domestic-ask="bestAsk?.price"
        />
      </div>

      <div v-if="usesLOFWeightedAnchor && lof && input?.ib && displayValuation" class="private-valuation-controls private-lof-valuation-controls">
        <PrivateLOFFormulaBar
          :lof="lof"
          :xop-quote="input.ib"
          :valuation="displayValuation"
          :domestic-bid="bestBid?.price"
          :domestic-ask="bestAsk?.price"
        />
      </div>

      <div v-if="usesSilverSettlement && silver && silverValuation" class="private-valuation-controls private-lof-valuation-controls">
        <PrivateSilverFormulaBar
          :silver="silver"
          :valuation="silverValuation"
          :market-price="quote?.price"
        />
      </div>

      <div class="detail-metrics" :class="{ 'private-silver-metrics': usesSilverSettlement }">
        <div v-if="!usesLOFWeightedAnchor">
          <span>现价</span>
          <strong>{{ formatNumber(quote?.price, usesSilverSettlement ? 3 : 4) }}</strong>
        </div>
        <div v-if="usesIndiaT2 && selectedIndiaReferenceQuote">
          <span>{{ selectedIndiaReferenceQuote.label }}</span>
          <strong class="private-reference-quote">
            {{ formatNumber(selectedIndiaReferenceQuote.bid, selectedIndiaReferenceQuote.digits) }}
            <small>/</small>
            {{ formatNumber(selectedIndiaReferenceQuote.ask, selectedIndiaReferenceQuote.digits) }}
          </strong>
        </div>
        <div v-else-if="usesLOFWeightedAnchor">
          <span>当前 XOP Bid / Ask</span>
          <strong class="private-reference-quote">
            {{ formatNumber(input?.ib.bid, 4) }}
            <small>/</small>
            {{ formatNumber(input?.ib.ask, 4) }}
          </strong>
        </div>
        <div v-else-if="usesSilverSettlement">
          <span>{{ silver?.contract || 'AG' }} 交易价 / 盘中均价</span>
          <strong class="private-reference-quote">
            {{ formatNumber(silver?.futures_price, 1) }}
            <small>/</small>
            {{ formatNumber(silver?.intraday_average, 1) }}
          </strong>
        </div>
        <div v-else>
          <span>涨幅</span>
          <strong :class="rateClass((quote?.change_pct ?? 0) / 100)">{{ formatNumber(quote?.change_pct, 2) }}%</strong>
        </div>
        <div v-if="usesSilverSettlement">
          <span>相对结算估值</span>
          <strong :class="rateClass(silverValuation?.settlement_premium_rate)">{{ formatPercent(silverValuation?.settlement_premium_rate, 3) }}</strong>
        </div>
        <div v-else>
          <span>{{ usesLOFWeightedAnchor ? '开仓溢价' : (usesMultiMarket || usesIndiaT2 ? '盘中指示折溢价（未扣费用）' : '买入方向溢价') }}</span>
          <strong :class="rateClass(displayValuation?.buy_direction_premium_rate)">{{ formatPercent(displayValuation?.buy_direction_premium_rate) }}</strong>
        </div>
        <div v-if="usesSilverSettlement">
          <span>相对交易估值</span>
          <strong :class="rateClass(silverValuation?.trading_premium_rate)">{{ formatPercent(silverValuation?.trading_premium_rate, 3) }}</strong>
        </div>
        <div v-else-if="usesLOFWeightedAnchor">
          <span>平仓溢价</span>
          <strong :class="rateClass(displayValuation?.sell_direction_premium_rate)">{{ formatPercent(displayValuation?.sell_direction_premium_rate) }}</strong>
        </div>
        <div v-if="usesSilverSettlement">
          <span>基金净值估值 · 结算口径</span>
          <strong>{{ formatNumber(silverValuation?.settlement_nav, 4) }}</strong>
        </div>
        <div v-else>
          <span>{{ usesLOFWeightedAnchor ? 'XOP 估值 NAV Bid' : (usesIndiaT2 ? `${selectedIndiaVariant?.quote_symbol || 'INDA'} 估值 Bid` : 'Basket Bid NAV') }}</span>
          <strong>{{ formatNumber(displayValuation?.nav_bid, 4) }}</strong>
        </div>
        <div v-if="usesSilverSettlement">
          <span>基金盘中交易估值</span>
          <strong>{{ formatNumber(silverValuation?.trading_nav, 4) }}</strong>
        </div>
        <div v-else>
          <span>{{ usesLOFWeightedAnchor ? 'XOP 估值 NAV Ask' : (usesIndiaT2 ? `${selectedIndiaVariant?.quote_symbol || 'INDA'} 估值 Ask` : 'Basket Ask NAV') }}</span>
          <strong>{{ formatNumber(displayValuation?.nav_ask, 4) }}</strong>
        </div>
        <div v-if="usesPCFDerivedXOPEquivalent">
          <span>固定 XOP 等价数量</span>
          <strong>{{ formatNumber(pcfDerivedXOPEquivalent?.shares, 4) }} 股</strong>
          <small>每 100 万份申赎单位 · 固定 1046 股{{ pcfDerivedXOPEquivalent?.historical ? ' · 历史快照' : '' }}</small>
        </div>
        <div>
          <span>状态</span>
          <strong :class="snapshot.calculation_state === 'preopen_fx_fallback' ? 'private-status-warn' : usesLOFWeightedAnchor ? (lofDataFresh ? 'private-status-good' : 'private-status-warn') : (snapshot.ready ? 'private-status-good' : 'private-status-bad')">
            {{ snapshot.calculation_state === 'preopen_fx_fallback' ? '预开盘估值 · 禁止执行' : snapshot.calculation_state === 'expired' ? '输入已失效 · 等待更新' : usesLOFWeightedAnchor ? (lofDataFresh ? '实时' : (snapshot.ready ? '需核对' : '未就绪')) : (snapshot.ready ? (quote?.realtime_status || 'ready') : 'not ready') }}
          </strong>
        </div>
      </div>
      <p v-if="!usesSilverSettlement" class="model-note">{{ modelNote }}</p>
    </section>

    <div
      class="detail-secondary-grid private-detail-secondary-grid"
      :class="{ 'detail-secondary-grid-expanded': minuteChartSpan !== 1 || !valuation }"
    >
      <PrivateOrderBookPremiumTable
        v-if="displayValuation"
        class="detail-secondary-orderbook"
        :snapshot="snapshot"
        :valuation="displayValuation"
        :order-book="displayOrderBook"
        :valuation-label="usesSilverSettlement ? '相对 AG 交易口径 NAV' : (usesIndiaT2 ? `${selectedIndiaVariant?.label || 'T−2 NAV'} 相对溢价` : undefined)"
        :hide-valuation-label="usesLOFWeightedAnchor"
      />
      <PrivateValuationMinuteChart
        class="detail-secondary-chart"
        :symbol="snapshot.symbol"
        :day-span="minuteChartSpan"
        :india-valuation-variant="usesIndiaT2 ? selectedIndiaVariant?.key : undefined"
        :silver-valuation="usesSilverSettlement"
        :valuation-reference-label="chartReferenceLabel"
        :market-instrument-label="usesLOFWeightedAnchor || usesSilverSettlement ? 'LOF 现价' : 'ETF 现价'"
        @update:day-span="minuteChartSpan = $event"
        @historical-calibration="historicalCalibration = $event"
      />
    </div>
    <section v-if="!valuation" class="table-section private-table-empty private-history-only-note">
      当前实时 BasketValuation 尚未生成；上方展示的是已回补的历史分钟估值，不影响历史图表查看。
    </section>

    <section class="table-section private-raw-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>估值原始数据</h2>
          <p class="table-inline-note">private 输入与公共 Sina 五档只在此页面汇合，不写入公开估值。</p>
        </div>
        <span class="private-raw-timestamp">快照 {{ formatDateTime(snapshot.as_of) }}</span>
      </div>
      <div class="table-wrap">
        <table class="private-raw-table">
          <thead>
            <tr>
              <th class="sticky-col">类型</th>
              <th>字段</th>
              <th>值</th>
              <th>时间 / 日期</th>
              <th>状态</th>
              <th>来源 / 说明</th>
            </tr>
          </thead>
          <tbody>
            <template v-if="usesSilverSettlement && silver">
              <tr>
                <td class="sticky-col code">官方 NAV</td>
                <td>昨日单位净值</td>
                <td>{{ formatNumber(silver.base_nav, 10) }}</td>
                <td>{{ silver.base_nav_date }}</td>
                <td>估值分母基准</td>
                <td>{{ silver.base_nav_source }} · 抓取 {{ formatDateTime(silver.base_nav_fetched_at) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">{{ silver.contract }}</td>
                <td>所选合约昨日结算价</td>
                <td>{{ formatNumber(silver.previous_settlement, 3) }}</td>
                <td>{{ silver.previous_settlement_date }}</td>
                <td>偶数月 10 日换月</td>
                <td>{{ silver.previous_settlement_source }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">Sina AG</td>
                <td>当前交易价 / 盘中累计均价</td>
                <td>{{ formatNumber(silver.futures_price, 3) }} / {{ formatNumber(silver.intraday_average, 3) }}</td>
                <td>{{ formatDateTime(silver.observed_at) }}</td>
                <td>仅 09:15–11:30、13:00–15:00</td>
                <td>{{ silver.source }}</td>
              </tr>
            </template>
            <template v-else-if="usesLOFWeightedAnchor && lof">
              <tr>
                <td class="sticky-col code">官方 NAV</td>
                <td>估值基准单位净值</td>
                <td>{{ formatNumber(lof.base_nav, 10) }}</td>
                <td>{{ lof.base_nav_date }}</td>
                <td>基准日</td>
                <td>{{ lof.base_nav_source }} · 抓取 {{ formatDateTime(lof.base_nav_fetched_at) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">XOP 锚点</td>
                <td>美东常规盘收盘价</td>
                <td>{{ formatNumber(lof.base_reference.price, 6) }}</td>
                <td>目标 {{ formatDateTime(lof.base_reference.target_at) }}<br>实际 {{ formatDateTime(lof.base_reference.observed_at) }}</td>
                <td :class="['exact', 'exact_close', 'stored_probe_window', 'public_anchor_record'].includes(lof.base_reference.capture_status) ? 'private-status-good' : 'private-status-warn'">
                  {{ lof.base_reference.capture_status }} · {{ lof.base_reference.price_basis }}
                </td>
                <td>{{ lof.base_reference.source }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">TWS XOP</td>
                <td>当前 Bid / Ask / Last</td>
                <td>{{ formatNumber(input?.ib.bid, 4) }} / {{ formatNumber(input?.ib.ask, 4) }} / {{ formatNumber(input?.ib.last, 4) }}</td>
                <td>{{ formatDateTime(input?.ib.observed_at) }}</td>
                <td :class="input?.ib.market_data_type === 'Live' ? 'private-status-good' : 'private-status-warn'">{{ input?.ib.market_data_type || '—' }}<span v-if="input?.ib.quote_session"> · {{ input.ib.quote_session }}</span></td>
                <td>{{ input?.ib.contract || input?.ib.source || '—' }}<span v-if="input?.ib.contract && input?.ib.source"> · {{ input.ib.source }}</span></td>
              </tr>
              <tr>
                <td class="sticky-col code">SAFE</td>
                <td>基准日 USD/CNY 中间价</td>
                <td>{{ formatNumber(lof.base_fx.rate, 6) }}</td>
                <td>{{ lof.base_fx.trading_day }} {{ lof.base_fx.quote_time }}</td>
                <td>{{ lof.base_fx.source }}</td>
                <td>{{ formatDateTime(lof.base_fx.fetched_at) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">SAFE</td>
                <td>T日 USD/CNY 中间价</td>
                <td>{{ formatNumber(lof.current_fx.rate, 6) }}</td>
                <td>{{ lof.current_fx.trading_day }} {{ lof.current_fx.quote_time }}</td>
                <td>{{ lof.current_fx.source }}</td>
                <td>{{ formatDateTime(lof.current_fx.fetched_at) }} · 汇率比 {{ formatNumber(lofFXRatio, 8) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">有效仓位</td>
                <td>XOP 风险占比 / 静态占比</td>
                <td>{{ formatPercent(lof.effective_ratio.value) }} / {{ formatPercent(lofStaticRatio) }}</td>
                <td>{{ hasDateTime(lof.effective_ratio.override_updated_at) ? formatDateTime(lof.effective_ratio.override_updated_at) : lof.base_nav_date }}</td>
                <td>{{ lof.effective_ratio.override_source ? '已覆盖默认值' : '默认值' }}</td>
                <td>
                  {{ lof.effective_ratio.source }}；默认 {{ formatPercent(lof.effective_ratio.default_value) }}（{{ lof.effective_ratio.default_source }}）
                  <span v-if="lof.effective_ratio.override_source">；覆盖 {{ lof.effective_ratio.override_source }}<span v-if="lof.effective_ratio.override_updated_by"> / {{ lof.effective_ratio.override_updated_by }}</span></span>
                </td>
              </tr>
            </template>
            <template v-else-if="usesIndiaT2 && india">
              <tr>
                <td class="sticky-col code">T−2 NAV</td>
                <td>最新公布单位净值</td>
                <td>{{ formatNumber(india.base_nav, 10) }}</td>
                <td>{{ india.base_nav_date }}</td>
                <td>基准日</td>
                <td>{{ india.portfolio_source }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">SAFE</td>
                <td>基准日 USD/CNY 中间价</td>
                <td>{{ formatNumber(india.base_fx.rate, 6) }}</td>
                <td>{{ india.base_fx.trading_day }} {{ india.base_fx.quote_time }}</td>
                <td>{{ india.base_fx.source }}</td>
                <td>{{ formatDateTime(india.base_fx.fetched_at) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">SAFE</td>
                <td>T日 USD/CNY 中间价</td>
                <td>{{ formatNumber(india.current_fx.rate, 6) }}</td>
                <td>{{ india.current_fx.trading_day }} {{ india.current_fx.quote_time }}</td>
                <td>{{ india.current_fx.source }}</td>
                <td>{{ formatDateTime(india.current_fx.fetched_at) }}</td>
              </tr>
              <tr>
                <td class="sticky-col code">IB</td>
                <td>INDA Bid / Ask</td>
                <td>{{ formatNumber(input?.ib.bid, 4) }} / {{ formatNumber(input?.ib.ask, 4) }}</td>
                <td>{{ formatDateTime(input?.ib.observed_at) }}</td>
                <td :class="input?.ib.market_data_type === 'Live' ? 'private-status-good' : 'private-status-warn'">{{ input?.ib.market_data_type || '—' }}</td>
                <td>{{ input?.ib.source || '—' }}</td>
              </tr>
              <template v-if="india.nifty_bridge">
                <tr>
                  <td class="sticky-col code">NIFTY</td>
                  <td>中国盘中代理 Bid / Ask</td>
                  <td>{{ formatNumber(india.nifty_bridge.nifty.bid, 1) }} / {{ formatNumber(india.nifty_bridge.nifty.ask, 1) }}</td>
                  <td>{{ formatDateTime(india.nifty_bridge.nifty.observed_at) }}</td>
                  <td :class="india.nifty_bridge.nifty.market_data_type === 'Live' ? 'private-status-good' : 'private-status-warn'">{{ india.nifty_bridge.nifty.market_data_type || '—' }}</td>
                  <td>{{ india.nifty_bridge.nifty.contract || india.nifty_bridge.nifty.source || '—' }}</td>
                </tr>
                <tr>
                  <td class="sticky-col code">桥接</td>
                  <td>纽约 15:49–15:51 同步参考（中心 15:50）</td>
                  <td>INDA {{ formatNumber(india.nifty_bridge.inda_reference.bid, 4) }} / {{ formatNumber(india.nifty_bridge.inda_reference.ask, 4) }}；NIFTY {{ formatNumber(india.nifty_bridge.nifty_reference.bid, 1) }} / {{ formatNumber(india.nifty_bridge.nifty_reference.ask, 1) }}</td>
                  <td>{{ formatDateTime(india.nifty_bridge.reference_at) }}</td>
                  <td>β={{ formatNumber(india.nifty_bridge.beta, 2) }}</td>
                  <td>参考点用于合成 INDA，不代表当时的基金净值</td>
                </tr>
                <tr v-if="india.nifty_bridge.roll_adjustment">
                  <td class="sticky-col code">NIFTY 换月</td>
                  <td>周一 12:28–12:32 BJT 同步基差</td>
                  <td>{{ india.nifty_bridge.roll_adjustment.old_contract }} → {{ india.nifty_bridge.roll_adjustment.new_contract }}；Bid × {{ formatNumber(india.nifty_bridge.roll_adjustment.bid_factor, 8) }} / Ask × {{ formatNumber(india.nifty_bridge.roll_adjustment.ask_factor, 8) }}</td>
                  <td>{{ formatDateTime(india.nifty_bridge.roll_adjustment.captured_at) }}</td>
                  <td>{{ india.nifty_bridge.roll_adjustment.direction }}</td>
                  <td>{{ india.nifty_bridge.roll_adjustment.source }}</td>
                </tr>
              </template>
            </template>
            <template v-else>
            <tr>
              <td class="sticky-col code">PCF</td>
              <td>EstimateCashComponent</td>
              <td>{{ formatMoney(input?.pcf.estimate_cash_component_cny) }}</td>
              <td>{{ input?.pcf.trading_day || '—' }}</td>
              <td>申购 {{ input?.pcf.creation || '—' }} · 赎回 {{ input?.pcf.redemption || '—' }} · {{ input?.pcf.component_count ?? '—' }} 成分</td>
              <td class="private-source-cell">
                <a v-if="safePCFSourceURL" :href="safePCFSourceURL" target="_blank" rel="noreferrer">PCF XML</a>
                <span v-else>{{ input?.pcf.source_url || '—' }}</span>
              </td>
            </tr>
            <tr v-if="usesPCFDerivedXOPEquivalent">
              <td class="sticky-col code">固定系数</td>
              <td>XOP 等价数量</td>
              <td>{{ formatNumber(pcfDerivedXOPEquivalent?.shares, 4) }} 股 / 100 万份</td>
              <td>PCF {{ pcfDerivedXOPEquivalent?.pcfTradingDay || '—' }}</td>
              <td>{{ pcfDerivedXOPEquivalent?.historical ? '历史快照（不可操作）' : '固定系数' }}</td>
              <td>{{ pcfDerivedXOPEquivalent?.historical ? `最近审计分钟 ${formatDateTime(pcfDerivedXOPEquivalent.minute)}` : '固定 1046 股 XOP；PCF 仅提供现金差额与审计信息' }}</td>
            </tr>
            <tr v-if="!usesMultiMarket">
              <td class="sticky-col code">CFETS</td>
              <td>{{ fxLabel }}</td>
              <td>{{ formatNumber(input?.fx.rate, 6) }}</td>
              <td>{{ input?.fx.trading_day || '—' }} {{ input?.fx.quote_time || '' }}</td>
              <td>{{ input?.fx.source || '—' }}</td>
              <td>{{ formatDateTime(input?.fx.fetched_at) }}</td>
            </tr>
            <tr v-if="!usesMultiMarket">
              <td class="sticky-col code">IB</td>
              <td>{{ referenceSymbol }} Bid / Ask</td>
              <td>{{ formatNumber(input?.ib.bid, 4) }} / {{ formatNumber(input?.ib.ask, 4) }}</td>
              <td>{{ formatDateTime(input?.ib.observed_at) }}</td>
              <td :class="input?.ib.market_data_type === 'Live' ? 'private-status-good' : 'private-status-warn'">{{ input?.ib.market_data_type || '—' }}</td>
              <td>{{ input?.ib.source || '—' }}</td>
            </tr>
            <tr v-else>
              <td class="sticky-col code">FX</td>
              <td>多市场盯市汇率</td>
              <td>{{ input?.fx_rates?.map((rate) => `${rate.pair} ${formatNumber(rate.rate, 6)}`).join(' / ') || '—' }}</td>
              <td>{{ input?.fx_rates?.map((rate) => `${rate.trading_day} ${rate.quote_time}`).join(' / ') || '—' }}</td>
              <td>{{ input?.fx_rates?.every((rate) => rate.source === 'CFETS_REFERENCE_RATE') ? 'CFETS' : '—' }}</td>
              <td>USD/CNY 与 HKD/CNY 分开保存</td>
            </tr>
            <tr v-if="usesMultiMarket">
              <td class="sticky-col code">MARKET</td>
              <td>PCF 成分双边价</td>
              <td>{{ input?.market_quotes?.length ?? 0 }} 只</td>
              <td>最近 {{ formatDateTime(input?.market_quotes?.[0]?.observed_at) }}</td>
              <td>{{ input?.market_quotes?.every((item) => item.market_data_type === 'Live') ? 'Live' : '待检查' }}</td>
              <td>港股与 ADR 分市场计算</td>
            </tr>
            </template>
            <tr>
              <td class="sticky-col code">Sina</td>
              <td>{{ snapshot.symbol }} Last / 买一 / 卖一</td>
              <td>{{ formatNumber(quote?.price, 3) }} / {{ formatNumber(bestBid?.price, 3) }} / {{ formatNumber(bestAsk?.price, 3) }}</td>
              <td>{{ quote?.quote_date || '—' }} {{ quote?.quote_time || '' }}</td>
              <td>{{ quote?.realtime_status || (quote?.is_realtime ? 'realtime' : '—') }}</td>
              <td>{{ quote?.source || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="usesIndiaT2 && india?.anchors?.length" class="table-section private-basket-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>T−2 多市场 INDA 锚点</h2>
          <p class="table-inline-note">权重来自 2026Q2 报告：美国 52.60%、欧洲 31.65%、日本 4.25%、香港 0.87%，在 89.37% 风险资产中归一化。</p>
        </div>
      </div>
      <div class="table-wrap compact">
        <table class="private-basket-table">
          <thead>
            <tr>
              <th class="sticky-col">市场窗口</th>
              <th>风险权重</th>
              <th>INDA 锚点价</th>
              <th>目标收盘时点</th>
              <th>实际取价时点</th>
              <th>取价状态</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="anchor in india.anchors" :key="anchor.key">
              <td class="sticky-col code">{{ anchor.label }}</td>
              <td>{{ formatPercent(anchor.weight) }}</td>
              <td>{{ formatNumber(anchor.price, 6) }}</td>
              <td>{{ formatDateTime(anchor.target_at) }}</td>
              <td>{{ formatDateTime(anchor.observed_at) }}</td>
              <td :class="anchor.capture_status === 'exact_1m' ? 'private-status-good' : 'private-status-warn'">{{ anchor.capture_status }}</td>
              <td>{{ anchor.source }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="usesLOFWeightedAnchor && lof && displayValuation" class="table-section private-basket-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>LOF · XOP 收盘锚点 NAV 测算</h2>
          <p class="table-inline-note">
            基准净值 ×［静态占比＋有效 XOP 占比 × 当前 XOP Bid/Ask ÷ 基准 XOP 常规盘收盘价 × T日中间价 ÷ 基准日中间价］
          </p>
        </div>
      </div>
      <div class="table-wrap compact">
        <table class="private-basket-table">
          <thead>
            <tr>
              <th class="sticky-col">方向</th>
              <th>当前 XOP</th>
              <th>XOP / 收盘锚点</th>
              <th>SAFE 汇率比</th>
              <th>风险资产分量（CNY/份）</th>
              <th>静态资产分量（CNY/份）</th>
              <th>估值 NAV</th>
              <th>国内方向溢价</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="sticky-col code">Bid</td>
              <td>{{ formatNumber(input?.ib.bid, 4) }}</td>
              <td>{{ formatNumber(lofXOPBidRatio, 8) }}</td>
              <td>{{ formatNumber(lofFXRatio, 8) }}</td>
              <td>{{ formatNumber(lofRiskBidContribution, 10) }}</td>
              <td>{{ formatNumber(lofStaticContribution, 10) }}</td>
              <td>{{ formatNumber(displayValuation.nav_bid, 10) }}</td>
              <td :class="rateClass(displayValuation.buy_direction_premium_rate)">买入：{{ formatPercent(displayValuation.buy_direction_premium_rate) }}</td>
            </tr>
            <tr>
              <td class="sticky-col code">Ask</td>
              <td>{{ formatNumber(input?.ib.ask, 4) }}</td>
              <td>{{ formatNumber(lofXOPAskRatio, 8) }}</td>
              <td>{{ formatNumber(lofFXRatio, 8) }}</td>
              <td>{{ formatNumber(lofRiskAskContribution, 10) }}</td>
              <td>{{ formatNumber(lofStaticContribution, 10) }}</td>
              <td>{{ formatNumber(displayValuation.nav_ask, 10) }}</td>
              <td :class="rateClass(displayValuation.sell_direction_premium_rate)">卖出：{{ formatPercent(displayValuation.sell_direction_premium_rate) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="displayValuation && !usesLOFWeightedAnchor && !usesSilverSettlement" class="table-section private-basket-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>{{ usesIndiaT2 ? `${selectedIndiaVariant?.label || 'T−2 多市场'} NAV 测算` : '篮子测算明细' }}</h2>
          <p class="table-inline-note">{{ displayValuation.formula || `${formatNumber(displayValuation.xop_equivalent_shares, 4)} × ${referenceSymbol} Bid/Ask × CFETS + EstimateCashComponent` }}</p>
        </div>
      </div>
      <div class="table-wrap compact">
        <table class="private-basket-table">
          <thead>
            <tr>
              <th class="sticky-col">方向</th>
              <th>{{ usesIndiaT2 ? 'INDA / T−2 锚点' : (usesMultiMarket ? 'PCF 市场成分' : `IB ${referenceSymbol}`) }}</th>
              <th>{{ usesIndiaT2 ? '风险资产分量（CNY/份）' : '股票篮子（CNY）' }}</th>
              <th>{{ usesIndiaT2 ? '静态资产分量（CNY/份）' : 'PCF 现金差额' }}</th>
              <th>总篮子（CNY）</th>
              <th>单位净值</th>
              <th>对应 ETF 方向</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="sticky-col code">Bid</td>
              <td>{{ componentInputSummary() }}</td>
              <td>{{ formatMoney(displayValuation.stock_component_bid_cny) }}</td>
              <td>{{ formatMoney(displayValuation.estimate_cash_component_cny) }}</td>
              <td>{{ formatMoney(displayValuation.basket_bid_cny) }}</td>
              <td>{{ formatNumber(displayValuation.nav_bid, 10) }}</td>
              <td :class="rateClass(displayValuation.buy_direction_premium_rate)">买入：{{ formatPercent(displayValuation.buy_direction_premium_rate) }}</td>
            </tr>
            <tr>
              <td class="sticky-col code">Ask</td>
              <td>{{ componentInputSummary() }}</td>
              <td>{{ formatMoney(displayValuation.stock_component_ask_cny) }}</td>
              <td>{{ formatMoney(displayValuation.estimate_cash_component_cny) }}</td>
              <td>{{ formatMoney(displayValuation.basket_ask_cny) }}</td>
              <td>{{ formatNumber(displayValuation.nav_ask, 10) }}</td>
              <td :class="rateClass(displayValuation.sell_direction_premium_rate)">卖出：{{ formatPercent(displayValuation.sell_direction_premium_rate) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="components.length" class="table-section private-basket-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>PCF 成分与多市场双边价</h2>
          <p class="table-inline-note">只包含 PCF 的 30 只可交易成分，不包含 159900 申赎现金虚拟行。</p>
        </div>
      </div>
      <div class="table-wrap compact">
        <table class="private-basket-table">
          <thead>
            <tr>
              <th class="sticky-col">成分</th>
              <th>市场 / 币种</th>
              <th>数量</th>
              <th>Bid / Ask</th>
              <th>汇率</th>
              <th>Bid 估值（CNY）</th>
              <th>Ask 估值（CNY）</th>
              <th>数据时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="component in components" :key="`${component.market}:${component.symbol}`">
              <td class="sticky-col code">{{ component.symbol }} {{ component.name }}</td>
              <td>{{ component.market }} / {{ component.currency }}</td>
              <td>{{ formatNumber(component.quantity, 0) }}</td>
              <td>{{ formatNumber(component.bid, 4) }} / {{ formatNumber(component.ask, 4) }}</td>
              <td>{{ component.fx_pair }} {{ formatNumber(component.fx_rate, 6) }}</td>
              <td>{{ formatMoney(component.bid_value_cny) }}</td>
              <td>{{ formatMoney(component.ask_value_cny) }}</td>
              <td>{{ formatDateTime(component.observed_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <details v-if="displayValuation" class="private-formula-details">
      <summary>查看公式与审计信息</summary>
      <p v-if="usesSilverSettlement">净值口径：昨日净值 ÷ 昨日结算价 × 今日盘中累计均价；交易口径：昨日净值 ÷ 昨日结算价 × 当前 AG 交易价。</p>
      <p v-else-if="usesLOFWeightedAnchor">买入方向：Sina 卖一 ÷ XOP Bid 估值 NAV − 1；卖出方向：Sina 买一 ÷ XOP Ask 估值 NAV − 1。</p>
      <p v-else>买入方向：Sina 卖一 ÷ Basket Bid NAV − 1；卖出方向：Sina 买一 ÷ Basket Ask NAV − 1。</p>
      <p v-if="usesSilverSettlement">161226 使用东方财富已公布净值和 Sina 上期所 AG 行情；每逢偶数月 10 日切换下一个偶数月合约，换月后直接采用新合约昨结，不做基差调整；采集器：{{ input?.source || '—' }}。</p>
      <p v-if="usesLOFWeightedAnchor">162411 使用已公布基准净值、同基准日 XOP 美东常规盘收盘价和 SAFE 人民币美元中间价；中国盘中只更新当前 XOP Bid/Ask 与 T日中间价。该口径不使用 PCF、赎回篮子、现金替代额或 CFETS 小时参考价；采集器：{{ input?.source || '—' }}。</p>
      <p v-else-if="usesIndiaT2">164824 使用 T−2 已公布净值、四个海外收盘窗口的 INDA 锚点与 SAFE USD/CNY 中间价；可切换的 NIFTY 口径只用于中国盘中 IOPV/对冲，不替代最终 INDA 净值代理；采集器：{{ input?.source || '—' }}。</p>
      <p v-else>PCF SHA-256：{{ input?.pcf.sha256 || '—' }}；采集器：{{ input?.source || '—' }}。</p>
    </details>

    <details v-if="snapshot.warnings.length" class="private-execution-note">
      <summary>
        <span :class="snapshot.calculation_state === 'preopen_fx_fallback' ? 'private-status-warn' : usesLOFWeightedAnchor ? (lofDataFresh ? 'private-status-good' : 'private-status-warn') : (snapshot.actionable ? 'private-status-good' : 'private-status-warn')">
          {{ usesLOFWeightedAnchor ? (lofDataFresh ? '估值实时 · 非篮子套利' : (snapshot.ready ? '估值仅展示 · 数据需核对' : '估值未就绪 · 非篮子套利')) : (snapshot.actionable ? '操作门控已通过' : '操作门控未通过') }}
        </span>
        <span>{{ snapshot.warnings.length }} 条运行预警，点击查看</span>
      </summary>
      <ul>
        <li v-for="warning in snapshot.warnings" :key="warning">{{ warning }}</li>
      </ul>
    </details>
  </section>
</template>
