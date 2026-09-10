<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getHKConnectFX, getHKConnectFXDailyHistory, getHKConnectFXHistory } from '../lib/privateApi'
import type { HKConnectFXDailyHistoryResponse, HKConnectFXHistoryResponse, HKConnectFXMinutePoint, HKConnectFXSnapshot } from '../lib/privateTypes'

defineEmits<{ back: []; openCloseHistory: [] }>()

type DailyMarket = 'shanghai' | 'shenzhen'
type DailyDisplayMode = DailyMarket | 'both'
type DailyWindow = '7d' | '15d' | '30d' | '1y' | '3y' | 'all'
type DailyValueKey = 'actualBuy' | 'actualSell' | 'predictedBuy' | 'predictedSell' | 'ibAnchor' | 'centralParity'
type DailySeriesKey =
  | 'shanghaiActualSell' | 'shanghaiActualBuy' | 'shanghaiForecastSell' | 'shanghaiForecastBuy'
  | 'shenzhenActualSell' | 'shenzhenActualBuy' | 'shenzhenForecastSell' | 'shenzhenForecastBuy'
  | 'ibAnchor' | 'centralParity'
type DailyMarketValues = Partial<Record<DailyValueKey, number>>
type DailyChartRow = {
  date: string
  markets: Partial<Record<DailyMarket, DailyMarketValues>>
  ibAnchor?: number
  centralParity?: number
}
type DailySeries = {
  key: DailySeriesKey
  market?: DailyMarket
  label: string
  color: string
  value: DailyValueKey
  forecast?: boolean
}

const dailyWindowOptions: Array<{ key: DailyWindow, label: string }> = [
  { key: '7d', label: '近 7 日' },
  { key: '15d', label: '近 15 日' },
  { key: '30d', label: '近 30 日' },
  { key: '1y', label: '近 1 年' },
  { key: '3y', label: '近 3 年' },
  { key: 'all', label: '全部' },
]
const maxDailyXAxisZoom = 16
const minDailyYAxisZoom = 0.25
const maxDailyYAxisZoom = 16

const snapshot = ref<HKConnectFXSnapshot | null>(null)
const history = ref<HKConnectFXHistoryResponse | null>(null)
const dailyHistories = ref<Record<DailyMarket, HKConnectFXDailyHistoryResponse | null>>({ shanghai: null, shenzhen: null })
const error = ref('')
const loading = ref(true)
const dailyChartRoot = ref<HTMLElement | null>(null)
const minuteChartRoot = ref<HTMLElement | null>(null)
const dailyHoverIndex = ref<number | null>(null)
const minuteHoverIndex = ref<number | null>(null)
const dailyTooltipLeft = ref(0)
const minuteTooltipLeft = ref(0)
const dailyDisplayMode = ref<DailyDisplayMode>('shanghai')
const dailyWindow = ref<DailyWindow>('7d')
const dailyXAxisZoom = ref(1)
const dailyXAxisStart = ref(0)
const dailyYAxisZoom = ref(1)
const dailyYAxisCenter = ref<number | null>(null)
const isDailyChartDragging = ref(false)
const dailyVisibleSeries = ref<Record<DailySeriesKey, boolean>>({
  shanghaiActualSell: true,
  shanghaiActualBuy: true,
  shanghaiForecastSell: true,
  shanghaiForecastBuy: true,
  shenzhenActualSell: true,
  shenzhenActualBuy: true,
  shenzhenForecastSell: true,
  shenzhenForecastBuy: true,
  ibAnchor: true,
  centralParity: false,
})
const minuteVisibleSeries = ref({ sell: true, buy: true, ib: true })
const activeMarket = ref<'shanghai' | 'shenzhen'>('shanghai')
let latestTimer: number | undefined
let historyTimer: number | undefined
let activeRequest: AbortController | null = null
let dailyRequest: AbortController | null = null
let dailyWheelZoomAccumulator = 0
let dailyWheelPanRemainder = 0
let dailyWheelYAxisZoomAccumulator = 0
let dailyDragStartX = 0
let dailyDragStartAxisStart = 0

const dailyHistoryRequestDays = computed(() => {
  switch (dailyWindow.value) {
    case '7d': return 7
    case '15d': return 15
    case '30d': return 30
    default: return 2500
  }
})

function subtractCalendarYears(value: string, years: number) {
  const parsed = new Date(`${value}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return ''
  parsed.setUTCFullYear(parsed.getUTCFullYear() - years)
  return parsed.toISOString().slice(0, 10)
}

const dailyWindowStartDate = computed(() => {
  const tradeDate = snapshot.value?.trade_date
  if (!tradeDate) return ''
  if (dailyWindow.value === '1y') return subtractCalendarYears(tradeDate, 1)
  if (dailyWindow.value === '3y') return subtractCalendarYears(tradeDate, 3)
  return ''
})

function formatRate(value: number | null | undefined, digits = 6) {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}

function formatPercent(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

function formatLiveHKDVsEstimate(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '实时港元与预测差额 —'
  if (value === 0) return '实时港元与预测持平'
  return `实时港元较预测${value > 0 ? '升值' : '贬值'} ${Math.abs(value * 100).toFixed(digits)}%`
}

function formatAmount(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value.toFixed(2)} 亿港元`
}

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function rateClass(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  return value > 0 ? 'private-rate-positive' : value < 0 ? 'private-rate-negative' : 'private-rate-flat'
}

function statusClass(status: string) {
  if (status === 'live' || status === 'final_comparison') return 'state-good'
  if (status === 'reference_only' || status === 'scheduled' || status === 'closed') return 'state-warn'
  return 'state-bad'
}

async function loadLatest(silent = false) {
  if (!silent) loading.value = true
  const request = new AbortController()
  activeRequest?.abort()
  activeRequest = request
  try {
    const response = await getHKConnectFX(request.signal)
    snapshot.value = response
    error.value = ''
    if (!history.value || history.value.trade_date !== response.trade_date || history.value.market !== activeMarket.value) {
      await loadHistory(response.trade_date)
    }
    if (Object.values(dailyHistories.value).some((history) => (
      !history || history.trade_date !== response.trade_date || history.requested_days !== dailyHistoryRequestDays.value
    ))) {
      await loadDailyHistory()
    }
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === 'AbortError') return
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    if (activeRequest === request) activeRequest = null
    loading.value = false
  }
}

