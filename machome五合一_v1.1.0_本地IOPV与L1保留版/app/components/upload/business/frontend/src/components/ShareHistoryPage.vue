<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getFundShareHistory } from '../lib/api'
import type { ShareHistoryRecord, ShareHistoryResponse } from '../lib/types'

type ShareHistoryLoader = (symbol: string, days: number) => Promise<ShareHistoryResponse>
type QuickWindow = '30' | '60' | '1y' | '5y' | 'all'

const props = defineProps<{
  history: ShareHistoryResponse
  loadHistory?: ShareHistoryLoader
  showExtendedWindows?: boolean
}>()
const chartWidth = 960
const chartHeight = 320
const defaultChartDays = 30
const defaultLoadedDays = 60
const oneYearHistoryDays = 366
const fiveYearHistoryDays = 365 * 5 + 2
const allHistoryDays = 10_000
const maxXAxisZoom = 16
const minYAxisZoom = 0.25
const maxYAxisZoom = 16
const pointMarkerMaxRangeDays = 100
const chartPadding = {
  top: 34,
  right: 22,
  bottom: 48,
  left: 70,
}

defineEmits<{
  backToFund: [symbol: string]
}>()

function fmt(value: number | null | undefined, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) return ''
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function fmtPct(value: number | null | undefined, digits = 2) {
  const formatted = fmt(value, digits)
  return formatted ? `${formatted}%` : ''
}

const hoverIndex = ref<number | null>(null)
const chartMode = ref<'recent' | 'range'>('recent')
const recentChartDays = ref(defaultChartDays)
const activeQuickWindow = ref<QuickWindow | null>('30')
const customStartDate = ref('')
const customEndDate = ref('')
const xAxisZoom = ref(1)
const xAxisStart = ref(0)
const yAxisZoom = ref(1)
const yAxisCenter = ref<number | null>(null)
const isChartDragging = ref(false)
const loadedRows = ref<ShareHistoryRecord[]>(props.history.rows.slice())
const isLoadingRange = ref(false)
const rangeLoadError = ref('')
let wheelZoomAccumulator = 0
let wheelPanRemainder = 0
let wheelYAxisZoomAccumulator = 0
let chartDragStartX = 0
let chartDragStartAxisStart = 0

function pctClass(value: number | null | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value)) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function chartX(index: number, total: number) {
  const innerWidth = chartWidth - chartPadding.left - chartPadding.right
  if (total <= 1) return chartPadding.left + innerWidth / 2
  return chartPadding.left + (innerWidth * index) / (total - 1)
}

function chartY(value: number, min: number, max: number) {
  const innerHeight = chartHeight - chartPadding.top - chartPadding.bottom
  if (max <= min) return chartPadding.top + innerHeight / 2
  return chartPadding.top + ((max - value) / (max - min)) * innerHeight
}

function mergeRows(current: ShareHistoryRecord[], incoming: ShareHistoryRecord[]) {
  const byDate = new Map<string, ShareHistoryRecord>()
  for (const row of current) {
    byDate.set(row.share_date, row)
  }
  for (const row of incoming) {
    byDate.set(row.share_date, row)
  }
  return Array.from(byDate.values()).sort((left, right) =>
    right.share_date.localeCompare(left.share_date),
  )
}

