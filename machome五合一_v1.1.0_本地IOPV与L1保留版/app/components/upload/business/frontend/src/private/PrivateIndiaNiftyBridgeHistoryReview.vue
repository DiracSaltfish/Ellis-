<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  PrivateIndiaNiftyReviewResponse,
  PrivateIndiaNiftyReviewRow,
  PrivateIndiaNiftyReviewRollAdjustment,
} from '../lib/privateTypes'

const props = defineProps<{
  review: PrivateIndiaNiftyReviewResponse
}>()

defineEmits<{
  backToFund: [symbol: string]
  openFinalReview: [symbol: string]
}>()

const selectedCheckpoint = ref('14:55')

watch(
  () => props.review.checkpoints,
  (checkpoints) => {
    if (checkpoints.some((item) => item.minute === selectedCheckpoint.value)) return
    selectedCheckpoint.value = checkpoints.find((item) => item.minute === '14:55')?.minute
      ?? checkpoints.find((item) => item.kind === 'normal')?.minute
      ?? checkpoints[0]?.minute
      ?? '14:55'
  },
  { immediate: true },
)

const selectedCheckpointMeta = computed(() => (
  props.review.checkpoints.find((item) => item.minute === selectedCheckpoint.value) ?? null
))

const selectedRows = computed(() => props.review.rows
  .filter((row) => row.checkpoint === selectedCheckpoint.value || minuteOf(row.minute) === selectedCheckpoint.value)
  .sort((left, right) => left.minute.localeCompare(right.minute)))

const auditRows = computed(() => [...selectedRows.value].reverse())

const selectedStats = computed(() => {
  const bridge = selectedRows.value.flatMap((row) => finiteValues(
    row.bridge_error_vs_bid_bps,
    row.bridge_error_vs_ask_bps,
  )).map(Math.abs)
  const direct = selectedRows.value.flatMap((row) => finiteValues(
    row.direct_error_vs_bid_bps,
    row.direct_error_vs_ask_bps,
  )).map(Math.abs)
  const deltas = selectedRows.value
    .map((row) => finite(row.paired_abs_error_delta_bps))
    .filter((value): value is number => value !== null)
  return {
    samples: selectedRows.value.length,
    bridgeMAE: mean(bridge),
    directMAE: mean(direct),
    pairedDelta: mean(deltas),
  }
})

const answer = computed(() => {
  const delta = finite(props.review.summary.paired_mae_delta_bps)
  if (delta === null) {
    return {
      tone: 'neutral',
      title: '配对样本尚不足以判断桥接增量误差',
      detail: '页面仍保留逐分钟公式输入和排除状态，待官方净值与同步行情齐备后自动形成结论。',
    }
  }
  if (delta < -0.05) {
    return {
      tone: 'better',
      title: `纳入 NIFTY 桥接后，配对绝对误差平均减少 ${fmtBps(Math.abs(delta))}`,
      detail: `桥接更接近官方净值的样本占 ${fmtPct(props.review.summary.bridge_win_rate_pct)}；负的 ΔMAE 表示相对同分钟直接 INDA 口径有所改善。`,
    }
  }
  if (delta > 0.05) {
    return {
      tone: 'worse',
      title: `纳入 NIFTY 桥接后，配对绝对误差平均增加 ${fmtBps(delta)}`,
      detail: `桥接更接近官方净值的样本占 ${fmtPct(props.review.summary.bridge_win_rate_pct)}；正的 ΔMAE 表示桥接在该样本内扩大了误差。`,
    }
  }
  return {
    tone: 'neutral',
    title: `纳入 NIFTY 桥接后，配对误差变化约为 ${fmtSignedBps(delta)}`,
    detail: '两种口径在当前配对样本内接近，仍应结合换月样本数和双边误差带判断。',
  }
})

type ChartPoint = {
  x: number
  y: number
  value: number
  row: PrivateIndiaNiftyReviewRow
}

