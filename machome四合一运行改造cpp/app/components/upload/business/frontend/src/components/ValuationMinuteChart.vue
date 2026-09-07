<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getFundMinuteHistory, getFundMinuteHistoryDates } from '../lib/api'
import { isMinuteHistoryAutoRefreshTime } from '../lib/tradingHours'
import type { FundSnapshot, MinuteHistoryPoint } from '../lib/types'

type ChartMode = 'valuation' | 'premium'
type DaySpan = '1d' | '3d' | '5d'
type SessionTick = {
  label: string
  minute: number
}

type ChartLine = {
  key: string
  points: string
}

const props = defineProps<{
  fund: FundSnapshot
  daySpan?: DaySpan
}>()

const emit = defineEmits<{
  'update:daySpan': [span: DaySpan]
}>()

const historyRows = ref<MinuteHistoryPoint[]>([])
const availableDates = ref<string[]>([])
const historicalDates = ref<string[]>([])
const selectedDate = ref('')
const error = ref('')
const hoverIndex = ref<number | null>(null)
const tooltipLeft = ref(0)
const tooltipTop = ref(0)
const chartRoot = ref<HTMLElement | null>(null)
const chartMode = ref<ChartMode>('valuation')
let timer: number | undefined
let historyRequestToken = 0
const minuteHistoryRefreshIntervalMs = 60_000
const mobileChartLayout = isMobileChartLayout()
const minuteHistoryDateLimit = 45
const multiDayFetchBudget = 6
const maxRowsPerDay = 520
const chartDaySpan = computed<DaySpan>(() => {
  if (props.daySpan === '5d') return '5d'
  if (props.daySpan === '3d') return '3d'
  return '1d'
})
const chartDayCount = computed(() => {
  if (chartDaySpan.value === '5d') return 5
  if (chartDaySpan.value === '3d') return 3
  return 1
})

const width = computed(() => {
  if (mobileChartLayout) return 390
  if (chartDayCount.value === 1) return 760
  return 1360
})
const height = mobileChartLayout ? 260 : 300
const margin = mobileChartLayout
  ? { top: 14, right: 12, bottom: 28, left: 34 }
  : { top: 22, right: 24, bottom: 34, left: 48 }
const plotWidth = computed(() => width.value - margin.left - margin.right)
const plotHeight = height - margin.top - margin.bottom
const sessionStartMinute = 9 * 60 + 30
const sessionEndMinute = 15 * 60
const sessionMinuteRange = sessionEndMinute - sessionStartMinute
const sessionTickLabels: SessionTick[] = [
  { label: '09:30', minute: sessionStartMinute },
  { label: '11:30', minute: 11 * 60 + 30 },
  { label: '13:00', minute: 13 * 60 },
  { label: '15:00', minute: sessionEndMinute },
]
const mobileMultiDayTickLabels: SessionTick[] = [
  { label: '09:30', minute: sessionStartMinute },
  { label: '15:00', minute: sessionEndMinute },
]

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function fmt(value: number | undefined | null, digits = 4) {
  if (!isFiniteNumber(value) || value <= 0) return '-'
  return value.toFixed(digits)
}

function fmtPct(value: number | undefined | null) {
  if (!isFiniteNumber(value)) return '-'
  return `${value.toFixed(2)}%`
}

function premiumLabel(row: MinuteHistoryPoint) {
  return fmtPct(row.premium_pct)
}

function hasPositive(value: unknown): value is number {
  return isFiniteNumber(value) && value > 0
}