const allChartRows = computed(() =>
  loadedRows.value.filter((row) => Number.isFinite(row.shares_10k)),
)
const tableRows = computed(() => loadedRows.value)
const chartBounds = computed(() => {
  const rows = allChartRows.value
  if (!rows.length) return null
  return {
    latest: rows[0].share_date,
    oldest: rows[rows.length - 1].share_date,
  }
})
const customRangeError = computed(() => {
  if (!customStartDate.value && !customEndDate.value) return ''
  const bounds = chartBounds.value
  if (!bounds) return ''
  const start = customStartDate.value || bounds.oldest
  const end = customEndDate.value || bounds.latest
  if (start > end) return '开始日期不能晚于结束日期'
  return ''
})
const selectedChartRows = computed(() => {
  const rows = allChartRows.value
  if (!rows.length) return []
  if (chartMode.value === 'range') {
    const bounds = chartBounds.value
    if (!bounds) return []
    const start = customStartDate.value || bounds.oldest
    const end = customEndDate.value || bounds.latest
    return rows
      .filter((row) => row.share_date >= start && row.share_date <= end)
      .reverse()
  }
  return rows.slice(0, recentChartDays.value).reverse()
})
const visibleChartPointCount = computed(() => {
  if (!selectedChartRows.value.length) return 0
  return Math.max(1, Math.ceil(selectedChartRows.value.length / xAxisZoom.value))
})
const maxXAxisStart = computed(() => Math.max(0, selectedChartRows.value.length - visibleChartPointCount.value))
const chartRows = computed(() => {
  const start = Math.min(xAxisStart.value, maxXAxisStart.value)
  return selectedChartRows.value.slice(start, start + visibleChartPointCount.value)
})
const selectedRangeDays = computed(() => {
  const rows = selectedChartRows.value
  if (rows.length <= 1) return rows.length
  const oldestUTC = dateToUTC(rows[0].share_date)
  const latestUTC = dateToUTC(rows[rows.length - 1].share_date)
  if (!Number.isFinite(oldestUTC) || !Number.isFinite(latestUTC) || latestUTC < oldestUTC) {
    return rows.length
  }
  return Math.floor((latestUTC - oldestUTC) / (24 * 60 * 60 * 1000)) + 1
})
const useWideHistoryLayout = computed(() => Boolean(props.showExtendedWindows) && selectedRangeDays.value >= 365)
const chartDataStats = computed(() => {
  if (!chartRows.value.length) {
    return null
  }
  const values = chartRows.value.map((row) => row.shares_10k)
  let min = Math.min(...values)
  let max = Math.max(...values)
  if (min === max) {
    const pad = Math.max(1, Math.abs(min) * 0.02)
    min -= pad
    max += pad
  }
  return { min, max }
})
const chartStats = computed(() => {
  const dataStats = chartDataStats.value
  if (!dataStats) return null
  const dataRange = dataStats.max - dataStats.min
  const center = yAxisCenter.value ?? (dataStats.min + dataStats.max) / 2
  const visibleRange = dataRange / yAxisZoom.value
  return {
    min: center - visibleRange / 2,
    max: center + visibleRange / 2,
  }
})
const chartPath = computed(() => {
  const stats = chartStats.value
  if (!stats || !chartRows.value.length) return ''
  return chartRows.value
    .map((row, index) => {
      const command = index === 0 ? 'M' : 'L'
      return `${command}${chartX(index, chartRows.value.length).toFixed(2)},${chartY(row.shares_10k, stats.min, stats.max).toFixed(2)}`
    })
    .join(' ')
})
const chartTicks = computed(() => {
  const stats = chartStats.value
  if (!stats) return []
  return Array.from({ length: 5 }, (_, index) => {
    const value = stats.max - ((stats.max - stats.min) * index) / 4
    return {
      value,
      y: chartY(value, stats.min, stats.max),
    }
  })
})
const chartDateLabels = computed(() => {
  const rows = chartRows.value
  if (!rows.length) return []
  const indexes = Array.from(new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1]))
  return indexes.map((index) => ({
    index,
    date: rows[index].share_date.slice(5),
    x: chartX(index, rows.length),
    align: index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle',
  }))
})
const chartRangeDays = computed(() => {
  const rows = chartRows.value
  if (rows.length <= 1) return rows.length
  const oldestUTC = dateToUTC(rows[0].share_date)
  const latestUTC = dateToUTC(rows[rows.length - 1].share_date)
  if (!Number.isFinite(oldestUTC) || !Number.isFinite(latestUTC) || latestUTC < oldestUTC) {
    return rows.length
  }
  const dayMs = 24 * 60 * 60 * 1000
  return Math.floor((latestUTC - oldestUTC) / dayMs) + 1
})
const showChartPointMarkers = computed(() => chartRangeDays.value <= pointMarkerMaxRangeDays)
const chartSubtitle = computed(() => {
  if (!chartRows.value.length) return '暂无可展示记录'
  return `${chartRows.value[0].share_date} - ${chartRows.value[chartRows.value.length - 1].share_date} · ${chartRows.value.length}条`
})
const chartAriaLabel = computed(() => {
  if (chartMode.value === 'range') {
    const bounds = chartBounds.value
    if (!bounds) return `${props.history.symbol} 份额走势`
    return `${props.history.symbol} ${customStartDate.value || bounds.oldest} 到 ${customEndDate.value || bounds.latest} 份额走势`
  }
  return `${props.history.symbol} 最近${recentChartDays.value}条份额走势`
})
const chartEmptyNote = computed(() => {
  if (!allChartRows.value.length) return '暂无可绘制的份额历史。'
  if (chartMode.value === 'range') return '所选时间段暂无可绘制记录，请调整日期。'
  return '暂无可绘制的份额历史。'
})
const hoverPoint = computed(() => {
  const index = hoverIndex.value
  const stats = chartStats.value
  if (index === null || !stats || index < 0 || index >= chartRows.value.length) return null
  const row = chartRows.value[index]
  return {
    row,
    x: chartX(index, chartRows.value.length),
    y: chartY(row.shares_10k, stats.min, stats.max),
  }
})
const hoverTooltipStyle = computed(() => {
  const point = hoverPoint.value
  if (!point) return {}
  return {
    left: `${(point.x / chartWidth) * 100}%`,
    top: `${(point.y / chartHeight) * 100}%`,
  }
})
const hoverTooltipClass = computed(() => ({
  'share-chart-tooltip': true,
  'align-right': (hoverPoint.value?.x || 0) > chartWidth - 180,
  'align-left': (hoverPoint.value?.x || 0) < chartPadding.left + 80,
  'align-bottom': (hoverPoint.value?.y || 0) < chartPadding.top + 82,
}))

