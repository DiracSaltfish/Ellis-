<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  getPrivateFundMinuteHistoryByDate,
  getPrivateFundMinuteHistoryDates,
} from '../lib/privateApi'
import { isMinuteHistoryAutoRefreshTime } from '../lib/tradingHours'
import type { PrivateMinuteHistoryPoint } from '../lib/privateTypes'

type DaySpan = 1 | 3 | 5
type ChartMode = 'valuation' | 'premium'
type ChartSeries = {
  key: string
  label: string
  color: string
  valueAt: (row: PrivateMinuteHistoryPoint) => number
}
type SessionTick = {
  label: string
  minute: number
}
type ChartLine = {
  key: string
  points: string
}
type HistoricalCalibration = {
  pcfTradingDay: string
  xopEquivalentShares: number
  minute: string
}

const NIFTY_BRIDGE_SELECTION_VERSION = 'sgx-monthly-effective-last-tuesday.v2'

const props = defineProps<{
  symbol: string
  daySpan?: DaySpan
  // SZ164824 has two distinct intraday estimators.  The selected estimator
  // controls both labels and the stored point fields; it never relabels a
  // direct-INDA history row as a NIFTY bridge row.
  indiaValuationVariant?: string
  silverValuation?: boolean
  valuationReferenceLabel?: string
  marketInstrumentLabel?: string
}>()

const emit = defineEmits<{
  'update:daySpan': [span: DaySpan]
  'historical-calibration': [calibration: HistoricalCalibration | null]
}>()

const historyRows = ref<PrivateMinuteHistoryPoint[]>([])
const availableDates = ref<string[]>([])
const selectedDate = ref('')
const error = ref('')
const loading = ref(false)
const hoverIndex = ref<number | null>(null)
const tooltipLeft = ref(0)
const tooltipTop = ref(0)
const chartRoot = ref<HTMLElement | null>(null)
const chartMode = ref<ChartMode>('valuation')
const hiddenSeriesKeys = ref<string[]>([])
let controller: AbortController | null = null
let refreshTimer: number | undefined
let dateRequestToken = 0

const sessionEndMinute = 15 * 60
const sessionStartMinute = computed(() => props.silverValuation ? 9 * 60 + 15 : 9 * 60 + 30)
const sessionMinuteRange = computed(() => sessionEndMinute - sessionStartMinute.value)
const sessionTickLabels = computed<SessionTick[]>(() => [
  { label: props.silverValuation ? '09:15' : '09:30', minute: sessionStartMinute.value },
  { label: '11:30', minute: 11 * 60 + 30 },
  { label: '13:00', minute: 13 * 60 },
  { label: '15:00', minute: sessionEndMinute },
])
const shanghaiPartsFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const activeSpan = computed<DaySpan>(() => props.daySpan ?? 1)
const chartDayCount = computed(() => activeSpan.value)
const activeDate = computed(() => selectedDate.value)
const isLatestDateSelected = computed(() => Boolean(selectedDate.value) && selectedDate.value === availableDates.value[0])
const selectedDateIndex = computed(() => availableDates.value.indexOf(selectedDate.value))
const canSelectPreviousDate = computed(() => selectedDateIndex.value >= 0 && selectedDateIndex.value < availableDates.value.length - 1)
const canSelectNextDate = computed(() => selectedDateIndex.value > 0)
const width = computed(() => (activeSpan.value === 1 ? 760 : 1360))
const height = 300
const margin = { top: 22, right: 24, bottom: 38, left: 52 }
const plotWidth = computed(() => width.value - margin.left - margin.right)
const plotHeight = height - margin.top - margin.bottom
const isPremiumChart = computed(() => chartMode.value === 'premium')
const usesNiftyBridge = computed(() => props.indiaValuationVariant === 'nifty_bridge')
const valuationProxyLabel = computed(() => (
  props.valuationReferenceLabel
  || (usesNiftyBridge.value ? 'NIFTY' : (props.indiaValuationVariant ? 'INDA' : '参考标的'))
))
const marketInstrumentLabel = computed(() => props.marketInstrumentLabel || 'ETF 现价')

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function bidNAV(row: PrivateMinuteHistoryPoint) {
  if (props.silverValuation) return row.settlement_nav ?? row.basket_bid_nav
  return usesNiftyBridge.value ? row.nifty_bridge_bid_nav ?? Number.NaN : row.basket_bid_nav
}