const chart = computed(() => {
  const width = 960
  const height = 286
  const left = 58
  const right = 24
  const top = 24
  const bottom = 48
  const rows = selectedRows.value
    .map((row) => ({
      row,
      bridge: rowMeanAbsoluteError(row, 'bridge'),
      direct: rowMeanAbsoluteError(row, 'direct'),
    }))
    .filter((item) => item.bridge !== null || item.direct !== null)
  const maximum = Math.max(10, ...rows.flatMap((item) => finiteValues(item.bridge, item.direct)))
  const yMax = Math.max(10, Math.ceil(maximum / 10) * 10)
  const innerWidth = width - left - right
  const innerHeight = height - top - bottom
  const xAt = (index: number) => left + (rows.length <= 1 ? innerWidth / 2 : (index / (rows.length - 1)) * innerWidth)
  const yAt = (value: number) => top + (1 - Math.min(Math.max(value, 0), yMax) / yMax) * innerHeight
  const bridgePoints: ChartPoint[] = []
  const directPoints: ChartPoint[] = []
  rows.forEach((item, index) => {
    if (item.bridge !== null) bridgePoints.push({ x: xAt(index), y: yAt(item.bridge), value: item.bridge, row: item.row })
    if (item.direct !== null) directPoints.push({ x: xAt(index), y: yAt(item.direct), value: item.direct, row: item.row })
  })
  const labelStep = Math.max(1, Math.ceil(rows.length / 6))
  const labels = rows
    .map((item, index) => ({ x: xAt(index), label: item.row.trading_day.slice(5), index }))
    .filter((item, index) => index === 0 || index === rows.length - 1 || index % labelStep === 0)
  return {
    width,
    height,
    left,
    right,
    top,
    bottom,
    innerHeight,
    yMax,
    bridgePoints,
    directPoints,
    bridgePolyline: bridgePoints.map((point) => `${point.x},${point.y}`).join(' '),
    directPolyline: directPoints.map((point) => `${point.x},${point.y}`).join(' '),
    labels,
    empty: rows.length === 0,
  }
})

type RollGroup = {
  adjustment: PrivateIndiaNiftyReviewRollAdjustment
  sample: PrivateIndiaNiftyReviewRow
  affectedSamples: number
  affectedDays: number
}

const rollGroups = computed<RollGroup[]>(() => {
  const groups = new Map<string, { adjustment: PrivateIndiaNiftyReviewRollAdjustment; rows: PrivateIndiaNiftyReviewRow[] }>()
  for (const row of props.review.rows) {
    const adjustment = row.roll_adjustment
    if (!adjustment) continue
    const key = [adjustment.roll_date, adjustment.captured_at, adjustment.old_contract, adjustment.new_contract, adjustment.direction].join('|')
    const existing = groups.get(key)
    if (existing) existing.rows.push(row)
    else groups.set(key, { adjustment, rows: [row] })
  }
  return [...groups.values()]
    .map(({ adjustment, rows }) => ({
      adjustment,
      sample: rows[0],
      affectedSamples: rows.length,
      affectedDays: new Set(rows.map((row) => row.trading_day)).size,
    }))
    .sort((left, right) => right.adjustment.captured_at.localeCompare(left.adjustment.captured_at))
})

function finite(value: number | null | undefined): number | null {
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null
}

function finiteValues(...values: Array<number | null | undefined>): number[] {
  return values.map(finite).filter((value): value is number => value !== null)
}

function mean(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
}

function minuteOf(value: string): string {
  const match = value.match(/T(\d{2}:\d{2})/) ?? value.match(/\b(\d{2}:\d{2})\b/)
  return match?.[1] ?? ''
}

function rowMeanAbsoluteError(row: PrivateIndiaNiftyReviewRow, mode: 'bridge' | 'direct'): number | null {
  const values = mode === 'bridge'
    ? finiteValues(row.bridge_error_vs_bid_bps, row.bridge_error_vs_ask_bps)
    : finiteValues(row.direct_error_vs_bid_bps, row.direct_error_vs_ask_bps)
  return mean(values.map(Math.abs))
}