function updateChartHover(event: MouseEvent) {
  if (updateChartDrag(event)) return
  const rows = chartRows.value
  if (!rows.length) {
    hoverIndex.value = null
    return
  }
  const rect = (event.currentTarget as SVGSVGElement).getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * chartWidth
  const innerLeft = chartPadding.left
  const innerRight = chartWidth - chartPadding.right
  if (x < innerLeft || x > innerRight) {
    hoverIndex.value = null
    return
  }
  const ratio = rows.length <= 1 ? 0 : (x - innerLeft) / (innerRight - innerLeft)
  const index = Math.round(ratio * (rows.length - 1))
  hoverIndex.value = Math.max(0, Math.min(rows.length - 1, index))
}

function clearChartHover() {
  hoverIndex.value = null
  endChartDrag()
}

function yLabelStyle(y: number) {
  return {
    top: `${(y / chartHeight) * 100}%`,
  }
}

function xLabelStyle(x: number) {
  return {
    left: `${(x / chartWidth) * 100}%`,
  }
}

function rowKey(row: ShareHistoryRecord) {
  return `${row.symbol}-${row.share_date}`
}

function resetChartSelection() {
  chartMode.value = 'recent'
  recentChartDays.value = defaultChartDays
  activeQuickWindow.value = '30'
  customStartDate.value = ''
  customEndDate.value = ''
  rangeLoadError.value = ''
  hoverIndex.value = null
  resetChartViewport()
}

function selectRecentWindow(days: number) {
  chartMode.value = 'recent'
  recentChartDays.value = Math.max(1, days)
  activeQuickWindow.value = days === defaultChartDays ? '30' : '60'
  hoverIndex.value = null
  resetChartViewport()
}

function resetXAxisViewport() {
  xAxisZoom.value = 1
  xAxisStart.value = 0
  wheelZoomAccumulator = 0
  wheelPanRemainder = 0
}

function resetYAxisViewport() {
  yAxisZoom.value = 1
  yAxisCenter.value = null
  wheelYAxisZoomAccumulator = 0
}

function resetChartViewport() {
  resetXAxisViewport()
  resetYAxisViewport()
}

function setXAxisZoomValue(nextZoom: number, anchorRatio = 0.5) {
  const normalizedZoom = Math.max(1, Math.min(maxXAxisZoom, nextZoom || 1))
  const previousVisibleCount = visibleChartPointCount.value
  const normalizedAnchor = Math.max(0, Math.min(1, anchorRatio))
  const anchorPoint = xAxisStart.value + Math.max(0, previousVisibleCount - 1) * normalizedAnchor
  xAxisZoom.value = normalizedZoom
  const nextVisibleCount = Math.max(1, Math.ceil(selectedChartRows.value.length / normalizedZoom))
  const nextMaxStart = Math.max(0, selectedChartRows.value.length - nextVisibleCount)
  xAxisStart.value = Math.max(0, Math.min(nextMaxStart, Math.round(anchorPoint - Math.max(0, nextVisibleCount - 1) * normalizedAnchor)))
  hoverIndex.value = null
}