function askNAV(row: PrivateMinuteHistoryPoint) {
  if (props.silverValuation) return row.trading_nav ?? row.basket_ask_nav
  return usesNiftyBridge.value ? row.nifty_bridge_ask_nav ?? Number.NaN : row.basket_ask_nav
}

function buyPremium(row: PrivateMinuteHistoryPoint) {
  if (!usesNiftyBridge.value) return row.buy_direction_premium_rate
  const nav = bidNAV(row)
  return isFiniteNumber(row.market_price) && nav > 0 ? row.market_price / nav - 1 : Number.NaN
}

function sellPremium(row: PrivateMinuteHistoryPoint) {
  if (!usesNiftyBridge.value) return row.sell_direction_premium_rate
  const nav = askNAV(row)
  return isFiniteNumber(row.market_price) && nav > 0 ? row.market_price / nav - 1 : Number.NaN
}

function minuteParts(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return null
  const parts = new Map(shanghaiPartsFormatter.formatToParts(parsed).map((part) => [part.type, part.value]))
  const year = parts.get('year') || ''
  const month = parts.get('month') || ''
  const day = parts.get('day') || ''
  const hour = Number(parts.get('hour') || '-1')
  const minute = Number(parts.get('minute') || '-1')
  const weekday = (parts.get('weekday') || '').toLowerCase()
  if (!/^\d{4}$/.test(year) || !/^\d{2}$/.test(month) || !/^\d{2}$/.test(day) || hour < 0 || minute < 0) return null
  return {
    dateKey: `${year}${month}${day}`,
    minuteOfDay: hour * 60 + minute,
    weekday,
  }
}

function isSessionMinute(value: string) {
  const parts = minuteParts(value)
  if (!parts || parts.weekday === 'sat' || parts.weekday === 'sun') return false
  return (parts.minuteOfDay >= sessionStartMinute.value && parts.minuteOfDay <= 11 * 60 + 30) ||
    (parts.minuteOfDay >= 13 * 60 && parts.minuteOfDay <= sessionEndMinute)
}

function shouldAutoRefresh() {
  if (isMinuteHistoryAutoRefreshTime()) return true
  if (!props.silverValuation) return false
  const parts = minuteParts(new Date().toISOString())
  return Boolean(parts && parts.weekday !== 'sat' && parts.weekday !== 'sun' && parts.minuteOfDay >= 9 * 60 + 15 && parts.minuteOfDay < 9 * 60 + 30)
}

function formatNumber(value: number, digits = 4) {
  return isFiniteNumber(value) ? value.toFixed(digits) : '—'
}

function formatPercent(value: number) {
  return isFiniteNumber(value) ? `${(value * 100).toFixed(4)}%` : '—'
}

function formatAxisValue(value: number) {
  return isPremiumChart.value ? `${value.toFixed(2)}%` : value.toFixed(4)
}

function formatMinute(value: string, includeDate = false) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: includeDate ? '2-digit' : undefined,
    day: includeDate ? '2-digit' : undefined,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
}

function normalizeDateKey(value: string) {
  const match = value.match(/^(\d{4})-?(\d{2})-?(\d{2})$/)
  if (!match) return ''
  return `${match[1]}${match[2]}${match[3]}`
}

function dateLabel(value: string) {
  const key = normalizeDateKey(value)
  if (!key) return value
  return `${key.slice(0, 4)}-${key.slice(4, 6)}-${key.slice(6, 8)}`
}