function chartMinute(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${d} ${hh}:${mm}`
}

function normalizeDateKey(value: string) {
  const match = value.match(/^(\d{4})-?(\d{2})-?(\d{2})/)
  if (!match) return ''
  return `${match[1]}${match[2]}${match[3]}`
}

function dateLabel(value: string) {
  const key = normalizeDateKey(value)
  if (!key) return value
  return `${key.slice(0, 4)}-${key.slice(4, 6)}-${key.slice(6, 8)}`
}

function dateKeyFromMinute(value: string) {
  return normalizeDateKey(value)
}

function minuteLabelForDay(dayKey: string, value: string) {
  const cleanDay = normalizeDateKey(dayKey)
  const minute = value.trim()
  if (!cleanDay || !minute) return value
  if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(minute)) return minute
  return `${dateLabel(cleanDay)} ${minute}`
}

function currentFundDateKey() {
  return dateKeyFromMinute(chartMinute(props.fund.as_of))
}

const activeDate = computed(() => selectedDate.value || currentFundDateKey())
const isLiveSelectedDate = computed(() => activeDate.value === currentFundDateKey())
const isPremiumChart = computed(() => chartMode.value === 'premium')

function minuteOfDay(value: string) {
  const match = value.match(/(?:^|\s|T)(\d{2}):(\d{2})(?::\d{2})?$/)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

function isSessionMinute(value: string) {
  const minute = minuteOfDay(value)
  return minute !== null && minute >= sessionStartMinute && minute <= sessionEndMinute
}

function livePoint(): MinuteHistoryPoint | null {
  if (!isLiveSelectedDate.value) return null
  const estimate = props.fund.estimate
  const quote = props.fund.quote
  if (!estimate || !quote || quote.price <= 0) return null
  if ((quote.realtime_status || '').toLowerCase() !== 'realtime') return null
  const estimated = estimate.realtime_est ?? estimate.fair_est
  if (!isFiniteNumber(estimated) || estimated <= 0) return null
  const premium = estimate.realtime_premium ?? (quote.price / estimated - 1) * 100
  const minute = chartMinute(props.fund.as_of)
  if (!minute || !isSessionMinute(minute)) return null
  return {
    minute,
    market_price: quote.price,
    estimated_nav: estimated,
    premium_pct: premium,
  }
}

function candidateDatesForHistory() {
  const dates: string[] = []
  const seen = new Set<string>()
  const push = (value: string) => {
    const key = normalizeDateKey(value)
    if (!key || seen.has(key)) return
    seen.add(key)
    dates.push(key)
  }

  const selected = activeDate.value
  if (selected) push(selected)

  const sourceDates = historicalDates.value
  const anchorIndex = sourceDates.indexOf(selected)
  if (anchorIndex >= 0) {
    for (let index = anchorIndex + 1; index < sourceDates.length; index += 1) {
      push(sourceDates[index])
    }
  } else {
    for (const date of sourceDates) {
      push(date)
    }
  }

  return dates.slice(0, chartDayCount.value === 1 ? 1 : chartDayCount.value + multiDayFetchBudget)
}

const baseRows = computed(() => {
  const byMinute = new Map<string, MinuteHistoryPoint>()
  for (const row of historyRows.value) {
    if (
      row.minute &&
      isSessionMinute(row.minute) &&
      hasPositive(row.estimated_nav)
    ) {
      byMinute.set(row.minute, row)
    }
  }
  const live = livePoint()
  if (live) byMinute.set(live.minute, live)
  return Array.from(byMinute.values())
    .sort((a, b) => a.minute.localeCompare(b.minute))
    .slice(-(maxRowsPerDay * chartDayCount.value))
})

const rows = computed(() => {
  if (!isPremiumChart.value) return baseRows.value
  return baseRows.value.filter((row) => isFiniteNumber(row.premium_pct))
})

const visibleDateKeys = computed(() => {
  const out: string[] = []
  const seen = new Set<string>()
  for (const row of baseRows.value) {
    const key = dateKeyFromMinute(row.minute)
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(key)
  }
  return out
})

const dateIndexByKey = computed(() => {
  const index = new Map<string, number>()
  visibleDateKeys.value.forEach((dayKey, dayIndex) => {
    index.set(dayKey, dayIndex)
  })
  return index
})

const axisDayCount = computed(() => visibleDateKeys.value.length || chartDayCount.value || 1)
const axisTimeTicks = computed(() => {
  const ticks: Array<SessionTick & { key: string; x: number; anchor: 'start' | 'middle' | 'end' }> = []
  const baseTicks =
    axisDayCount.value > 1 && mobileChartLayout
      ? mobileMultiDayTickLabels
      : sessionTickLabels
  for (let dayIndex = 0; dayIndex < axisDayCount.value; dayIndex += 1) {
    for (const tick of baseTicks) {
      ticks.push({
        ...tick,
        key: `${dayIndex}-${tick.label}`,
        x: xAtSessionMinute(tick.minute, dayIndex),
        anchor:
          tick.minute === sessionStartMinute
            ? 'start'
            : tick.minute === sessionEndMinute
              ? 'end'
              : 'middle',
      })
    }
  }
  return ticks
})

const daySeparators = computed(() =>
  Array.from({ length: Math.max(axisDayCount.value - 1, 0) }, (_, index) => ({
    key: `day-${index + 1}`,
    x: xAtSessionMinute(sessionStartMinute, index + 1),
  })),
)

const scale = computed(() => {
  const values = isPremiumChart.value
    ? rows.value.map((row) => row.premium_pct).filter(isFiniteNumber)
    : rows.value
      .flatMap((row) => [hasPositive(row.market_price) ? row.market_price : null, row.estimated_nav])
      .filter(hasPositive)
  if (!values.length) {
    return isPremiumChart.value ? { min: -1, max: 1 } : { min: 0, max: 1 }
  }
  let min = Math.min(...values)
  let max = Math.max(...values)
  const pad = isPremiumChart.value
    ? Math.max((max - min) * 0.12, Math.abs(max) * 0.05, Math.abs(min) * 0.05, 0.2)
    : Math.max((max - min) * 0.08, Math.abs(max) * 0.005, 0.002)
  min -= pad
  max += pad
  return { min, max }
})

function xAtSessionMinute(minute: number, dayIndex = 0) {
  const bounded = Math.max(sessionStartMinute, Math.min(sessionEndMinute, minute))
  const totalRange = axisDayCount.value * sessionMinuteRange || 1
  const offset = dayIndex * sessionMinuteRange + (bounded - sessionStartMinute)
  return margin.left + (offset / totalRange) * plotWidth.value
}

function xAt(index: number) {
  const row = rows.value[index]
  const minute = row ? minuteOfDay(row.minute) : null
  const dayKey = row ? dateKeyFromMinute(row.minute) : ''
  const dayIndex = dayKey ? dateIndexByKey.value.get(dayKey) ?? 0 : 0
  if (minute === null) return margin.left
  return xAtSessionMinute(minute, dayIndex)
}

function yAt(value: number) {
  const range = scale.value.max - scale.value.min || 1
  return margin.top + (1 - (value - scale.value.min) / range) * plotHeight
}

function pointsFor(valueAt: (row: MinuteHistoryPoint) => number | undefined | null) {
  const pointsByDay = new Map<string, string[]>()
  for (let index = 0; index < rows.value.length; index += 1) {
    const row = rows.value[index]
    const value = valueAt(row)
    if (!isFiniteNumber(value)) continue
    const dayKey = dateKeyFromMinute(row.minute) || `row-${index}`
    if (!pointsByDay.has(dayKey)) {
      pointsByDay.set(dayKey, [])
    }
    pointsByDay.get(dayKey)?.push(`${xAt(index).toFixed(2)},${yAt(value).toFixed(2)}`)
  }
  return Array.from(pointsByDay.entries())
    .map(([dayKey, points]) => ({ key: dayKey, points: points.join(' ') }))
    .filter((line): line is ChartLine => Boolean(line.points))
}

const estimatedLines = computed(() => pointsFor((row) => row.estimated_nav))
const priceLines = computed(() => pointsFor((row) => (hasPositive(row.market_price) ? row.market_price : null)))
const premiumLines = computed(() => pointsFor((row) => row.premium_pct))
const yTicks = computed(() => {
  if (isPremiumChart.value && scale.value.min < 0 && scale.value.max > 0) {
    return [scale.value.max, 0, scale.value.min]
  }
  const mid = (scale.value.min + scale.value.max) / 2
  return [scale.value.max, mid, scale.value.min]
})
const hovered = computed(() => {
  if (hoverIndex.value === null) return null
  return rows.value[hoverIndex.value] || null
})
const hoverX = computed(() => (hoverIndex.value === null ? 0 : xAt(hoverIndex.value)))

function axisLabel(value: number) {
  return isPremiumChart.value ? fmtPct(value) : fmt(value, 3)
}

function isMobileChartLayout() {
  if (typeof document === 'undefined') return false
  return document.querySelector('main')?.classList.contains('ua-mobile') === true
}

function emptyText() {
  if (chartDayCount.value > 1) return `最近 ${chartDayCount.value} 个有数据交易日暂无分钟估值数据`
  return activeDate.value ? '该日无分钟估值数据' : '等待分钟估值数据'
}

function toggleChartMode() {
  chartMode.value = isPremiumChart.value ? 'valuation' : 'premium'
  clearHover()
}

function setDaySpan(span: DaySpan) {
  if (span === chartDaySpan.value) return
  emit('update:daySpan', span)
}

function toggleButtonLabel() {
  return isPremiumChart.value ? '切换分时估值图' : '切换溢价率图'
}

function chartAriaLabel() {
  const prefix = chartDayCount.value > 1 ? `${chartDayCount.value}日连续` : '1日'
  return isPremiumChart.value ? `${prefix}分时溢价率折线图` : `${prefix}分时估值折线图`
}

function chartRangeLabel() {
  const labels = visibleDateKeys.value.map((value) => dateLabel(value))
  if (labels.length > 1) return labels.join(' / ')
  if (labels.length === 1) return labels[0]
  return activeDate.value ? dateLabel(activeDate.value) : ''
}

async function loadDates() {
  const payload = await getFundMinuteHistoryDates(props.fund.symbol, minuteHistoryDateLimit)
  const current = currentFundDateKey()
  const seen = new Set<string>()
  const dates: string[] = []
  for (const value of payload.dates || []) {
    const key = normalizeDateKey(value)
    if (!key || seen.has(key)) continue
    seen.add(key)
    dates.push(key)
  }
  historicalDates.value = dates
  const selectableDates = [...dates]
  if (current && !seen.has(current)) {
    selectableDates.unshift(current)
  }
  availableDates.value = selectableDates
  if (!selectedDate.value || !selectableDates.includes(selectedDate.value)) {
    selectedDate.value = selectableDates[0] || current
  }
}

async function loadHistory() {
  const requestToken = ++historyRequestToken
  try {
    const reserveLiveOnlyCurrentDay =
      chartDayCount.value > 1 &&
      isLiveSelectedDate.value &&
      !historicalDates.value.includes(currentFundDateKey()) &&
      livePoint() !== null
    const targetHistoryDays = Math.max(chartDayCount.value - (reserveLiveOnlyCurrentDay ? 1 : 0), 1)
    const candidateDates = candidateDatesForHistory()
    // Fetch candidate days in parallel, but keep merge order aligned with the
    // original date priority so the chart stays deterministic.
    const payloads = await Promise.all(
      candidateDates.map(async (date) => ({
        date,
        payload: await getFundMinuteHistory(props.fund.symbol, { date }),
      })),
    )
    if (requestToken !== historyRequestToken) return
    const mergedRows: MinuteHistoryPoint[] = []
    let loadedDays = 0

    for (const { date, payload } of payloads) {
      const dayRows = (payload.rows || []).map((row) => ({
        ...row,
        minute: minuteLabelForDay(date, row.minute),
      }))
      if (!dayRows.length) {
        if (chartDaySpan.value === '1d' && date === activeDate.value) {
          historyRows.value = []
        }
        continue
      }
      mergedRows.push(...dayRows)
      loadedDays += 1
      if (loadedDays >= targetHistoryDays) break
    }

    historyRows.value = mergedRows
    error.value = ''
  } catch (err) {
    if (requestToken !== historyRequestToken) return
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function updateHover(clientX: number, rect: DOMRect, chartRect: DOMRect) {
  if (!rows.value.length || !chartRoot.value) return
  const viewX = ((clientX - rect.left) / rect.width) * width.value
  let bestIndex = 0
  let bestDistance = Number.POSITIVE_INFINITY
  for (let index = 0; index < rows.value.length; index += 1) {
    const distance = Math.abs(xAt(index) - viewX)
    if (distance < bestDistance) {
      bestDistance = distance
      bestIndex = index
    }
  }
  hoverIndex.value = bestIndex
  const tooltipWidth = mobileChartLayout ? 170 : 210
  tooltipLeft.value = Math.max(8, Math.min(chartRect.width - tooltipWidth, clientX - chartRect.left + 10))
  tooltipTop.value = 12
}

function onMouseMove(event: MouseEvent) {
  const svg = event.currentTarget as SVGRectElement
  const rect = svg.getBoundingClientRect()
  const chartRect = chartRoot.value?.getBoundingClientRect()
  if (!chartRect) return
  updateHover(event.clientX, rect, chartRect)
}

function onTouchMove(event: TouchEvent) {
  if (!event.touches.length) return
  const svg = event.currentTarget as SVGRectElement
  const rect = svg.getBoundingClientRect()
  const chartRect = chartRoot.value?.getBoundingClientRect()
  if (!chartRect) return
  updateHover(event.touches[0].clientX, rect, chartRect)
}

function clearHover() {
  hoverIndex.value = null
}

function onDateChange() {
  historyRows.value = []
  clearHover()
  void loadHistory()
}

async function reloadDatesAndHistory() {
  try {
    await loadDates()
    await loadHistory()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

watch(
  () => props.fund.symbol,
  () => {
    historyRows.value = []
    availableDates.value = []
    historicalDates.value = []
    selectedDate.value = ''
    clearHover()
    void reloadDatesAndHistory()
  },
)

watch(
  () => props.daySpan,
  () => {
    historyRows.value = []
    clearHover()
    void loadHistory()
  },
)

onMounted(() => {
  void reloadDatesAndHistory()
  timer = window.setInterval(() => {
    if (!isLiveSelectedDate.value) return
    if (!isMinuteHistoryAutoRefreshTime()) return
    void reloadDatesAndHistory()
  }, minuteHistoryRefreshIntervalMs)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section
    class="table-section minute-chart-section"
    :class="{ 'minute-chart-section-expanded': chartDaySpan !== '1d' }"
  >
    <div class="minute-chart-head">
      <div class="minute-chart-head-main">
        <h2>分时估值</h2>
        <div class="minute-chart-legend">
          <template v-if="isPremiumChart">
            <span><i class="premium"></i>溢价率</span>
          </template>
          <template v-else>
            <span><i class="estimated"></i>实时估值</span>
            <span><i class="price"></i>标的价格</span>
          </template>
        </div>
      </div>
      <div class="minute-chart-actions">
        <div class="minute-chart-span-toggle" role="group" aria-label="切换分时跨度">
          <button
            type="button"
            class="minute-chart-toggle"
            :class="{ active: chartDaySpan === '1d' }"
            :aria-pressed="chartDaySpan === '1d'"
            @click="setDaySpan('1d')"
          >
            1日
          </button>
          <button
            type="button"
            class="minute-chart-toggle"
            :class="{ active: chartDaySpan === '3d' }"
            :aria-pressed="chartDaySpan === '3d'"
            @click="setDaySpan('3d')"
          >
            3日
          </button>
          <button
            type="button"
            class="minute-chart-toggle"
            :class="{ active: chartDaySpan === '5d' }"
            :aria-pressed="chartDaySpan === '5d'"
            @click="setDaySpan('5d')"
          >
            5日
          </button>
        </div>
        <button
          type="button"
          class="minute-chart-toggle"
          :class="{ active: isPremiumChart }"
          @click="toggleChartMode"
        >
          {{ toggleButtonLabel() }}
        </button>
        <select
          v-model="selectedDate"
          class="minute-chart-date-select"
          :disabled="!availableDates.length"
          @change="onDateChange"
        >
          <option
            v-for="date in availableDates"
            :key="date"
            :value="date"
          >
            {{ dateLabel(date) }}
          </option>
        </select>
      </div>
    </div>
    <p v-if="error" class="notice error">{{ error }}</p>
    <div ref="chartRoot" class="minute-chart" :class="{ 'minute-chart-empty-state': !rows.length }">
      <svg
        :viewBox="`0 0 ${width} ${height}`"
        role="img"
        :aria-label="chartAriaLabel()"
      >
        <g class="minute-chart-grid">
          <line
            v-for="tick in yTicks"
            :key="tick"
            :x1="margin.left"
            :x2="width - margin.right"
            :y1="yAt(tick)"
            :y2="yAt(tick)"
          />
        </g>
        <g v-if="daySeparators.length" class="minute-chart-day-separators">
          <line
            v-for="separator in daySeparators"
            :key="separator.key"
            :x1="separator.x"
            :x2="separator.x"
            :y1="margin.top"
            :y2="height - margin.bottom"
          />
        </g>
        <g class="minute-chart-axis">
          <text
            v-for="tick in yTicks"
            :key="`label-${tick}`"
            :x="margin.left - 8"
            :y="yAt(tick) + 4"
          >
            {{ axisLabel(tick) }}
          </text>
          <text
            v-for="tick in axisTimeTicks"
            :key="tick.key"
            :x="tick.x"
            :y="height - 9"
            :text-anchor="tick.anchor"
          >
            {{ tick.label }}
          </text>
        </g>
        <template v-if="rows.length && isPremiumChart">
          <polyline
            v-for="line in premiumLines"
            :key="`premium-${line.key}`"
            class="minute-chart-line premium"
            :points="line.points"
          />
        </template>
        <template v-if="rows.length && !isPremiumChart">
          <polyline
            v-for="line in estimatedLines"
            :key="`estimated-${line.key}`"
            class="minute-chart-line estimated"
            :points="line.points"
          />
          <polyline
            v-for="line in priceLines"
            :key="`price-${line.key}`"
            class="minute-chart-line price"
            :points="line.points"
          />
        </template>
        <g v-if="hovered" class="minute-chart-hover">
          <line :x1="hoverX" :x2="hoverX" :y1="margin.top" :y2="height - margin.bottom" />
          <circle
            v-if="isPremiumChart"
            :cx="hoverX"
            :cy="yAt(hovered.premium_pct)"
            r="4"
            class="premium"
          />
          <template v-else>
            <circle :cx="hoverX" :cy="yAt(hovered.estimated_nav)" r="4" class="estimated" />
            <circle v-if="hasPositive(hovered.market_price)" :cx="hoverX" :cy="yAt(hovered.market_price)" r="4" class="price" />
          </template>
        </g>
        <text v-if="!rows.length" x="50%" y="50%" text-anchor="middle" class="minute-chart-empty">
          {{ emptyText() }}
        </text>
        <rect
          class="minute-chart-hit"
          :x="margin.left"
          :y="margin.top"
          :width="plotWidth"
          :height="plotHeight"
          @mousemove="onMouseMove"
          @touchstart.prevent="onTouchMove"
          @touchmove.prevent="onTouchMove"
          @touchend="clearHover"
          @mouseleave="clearHover"
        />
      </svg>
      <div
        v-if="hovered"
        class="minute-chart-tooltip"
        :style="{ left: `${tooltipLeft}px`, top: `${tooltipTop}px` }"
      >
        <strong>{{ hovered.minute }}</strong>
        <template v-if="isPremiumChart">
          <span>溢价率 {{ premiumLabel(hovered) }}</span>
        </template>
        <template v-else>
          <span>实时估值 {{ fmt(hovered.estimated_nav) }}</span>
          <span>标的价格 {{ fmt(hovered.market_price) }}</span>
          <span>溢价率 {{ premiumLabel(hovered) }}</span>
        </template>
      </div>
    </div>
  </section>
</template>