async function loadHistory(date?: string) {
  const day = date || snapshot.value?.trade_date
  if (!day) return
  try {
    history.value = await getHKConnectFXHistory(day, activeMarket.value)
  } catch (caught) {
    if (!(caught instanceof DOMException && caught.name === 'AbortError')) {
      error.value = caught instanceof Error ? caught.message : String(caught)
    }
  }
}

function selectMarket(market: 'shanghai' | 'shenzhen') {
  if (activeMarket.value === market) return
  activeMarket.value = market
  minuteHoverIndex.value = null
  void loadHistory()
}

const activeMarketLabel = computed(() => activeMarket.value === 'shanghai' ? '上海' : '深圳')
const activeFlow = computed(() => activeMarket.value === 'shanghai' ? snapshot.value?.flow : snapshot.value?.shenzhen_flow)
const activeEstimate = computed(() => activeMarket.value === 'shanghai' ? snapshot.value?.estimate : snapshot.value?.shenzhen_estimate)
const activeStatus = computed(() => activeMarket.value === 'shanghai' ? snapshot.value?.status || 'scheduled' : snapshot.value?.shenzhen_status || 'scheduled')
const marketCards = computed(() => {
  if (!snapshot.value) return []
  return [
    {
      market: 'shanghai' as const,
      label: '上海',
      estimate: snapshot.value.estimate,
    },
    {
      market: 'shenzhen' as const,
      label: '深圳',
      estimate: snapshot.value.shenzhen_estimate,
    },
  ]
})

const inputMarkets = computed(() => {
  if (!snapshot.value) return []
  return [
    {
      market: 'shanghai' as const,
      label: '上海',
      flow: snapshot.value.flow,
      estimate: snapshot.value.estimate,
      previousSettlement: snapshot.value.previous_settlement,
    },
    {
      market: 'shenzhen' as const,
      label: '深圳',
      flow: snapshot.value.shenzhen_flow,
      estimate: snapshot.value.shenzhen_estimate,
      previousSettlement: snapshot.value.shenzhen_previous_settlement,
    },
  ]
})

async function loadDailyHistory() {
  dailyRequest?.abort()
  const request = new AbortController()
  dailyRequest = request
  const days = dailyHistoryRequestDays.value
  try {
    const [shanghai, shenzhen] = await Promise.all([
      getHKConnectFXDailyHistory(days, 'shanghai', request.signal),
      getHKConnectFXDailyHistory(days, 'shenzhen', request.signal),
    ])
    if (dailyRequest === request) {
      dailyHistories.value = { shanghai, shenzhen }
    }
  } catch (caught) {
    if (!(caught instanceof DOMException && caught.name === 'AbortError')) {
      error.value = caught instanceof Error ? caught.message : String(caught)
    }
  } finally {
    if (dailyRequest === request) dailyRequest = null
  }
}

function selectDailyDisplayMode(mode: DailyDisplayMode) {
  if (dailyDisplayMode.value === mode) return
  dailyDisplayMode.value = mode
  dailyHoverIndex.value = null
}

function selectDailyWindow(days: DailyWindow) {
  if (dailyWindow.value === days) return
  dailyWindow.value = days
  dailyHoverIndex.value = null
  resetDailyViewport()
  void loadDailyHistory()
}

const chartRows = computed(() => (history.value?.rows || []).filter((row) => (
  Number.isFinite(row.predicted_buy_settlement) && Number.isFinite(row.predicted_sell_settlement)
)))

type MinuteSeriesKey = 'sell' | 'buy' | 'ib'

const dailySeries = computed<DailySeries[]>(() => [
  { key: 'shanghaiActualSell', market: 'shanghai', label: '上海 实际卖出结算（买港股）', color: '#c43b3b', value: 'actualSell' },
  { key: 'shanghaiActualBuy', market: 'shanghai', label: '上海 实际买入结算（卖港股）', color: '#0f766e', value: 'actualBuy' },
  { key: 'shanghaiForecastSell', market: 'shanghai', label: '上海 当日预测卖出结算', color: '#9d174d', value: 'predictedSell', forecast: true },
  { key: 'shanghaiForecastBuy', market: 'shanghai', label: '上海 当日预测买入结算', color: '#5b21b6', value: 'predictedBuy', forecast: true },
  { key: 'shenzhenActualSell', market: 'shenzhen', label: '深圳 实际卖出结算（买港股）', color: '#d97706', value: 'actualSell' },
  { key: 'shenzhenActualBuy', market: 'shenzhen', label: '深圳 实际买入结算（卖港股）', color: '#0e7490', value: 'actualBuy' },
  { key: 'shenzhenForecastSell', market: 'shenzhen', label: '深圳 当日预测卖出结算', color: '#c2410c', value: 'predictedSell', forecast: true },
  { key: 'shenzhenForecastBuy', market: 'shenzhen', label: '深圳 当日预测买入结算', color: '#0369a1', value: 'predictedBuy', forecast: true },
  { key: 'ibAnchor', label: 'CFETS HKD/CNY 16:00 参考汇率（今日为即时报价中点）', color: '#2563eb', value: 'ibAnchor' },
  { key: 'centralParity', label: '人民币对港币中间价（HKD/CNY）', color: '#b7791f', value: 'centralParity' },
])

const selectedDailyMarkets = computed<DailyMarket[]>(() => (
  dailyDisplayMode.value === 'both' ? ['shanghai', 'shenzhen'] : [dailyDisplayMode.value]
))

const displayedDailySeries = computed(() => (
  dailySeries.value.filter((series) => !series.market || selectedDailyMarkets.value.includes(series.market))
))

const dailyChartTitle = '沪深港股通结算汇率与 CFETS 锚点'

const minuteSeries = computed(() => [
  { key: 'sell' as const, label: '预测卖出结算（买港股）', color: '#c43b3b', valueAt: (row: HKConnectFXMinutePoint) => row.predicted_sell_settlement },
  { key: 'buy' as const, label: '预测买入结算（卖港股）', color: '#0f766e', valueAt: (row: HKConnectFXMinutePoint) => row.predicted_buy_settlement },
  { key: 'ib' as const, label: 'CFETS HKD/CNY 方向价', color: '#2563eb', valueAt: (row: HKConnectFXMinutePoint) => row.selected_hkd_cny },
])

