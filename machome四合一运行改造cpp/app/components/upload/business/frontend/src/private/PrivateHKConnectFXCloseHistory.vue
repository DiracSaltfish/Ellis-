<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getHKConnectFXCloseHistory } from '../lib/privateApi'
import type { HKConnectFXCloseAuditHistoryResponse, HKConnectFXCloseAuditRow } from '../lib/privateTypes'

defineEmits<{ back: [] }>()

const market = ref<'shanghai' | 'shenzhen'>('shanghai')
const history = ref<HKConnectFXCloseAuditHistoryResponse | null>(null)
const error = ref('')
const loading = ref(true)
let activeRequest: AbortController | null = null

const marketLabel = computed(() => market.value === 'shanghai' ? '上海' : '深圳')
const rows = computed(() => history.value?.rows || [])
const reconciledRows = computed(() => rows.value.filter((row) => finite(row.mean_absolute_error_bp)))
const latestRow = computed(() => rows.value[0] ?? null)
const averageAbsoluteError = computed(() => {
  const values = reconciledRows.value.map((row) => row.mean_absolute_error_bp as number)
  if (!values.length) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
})

function finite(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value)
}

function fmt(value: number | null | undefined, digits = 6) {
  return finite(value) ? value.toFixed(digits) : '—'
}

function fmtAmount(value: number | null | undefined) {
  return finite(value) ? `${value.toFixed(2)} 亿` : '—'
}

function fmtRatio(value: number | null | undefined, digits = 2) {
  return finite(value) ? `${(value * 100).toFixed(digits)}%` : '—'
}