function setYAxisZoomValue(nextZoom: number, anchorRatio = 0.5) {
  const dataStats = chartDataStats.value
  const currentStats = chartStats.value
  if (!dataStats || !currentStats) return
  const normalizedZoom = Math.max(minYAxisZoom, Math.min(maxYAxisZoom, nextZoom || 1))
  const normalizedAnchor = Math.max(0, Math.min(1, anchorRatio))
  const currentRange = currentStats.max - currentStats.min
  const anchorValue = currentStats.max - currentRange * normalizedAnchor
  const nextRange = (dataStats.max - dataStats.min) / normalizedZoom
  yAxisZoom.value = normalizedZoom
  yAxisCenter.value = anchorValue + (normalizedAnchor - 0.5) * nextRange
  hoverIndex.value = null
}

function shiftXAxis(pointDelta: number) {
  xAxisStart.value = Math.max(0, Math.min(maxXAxisStart.value, xAxisStart.value + pointDelta))
  hoverIndex.value = null
}

function chartPointerRatios(event: MouseEvent | WheelEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  return {
    x: rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5,
    y: rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5,
  }
}

function isYAxisZone(xRatio: number) {
  return xRatio <= chartPadding.left / chartWidth
}

function isXAxisZone(yRatio: number) {
  return yRatio >= (chartHeight - chartPadding.bottom) / chartHeight
}

function zoomXAxisByWheel(event: WheelEvent, anchorRatio: number) {
  wheelZoomAccumulator += event.deltaY
  if (Math.abs(wheelZoomAccumulator) < 24) return
  const zoomDirection = wheelZoomAccumulator < 0 ? 1 : -1
  wheelZoomAccumulator = 0
  setXAxisZoomValue(xAxisZoom.value + zoomDirection, anchorRatio)
}

function zoomYAxisByWheel(event: WheelEvent, anchorRatio: number) {
  wheelYAxisZoomAccumulator += event.deltaY
  if (Math.abs(wheelYAxisZoomAccumulator) < 24) return
  const zoomDirection = wheelYAxisZoomAccumulator < 0 ? 1 : -1
  wheelYAxisZoomAccumulator = 0
  setYAxisZoomValue(yAxisZoom.value + zoomDirection, anchorRatio)
}

function isTouchpadLikeWheel(event: WheelEvent) {
  return event.deltaMode === WheelEvent.DOM_DELTA_PIXEL && Math.max(Math.abs(event.deltaX), Math.abs(event.deltaY)) < 50
}

function handleChartWheel(event: WheelEvent) {
  const canNavigate = Boolean(props.showExtendedWindows) && selectedChartRows.value.length > defaultLoadedDays
  if (!canNavigate) return

  const pointer = chartPointerRatios(event)
  const horizontalGesture = Math.abs(event.deltaX) > Math.abs(event.deltaY)
  if (isYAxisZone(pointer.x) && event.deltaY !== 0) {
    event.preventDefault()
    zoomYAxisByWheel(event, pointer.y)
    return
  }

  if (isXAxisZone(pointer.y) && event.deltaY !== 0) {
    event.preventDefault()
    zoomXAxisByWheel(event, pointer.x)
    return
  }

  if (horizontalGesture && maxXAxisStart.value > 0) {
    event.preventDefault()
    const pointsPerPixel = Math.max(1, visibleChartPointCount.value / 16) / 48
    wheelPanRemainder += event.deltaX * pointsPerPixel
    const wholePointDelta = wheelPanRemainder > 0 ? Math.floor(wheelPanRemainder) : Math.ceil(wheelPanRemainder)
    if (wholePointDelta !== 0) {
      wheelPanRemainder -= wholePointDelta
      shiftXAxis(wholePointDelta)
    }
    return
  }

  if (!horizontalGesture && event.deltaY !== 0 && isTouchpadLikeWheel(event)) {
    event.preventDefault()
    zoomXAxisByWheel(event, pointer.x)
  }
}