const visibleMinuteSeries = computed(() => minuteSeries.value.filter((series) => minuteVisibleSeries.value[series.key]))

const chart = computed(() => {
  const rows = chartRows.value
  if (rows.length < 2) return null
  const width = 880
  const height = 250
  const padding = { left: 54, right: 18, top: 20, bottom: 32 }
  const domainSeries = visibleMinuteSeries.value.length ? visibleMinuteSeries.value : minuteSeries.value
  const values = rows.flatMap((row) => domainSeries.map((series) => series.valueAt(row)).filter(validNumber))
  if (!values.length) return null
  let min = Math.min(...values)
  let max = Math.max(...values)
  if (max === min) { min -= 0.00005; max += 0.00005 }
  const x = (index: number) => padding.left + (index / (rows.length - 1)) * (width - padding.left - padding.right)
  const y = (value: number) => padding.top + ((max - value) / (max - min)) * (height - padding.top - padding.bottom)
  const path = (valueAt: (row: HKConnectFXMinutePoint) => number | null | undefined) => {
    let started = false
    return rows.map((row, index) => {
      const value = valueAt(row)
      if (!validNumber(value)) { started = false; return '' }
      const command = started ? 'L' : 'M'
      started = true
      return `${command}${x(index).toFixed(1)},${y(value).toFixed(1)}`
    }).filter(Boolean).join(' ')
  }
  return {
    width,
    height,
    min,
    max,
    x,
    y,
    buy: path((row) => row.predicted_buy_settlement),
    sell: path((row) => row.predicted_sell_settlement),
    ib: path((row) => row.selected_hkd_cny),
    first: rows[0].timestamp.slice(11, 16),
    last: rows[rows.length - 1].timestamp.slice(11, 16),
  }
})

const recentRows = computed<HKConnectFXMinutePoint[]>(() => [...(history.value?.rows || [])].slice(-12).reverse())

function validNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function hasChartValue<T extends { value: number | null | undefined }>(item: T): item is T & { value: number } {
  return validNumber(item.value)
}

function formatDay(value: string) {
  return value.length >= 10 ? value.slice(5).replace('-', '/') : value
}

function isDailyTickVisible(index: number, total: number) {
  if (total <= 7) return true
  const step = Math.ceil((total - 1) / 6)
  return index === 0 || index === total - 1 || index % step === 0
}

const dailyChartRows = computed<DailyChartRow[]>(() => {
  const rowsByDate = new Map<string, DailyChartRow>()
  const date = snapshot.value?.trade_date
  for (const market of selectedDailyMarkets.value) {
    for (const row of dailyHistories.value[market]?.rows || []) {
      const chartRow = rowsByDate.get(row.trade_date) || { date: row.trade_date, markets: {} }
      chartRow.markets[market] = {
        ...chartRow.markets[market],
        actualBuy: row.actual_buy_settlement,
        actualSell: row.actual_sell_settlement,
      }
      const anchor = row.cfets_hkd_cny_1600
      if (!validNumber(chartRow.ibAnchor) && validNumber(anchor)) chartRow.ibAnchor = anchor
      const centralParity = row.hkd_cny_central_parity
      if (!validNumber(chartRow.centralParity) && validNumber(centralParity)) chartRow.centralParity = centralParity
      rowsByDate.set(row.trade_date, chartRow)
    }
    const actualSettlement = market === 'shanghai'
      ? snapshot.value?.actual_settlement
      : snapshot.value?.shenzhen_actual_settlement
    if (
      actualSettlement
      && date
      && actualSettlement.valid_date === date
      && validNumber(actualSettlement.buy_rate)
      && validNumber(actualSettlement.sell_rate)
    ) {
      const chartRow: DailyChartRow = rowsByDate.get(date) || { date, markets: {} }
      chartRow.markets[market] = {
        ...chartRow.markets[market],
        actualBuy: actualSettlement.buy_rate,
        actualSell: actualSettlement.sell_rate,
      }
      rowsByDate.set(date, chartRow)
    }
    const estimate = market === 'shanghai' ? snapshot.value?.estimate : snapshot.value?.shenzhen_estimate
    if (estimate && date && Number.isFinite(estimate.predicted_buy_settlement) && Number.isFinite(estimate.predicted_sell_settlement)) {
      const chartRow = rowsByDate.get(date) || { date, markets: {} }
      chartRow.markets[market] = {
        ...chartRow.markets[market],
        predictedBuy: estimate.predicted_buy_settlement,
        predictedSell: estimate.predicted_sell_settlement,
      }
      rowsByDate.set(date, chartRow)
    }
  }
  const bid = snapshot.value?.fx.bid
  const ask = snapshot.value?.fx.ask
  if (date && validNumber(bid) && validNumber(ask)) {
    const chartRow = rowsByDate.get(date) || { date, markets: {} }
    chartRow.ibAnchor = (bid + ask) / 2
    rowsByDate.set(date, chartRow)
  }
  const centralParity = snapshot.value?.central_parity
  if (date && centralParity?.trade_date === date && validNumber(centralParity.rate)) {
    const chartRow = rowsByDate.get(date) || { date, markets: {} }
    chartRow.centralParity = centralParity.rate
    rowsByDate.set(date, chartRow)
  }
  const ordered = [...rowsByDate.values()].sort((left, right) => left.date.localeCompare(right.date))
  const startDate = dailyWindowStartDate.value
  if (startDate) return ordered.filter((row) => row.date >= startDate)
  return ordered.slice(-dailyHistoryRequestDays.value)
})

function dailySeriesValue(row: DailyChartRow, series: DailySeries) {
  if (series.value === 'ibAnchor') return row.ibAnchor
  if (series.value === 'centralParity') return row.centralParity
  if (!series.market) return undefined
  return row.markets[series.market]?.[series.value]
}

const visibleDailyPointCount = computed(() => {
  if (!dailyChartRows.value.length) return 0
  return Math.max(1, Math.ceil(dailyChartRows.value.length / dailyXAxisZoom.value))
})

const maxDailyXAxisStart = computed(() => Math.max(0, dailyChartRows.value.length - visibleDailyPointCount.value))

const dailyViewportRows = computed(() => {
  const start = Math.max(0, Math.min(maxDailyXAxisStart.value, dailyXAxisStart.value))
  return dailyChartRows.value.slice(start, start + visibleDailyPointCount.value)
})