function fmt(value: number | null | undefined, digits = 4): string {
  const parsed = finite(value)
  if (parsed === null) return '—'
  return parsed.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function fmtBps(value: number | null | undefined): string {
  const parsed = finite(value)
  return parsed === null ? '—' : `${Math.abs(parsed).toFixed(2)} bps`
}

function fmtSignedBps(value: number | null | undefined): string {
  const parsed = finite(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(2)} bps`
}

function fmtPct(value: number | null | undefined, digits = 1): string {
  const parsed = finite(value)
  return parsed === null ? '—' : `${parsed.toFixed(digits)}%`
}

function fmtRatio(value: number | null | undefined): string {
  const parsed = finite(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(2)}%`
}

function valueClass(value: number | null | undefined): string {
  const parsed = finite(value)
  if (parsed === null || Math.abs(parsed) < 0.005) return 'flat'
  return parsed < 0 ? 'better' : 'worse'
}

function stateLabel(state: string): string {
  if (state === 'ordinary_same_contract') return '普通日 · 同合约'
  if (state === 'calendar_roll_day_same_contract') return '换月规则日 · 同合约'
  if (state === 'cross_contract_adjusted') return '实际跨合约校正'
  if (state === 'pending_official_nav') return '待官方净值'
  return state || '未标记'
}

function stateClass(state: string): string {
  if (state === 'cross_contract_adjusted') return 'roll-adjusted'
  if (state === 'calendar_roll_day_same_contract') return 'roll-calendar'
  return 'ordinary'
}

function checkpointKindLabel(kind: 'normal' | 'close_observation' | 'roll_open'): string {
  if (kind === 'close_observation') return '收盘观察'
  if (kind === 'roll_open') return '换月开盘'
  return '普通时点'
}

const formatters = new Map<string, Intl.DateTimeFormat>()

function dateTimeFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = formatters.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    formatters.set(timeZone, formatter)
  }
  return formatter
}

function fmtDateTime(value: string, timeZone = 'Asia/Shanghai'): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return value
  return dateTimeFormatter(timeZone).format(parsed)
}

function chartTooltip(point: ChartPoint, label: string): string {
  return `${point.row.trading_day} ${point.row.checkpoint} · ${label} ${point.value.toFixed(2)} bps`
}
</script>