function startChartDrag(event: MouseEvent) {
  const canPan = Boolean(props.showExtendedWindows) && maxXAxisStart.value > 0
  const pointer = chartPointerRatios(event)
  if (!canPan || event.button !== 0 || isYAxisZone(pointer.x) || isXAxisZone(pointer.y)) return
  isChartDragging.value = true
  chartDragStartX = event.clientX
  chartDragStartAxisStart = xAxisStart.value
  hoverIndex.value = null
  event.preventDefault()
}

function updateChartDrag(event: MouseEvent) {
  if (!isChartDragging.value) return false
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width <= 0) return true
  const pointDelta = ((chartDragStartX - event.clientX) / rect.width) * visibleChartPointCount.value
  xAxisStart.value = Math.max(0, Math.min(maxXAxisStart.value, Math.round(chartDragStartAxisStart + pointDelta)))
  hoverIndex.value = null
  return true
}

function endChartDrag() {
  isChartDragging.value = false
}

function dateToUTC(value: string) {
  const parts = value.split('-').map((item) => Number(item))
  if (parts.length !== 3 || parts.some((item) => !Number.isFinite(item))) return NaN
  return Date.UTC(parts[0], parts[1] - 1, parts[2])
}

function requestedDaysForOldestDate(oldestDate: string, latestDate: string) {
  const oldestUTC = dateToUTC(oldestDate)
  const latestUTC = dateToUTC(latestDate)
  if (!Number.isFinite(oldestUTC) || !Number.isFinite(latestUTC) || latestUTC < oldestUTC) {
    return defaultLoadedDays
  }
  const dayMs = 24 * 60 * 60 * 1000
  return Math.max(defaultLoadedDays, Math.floor((latestUTC-oldestUTC)/dayMs) + 1)
}

async function ensureRangeCoverage(oldestDate: string) {
  const bounds = chartBounds.value
  if (!bounds || oldestDate >= bounds.oldest) return true
  isLoadingRange.value = true
  rangeLoadError.value = ''
  try {
    const response = await (props.loadHistory || getFundShareHistory)(
      props.history.symbol,
      requestedDaysForOldestDate(oldestDate, bounds.latest),
    )
    loadedRows.value = mergeRows(loadedRows.value, response.rows)
    return true
  } catch (err) {
    rangeLoadError.value = err instanceof Error ? err.message : String(err)
    return false
  } finally {
    isLoadingRange.value = false
  }
}

async function loadHistoryDays(days: number) {
  isLoadingRange.value = true
  rangeLoadError.value = ''
  try {
    const response = await (props.loadHistory || getFundShareHistory)(props.history.symbol, days)
    loadedRows.value = mergeRows(loadedRows.value, response.rows)
    return true
  } catch (err) {
    rangeLoadError.value = err instanceof Error ? err.message : String(err)
    return false
  } finally {
    isLoadingRange.value = false
  }
}

function yearsBefore(latestDate: string, years: number) {
  const parts = latestDate.split('-').map((item) => Number(item))
  if (parts.length !== 3 || parts.some((item) => !Number.isFinite(item))) return latestDate
  const date = new Date(Date.UTC(parts[0] - years, parts[1] - 1, parts[2]))
  return date.toISOString().slice(0, 10)
}

async function selectCalendarWindow(years: 1 | 5) {
  const bounds = chartBounds.value
  if (!bounds || isLoadingRange.value) return
  const start = yearsBefore(bounds.latest, years)
  const ok = await loadHistoryDays(years === 1 ? oneYearHistoryDays : fiveYearHistoryDays)
  if (!ok) return
  chartMode.value = 'range'
  customStartDate.value = start
  customEndDate.value = bounds.latest
  activeQuickWindow.value = years === 1 ? '1y' : '5y'
  hoverIndex.value = null
  resetChartViewport()
}

async function selectAllWindow() {
  if (!chartBounds.value || isLoadingRange.value) return
  const ok = await loadHistoryDays(allHistoryDays)
  if (!ok || !chartBounds.value) return
  chartMode.value = 'range'
  customStartDate.value = chartBounds.value.oldest
  customEndDate.value = chartBounds.value.latest
  activeQuickWindow.value = 'all'
  hoverIndex.value = null
  resetChartViewport()
}