const dailyChartDataStats = computed(() => {
  const visibleSeries = displayedDailySeries.value.filter((series) => dailyVisibleSeries.value[series.key])
  const domainSeries = visibleSeries.length ? visibleSeries : displayedDailySeries.value
  const values = dailyViewportRows.value.flatMap((row) => (
    domainSeries.map((series) => dailySeriesValue(row, series)).filter(validNumber)
  ))
  if (values.length < 2) return null
  let min = Math.min(...values)
  let max = Math.max(...values)
  if (max === min) { min -= 0.00005; max += 0.00005 }
  return { min, max }
})

const dailyChart = computed(() => {
  const rows = dailyViewportRows.value
  const candidateSeries = displayedDailySeries.value
  const dataStats = dailyChartDataStats.value
  if (rows.length < 2 || !dataStats) return null
  const width = 880
  const height = 270
  const padding = { left: 54, right: 18, top: 20, bottom: 42 }
  const dataRange = dataStats.max - dataStats.min
  const center = dailyYAxisCenter.value ?? (dataStats.min + dataStats.max) / 2
  const visibleRange = dataRange / dailyYAxisZoom.value
  const min = center - visibleRange / 2
  const max = center + visibleRange / 2
  const x = (index: number) => padding.left + (index / (rows.length - 1)) * (width - padding.left - padding.right)
  const y = (value: number) => padding.top + ((max - value) / (max - min)) * (height - padding.top - padding.bottom)
  const linePath = (series: DailySeries) => {
    let started = false
    return rows.map((row, index) => {
      const value = dailySeriesValue(row, series)
      if (!validNumber(value)) { started = false; return '' }
      const command = started ? 'L' : 'M'
      started = true
      return `${command}${x(index).toFixed(1)},${y(value).toFixed(1)}`
    }).filter(Boolean).join(' ')
  }
  const forecast = (series: DailySeries) => {
	if (!series.market) return null
    const forecastIndex = rows.findIndex((row) => validNumber(dailySeriesValue(row, series)))
    if (forecastIndex < 1) return null
    const prediction = dailySeriesValue(rows[forecastIndex], series)
    if (!validNumber(prediction)) return null
    const actualKey: DailyValueKey = series.value === 'predictedBuy' ? 'actualBuy' : 'actualSell'
    for (let index = forecastIndex - 1; index >= 0; index -= 1) {
      const actual = rows[index].markets[series.market]?.[actualKey]
      if (validNumber(actual)) {
        return { path: `M${x(index).toFixed(1)},${y(actual).toFixed(1)} L${x(forecastIndex).toFixed(1)},${y(prediction).toFixed(1)}`, x: x(forecastIndex), y: y(prediction) }
      }
    }
    return null
  }
  return {
    width,
    height,
    min,
    max,
    x,
    y,
    rows,
    series: candidateSeries.map((series) => ({
      ...series,
      path: series.forecast ? '' : linePath(series),
      forecastSegment: series.forecast ? forecast(series) : null,
    })),
  }
})

function resetDailyXAxisViewport() {
  dailyXAxisZoom.value = 1
  dailyXAxisStart.value = 0
  dailyWheelZoomAccumulator = 0
  dailyWheelPanRemainder = 0
}

function resetDailyYAxisViewport() {
  dailyYAxisZoom.value = 1
  dailyYAxisCenter.value = null
  dailyWheelYAxisZoomAccumulator = 0
}

function resetDailyViewport() {
  resetDailyXAxisViewport()
  resetDailyYAxisViewport()
  dailyHoverIndex.value = null
}

function setDailyXAxisZoomValue(nextZoom: number, anchorRatio = 0.5) {
  const normalizedZoom = Math.max(1, Math.min(maxDailyXAxisZoom, nextZoom || 1))
  const previousVisibleCount = visibleDailyPointCount.value
  const normalizedAnchor = Math.max(0, Math.min(1, anchorRatio))
  const anchorPoint = dailyXAxisStart.value + Math.max(0, previousVisibleCount - 1) * normalizedAnchor
  dailyXAxisZoom.value = normalizedZoom
  const nextVisibleCount = Math.max(1, Math.ceil(dailyChartRows.value.length / normalizedZoom))
  const nextMaxStart = Math.max(0, dailyChartRows.value.length - nextVisibleCount)
  dailyXAxisStart.value = Math.max(0, Math.min(nextMaxStart, Math.round(anchorPoint - Math.max(0, nextVisibleCount - 1) * normalizedAnchor)))
  dailyHoverIndex.value = null
}

function setDailyYAxisZoomValue(nextZoom: number, anchorRatio = 0.5) {
  const dataStats = dailyChartDataStats.value
  const activeChart = dailyChart.value
  if (!dataStats || !activeChart) return
  const normalizedZoom = Math.max(minDailyYAxisZoom, Math.min(maxDailyYAxisZoom, nextZoom || 1))
  const normalizedAnchor = Math.max(0, Math.min(1, anchorRatio))
  const currentRange = activeChart.max - activeChart.min
  const anchorValue = activeChart.max - currentRange * normalizedAnchor
  const nextRange = (dataStats.max - dataStats.min) / normalizedZoom
  dailyYAxisZoom.value = normalizedZoom
  dailyYAxisCenter.value = anchorValue + (normalizedAnchor - 0.5) * nextRange
  dailyHoverIndex.value = null
}

function shiftDailyXAxis(pointDelta: number) {
  dailyXAxisStart.value = Math.max(0, Math.min(maxDailyXAxisStart.value, dailyXAxisStart.value + pointDelta))
  dailyHoverIndex.value = null
}

function dailyChartPointerRatios(event: MouseEvent | WheelEvent) {
  const svg = event.currentTarget as SVGElement
  const rect = svg.getBoundingClientRect()
  return {
    x: rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5,
    y: rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5,
  }
}

function isDailyYAxisZone(xRatio: number) {
  return xRatio <= 54 / 880
}

function isDailyXAxisZone(yRatio: number) {
  return yRatio >= (270 - 42) / 270
}

function zoomDailyXAxisByWheel(event: WheelEvent, anchorRatio: number) {
  dailyWheelZoomAccumulator += event.deltaY
  if (Math.abs(dailyWheelZoomAccumulator) < 24) return
  const direction = dailyWheelZoomAccumulator < 0 ? 1 : -1
  dailyWheelZoomAccumulator = 0
  setDailyXAxisZoomValue(dailyXAxisZoom.value + direction, anchorRatio)
}