const baseRows = computed(() => {
  const byMinute = new Map<string, PrivateMinuteHistoryPoint>()
  for (const row of historyRows.value) {
    if (!row.minute || !isSessionMinute(row.minute)) continue
    if (!isFiniteNumber(row.basket_bid_nav) || !isFiniteNumber(row.basket_ask_nav)) continue
    // A bridge series must be recorded from simultaneous NIFTY/INDA reference
    // data. Do not fall back to the direct series here: that would make an old
    // final-NAV replay look like a tradeable NIFTY hedge estimate.
    if (usesNiftyBridge.value && (
      !isFiniteNumber(row.nifty_bridge_bid_nav) || !isFiniteNumber(row.nifty_bridge_ask_nav)
      || row.nifty_bridge_selection_version !== NIFTY_BRIDGE_SELECTION_VERSION
    )) continue
    byMinute.set(row.minute, row)
  }
  return Array.from(byMinute.values())
    .sort((left, right) => left.minute.localeCompare(right.minute))
    .slice(-(250 * activeSpan.value))
})

const rows = computed(() => {
  if (!isPremiumChart.value) return baseRows.value
  return baseRows.value.filter((row) => isFiniteNumber(buyPremium(row)) && isFiniteNumber(sellPremium(row)))
})

const visibleDateKeys = computed(() => {
  const keys: string[] = []
  const seen = new Set<string>()
  for (const row of rows.value) {
    const key = minuteParts(row.minute)?.dateKey
    if (!key || seen.has(key)) continue
    seen.add(key)
    keys.push(key)
  }
  return keys
})

const dateIndexByKey = computed(() => new Map(visibleDateKeys.value.map((key, index) => [key, index])))
const axisDayCount = computed(() => visibleDateKeys.value.length || chartDayCount.value || 1)

const series = computed<ChartSeries[]>(() => {
  if (isPremiumChart.value) {
    if (props.silverValuation) {
      return [
        { key: 'buy', label: 'LOF现价 / 结算口径 NAV', color: '#0f766e', valueAt: (row) => buyPremium(row) * 100 },
        { key: 'sell', label: 'LOF现价 / 交易口径 NAV', color: '#d97706', valueAt: (row) => sellPremium(row) * 100 },
      ]
    }
    return [
      { key: 'buy', label: `买入方向溢价（卖一 / ${valuationProxyLabel.value} Bid NAV）`, color: '#0f766e', valueAt: (row) => buyPremium(row) * 100 },
      { key: 'sell', label: `卖出方向溢价（买一 / ${valuationProxyLabel.value} Ask NAV）`, color: '#d97706', valueAt: (row) => sellPremium(row) * 100 },
    ]
  }
  if (props.silverValuation) {
    return [
      { key: 'bid-nav', label: '基金净值估值（结算口径）', color: '#0f766e', valueAt: bidNAV },
      { key: 'ask-nav', label: '基金盘中交易估值', color: '#7c3aed', valueAt: askNAV },
      { key: 'market', label: marketInstrumentLabel.value, color: '#2563eb', valueAt: (row) => row.market_price },
    ]
  }
  return [
    {
      key: 'bid-nav',
      label: `${valuationProxyLabel.value} Bid NAV`,
      color: '#0f766e',
      valueAt: bidNAV,
    },
    {
      key: 'ask-nav',
      label: `${valuationProxyLabel.value} Ask NAV`,
      color: '#7c3aed',
      valueAt: askNAV,
    },
    { key: 'market', label: marketInstrumentLabel.value, color: '#2563eb', valueAt: (row) => row.market_price },
  ]
})

const visibleSeries = computed(() => series.value.filter((item) => !hiddenSeriesKeys.value.includes(item.key)))
const values = computed(() => rows.value.flatMap((row) => visibleSeries.value.map((item) => item.valueAt(row))).filter(isFiniteNumber))
const domain = computed(() => {
  if (!values.value.length) return isPremiumChart.value ? { min: -1, max: 1 } : { min: 0, max: 1 }
  const min = Math.min(...values.value)
  const max = Math.max(...values.value)
  const spread = max - min
  const padding = spread > 0
    ? spread * 0.16
    : Math.max(Math.abs(max) * 0.004, isPremiumChart.value ? 0.05 : 0.002)
  return { min: min - padding, max: max + padding }
})