async function applyCustomRange() {
  if (!chartBounds.value || customRangeError.value) return
  if (!customStartDate.value && !customEndDate.value) {
    resetChartSelection()
    return
  }
  const requestedOldestDate = customStartDate.value || customEndDate.value || chartBounds.value.oldest
  const ok = await ensureRangeCoverage(requestedOldestDate)
  if (!ok) return
  chartMode.value = 'range'
  activeQuickWindow.value = null
  rangeLoadError.value = ''
  hoverIndex.value = null
  resetChartViewport()
}

watch(() => props.history, () => {
  loadedRows.value = mergeRows([], props.history.rows)
  resetChartSelection()
})
watch(() => chartRows.value.length, () => {
  if (hoverIndex.value !== null && hoverIndex.value >= chartRows.value.length) {
    hoverIndex.value = null
  }
})
watch(() => [selectedChartRows.value.length, visibleChartPointCount.value], () => {
  xAxisStart.value = Math.max(0, Math.min(maxXAxisStart.value, xAxisStart.value))
})
</script>

<template>
  <section class="section share-history-page" :class="{ 'share-history-page-wide': useWideHistoryLayout }">
    <section class="share-chart-section">
      <div class="share-chart-header">
        <div class="share-chart-title">
          <h2>{{ history.symbol }} {{ history.name || '' }}</h2>
          <p class="model-note">{{ chartSubtitle }}</p>
        </div>
        <div class="share-chart-actions">
          <button
            type="button"
            class="inline-link detail-history-link"
            @click="$emit('backToFund', history.symbol)"
          >
            返回标的详情
          </button>
          <span class="ratio-badge">{{ history.unit || '万份' }}</span>
        </div>
      </div>
      <div v-if="allChartRows.length" class="share-chart-controls">
        <div class="share-chart-window-buttons" role="group" aria-label="图表窗口">
          <button
            type="button"
            :class="{ active: activeQuickWindow === '30' }"
            @click="selectRecentWindow(defaultChartDays)"
          >
            近{{ defaultChartDays }}条
          </button>
          <button
            type="button"
            :class="{ active: activeQuickWindow === '60' }"
            @click="selectRecentWindow(defaultLoadedDays)"
          >
            近{{ defaultLoadedDays }}条
          </button>
          <button
            v-if="showExtendedWindows"
            type="button"
            :class="{ active: activeQuickWindow === '1y' }"
            :disabled="isLoadingRange"
            @click="selectCalendarWindow(1)"
          >
            近1年
          </button>
          <button
            v-if="showExtendedWindows"
            type="button"
            :class="{ active: activeQuickWindow === '5y' }"
            :disabled="isLoadingRange"
            @click="selectCalendarWindow(5)"
          >
            近5年
          </button>
          <button
            v-if="showExtendedWindows"
            type="button"
            :class="{ active: activeQuickWindow === 'all' }"
            :disabled="isLoadingRange"
            @click="selectAllWindow"
          >
            全部
          </button>
        </div>
        <form class="share-chart-range-form" @submit.prevent="applyCustomRange">
          <label>
            开始
            <input
              v-model="customStartDate"
              type="date"
              :max="chartBounds?.latest"
            />
          </label>
          <span class="share-chart-range-separator">至</span>
          <label>
            结束
            <input
              v-model="customEndDate"
              type="date"
              :max="chartBounds?.latest"
            />
          </label>
          <button
            type="submit"
            :class="{ active: chartMode === 'range' }"
            :disabled="Boolean(customRangeError) || isLoadingRange"
          >
            {{ isLoadingRange ? '加载中...' : '应用区间' }}
          </button>
          <button
            v-if="chartMode === 'range' || customStartDate || customEndDate"
            type="button"
            class="ghost"
            @click="resetChartSelection"
          >
            恢复默认
          </button>
        </form>
      </div>
      <p v-if="customRangeError" class="share-chart-range-error">{{ customRangeError }}</p>
      <p v-else-if="rangeLoadError" class="share-chart-range-error">{{ rangeLoadError }}</p>
      <div
        v-if="chartRows.length"
        class="share-chart-wrap"
        :class="{ 'is-pannable': showExtendedWindows && maxXAxisStart > 0 }"
        @wheel="handleChartWheel"
        @mousedown="startChartDrag"
        @mouseup="endChartDrag"
      >
        <svg
          class="share-chart"
          role="img"
          :aria-label="chartAriaLabel"
          :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
          preserveAspectRatio="none"
          @mousemove="updateChartHover"
          @mouseleave="clearChartHover"
        >
          <g class="share-chart-grid">
            <g v-for="tick in chartTicks" :key="tick.y">
              <line
                :x1="chartPadding.left"
                :x2="chartWidth - chartPadding.right"
                :y1="tick.y"
                :y2="tick.y"
              />
            </g>
          </g>
          <path class="share-chart-line" :d="chartPath" />
          <g v-if="hoverPoint" class="share-chart-hover-layer">
            <line
              :x1="hoverPoint.x"
              :x2="hoverPoint.x"
              :y1="chartPadding.top"
              :y2="chartHeight - chartPadding.bottom"
            />
            <circle :cx="hoverPoint.x" :cy="hoverPoint.y" r="6" />
          </g>
          <g v-if="showChartPointMarkers" class="share-chart-points">
            <circle
              v-for="(row, index) in chartRows"
              :key="`${row.symbol}-${row.share_date}`"
              :cx="chartX(index, chartRows.length)"
              :cy="chartY(row.shares_10k, chartStats?.min || 0, chartStats?.max || 1)"
              r="3.5"
            >
              <title>{{ row.share_date }} {{ fmt(row.shares_10k) }}{{ history.unit || '万份' }}</title>
            </circle>
          </g>
        </svg>
        <div class="share-chart-y-labels" aria-hidden="true">
          <span
            v-for="tick in chartTicks"
            :key="`y-${tick.y}`"
            class="share-chart-y-label"
            :style="yLabelStyle(tick.y)"
          >
            {{ fmt(tick.value, 0) }}
          </span>
        </div>
        <div class="share-chart-x-labels" aria-hidden="true">
          <span
            v-for="label in chartDateLabels"
            :key="label.index"
            class="share-chart-x-label"
            :class="`align-${label.align}`"
            :style="xLabelStyle(label.x)"
          >
            {{ label.date }}
          </span>
        </div>
        <div v-if="hoverPoint" :class="hoverTooltipClass" :style="hoverTooltipStyle">
          <strong>{{ hoverPoint.row.share_date }}</strong>
          <span>{{ fmt(hoverPoint.row.shares_10k) }}{{ history.unit || '万份' }}</span>
          <span :class="pctClass(hoverPoint.row.share_change_10k)">
            {{ fmt(hoverPoint.row.share_change_10k) || '-' }} / {{ fmtPct(hoverPoint.row.share_change_pct) || '-' }}
          </span>
        </div>
      </div>
      <p v-else class="model-note">
        {{ chartEmptyNote }}
      </p>
    </section>

    <section class="table-section">
      <h2>历史份额明细</h2>
      <p v-if="!tableRows.length" class="model-note">
        暂无 LOF/ETF 份额历史。
      </p>
      <div v-else class="table-wrap">
        <table class="share-history-table">
          <thead>
            <tr>
              <th class="sticky-col">日期</th>
              <th>基金简称</th>
              <th>份额(万份)</th>
              <th>新增(万份)</th>
              <th>新增比例</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in tableRows" :key="rowKey(row)">
              <td class="sticky-col" data-label="日期">{{ row.share_date }}</td>
              <td class="name" data-label="基金简称">{{ row.name || history.name || '-' }}</td>
              <td data-label="份额(万份)">{{ fmt(row.shares_10k) }}</td>
              <td :class="pctClass(row.share_change_10k)" data-label="新增(万份)">
                {{ fmt(row.share_change_10k) || '-' }}
              </td>
              <td :class="pctClass(row.share_change_pct)" data-label="新增比例">
                {{ fmtPct(row.share_change_pct) || '-' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<style scoped>
.share-history-page {
  width: min(100%, 960px);
  margin: 0 auto;
}

.share-history-page.share-history-page-wide {
  width: 100%;
  max-width: none;
}

.share-chart-section {
  border: 1px solid #dbe2ee;
  border-radius: 6px;
  background: white;
  padding: 14px;
}

.share-chart-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.share-chart-title {
  min-width: 0;
}

.share-chart-header h2 {
  margin-bottom: 4px;
}

.share-chart-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 10px;
}

.share-chart-wrap {
  position: relative;
  width: 100%;
  height: 380px;
  overflow: visible;
  border: 1px solid #e1e6f0;
  border-radius: 6px;
  background: #fbfcff;
}

.share-chart-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 14px;
  margin-bottom: 10px;
}

