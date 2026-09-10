<script setup lang="ts">
import { computed } from 'vue'
import type { VisitCount, VisitCountResponse } from '../lib/types'

const props = defineProps<{
  visits: VisitCountResponse
  selectedDate?: string
}>()

const emit = defineEmits<{
  'select-date': [date: string]
  'clear-date': []
}>()

type DailyRow = {
  date: string
  page: number
  api: number
  total: number
}

type DetailRow = VisitCount & {
  rank: number
  group: string
  dayShare: number
  kindShare: number
}

type GroupRow = {
  group: string
  count: number
  share: number
}

const rows = computed(() => props.visits.rows || [])
const selectedDate = computed(() => props.selectedDate || '')
const total = computed(() => rows.value.reduce((sum, row) => sum + row.count, 0))
const pageTotal = computed(() => rows.value.filter((row) => row.kind === 'page').reduce((sum, row) => sum + row.count, 0))
const apiTotal = computed(() => rows.value.filter((row) => row.kind === 'api').reduce((sum, row) => sum + row.count, 0))
const todayTotal = computed(() => dailyRows.value.find((row) => row.date === todayKey())?.total || 0)
const maxDailyTotal = computed(() => Math.max(...dailyRows.value.map((row) => row.total), 1))

const dailyRows = computed<DailyRow[]>(() => {
  const byDate = new Map<string, DailyRow>()
  for (const row of rows.value) {
    const current = byDate.get(row.date) || { date: row.date, page: 0, api: 0, total: 0 }
    if (row.kind === 'page') current.page += row.count
    if (row.kind === 'api') current.api += row.count
    current.total += row.count
    byDate.set(row.date, current)
  }
  return Array.from(byDate.values()).sort((a, b) => b.date.localeCompare(a.date))
})

const selectedDailyRow = computed(() => dailyRows.value.find((row) => row.date === selectedDate.value))
const selectedRows = computed(() => rows.value.filter((row) => row.date === selectedDate.value))
const detailRows = computed<DetailRow[]>(() => {
  const day = selectedDailyRow.value
  const kindTotals = new Map<string, number>([
    ['page', day?.page || 0],
    ['api', day?.api || 0],
  ])
  return selectedRows.value
    .slice()
    .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind) || a.target.localeCompare(b.target))
    .map((row, index) => {
      const kindTotal = kindTotals.get(row.kind) || 0
      return {
        ...row,
        rank: index + 1,
        group: auditGroup(row),
        dayShare: day?.total ? row.count / day.total : 0,
        kindShare: kindTotal ? row.count / kindTotal : 0,
      }
    })
})
const detailGroupRows = computed<GroupRow[]>(() => {
  const byGroup = new Map<string, number>()
  for (const row of detailRows.value) {
    byGroup.set(row.group, (byGroup.get(row.group) || 0) + row.count)
  }
  const totalCount = selectedDailyRow.value?.total || 0
  return Array.from(byGroup.entries())
    .map(([group, count]) => ({
      group,
      count,
      share: totalCount ? count / totalCount : 0,
    }))
    .sort((a, b) => b.count - a.count || a.group.localeCompare(b.group))
})

const topPages = computed(() => topTargets('page'))
const topAPIs = computed(() => topTargets('api'))

function topTargets(kind: string) {
  const byTarget = new Map<string, VisitCount>()
  for (const row of rows.value) {
    if (row.kind !== kind) continue
    const key = `${row.target}\u0000${row.method || ''}`
    const current = byTarget.get(key)
    if (current) {
      current.count += row.count
    } else {
      byTarget.set(key, { ...row })
    }
  }
  return Array.from(byTarget.values())
    .sort((a, b) => b.count - a.count || a.target.localeCompare(b.target))
    .slice(0, 20)
}

function selectDate(date: string) {
  emit('select-date', date)
}

function clearDate() {
  emit('clear-date')
}

function visitKindName(kind: string) {
  if (kind === 'page') return '页面'
  if (kind === 'api') return '接口'
  return kind
}

function auditGroup(row: VisitCount) {
  if (row.kind === 'page') {
    if (row.target.startsWith('fund-ratio-history:')) return '仓位历史页'
    if (row.target.startsWith('fund-share-history:')) return '规模历史页'
    if (row.target.startsWith('fund:')) return '基金页'
    if (row.target.startsWith('branch:')) return '分支页'
    if (row.target.startsWith('visits')) return '访问统计页'
    if (row.target.startsWith('contact-admin')) return '联系管理页'
    if (row.target === 'home') return '首页'
    if (row.target === 'debug') return '调试页'
    if (row.target === 'guide') return '指南页'
    if (row.target === 'navsettings') return '导航设置页'
    return '其他页面'
  }
  if (row.target === '/api/v1/home') return '首页接口'
  if (row.target.startsWith('/api/v1/funds/')) return '基金接口'
  if (row.target.startsWith('/api/v1/branches')) return '分支接口'
  if (row.target.startsWith('/api/v1/uploads') || row.target.startsWith('/api/v1/minute-history') || row.target.startsWith('/api/v1/valuation-anchors')) {
    return '上传与回填接口'
  }
  if (row.target.startsWith('/api/v1/navsettings')) return '导航设置接口'
  if (row.target.startsWith('/api/v1/visits')) return '访问统计接口'
  if (row.target.startsWith('/api/v1/contact') || row.target.startsWith('/api/v1/admin/contact')) return '联系表单接口'
  if (row.target === '/api/v1/debug/status' || row.target === '/api/v1/snapshots/status' || row.target === '/api/v1/refresh') return '运维状态接口'
  if (row.target === '/api/unknown') return '未知接口'
  return '其他接口'
}