const yTicks = computed(() => {
  if (isPremiumChart.value && domain.value.min < 0 && domain.value.max > 0) {
    return [domain.value.max, 0, domain.value.min]
  }
  return [domain.value.max, (domain.value.min + domain.value.max) / 2, domain.value.min]
})

function xAtSessionMinute(minute: number, dayIndex = 0) {
  const bounded = Math.max(sessionStartMinute.value, Math.min(sessionEndMinute, minute))
  const totalRange = axisDayCount.value * sessionMinuteRange.value || 1
  const offset = dayIndex * sessionMinuteRange.value + bounded - sessionStartMinute.value
  return margin.left + (offset / totalRange) * plotWidth.value
}

function xAt(index: number) {
  const row = rows.value[index]
  const parts = row ? minuteParts(row.minute) : null
  if (!parts) return margin.left
  return xAtSessionMinute(parts.minuteOfDay, dateIndexByKey.value.get(parts.dateKey) ?? 0)
}

function yAt(value: number) {
  const spread = domain.value.max - domain.value.min || 1
  return margin.top + ((domain.value.max - value) / spread) * plotHeight
}

function pointsFor(valueAt: (row: PrivateMinuteHistoryPoint) => number) {
  const grouped = new Map<string, string[]>()
  rows.value.forEach((row, index) => {
    const value = valueAt(row)
    const dateKey = minuteParts(row.minute)?.dateKey || `row-${index}`
    if (!isFiniteNumber(value)) return
    const points = grouped.get(dateKey) || []
    points.push(`${xAt(index).toFixed(2)},${yAt(value).toFixed(2)}`)
    grouped.set(dateKey, points)
  })
  return Array.from(grouped.entries())
    .map(([key, points]) => ({ key, points: points.join(' ') }))
    .filter((line): line is ChartLine => Boolean(line.points))
}

const chartLines = computed(() => visibleSeries.value.map((item) => ({ ...item, lines: pointsFor(item.valueAt) })))
const axisTimeTicks = computed(() => {
  const ticks: Array<SessionTick & { key: string; x: number; anchor: 'start' | 'middle' | 'end' }> = []
  for (let dayIndex = 0; dayIndex < axisDayCount.value; dayIndex += 1) {
    for (const tick of sessionTickLabels.value) {
      ticks.push({
        ...tick,
        key: `${dayIndex}-${tick.label}`,
        x: xAtSessionMinute(tick.minute, dayIndex),
        anchor: tick.minute === sessionStartMinute.value ? 'start' : tick.minute === sessionEndMinute ? 'end' : 'middle',
      })
    }
  }
  return ticks
})
const daySeparators = computed(() => Array.from({ length: Math.max(axisDayCount.value - 1, 0) }, (_, index) => ({
  key: `day-${index + 1}`,
  x: xAtSessionMinute(sessionStartMinute.value, index + 1),
})))
const hovered = computed(() => hoverIndex.value === null ? null : rows.value[hoverIndex.value] || null)
const hoverX = computed(() => hoverIndex.value === null ? 0 : xAt(hoverIndex.value))
const hoverValues = computed(() => hovered.value ? visibleSeries.value.map((item) => ({ ...item, value: item.valueAt(hovered.value!) })) : [])
const chartLabel = computed(() => `${chartDayCount.value}日${isPremiumChart.value ? '分时溢价率' : '分时估值'}图`)
const emptyMessage = computed(() => loading.value
  ? '正在读取 private 分时快照…'
  : error.value || (usesNiftyBridge.value
    ? '该日没有按“最后一个周二起使用下月合约”规则复算的 NIFTY 桥接分钟估值数据'
    : (chartDayCount.value > 1 ? `所选日期起最近 ${chartDayCount.value} 个交易日暂无分钟估值数据` : '该日无中国交易时段分钟估值数据')))