.share-chart-window-buttons,
.share-chart-range-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.share-chart-window-buttons button,
.share-chart-range-form button {
  border: 1px solid #cad6ea;
  border-radius: 999px;
  background: #f7f9fd;
  color: #2456b8;
  font-size: 12px;
  line-height: 1;
  padding: 7px 12px;
}

.share-chart-window-buttons button.active,
.share-chart-range-form button.active {
  border-color: #2456b8;
  background: #2456b8;
  color: #ffffff;
}

.share-chart-range-form button.ghost {
  background: #ffffff;
  color: #5e6c85;
}

.share-chart-range-form button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.share-chart-range-form label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #4b5870;
  font-size: 12px;
}

.share-chart-range-form input {
  border: 1px solid #cad6ea;
  border-radius: 8px;
  background: #ffffff;
  color: #172033;
  font-size: 12px;
  line-height: 1.2;
  padding: 7px 10px;
}

.share-chart-range-separator {
  color: #657084;
  font-size: 12px;
}

.share-chart-range-error {
  margin: 0 0 10px;
  font-size: 12px;
}

.share-chart-range-error {
  color: #b42318;
}

.share-chart {
  display: block;
  width: 100%;
  height: 100%;
  cursor: crosshair;
}

.share-chart-wrap.is-pannable .share-chart {
  cursor: grab;
}