function fmtBP(value: number | null | undefined) {
  return finite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(3)} bp` : '待公布'
}

function valueClass(value: number | null | undefined) {
  if (!finite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function timestamp(value: string | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

async function loadAudit() {
  activeRequest?.abort()
  const request = new AbortController()
  activeRequest = request
  loading.value = true
  try {
    const response = await getHKConnectFXCloseHistory(60, market.value, request.signal)
    if (activeRequest === request && response.market === market.value) {
      history.value = response
      error.value = ''
    }
  } catch (caught) {
    if (!(caught instanceof DOMException && caught.name === 'AbortError')) {
      error.value = caught instanceof Error ? caught.message : String(caught)
    }
  } finally {
    if (activeRequest === request) {
      activeRequest = null
      loading.value = false
    }
  }
}

function selectMarket(next: 'shanghai' | 'shenzhen') {
  if (market.value === next) return
  market.value = next
  history.value = null
  void loadAudit()
}

function officialState(row: HKConnectFXCloseAuditRow) {
  return finite(row.actual_buy_settlement) && finite(row.actual_sell_settlement) ? '已回填' : '待公布'
}

onMounted(() => { void loadAudit() })
onBeforeUnmount(() => activeRequest?.abort())
</script>

<template>
  <section class="section private-hkfx-audit-page">
    <button type="button" class="private-detail-back" @click="$emit('back')">← 返回港股通汇率估值</button>

    <section class="detail-panel private-detail-panel">
      <div class="detail-header-top">
        <p class="eyebrow">历史复盘 · 收盘估值审计</p>
        <span class="confirmation-badge">{{ history?.schema_version || 'loading' }}</span>
      </div>
      <h2>{{ marketLabel }}港股通收盘汇率估计审计</h2>
      <p class="model-note">{{ history?.methodology_note || '正在读取已保存的收盘估值样本…' }}</p>
      <div class="private-hkfx-market-tabs" aria-label="选择港股通市场">
        <button type="button" :class="{ active: market === 'shanghai' }" @click="selectMarket('shanghai')">上海</button>
        <button type="button" :class="{ active: market === 'shenzhen' }" @click="selectMarket('shenzhen')">深圳</button>
        <small>16:00 估值与 CFETS 实时中间价先行冻结；中国货币网官方 16:00 参考汇率随后自动覆盖，结算数据到达后回填。</small>
      </div>
    </section>

    <div v-if="error" class="private-alert private-alert-error" role="alert"><strong>审计数据读取异常</strong><span>{{ error }}</span></div>

    <section class="summary-grid private-hkfx-audit-summary">
      <article class="metric-card"><span>已保存收盘样本</span><strong>{{ rows.length }} 日</strong></article>
      <article class="metric-card"><span>已回填官方结算</span><strong>{{ reconciledRows.length }} 日</strong></article>
      <article class="metric-card"><span>最新收盘样本</span><strong>{{ latestRow?.trade_date || '—' }}</strong></article>
      <article class="metric-card"><span>平均双边绝对误差</span><strong>{{ finite(averageAbsoluteError) ? `${averageAbsoluteError.toFixed(3)} bp` : '待公布' }}</strong></article>
    </section>

    <section class="table-section private-review-table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>每日 16:00 估值、CFETS 锚点与最终结算比对</h2>
          <p class="table-inline-note">预测误差 =（预测结算汇率 ÷ 官方最终结算汇率 − 1）×10000；正值表示预测值更高。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="private-review-table private-hkfx-audit-table">
          <thead>
            <tr>
              <th class="sticky-col">日期</th><th>收盘保存</th><th>官方状态</th><th>参考中点 M</th><th>买 / 卖额</th><th>q</th><th>CFETS HKD/CNY BID / ASK</th><th>CFETS HKD/CNY<br><small>16:00 参考汇率</small></th>
              <th>预测卖出结算<br><small>买入港股</small></th><th>实际卖出结算</th><th>预测买入结算<br><small>卖出港股</small></th><th>实际买入结算</th><th>买 / 卖误差</th><th>平均绝对误差</th><th>模型</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="`${row.market}-${row.trade_date}`">
              <td class="sticky-col" data-label="日期">{{ row.trade_date }}</td>
              <td data-label="收盘保存">{{ timestamp(row.captured_at) }}</td>
              <td data-label="官方状态"><span :class="finite(row.mean_absolute_error_bp) ? 'private-history-confirmed' : 'private-history-pending'">{{ officialState(row) }}</span><small v-if="row.official_source">{{ row.market === 'shanghai' ? '上交所' : '深交所' }}</small></td>
              <td data-label="参考中点 M">{{ fmt(row.reference_mid) }}</td>
              <td data-label="买 / 卖额">{{ fmtAmount(row.buy_amount_hkd_100m) }} / {{ fmtAmount(row.sell_amount_hkd_100m) }}</td>
              <td data-label="q">{{ fmtRatio(row.net_ratio) }}</td>
              <td data-label="CFETS HKD/CNY">{{ fmt(row.hkd_cny_bid, 6) }} / {{ fmt(row.hkd_cny_ask, 6) }}</td>
              <td data-label="CFETS 16:00 锚点">{{ fmt(row.cfets_hkd_cny_1600, 6) }}</td>
              <td data-label="预测卖出结算">{{ fmt(row.predicted_sell_settlement) }}</td>
              <td data-label="实际卖出结算">{{ fmt(row.actual_sell_settlement) }}</td>
              <td data-label="预测买入结算">{{ fmt(row.predicted_buy_settlement) }}</td>
              <td data-label="实际买入结算">{{ fmt(row.actual_buy_settlement) }}</td>
              <td data-label="买 / 卖误差"><span :class="valueClass(row.sell_error_bp)">{{ fmtBP(row.sell_error_bp) }}</span><br><span :class="valueClass(row.buy_error_bp)">{{ fmtBP(row.buy_error_bp) }}</span></td>
              <td :class="valueClass(row.mean_absolute_error_bp)" data-label="平均绝对误差">{{ finite(row.mean_absolute_error_bp) ? `${row.mean_absolute_error_bp.toFixed(3)} bp` : '待公布' }}</td>
              <td data-label="模型"><small>{{ row.model_version }}</small><small>{{ row.calibration_status }} · residual {{ fmt(row.residual_hkd_cny, 6) }}</small></td>
            </tr>
            <tr v-if="!loading && !rows.length"><td colspan="15" class="private-history-empty">尚无已保存的 16:00 港股通汇率估值。今日收盘保存后会在此显示，官方最终结算汇率随后自动回填。</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