function selectSpan(span: DaySpan) {
  if (span !== activeSpan.value) emit('update:daySpan', span)
}

function selectHistoryDate(date: string) {
  if (!date || date === selectedDate.value) return
  selectedDate.value = date
  onDateChange()
}

function selectPreviousDate() {
  const index = selectedDateIndex.value
  if (index < 0) return
  selectHistoryDate(availableDates.value[index + 1] || '')
}

function selectTodayDate() {
  // The newest entry is today's date while intraday data is available.  On a
  // non-trading day, or before the first snapshot, it is the newest usable
  // trading date instead of a deliberately empty calendar date.
  selectHistoryDate(availableDates.value[0] || '')
}

function selectNextDate() {
  const index = selectedDateIndex.value
  if (index <= 0) return
  selectHistoryDate(availableDates.value[index - 1] || '')
}

function toggleMode() {
  chartMode.value = isPremiumChart.value ? 'valuation' : 'premium'
  clearHover()
}

function isSeriesVisible(key: string) {
  return !hiddenSeriesKeys.value.includes(key)
}

function setSeriesVisible(key: string, visible: boolean) {
  hiddenSeriesKeys.value = visible
    ? hiddenSeriesKeys.value.filter((hiddenKey) => hiddenKey !== key)
    : [...new Set([...hiddenSeriesKeys.value, key])]
  clearHover()
}

function updateHover(clientX: number, rect: DOMRect, chartRect: DOMRect) {
  if (!rows.value.length) return
  const viewX = ((clientX - rect.left) / rect.width) * width.value
  let closestIndex = 0
  let closestDistance = Number.POSITIVE_INFINITY
  rows.value.forEach((_row, index) => {
    const distance = Math.abs(xAt(index) - viewX)
    if (distance < closestDistance) {
      closestDistance = distance
      closestIndex = index
    }
  })
  hoverIndex.value = closestIndex
  const tooltipWidth = activeSpan.value === 1 ? 236 : 252
  tooltipLeft.value = Math.max(8, Math.min(chartRect.width - tooltipWidth, clientX - chartRect.left + 10))
  tooltipTop.value = 12
}

function onMouseMove(event: MouseEvent) {
  const svg = event.currentTarget as SVGRectElement
  const chartRect = chartRoot.value?.getBoundingClientRect()
  if (!chartRect) return
  updateHover(event.clientX, svg.getBoundingClientRect(), chartRect)
}

function onTouchMove(event: TouchEvent) {
  if (!event.touches.length) return
  const svg = event.currentTarget as SVGRectElement
  const chartRect = chartRoot.value?.getBoundingClientRect()
  if (!chartRect) return
  updateHover(event.touches[0].clientX, svg.getBoundingClientRect(), chartRect)
}

function clearHover() {
  hoverIndex.value = null
}

function emitHistoricalCalibration(rows: PrivateMinuteHistoryPoint[]) {
  const latest = [...rows]
    .sort((left, right) => right.minute.localeCompare(left.minute))
    .find((row) => (
      Boolean(row.pcf_trading_day)
      && isFiniteNumber(row.xop_equivalent_shares)
      && row.xop_equivalent_shares > 0
    ))
  emit('historical-calibration', latest
    ? {
        pcfTradingDay: latest.pcf_trading_day!,
        xopEquivalentShares: latest.xop_equivalent_shares!,
        minute: latest.minute,
      }
    : null)
}