.share-chart-wrap.is-pannable:active .share-chart {
  cursor: grabbing;
}

.share-chart-grid line {
  stroke: #e3e9f3;
  stroke-width: 1;
}

.share-chart-line {
  fill: none;
  stroke: #2456b8;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2.5;
}

.share-chart-points circle {
  fill: #ffffff;
  stroke: #2456b8;
  stroke-width: 2;
}

.share-chart-hover-layer line {
  stroke: #8da8dc;
  stroke-dasharray: 4 4;
  stroke-width: 1.2;
}

.share-chart-hover-layer circle {
  fill: #2456b8;
  stroke: #ffffff;
  stroke-width: 2.5;
}

.share-chart-tooltip {
  position: absolute;
  z-index: 2;
  display: grid;
  gap: 3px;
  min-width: 128px;
  padding: 8px 10px;
  border: 1px solid #cad6ea;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 10px 24px rgba(23, 32, 51, 0.14);
  color: #172033;
  font-size: 12px;
  line-height: 1.35;
  pointer-events: none;
  transform: translate(-50%, calc(-100% - 12px));
  white-space: nowrap;
}

.share-chart-tooltip.align-left {
  transform: translate(0, calc(-100% - 12px));
}

.share-chart-tooltip.align-right {
  transform: translate(-100%, calc(-100% - 12px));
}

.share-chart-tooltip.align-bottom {
  transform: translate(-50%, 12px);
}

.share-chart-tooltip.align-bottom.align-left {
  transform: translate(0, 12px);
}

.share-chart-tooltip.align-bottom.align-right {
  transform: translate(-100%, 12px);
}

.share-chart-tooltip strong {
  font-size: 12px;
}

.share-chart-y-labels,
.share-chart-x-labels {
  position: absolute;
  inset: 0;
  color: #657084;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  pointer-events: none;
}

.share-chart-y-label {
  position: absolute;
  left: 14px;
  transform: translateY(-50%);
}

.share-chart-x-label {
  position: absolute;
  bottom: 13px;
  transform: translateX(-50%);
  white-space: nowrap;
}

.share-chart-x-label.align-start {
  transform: translateX(0);
}

.share-chart-x-label.align-end {
  transform: translateX(-100%);
}

.share-history-table {
  min-width: 640px;
}

@media (max-width: 720px) {
  .share-chart-header {
    align-items: stretch;
    flex-direction: column;
  }

  .share-chart-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .share-chart-window-buttons,
  .share-chart-actions {
    justify-content: space-between;
  }

  .share-chart-range-form {
    align-items: flex-start;
  }

  .share-chart-wrap {
    height: 320px;
  }
}
</style>