function zoomDailyYAxisByWheel(event: WheelEvent, anchorRatio: number) {
  dailyWheelYAxisZoomAccumulator += event.deltaY
  if (Math.abs(dailyWheelYAxisZoomAccumulator) < 24) return
  const direction = dailyWheelYAxisZoomAccumulator < 0 ? 1 : -1
  dailyWheelYAxisZoomAccumulator = 0
  setDailyYAxisZoomValue(dailyYAxisZoom.value + direction, anchorRatio)
}

function handleDailyChartWheel(event: WheelEvent) {
  if (dailyChartRows.value.length <= 30) return
  const pointer = dailyChartPointerRatios(event)
  const horizontalGesture = Math.abs(event.deltaX) > Math.abs(event.deltaY)
  if (isDailyYAxisZone(pointer.x) && event.deltaY !== 0) {
    event.preventDefault()
    zoomDailyYAxisByWheel(event, pointer.y)
    return
  }
  if (isDailyXAxisZone(pointer.y) && event.deltaY !== 0) {
    event.preventDefault()
    zoomDailyXAxisByWheel(event, pointer.x)
    return
  }
  if (horizontalGesture && maxDailyXAxisStart.value > 0) {
    event.preventDefault()
    const pointsPerPixel = Math.max(1, visibleDailyPointCount.value / 16) / 48
    dailyWheelPanRemainder += event.deltaX * pointsPerPixel
    const wholePointDelta = dailyWheelPanRemainder > 0 ? Math.floor(dailyWheelPanRemainder) : Math.ceil(dailyWheelPanRemainder)
    if (wholePointDelta !== 0) {
      dailyWheelPanRemainder -= wholePointDelta
      shiftDailyXAxis(wholePointDelta)
    }
    return
  }
  if (!horizontalGesture && event.deltaY !== 0) {
    event.preventDefault()
    zoomDailyXAxisByWheel(event, pointer.x)
  }
}

function startDailyChartDrag(event: MouseEvent) {
  const pointer = dailyChartPointerRatios(event)
  if (maxDailyXAxisStart.value <= 0 || event.button !== 0 || isDailyYAxisZone(pointer.x) || isDailyXAxisZone(pointer.y)) return
  isDailyChartDragging.value = true
  dailyDragStartX = event.clientX
  dailyDragStartAxisStart = dailyXAxisStart.value
  dailyHoverIndex.value = null
  event.preventDefault()
}

function updateDailyChartDrag(event: MouseEvent) {
  if (!isDailyChartDragging.value) return false
  const svg = event.currentTarget as SVGElement
  const rect = svg.getBoundingClientRect()
  if (rect.width <= 0) return true
  const pointDelta = ((dailyDragStartX - event.clientX) / rect.width) * visibleDailyPointCount.value
  dailyXAxisStart.value = Math.max(0, Math.min(maxDailyXAxisStart.value, Math.round(dailyDragStartAxisStart + pointDelta)))
  dailyHoverIndex.value = null
  return true
}

function endDailyChartDrag() {
  isDailyChartDragging.value = false
}

const dailyHover = computed(() => {
  const activeChart = dailyChart.value
  const index = dailyHoverIndex.value
  if (!activeChart || index === null) return null
  const row = activeChart.rows[index]
  return row ? { row, index, x: activeChart.x(index) } : null
})

const minuteHover = computed(() => {
  const activeChart = chart.value
  const index = minuteHoverIndex.value
  if (!activeChart || index === null) return null
  const row = chartRows.value[index]
  return row ? { row, index, x: activeChart.x(index) } : null
})

const dailyHoverValues = computed(() => {
  if (!dailyHover.value) return []
  const row = dailyHover.value.row
  return displayedDailySeries.value
    .filter((series) => dailyVisibleSeries.value[series.key])
    .map((series) => ({ ...series, value: dailySeriesValue(row, series) }))
    .filter(hasChartValue)
})

const minuteHoverValues = computed(() => {
  if (!minuteHover.value) return []
  const row = minuteHover.value.row
  return visibleMinuteSeries.value
    .map((series) => ({ ...series, value: series.valueAt(row) }))
    .filter(hasChartValue)
})

function isDailySeriesVisible(key: DailySeriesKey) {
  return dailyVisibleSeries.value[key]
}

function setDailySeriesVisible(key: DailySeriesKey, visible: boolean) {
  dailyVisibleSeries.value[key] = visible
  resetDailyYAxisViewport()
  dailyHoverIndex.value = null
}

function isMinuteSeriesVisible(key: MinuteSeriesKey) {
  return minuteVisibleSeries.value[key]
}

function setMinuteSeriesVisible(key: MinuteSeriesKey, visible: boolean) {
  minuteVisibleSeries.value[key] = visible
  minuteHoverIndex.value = null
}

function closestChartIndex(event: MouseEvent, rowCount: number, width: number, left: number, right: number) {
  const svg = event.currentTarget as SVGElement
  const rect = svg.getBoundingClientRect()
  if (rowCount < 1 || !rect.width) return null
  const viewX = ((event.clientX - rect.left) / rect.width) * width
  const ratio = (viewX - left) / (width - left - right)
  return Math.max(0, Math.min(rowCount - 1, Math.round(ratio * (rowCount - 1))))
}

function updateTooltipLeft(event: MouseEvent, root: HTMLElement | null, setLeft: (left: number) => void) {
  const rootRect = root?.getBoundingClientRect()
  if (!rootRect) return
  const tooltipWidth = 242
  setLeft(Math.max(8, Math.min(rootRect.width - tooltipWidth - 8, event.clientX - rootRect.left + 10)))
}

function onDailyMouseMove(event: MouseEvent) {
  const activeChart = dailyChart.value
  if (!activeChart) return
  dailyHoverIndex.value = closestChartIndex(event, activeChart.rows.length, activeChart.width, 54, 18)
  updateTooltipLeft(event, dailyChartRoot.value, (left) => { dailyTooltipLeft.value = left })
}

function onDailySvgMouseMove(event: MouseEvent) {
  if (updateDailyChartDrag(event)) return
  onDailyMouseMove(event)
}