async function loadHistory() {
  controller?.abort()
  controller = new AbortController()
  const request = controller
  loading.value = true
  error.value = ''
  try {
    const selectedIndex = availableDates.value.indexOf(activeDate.value)
    const dates = availableDates.value.slice(selectedIndex >= 0 ? selectedIndex : 0, (selectedIndex >= 0 ? selectedIndex : 0) + chartDayCount.value)
    if (!dates.length) {
      historyRows.value = []
      emit('historical-calibration', null)
      return
    }
    const payloads = await Promise.all(
      dates.map(async (date) => ({
        date,
        payload: await getPrivateFundMinuteHistoryByDate(props.symbol, date, request.signal),
      })),
    )
    if (controller !== request) return
    const mergedRows: PrivateMinuteHistoryPoint[] = []
    for (const { payload } of payloads) {
      const dayRows = payload.rows || []
      if (!dayRows.length) continue
      mergedRows.push(...dayRows)
    }
    historyRows.value = mergedRows
    emitHistoricalCalibration(mergedRows)
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === 'AbortError') return
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    if (controller === request) {
      controller = null
      loading.value = false
    }
  }
}

async function loadDates() {
  const requestToken = ++dateRequestToken
  const response = await getPrivateFundMinuteHistoryDates(props.symbol)
  if (requestToken !== dateRequestToken) return
  const seen = new Set<string>()
  const dates: string[] = []
  for (const value of response.dates || []) {
    const key = normalizeDateKey(value)
    if (!key || seen.has(key)) continue
    seen.add(key)
    dates.push(key)
  }
  availableDates.value = dates
  if (!selectedDate.value || !dates.includes(selectedDate.value)) {
    selectedDate.value = dates[0] || ''
  }
}

async function reloadDatesAndHistory() {
  try {
    await loadDates()
    await loadHistory()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  }
}

function onDateChange() {
  historyRows.value = []
  clearHover()
  void loadHistory()
}

watch(() => props.symbol, () => {
  historyRows.value = []
  availableDates.value = []
  selectedDate.value = ''
  clearHover()
  emit('historical-calibration', null)
  void reloadDatesAndHistory()
})

watch(() => props.daySpan, () => {
  clearHover()
  void loadHistory()
})

watch(() => props.indiaValuationVariant, () => {
  hiddenSeriesKeys.value = []
  clearHover()
})

watch(() => props.silverValuation, () => {
  hiddenSeriesKeys.value = []
  clearHover()
})

onMounted(() => {
  void reloadDatesAndHistory()
  refreshTimer = window.setInterval(() => {
    if (isLatestDateSelected.value && shouldAutoRefresh()) void reloadDatesAndHistory()
  }, 60_000)
})