function fmt(value: number) {
  return Math.round(value).toLocaleString('zh-CN')
}

function pct(value: number) {
  return `${(value * 100).toFixed(value >= 0.1 ? 1 : 2)}%`
}

function todayKey() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function barWidth(value: number) {
  return `${Math.max(4, Math.round((value / maxDailyTotal.value) * 100))}%`
}
</script>

<template>
  <section class="section visit-page">
    <section class="summary-grid">
      <div class="metric-card">
        <span>统计天数</span>
        <strong>{{ visits.days }}</strong>
      </div>
      <div class="metric-card">
        <span>总访问</span>
        <strong>{{ fmt(total) }}</strong>
      </div>
      <div class="metric-card">
        <span>页面访问</span>
        <strong>{{ fmt(pageTotal) }}</strong>
      </div>
      <div class="metric-card">
        <span>接口访问</span>
        <strong>{{ fmt(apiTotal) }}</strong>
      </div>
      <div class="metric-card">
        <span>今日访问</span>
        <strong>{{ fmt(todayTotal) }}</strong>
      </div>
    </section>

    <section class="table-section">
      <h2>按日访问</h2>
      <div class="table-wrap compact">
        <table class="visit-daily-table">
          <thead>
            <tr>
              <th class="sticky-col">日期</th>
              <th>总访问</th>
              <th class="visit-col-split">页面</th>
              <th class="visit-col-split">接口</th>
              <th>占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in dailyRows" :key="row.date" :class="{ 'is-selected': row.date === selectedDate }">
              <td class="sticky-col code">
                <button
                  type="button"
                  class="visit-date-button"
                  :aria-current="row.date === selectedDate ? 'page' : undefined"
                  @click="selectDate(row.date)"
                >
                  {{ row.date }}
                </button>
              </td>
              <td>{{ fmt(row.total) }}</td>
              <td class="visit-col-split">{{ fmt(row.page) }}</td>
              <td class="visit-col-split">{{ fmt(row.api) }}</td>
              <td class="visit-bar-cell">
                <span class="visit-bar"><i :style="{ width: barWidth(row.total) }"></i></span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="selectedDate" class="table-section visit-detail-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>单日明细</h2>
          <p class="table-inline-note">{{ selectedDate }}</p>
        </div>
        <button type="button" class="table-action-button" @click="clearDate">返回总览</button>
      </div>
      <section class="summary-grid visit-detail-summary">
        <div class="metric-card">
          <span>当日访问</span>
          <strong>{{ fmt(selectedDailyRow?.total || 0) }}</strong>
        </div>
        <div class="metric-card">
          <span>页面访问</span>
          <strong>{{ fmt(selectedDailyRow?.page || 0) }}</strong>
        </div>
        <div class="metric-card">
          <span>接口访问</span>
          <strong>{{ fmt(selectedDailyRow?.api || 0) }}</strong>
        </div>
        <div class="metric-card">
          <span>审计行数</span>
          <strong>{{ fmt(detailRows.length) }}</strong>
        </div>
      </section>
      <section class="visit-grid visit-detail-grid">
        <section class="table-section visit-inner-section">
          <h2>分组汇总</h2>
          <div class="table-wrap compact">
            <table class="visit-detail-group-table">
              <thead>
                <tr>
                  <th class="sticky-col">分组</th>
                  <th>访问</th>
                  <th>占比</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in detailGroupRows" :key="row.group">
                  <td class="sticky-col name">{{ row.group }}</td>
                  <td>{{ fmt(row.count) }}</td>
                  <td>{{ pct(row.share) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="table-section visit-inner-section">
          <h2>目标明细</h2>
          <div class="table-wrap compact">
            <table class="visit-detail-table">
              <thead>
                <tr>
                  <th class="sticky-col">目标</th>
                  <th>#</th>
                  <th>类型</th>
                  <th>分组</th>
                  <th class="visit-col-method">方法</th>
                  <th>访问</th>
                  <th class="visit-col-share">当日占比</th>
                  <th class="visit-col-share">类型内占比</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in detailRows" :key="`${row.kind}-${row.method || ''}-${row.target}`">
                  <td class="sticky-col name">{{ row.target }}</td>
                  <td>{{ row.rank }}</td>
                  <td>{{ visitKindName(row.kind) }}</td>
                  <td>{{ row.group }}</td>
                  <td class="visit-col-method">{{ row.method || '-' }}</td>
                  <td>{{ fmt(row.count) }}</td>
                  <td class="visit-col-share">{{ pct(row.dayShare) }}</td>
                  <td class="visit-col-share">{{ pct(row.kindShare) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </section>

    <section class="visit-grid">
      <section class="table-section">
        <h2>页面排行</h2>
        <div class="table-wrap compact">
          <table class="visit-target-table">
            <thead>
              <tr>
                <th class="sticky-col">页面</th>
                <th>访问</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in topPages" :key="row.target">
                <td class="sticky-col name">{{ row.target }}</td>
                <td>{{ fmt(row.count) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="table-section">
        <h2>接口排行</h2>
        <div class="table-wrap compact">
          <table class="visit-target-table">
            <thead>
              <tr>
                <th class="sticky-col">接口</th>
                <th class="visit-col-method">方法</th>
                <th>访问</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in topAPIs" :key="`${row.method || ''}-${row.target}`">
                <td class="sticky-col name">{{ row.target }}</td>
                <td class="visit-col-method">{{ row.method }}</td>
                <td>{{ fmt(row.count) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </section>
  </section>
</template>