function onDailySvgMouseLeave() {
  endDailyChartDrag()
  clearDailyHover()
}

function onMinuteMouseMove(event: MouseEvent) {
  const activeChart = chart.value
  if (!activeChart) return
  minuteHoverIndex.value = closestChartIndex(event, chartRows.value.length, activeChart.width, 54, 18)
  updateTooltipLeft(event, minuteChartRoot.value, (left) => { minuteTooltipLeft.value = left })
}

function clearDailyHover() {
  dailyHoverIndex.value = null
}

function clearMinuteHover() {
  minuteHoverIndex.value = null
}

watch(() => [dailyChartRows.value.length, visibleDailyPointCount.value], () => {
  dailyXAxisStart.value = Math.max(0, Math.min(maxDailyXAxisStart.value, dailyXAxisStart.value))
})

onMounted(() => {
  void loadLatest()
  latestTimer = window.setInterval(() => void loadLatest(true), 3_000)
  historyTimer = window.setInterval(() => void loadHistory(), 30_000)
})

onBeforeUnmount(() => {
  activeRequest?.abort()
  dailyRequest?.abort()
  if (latestTimer !== undefined) window.clearInterval(latestTimer)
  if (historyTimer !== undefined) window.clearInterval(historyTimer)
})
</script>

<template>
  <section class="private-page-stack private-hkfx-page">
    <div class="private-route-topbar">
      <button type="button" class="private-back-button" @click="$emit('back')">← 返回 Private 目录</button>
      <div class="private-route-actions">
        <button type="button" class="private-back-button" @click="$emit('openCloseHistory')">收盘估值审计 →</button>
        <span v-if="snapshot" class="private-state-pill" :class="statusClass(activeStatus)">{{ activeStatus }}</span>
        <span>{{ snapshot ? formatTime(snapshot.generated_at) : '正在连接…' }}</span>
      </div>
    </div>

    <div v-if="error" class="private-alert private-alert-error" role="alert"><strong>数据读取异常</strong><span>{{ error }}</span></div>
    <div v-if="loading && !snapshot" class="private-loading">正在读取港股通汇率估值…</div>

    <template v-if="snapshot">
      <section class="private-panel private-hkfx-hero">
        <div class="private-section-heading">
          <div><h2>港股通结算汇率实时估值</h2></div>
        </div>

        <article class="private-hkfx-central-parity-card">
          <div>
            <span>人民币对港币汇率中间价</span>
            <small>HKD/CNY · 1 港元对应人民币</small>
          </div>
          <strong>{{ formatRate(snapshot.central_parity?.rate, 5) }}</strong>
          <em v-if="snapshot.central_parity">适用日 {{ snapshot.central_parity.trade_date }} · 09:15 公布后当日缓存</em>
          <em v-else>等待当日 09:15 官方公布</em>
        </article>

        <div class="private-hkfx-primary-grid">
          <section v-for="card in marketCards" :key="card.market" class="private-hkfx-summary-card private-hkfx-market-summary" :class="{ 'is-active': activeMarket === card.market }">
            <button type="button" class="private-hkfx-market-summary-head" :aria-pressed="activeMarket === card.market" @click="selectMarket(card.market)">
              <span>{{ card.label }}</span>
              <small>双边结算汇率预测</small>
            </button>
            <article class="private-hkfx-summary-row sell-side">
              <div><span>买入港股使用</span><small>预测卖出结算汇兑比率</small></div>
              <strong>{{ formatRate(card.estimate?.predicted_sell_settlement) }}</strong>
              <em :class="rateClass(card.estimate?.sell_live_vs_estimate)">{{ formatLiveHKDVsEstimate(card.estimate?.sell_live_vs_estimate) }}</em>
            </article>
            <article class="private-hkfx-summary-row buy-side">
              <div><span>卖出港股使用</span><small>预测买入结算汇兑比率</small></div>
              <strong>{{ formatRate(card.estimate?.predicted_buy_settlement) }}</strong>
              <em :class="rateClass(card.estimate?.buy_live_vs_estimate)">{{ formatLiveHKDVsEstimate(card.estimate?.buy_live_vs_estimate) }}</em>
            </article>
            <article class="private-hkfx-summary-row neutral">
              <div><span>参考汇率中点 M</span><small>{{ snapshot.reference ? `${formatRate(snapshot.reference.buy_rate, 5)} / ${formatRate(snapshot.reference.sell_rate, 5)}` : '等待上交所参考汇率' }}</small></div>
              <strong>{{ formatRate(snapshot.reference?.mid_rate) }}</strong>
              <em>适用日 {{ snapshot.reference?.valid_date || '—' }}</em>
            </article>
          </section>
        </div>
        <p class="private-hkfx-summary-note">点击任一市场卡片，可切换下方日线与分钟轨迹。上海、深圳盘中输入及来源状态同时展示；东财盘中金额每 2 分钟抓取一次。</p>
      </section>

      <section class="private-panel">
        <div class="private-section-heading private-hkfx-daily-heading">
          <div><h2>{{ dailyChartTitle }}</h2></div>
          <div class="private-hkfx-daily-options">
            <div class="private-hkfx-daily-option-group" aria-label="日线市场范围">
              <button type="button" :class="{ active: dailyDisplayMode === 'shanghai' }" :aria-pressed="dailyDisplayMode === 'shanghai'" @click="selectDailyDisplayMode('shanghai')">仅上海</button>
              <button type="button" :class="{ active: dailyDisplayMode === 'shenzhen' }" :aria-pressed="dailyDisplayMode === 'shenzhen'" @click="selectDailyDisplayMode('shenzhen')">仅深圳</button>
              <button type="button" :class="{ active: dailyDisplayMode === 'both' }" :aria-pressed="dailyDisplayMode === 'both'" @click="selectDailyDisplayMode('both')">两市叠加</button>
            </div>
            <div class="private-hkfx-daily-option-group" aria-label="日线时间范围">
              <button v-for="option in dailyWindowOptions" :key="option.key" type="button" :class="{ active: dailyWindow === option.key }" :aria-pressed="dailyWindow === option.key" @click="selectDailyWindow(option.key)">{{ option.label }}</button>
            </div>
            <button type="button" class="private-hkfx-daily-viewport-reset" @click="resetDailyViewport">复位视图</button>
            <span>实线为实际结算 · 虚线末点为当日预测 · 蓝线为 CFETS 16:00 参考汇率 · 金线为每日人民币对港币中间价。长周期图内上下滚动缩放；横向滚动或拖拽平移。</span>
          </div>
        </div>
        <div v-if="dailyChart" ref="dailyChartRoot" class="private-hkfx-chart-wrap private-hkfx-daily-chart-wrap" :class="{ 'is-pannable': maxDailyXAxisStart > 0, 'is-dragging': isDailyChartDragging }">
          <svg :viewBox="`0 0 ${dailyChart.width} ${dailyChart.height}`" role="img" aria-label="港股通实际结算汇率、当日预测、CFETS 参考汇率与人民币对港币中间价日线图" @wheel="handleDailyChartWheel" @mousedown="startDailyChartDrag" @mousemove="onDailySvgMouseMove" @mouseup="endDailyChartDrag" @mouseleave="onDailySvgMouseLeave">
            <line x1="54" y1="20" x2="54" y2="228" class="private-hkfx-axis" />
            <line x1="54" y1="228" x2="862" y2="228" class="private-hkfx-axis" />
            <rect class="private-hkfx-chart-hit" x="54" y="20" width="808" height="208" />
            <template v-for="series in dailyChart.series" :key="series.key">
              <path v-if="!series.forecast && isDailySeriesVisible(series.key) && series.path" :d="series.path" class="private-hkfx-line" :style="{ stroke: series.color }" />
              <template v-else-if="series.forecast && isDailySeriesVisible(series.key) && series.forecastSegment">
                <path :d="series.forecastSegment.path" class="private-hkfx-forecast" :style="{ stroke: series.color }" />
                <circle :cx="series.forecastSegment.x" :cy="series.forecastSegment.y" r="5" class="private-hkfx-forecast-point" :style="{ stroke: series.color }" />
              </template>
            </template>
            <g v-if="dailyHover && dailyHoverValues.length" class="private-hkfx-chart-hover">
              <line :x1="dailyHover.x" :x2="dailyHover.x" y1="20" y2="228" />
              <circle v-for="item in dailyHoverValues" :key="`daily-hover-${item.key}`" :cx="dailyHover.x" :cy="dailyChart.y(item.value)" r="4" :fill="item.color" />
            </g>
            <text x="6" y="26">{{ formatRate(dailyChart.max) }}</text><text x="6" y="228">{{ formatRate(dailyChart.min) }}</text>
            <template v-for="(row, index) in dailyChart.rows" :key="row.date">
              <text v-if="isDailyTickVisible(index, dailyChart.rows.length)" :x="54 + (index / (dailyChart.rows.length - 1)) * 808" y="254" text-anchor="middle">{{ formatDay(row.date) }}</text>
            </template>
          </svg>
          <div v-if="dailyHover && dailyHoverValues.length" class="private-hkfx-chart-tooltip" :style="{ left: `${dailyTooltipLeft}px`, top: '12px' }">
            <strong>{{ dailyHover.row.date }}</strong>
            <span v-for="item in dailyHoverValues" :key="`daily-tooltip-${item.key}`"><i :style="{ background: item.color }"></i>{{ item.label }} {{ formatRate(item.value) }}</span>
          </div>
          <div class="private-hkfx-legend private-hkfx-daily-legend">
            <label v-for="item in displayedDailySeries" :key="item.key" class="private-hkfx-legend-item" :class="{ 'is-hidden': !isDailySeriesVisible(item.key), forecast: item.forecast }" :title="`${isDailySeriesVisible(item.key) ? '点击隐藏' : '点击显示'}：${item.label}`">
              <input type="checkbox" :checked="isDailySeriesVisible(item.key)" :aria-label="`${isDailySeriesVisible(item.key) ? '隐藏' : '显示'} ${item.label}`" @change="setDailySeriesVisible(item.key, ($event.target as HTMLInputElement).checked)">
              <i :style="{ '--private-series-color': item.color }"></i><span>{{ item.label }}</span>
            </label>
          </div>
        </div>
        <div v-else class="private-inline-empty">正在读取历史实际结算汇率，并等待当日预测值。</div>
      </section>

      <section class="private-panel">
        <div class="private-hkfx-input-market-grid">
          <section v-for="market in inputMarkets" :key="market.market" class="private-hkfx-input-market">
            <div class="private-hkfx-input-market-heading"><strong>{{ market.label }}</strong><span>盘中输入</span></div>
            <div class="private-hkfx-input-grid private-hkfx-market-input-grid">
              <article>
                <span>东财 {{ market.label }}买入成交额</span><strong>{{ formatAmount(market.flow?.buy_amount_hkd_100m) }}</strong>
                <small>卖出 {{ formatAmount(market.flow?.sell_amount_hkd_100m) }} · 总额 {{ formatAmount(market.flow?.total_amount_hkd_100m) }}</small>
              </article>
              <article>
                <span>外汇交易中心 HKD/CNY</span><strong>{{ formatRate(snapshot.fx.bid) }} / {{ formatRate(snapshot.fx.ask) }}</strong>
                <small>BID / ASK · {{ snapshot.fx.healthy ? '正常' : '异常' }} · {{ formatTime(snapshot.fx.observed_at) }}</small>
              </article>
              <article>
                <span>CFETS HKD/CNY 即时报价中点</span><strong>{{ formatRate(market.estimate?.market_hkd_cny) }}</strong>
                <small>模型方向价 {{ formatRate(market.estimate?.selected_hkd_cny) }}</small>
              </article>
              <article>
                <span>前一交易日实际结算</span><strong>{{ formatRate(market.previousSettlement?.buy_rate) }} / {{ formatRate(market.previousSettlement?.sell_rate) }}</strong>
                <small v-if="market.previousSettlement">买入 / 卖出 · {{ market.previousSettlement.valid_date }}</small>
                <small v-else>前日实际结算待接入，故不展示涨跌幅</small>
              </article>
            </div>
          </section>
        </div>

        <div class="private-table-wrap">
          <table class="private-data-table private-hkfx-source-table">
            <thead><tr><th>来源</th><th>状态/数值</th><th>最近抓取或报价</th><th>可审计信息</th></tr></thead>
            <tbody>
              <tr v-for="market in inputMarkets" :key="`source-${market.market}`">
                <td><a href="https://data.eastmoney.com/hsgt/hsgtV2.html" target="_blank" rel="noopener noreferrer">东财 {{ market.label }}盘中成交额</a></td>
                <td>{{ market.flow ? `${market.flow.samples} 次采样` : '等待当日数据' }}</td>
                <td>{{ formatTime(market.flow?.published_at) }}</td>
                <td>抓取 {{ formatTime(market.flow?.fetched_at) }} · 数值变化 {{ market.flow?.changes ?? 0 }} 次 · 外部抓取 2 分钟</td>
              </tr>
              <tr><td>上交所参考汇率</td><td>{{ snapshot.reference ? '已获取' : '缺失，每 2 分钟重试' }}</td><td>{{ formatTime(snapshot.reference?.fetched_at) }}</td><td>公布日 {{ snapshot.reference?.published_date || '—' }}</td></tr>
              <tr>
                <td><a href="https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2" target="_blank" rel="noopener noreferrer">人民币对港币中间价</a></td>
                <td>{{ snapshot.central_parity ? `${formatRate(snapshot.central_parity.rate, 5)} HKD/CNY` : '等待当日公布' }}</td>
                <td>{{ formatTime(snapshot.central_parity?.fetched_at) }}</td>
                <td>公布日 {{ snapshot.central_parity?.trade_date || '—' }} · 获取成功后当日不再请求</td>
              </tr>
              <tr><td>外汇交易中心</td><td>{{ snapshot.fx.healthy ? 'HEALTHY' : 'UNAVAILABLE' }}</td><td>{{ formatTime(snapshot.fx.observed_at) }}</td><td>{{ snapshot.fx.error || '网站后端每 2 分钟抓取 · 09:10–16:00' }}</td></tr>
              <tr><td>处理后 CSV</td><td>{{ snapshot.storage.kind }}</td><td>{{ formatTime(snapshot.storage.last_write_at) }}</td><td>{{ snapshot.storage.last_error || '每分钟一行，不保存原始流' }}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="private-panel">
        <div class="private-section-heading"><div><p class="private-kicker">MINUTE SERIES</p><h2>{{ activeMarketLabel }}当日双边估值轨迹</h2></div><span>{{ chartRows.length }} 个有效点</span></div>
        <div v-if="chart" ref="minuteChartRoot" class="private-hkfx-chart-wrap">
          <svg :viewBox="`0 0 ${chart.width} ${chart.height}`" role="img" aria-label="港股通结算汇率分钟估值">
            <line x1="54" y1="20" x2="54" y2="218" class="private-hkfx-axis" />
            <line x1="54" y1="218" x2="862" y2="218" class="private-hkfx-axis" />
            <rect class="private-hkfx-chart-hit" x="54" y="20" width="808" height="198" @mousemove="onMinuteMouseMove" @mouseleave="clearMinuteHover" />
            <path v-if="isMinuteSeriesVisible('sell') && chart.sell" :d="chart.sell" class="private-hkfx-line sell" />
            <path v-if="isMinuteSeriesVisible('buy') && chart.buy" :d="chart.buy" class="private-hkfx-line buy" />
            <path v-if="isMinuteSeriesVisible('ib') && chart.ib" :d="chart.ib" class="private-hkfx-line ib" />
            <g v-if="minuteHover && minuteHoverValues.length" class="private-hkfx-chart-hover">
              <line :x1="minuteHover.x" :x2="minuteHover.x" y1="20" y2="218" />
              <circle v-for="item in minuteHoverValues" :key="`minute-hover-${item.key}`" :cx="minuteHover.x" :cy="chart.y(item.value)" r="4" :fill="item.color" />
            </g>
            <text x="6" y="26">{{ formatRate(chart.max) }}</text><text x="6" y="218">{{ formatRate(chart.min) }}</text>
            <text x="54" y="242">{{ chart.first }}</text><text x="830" y="242">{{ chart.last }}</text>
          </svg>
          <div v-if="minuteHover && minuteHoverValues.length" class="private-hkfx-chart-tooltip" :style="{ left: `${minuteTooltipLeft}px`, top: '12px' }">
            <strong>{{ formatTime(minuteHover.row.timestamp) }}</strong>
            <span v-for="item in minuteHoverValues" :key="`minute-tooltip-${item.key}`"><i :style="{ background: item.color }"></i>{{ item.label }} {{ formatRate(item.value) }}</span>
          </div>
          <div class="private-hkfx-legend">
            <label v-for="item in minuteSeries" :key="item.key" class="private-hkfx-legend-item" :class="{ 'is-hidden': !isMinuteSeriesVisible(item.key) }" :title="`${isMinuteSeriesVisible(item.key) ? '点击隐藏' : '点击显示'}：${item.label}`">
              <input type="checkbox" :checked="isMinuteSeriesVisible(item.key)" :aria-label="`${isMinuteSeriesVisible(item.key) ? '隐藏' : '显示'} ${item.label}`" @change="setMinuteSeriesVisible(item.key, ($event.target as HTMLInputElement).checked)">
              <i :style="{ '--private-series-color': item.color }"></i><span>{{ item.label }}</span>
            </label>
          </div>
        </div>
        <div v-else class="private-inline-empty">当日尚未积累两个有效分钟点。</div>

        <div class="private-table-wrap">
          <table class="private-data-table private-hkfx-minute-table">
            <thead><tr><th>分钟</th><th>状态</th><th>东财 {{ activeMarketLabel }}买/卖（亿港元）</th><th>q</th><th>HKD/CNY BID/ASK</th><th>预测买入结算</th><th>预测卖出结算</th></tr></thead>
            <tbody>
              <tr v-for="row in recentRows" :key="row.timestamp">
                <td>{{ row.timestamp.slice(11, 16) }}</td><td>{{ row.status }}</td>
                <td>{{ formatRate(row.flow_buy_amount_hkd_100m, 2) }} / {{ formatRate(row.flow_sell_amount_hkd_100m, 2) }}</td>
                <td>{{ formatPercent(row.net_ratio, 2) }}</td><td>{{ formatRate(row.hkd_cny_bid) }} / {{ formatRate(row.hkd_cny_ask) }}</td>
                <td>{{ formatRate(row.predicted_buy_settlement) }}</td><td>{{ formatRate(row.predicted_sell_settlement) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

    </template>
  </section>
</template>