onBeforeUnmount(() => {
  controller?.abort()
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <section class="table-section minute-chart-section private-minute-chart-section" :class="{ 'minute-chart-section-expanded': activeSpan !== 1 }">
    <div class="minute-chart-head">
      <div class="minute-chart-head-main">
        <h2>{{ isPremiumChart ? '分时溢价率' : '分时估值' }}</h2>
        <div class="minute-chart-legend">
          <label
            v-for="item in series"
            :key="item.key"
            class="minute-chart-legend-item"
            :class="{ 'is-hidden': !isSeriesVisible(item.key) }"
            :title="`${isSeriesVisible(item.key) ? '点击隐藏' : '点击显示'}：${item.label}`"
          >
            <input
              class="minute-chart-legend-input"
              type="checkbox"
              :checked="isSeriesVisible(item.key)"
              :aria-label="`${isSeriesVisible(item.key) ? '隐藏' : '显示'} ${item.label}`"
              @change="setSeriesVisible(item.key, ($event.target as HTMLInputElement).checked)"
            >
            <i :style="{ background: item.color }"></i><span>{{ item.label }}</span>
          </label>
        </div>
      </div>
      <div class="minute-chart-actions">
        <div class="minute-chart-span-toggle" role="group" aria-label="切换 private 分时跨度">
          <button v-for="span in ([1, 3, 5] as DaySpan[])" :key="span" type="button" class="minute-chart-toggle" :class="{ active: activeSpan === span }" :aria-pressed="activeSpan === span" @click="selectSpan(span)">{{ span }}日</button>
        </div>
        <button type="button" class="minute-chart-toggle" :class="{ active: isPremiumChart }" @click="toggleMode">{{ isPremiumChart ? '切换分时估值图' : '切换溢价率图' }}</button>
        <div class="minute-chart-date-actions" role="group" aria-label="快捷切换 private 历史分时估值日期">
          <button type="button" class="minute-chart-toggle" :disabled="!canSelectPreviousDate" @click="selectPreviousDate">前一日</button>
          <button type="button" class="minute-chart-toggle" :disabled="!availableDates.length || isLatestDateSelected" title="无今日数据时切换至最新可用交易日" @click="selectTodayDate">今日</button>
          <button type="button" class="minute-chart-toggle" :disabled="!canSelectNextDate" @click="selectNextDate">后一日</button>
          <select v-model="selectedDate" class="minute-chart-date-select" :disabled="!availableDates.length" aria-label="选择 private 历史分时估值日期" @change="onDateChange">
            <option v-for="date in availableDates" :key="date" :value="date">{{ dateLabel(date) }}</option>
          </select>
        </div>
      </div>
    </div>
    <p v-if="error" class="notice error">{{ error }}</p>
    <div ref="chartRoot" class="minute-chart" :class="{ 'minute-chart-empty-state': !rows.length }">
      <svg :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="chartLabel">
        <g class="minute-chart-grid"><line v-for="tick in yTicks" :key="tick" :x1="margin.left" :x2="width - margin.right" :y1="yAt(tick)" :y2="yAt(tick)" /></g>
        <g v-if="daySeparators.length" class="minute-chart-day-separators"><line v-for="separator in daySeparators" :key="separator.key" :x1="separator.x" :x2="separator.x" :y1="margin.top" :y2="height - margin.bottom" /></g>
        <g class="minute-chart-axis">
          <text v-for="tick in yTicks" :key="`y-${tick}`" :x="margin.left - 8" :y="yAt(tick) + 4" text-anchor="end">{{ formatAxisValue(tick) }}</text>
          <text v-for="tick in axisTimeTicks" :key="tick.key" :x="tick.x" :y="height - 10" :text-anchor="tick.anchor">{{ tick.label }}</text>
        </g>
        <template v-for="line in chartLines" :key="line.key"><polyline v-for="dayLine in line.lines" :key="`${line.key}-${dayLine.key}`" class="private-minute-chart-line" :stroke="line.color" :points="dayLine.points" /></template>
        <g v-if="hovered && hoverValues.length" class="minute-chart-hover">
          <line :x1="hoverX" :x2="hoverX" :y1="margin.top" :y2="height - margin.bottom" />
          <circle v-for="item in hoverValues" :key="`hover-${item.key}`" v-show="isFiniteNumber(item.value)" :cx="hoverX" :cy="yAt(item.value)" r="4" :fill="item.color" />
        </g>
        <text v-if="!rows.length" class="minute-chart-empty" :x="width / 2" :y="height / 2" text-anchor="middle">{{ emptyMessage }}</text>
        <rect class="minute-chart-hit" :x="margin.left" :y="margin.top" :width="plotWidth" :height="plotHeight" @mousemove="onMouseMove" @touchstart.prevent="onTouchMove" @touchmove.prevent="onTouchMove" @touchend="clearHover" @mouseleave="clearHover" />
      </svg>
      <div v-if="hovered && hoverValues.length" class="minute-chart-tooltip" :style="{ left: `${tooltipLeft}px`, top: `${tooltipTop}px` }">
        <strong>{{ formatMinute(hovered.minute, true) }}</strong>
        <template v-if="isPremiumChart">
          <span v-for="item in hoverValues" :key="`tooltip-${item.key}`">{{ item.label }} {{ formatAxisValue(item.value) }}</span>
        </template>
        <template v-else>
          <span v-for="item in hoverValues" :key="`tooltip-${item.key}`">{{ item.label }} {{ formatNumber(item.value, item.key === 'market' ? 4 : 8) }}</span>
          <span>买 / 卖方向溢价 {{ formatPercent(buyPremium(hovered)) }} / {{ formatPercent(sellPremium(hovered)) }}</span>
        </template>
      </div>
    </div>
  </section>
</template>