<template>
  <section class="section nir-page">
    <nav class="nir-breadcrumbs" aria-label="页面导航">
      <a :href="`/private/${review.symbol}`" @click.prevent="$emit('backToFund', review.symbol)">← 返回 {{ review.symbol }} 标的详情</a>
      <button type="button" @click="$emit('openFinalReview', review.symbol)">查看最终净值复盘 →</button>
    </nav>

    <header class="nir-hero">
      <div class="nir-hero-copy">
        <p class="nir-eyebrow">NIFTY BRIDGE · PAIRED PREMIUM ERROR</p>
        <h1>{{ review.symbol }}{{ review.name }} · NIFTY 桥接溢价率复盘</h1>
        <p>{{ review.methodology_note }}</p>
      </div>
      <div class="nir-hero-meta">
        <span>{{ review.model_version }}</span>
        <span>数据截至 {{ fmtDateTime(review.as_of) }}</span>
        <span>{{ review.schema_version }}</span>
      </div>
    </header>

    <section class="nir-answer" :class="`is-${answer.tone}`">
      <div>
        <span class="nir-answer-label">先看结论</span>
        <h2>{{ answer.title }}</h2>
        <p>{{ answer.detail }}</p>
      </div>
      <div class="nir-answer-number" :class="valueClass(review.summary.paired_mae_delta_bps)">
        <small>桥接 − 直接 INDA</small>
        <strong>{{ fmtSignedBps(review.summary.paired_mae_delta_bps) }}</strong>
        <span>配对绝对误差 ΔMAE</span>
      </div>
    </section>

    <aside class="nir-price-warning">
      <strong>口径提醒</strong>
      <span>历史国内价格是公开分钟收盘价，不是当时盘口 Bid/Ask。页面复原的是“相对分钟价”的双边 NAV 溢价带，不把它表述为真实可成交价差。</span>
    </aside>

    <section class="nir-kpi-grid" aria-label="整体误差指标">
      <article>
        <span>NIFTY 桥接 MAE</span>
        <strong>{{ fmtBps(review.summary.bridge_bid_premium_mae_bps) }} <small>Bid</small></strong>
        <strong>{{ fmtBps(review.summary.bridge_ask_premium_mae_bps) }} <small>Ask</small></strong>
      </article>
      <article>
        <span>直接 INDA MAE</span>
        <strong>{{ fmtBps(review.summary.direct_bid_premium_mae_bps) }} <small>Bid</small></strong>
        <strong>{{ fmtBps(review.summary.direct_ask_premium_mae_bps) }} <small>Ask</small></strong>
      </article>
      <article>
        <span>桥接更接近官方</span>
        <strong>{{ fmtPct(review.summary.bridge_win_rate_pct) }}</strong>
        <small>{{ review.summary.paired_samples.toLocaleString('zh-CN') }} 个同分钟配对样本</small>
      </article>
      <article>
        <span>官方 NAV 落入桥接区间</span>
        <strong>{{ fmtPct(review.summary.bridge_interval_coverage_pct) }}</strong>
        <small>Bridge NAV Bid–Ask 覆盖率</small>
      </article>
      <article>
        <span>复盘覆盖</span>
        <strong>{{ review.summary.trading_days }} <small>日</small></strong>
        <small>{{ review.summary.minute_samples.toLocaleString('zh-CN') }} 个分钟样本</small>
      </article>
      <article>
        <span>真实状态分组</span>
        <strong>{{ review.summary.ordinary_days }} / {{ review.summary.calendar_roll_days }} / {{ review.summary.cross_contract_adjusted_days }}</strong>
        <small>普通日 / 日历换月日 / 实际跨约校正日</small>
      </article>
    </section>

    <section class="nir-panel nir-timing-panel">
      <div class="nir-section-heading">
        <div>
          <p class="nir-eyebrow">ACTUAL CLOCK &amp; CONTRACT RULE</p>
          <h2>实际使用的普通时点与换仓时点</h2>
        </div>
        <code>{{ review.timing.selection_version }}</code>
      </div>
      <div class="nir-timing-grid">
        <article>
          <span>国内样本时间</span>
          <strong>{{ review.timing.china_sessions.join(' / ') }}</strong>
          <p>NIFTY 自动采用窗口：{{ review.timing.nifty_active_window }}</p>
        </article>
        <article>
          <span>普通桥接参考</span>
          <strong>{{ review.timing.reference_window_et }}</strong>
          <p>中心点 {{ review.timing.reference_center_et }}；按纽约时区校验，不固定映射为北京时间。</p>
        </article>
        <article>
          <span>合约切换规则</span>
          <strong>{{ review.timing.roll_rule }}</strong>
          <p>“最后周二”是日历生效规则，并不等于每个最后周二都实际做了跨合约校正。</p>
        </article>
        <article>
          <span>跨合约基差采样</span>
          <strong>{{ review.timing.roll_basis_window_bjt }}</strong>
          <p>中心点 {{ review.timing.roll_basis_center_bjt }}；仅参考合约与当前合约不同时带入。</p>
        </article>
      </div>
      <div class="nir-checkpoints" role="group" aria-label="选择复盘时点">
        <button
          v-for="checkpoint in review.checkpoints"
          :key="checkpoint.minute"
          type="button"
          :class="{ active: selectedCheckpoint === checkpoint.minute }"
          :aria-pressed="selectedCheckpoint === checkpoint.minute"
          @click="selectedCheckpoint = checkpoint.minute"
        >
          <span>{{ checkpoint.label }}</span>
          <strong>{{ checkpoint.minute }}</strong>
          <small>{{ checkpointKindLabel(checkpoint.kind) }}</small>
        </button>
      </div>
    </section>

    <section class="nir-panel nir-chart-panel">
      <div class="nir-section-heading nir-chart-heading">
        <div>
          <p class="nir-eyebrow">CHECKPOINT ERROR TREND</p>
          <h2>{{ selectedCheckpointMeta?.label || selectedCheckpoint }} · 同日双边平均绝对误差</h2>
        </div>
        <div class="nir-selected-stats">
          <span>桥接 <strong>{{ fmtBps(selectedStats.bridgeMAE) }}</strong></span>
          <span>直接 <strong>{{ fmtBps(selectedStats.directMAE) }}</strong></span>
          <span>Δ <strong :class="valueClass(selectedStats.pairedDelta)">{{ fmtSignedBps(selectedStats.pairedDelta) }}</strong></span>
          <span>{{ selectedStats.samples }} 日</span>
        </div>
      </div>
      <div class="nir-chart-legend" aria-hidden="true">
        <span><i class="bridge"></i>NIFTY 桥接</span>
        <span><i class="direct"></i>直接 INDA</span>
      </div>
      <div v-if="chart.empty" class="nir-empty">该时点暂无可绘制的配对误差。</div>
      <svg
        v-else
        class="nir-error-chart"
        :viewBox="`0 0 ${chart.width} ${chart.height}`"
        role="img"
        aria-labelledby="nir-chart-title nir-chart-desc"
      >
        <title id="nir-chart-title">{{ selectedCheckpoint }} 桥接与直接 INDA 绝对误差趋势</title>
        <desc id="nir-chart-desc">纵轴为双边平均绝对溢价率误差，单位基点；横轴为交易日。</desc>
        <line :x1="chart.left" :x2="chart.width - chart.right" :y1="chart.top" :y2="chart.top" class="nir-grid-line" />
        <line :x1="chart.left" :x2="chart.width - chart.right" :y1="chart.top + chart.innerHeight / 2" :y2="chart.top + chart.innerHeight / 2" class="nir-grid-line" />
        <line :x1="chart.left" :x2="chart.width - chart.right" :y1="chart.top + chart.innerHeight" :y2="chart.top + chart.innerHeight" class="nir-grid-line" />
        <text :x="chart.left - 10" :y="chart.top + 4" text-anchor="end" class="nir-axis-label">{{ chart.yMax }}</text>
        <text :x="chart.left - 10" :y="chart.top + chart.innerHeight / 2 + 4" text-anchor="end" class="nir-axis-label">{{ chart.yMax / 2 }}</text>
        <text :x="chart.left - 10" :y="chart.top + chart.innerHeight + 4" text-anchor="end" class="nir-axis-label">0</text>
        <polyline v-if="chart.directPoints.length > 1" :points="chart.directPolyline" class="nir-line direct" />
        <polyline v-if="chart.bridgePoints.length > 1" :points="chart.bridgePolyline" class="nir-line bridge" />
        <g v-for="point in chart.directPoints" :key="`direct-${point.row.minute}`">
          <circle :cx="point.x" :cy="point.y" r="3" class="nir-dot direct"><title>{{ chartTooltip(point, '直接 INDA') }}</title></circle>
        </g>
        <g v-for="point in chart.bridgePoints" :key="`bridge-${point.row.minute}`">
          <circle :cx="point.x" :cy="point.y" r="3.5" class="nir-dot bridge"><title>{{ chartTooltip(point, 'NIFTY 桥接') }}</title></circle>
        </g>
        <text
          v-for="label in chart.labels"
          :key="`${label.index}-${label.label}`"
          :x="label.x"
          :y="chart.height - 14"
          text-anchor="middle"
          class="nir-axis-label nir-date-label"
        >{{ label.label }}</text>
      </svg>
    </section>

    <section class="nir-panel">
      <div class="nir-section-heading">
        <div>
          <p class="nir-eyebrow">FORMULA AUDIT</p>
          <h2>{{ selectedCheckpoint }} 逐日公式与误差审计</h2>
        </div>
        <span>{{ auditRows.length }} 行</span>
      </div>
      <div class="nir-table-wrap">
        <table class="nir-table nir-audit-table">
          <thead>
            <tr>
              <th>日期 / 时点</th>
              <th>实际状态</th>
              <th>国内分钟价 / 官方 NAV</th>
              <th>直接 INDA NAV<br>Bid / Ask</th>
              <th>NIFTY 桥接 NAV<br>Bid / Ask</th>
              <th>桥接溢价误差<br>Bid / Ask</th>
              <th>相对直接 Δ绝对误差</th>
              <th>当前 / 参考 NIFTY</th>
              <th>实际参考时间</th>
              <th>结论 / 公式输入</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in auditRows" :key="row.minute">
              <td>
                <strong>{{ row.trading_day }}</strong>
                <small>{{ row.checkpoint || minuteOf(row.minute) }}</small>
              </td>
              <td><span class="nir-state" :class="stateClass(row.state)">{{ stateLabel(row.state) }}</span></td>
              <td>
                <strong>{{ fmt(row.market_price) }}</strong>
                <small>官方 {{ fmt(row.official_nav) }}</small>
                <small>官方溢价 {{ fmtPct(row.official_premium_pct, 3) }}</small>
              </td>
              <td>
                <strong>{{ fmt(row.direct_nav_bid) }} / {{ fmt(row.direct_nav_ask) }}</strong>
                <small>误差 {{ fmtSignedBps(row.direct_error_vs_bid_bps) }} / {{ fmtSignedBps(row.direct_error_vs_ask_bps) }}</small>
              </td>
              <td><strong>{{ fmt(row.bridge_nav_bid) }} / {{ fmt(row.bridge_nav_ask) }}</strong></td>
              <td>
                <strong>{{ fmtSignedBps(row.bridge_error_vs_bid_bps) }}</strong>
                <small>/ {{ fmtSignedBps(row.bridge_error_vs_ask_bps) }}</small>
              </td>
              <td><strong :class="valueClass(row.paired_abs_error_delta_bps)">{{ fmtSignedBps(row.paired_abs_error_delta_bps) }}</strong></td>
              <td>
                <strong>{{ row.nifty_contract || '—' }}</strong>
                <small>{{ fmt(row.nifty_bid, 1) }} / {{ fmt(row.nifty_ask, 1) }}</small>
                <small>观测 {{ fmtDateTime(row.nifty_observed_at) }}</small>
                <small>参考 {{ row.nifty_reference_contract || '—' }}</small>
              </td>
              <td>
                <strong>ET {{ fmtDateTime(row.reference_at, 'America/New_York') }}</strong>
                <small>BJT {{ fmtDateTime(row.reference_at) }}</small>
              </td>
              <td>
                <div class="nir-result-chips">
                  <span :class="row.bridge_closer === true ? 'yes' : row.bridge_closer === false ? 'no' : ''">
                    {{ row.bridge_closer === true ? '桥接更近' : row.bridge_closer === false ? '直接更近' : '待配对' }}
                  </span>
                  <span :class="row.bridge_contains_official === true ? 'yes' : row.bridge_contains_official === false ? 'no' : ''">
                    {{ row.bridge_contains_official === true ? '覆盖官方' : row.bridge_contains_official === false ? '未覆盖官方' : '待官方' }}
                  </span>
                </div>
                <details class="nir-audit-details">
                  <summary>展开带入值</summary>
                  <dl>
                    <div><dt>T−2 NAV</dt><dd>{{ fmt(row.base_nav, 6) }} · {{ row.base_nav_date || '—' }}</dd></div>
                    <div><dt>风险 / 静态</dt><dd>{{ fmtRatio(row.investment_ratio) }} / {{ fmtRatio(row.static_ratio) }}</dd></div>
                    <div><dt>INDA 锚点</dt><dd>{{ fmt(row.anchor_price, 6) }}</dd></div>
                    <div><dt>SAFE T / T−2</dt><dd>{{ fmt(row.current_fx, 6) }} / {{ fmt(row.base_fx, 6) }}</dd></div>
                    <div><dt>合成 INDA</dt><dd>{{ fmt(row.synthetic_inda_bid, 6) }} / {{ fmt(row.synthetic_inda_ask, 6) }}</dd></div>
                    <div><dt>INDA 参考</dt><dd>{{ fmt(row.inda_reference_bid, 4) }} / {{ fmt(row.inda_reference_ask, 4) }} · {{ row.inda_reference_contract || '—' }}</dd></div>
                    <div><dt>NIFTY 参考</dt><dd>{{ fmt(row.nifty_reference_bid, 1) }} / {{ fmt(row.nifty_reference_ask, 1) }}</dd></div>
                    <div><dt>β / 选月规则</dt><dd>{{ fmt(row.beta, 2) }} · {{ row.selection_version || '—' }}</dd></div>
                    <div><dt>直接溢价</dt><dd>{{ fmtPct(row.direct_premium_vs_bid_pct, 3) }} / {{ fmtPct(row.direct_premium_vs_ask_pct, 3) }}</dd></div>
                    <div><dt>桥接溢价</dt><dd>{{ fmtPct(row.bridge_premium_vs_bid_pct, 3) }} / {{ fmtPct(row.bridge_premium_vs_ask_pct, 3) }}</dd></div>
                  </dl>
                </details>
              </td>
            </tr>
            <tr v-if="!auditRows.length">
              <td colspan="10" class="nir-empty">该时点暂无审计样本。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="nir-panel">
      <div class="nir-section-heading">
        <div>
          <p class="nir-eyebrow">ACTUAL ROLL ADJUSTMENTS</p>
          <h2>实际发生的跨合约校正</h2>
          <p>仅列出公式真正带入了旧/新合约基差的记录；日历最后周二但同合约的样本不会混入。</p>
        </div>
        <span>{{ rollGroups.length }} 组</span>
      </div>
      <div v-if="!rollGroups.length" class="nir-empty nir-roll-empty">
        当前样本没有实际跨合约校正。这不代表缺少换月规则，而是参考与当前 NIFTY 合约一致，无需人为加入基差。
      </div>
      <div v-else class="nir-table-wrap">
        <table class="nir-table nir-roll-table">
          <thead>
            <tr>
              <th>换月日</th>
              <th>实际采样时间 (BJT)</th>
              <th>旧合约 → 新合约</th>
              <th>方向</th>
              <th>Bid 因子</th>
              <th>Ask 因子</th>
              <th>影响范围</th>
              <th>审计来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="group in rollGroups" :key="`${group.adjustment.roll_date}-${group.adjustment.captured_at}-${group.adjustment.direction}`">
              <td><strong>{{ group.adjustment.roll_date }}</strong></td>
              <td>{{ fmtDateTime(group.adjustment.captured_at) }}</td>
              <td><strong>{{ group.adjustment.old_contract }} → {{ group.adjustment.new_contract }}</strong></td>
              <td><span class="nir-state roll-adjusted">{{ group.adjustment.direction }}</span></td>
              <td>{{ fmt(group.adjustment.bid_factor, 8) }}</td>
              <td>{{ fmt(group.adjustment.ask_factor, 8) }}</td>
              <td>{{ group.affectedDays }} 日 / {{ group.affectedSamples }} 个展示时点</td>
              <td><code>{{ group.adjustment.source }}</code></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<style scoped src="../styles/private-india-nifty-review.css"></style>
