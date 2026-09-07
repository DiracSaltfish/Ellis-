<script setup lang="ts">
import { computed } from 'vue'
import type { BranchEstimateRow } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'

type EstimateSortField = 'official_premium' | 'fair_premium' | 'realtime_premium'
type SortDirection = 'asc' | 'desc'

const props = withDefaults(
  defineProps<{
    rows: BranchEstimateRow[]
    title?: string
    headerNote?: string
    headerNoteShowLegend?: boolean
    toggleLabel?: string
    sortField?: EstimateSortField | null
    sortDirection?: SortDirection
    allowRealtimeSort?: boolean
    mobileMode?: boolean
    showDetailHint?: boolean
  }>(),
  {
    title: '',
    headerNote: '',
    headerNoteShowLegend: false,
    toggleLabel: '',
    sortField: null,
    sortDirection: 'desc',
    allowRealtimeSort: false,
    mobileMode: false,
    showDetailHint: false,
  },
)

defineEmits<{
  select: [symbol: string]
  toggleVisibility: []
  sort: [field: EstimateSortField]
}>()

const showRealtimeColumns = computed(() =>
  props.rows.some(
    (row) =>
      row.realtime_est !== null &&
      row.realtime_est !== undefined &&
      row.realtime_premium !== null &&
      row.realtime_premium !== undefined,
  ),
)

const showT1CloseColumns = computed(() => props.rows.some((row) => showT1CloseEstimate(row)))

function pctClass(value: number | null | undefined) {
  if (value === null || value === undefined) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function hasRealtimeEstimate(row: BranchEstimateRow) {
  return (
    row.realtime_est !== null &&
    row.realtime_est !== undefined &&
    row.realtime_premium !== null &&
    row.realtime_premium !== undefined
  )
}

function showT1CloseEstimate(row: BranchEstimateRow) {
  return !hasRealtimeEstimate(row)
}

function fmt(value: number | null | undefined, digits = 3) {
  if (value === null || value === undefined) return ''
  if (!Number.isFinite(value)) return ''
  return value.toFixed(digits)
}

function fmtPct(value: number | null | undefined, digits = 2) {
  const formatted = fmt(value, digits)
  return formatted ? `${formatted}%` : ''
}

function fmtPurchaseLimit(value: number | null | undefined, status?: string) {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  if (value <= 0 && status?.includes('场内交易')) return ''
  if (value <= 0) return '0'
  if (value >= 800_000_000) return '无限额'
  if (value < 10_000) return `${value.toFixed(0)}元`
  if (value < 100_000_000) return `${(value / 10_000).toFixed(1)}万`
  return `${(value / 100_000_000).toFixed(1)}亿`
}

function sortIndicator(field: EstimateSortField) {
  if (props.sortField !== field) return ''
  return props.sortDirection === 'desc' ? '↓' : '↑'
}

function isUnconfirmed(symbol: string) {
  return !isValuationConfirmed(symbol)
}
</script>

<template>
  <section class="table-section estimate-table-section">
    <div v-if="title || headerNote || toggleLabel" class="table-section-header">
      <div v-if="title || headerNote" class="table-section-heading">
        <h2 v-if="title">{{ title }}</h2>
        <p v-if="headerNote" class="table-inline-note">
          <span v-if="headerNoteShowLegend" class="valuation-unconfirmed">删除线</span>
          <span>{{ headerNote }}</span>
        </p>
      </div>
      <button
        v-if="toggleLabel"
        type="button"
        class="table-action-button"
        @click="$emit('toggleVisibility')"
      >
        {{ toggleLabel }}
      </button>
    </div>
    <div v-if="showDetailHint && !mobileMode" class="estimate-detail-hint" aria-hidden="true">
      <span class="estimate-detail-hint-label">
        <span>点击代码</span>
        <span>进入详情</span>
      </span>
      <svg
        class="estimate-detail-hint-arrow"
        viewBox="0 0 132 88"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M18 18C10 46 19 71 48 73C63 74 76 68 85 58"
          stroke="currentColor"
          stroke-linecap="round"
          stroke-width="5"
        />
        <path
          d="M87 49L104 58M87 67L104 58"
          stroke="currentColor"
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="5"
        />
      </svg>
    </div>
    <div class="table-wrap">
      <table class="estimate-table">
        <thead>
          <tr>
            <th v-if="!mobileMode" class="sticky-col">代码</th>
            <th class="estimate-name-col">名称</th>
            <th class="purchase-limit-col">申购限额</th>
            <th class="est-value-col">T-2官方公布净值</th>
            <th class="premium-col">
              <button
                type="button"
                class="table-sort-button"
                :class="{ active: sortField === 'official_premium' }"
                @click="$emit('sort', 'official_premium')"
              >
                <span>T-2官方溢价</span>
                <span class="table-sort-indicator">{{ sortIndicator('official_premium') }}</span>
              </button>
            </th>
            <th v-if="showT1CloseColumns" class="est-value-col">T-1收盘时点估值</th>
            <th v-if="showT1CloseColumns" class="premium-col">
              <button
                type="button"
                class="table-sort-button"
                :class="{ active: sortField === 'fair_premium' }"
                @click="$emit('sort', 'fair_premium')"
              >
                <span>T-1收盘时点溢价</span>
                <span class="table-sort-indicator">{{ sortIndicator('fair_premium') }}</span>
              </button>
            </th>
            <th v-if="showRealtimeColumns" class="est-value-col">实时估值</th>
            <th v-if="showRealtimeColumns" class="premium-col">
              <button
                v-if="allowRealtimeSort"
                type="button"
                class="table-sort-button"
                :class="{ active: sortField === 'realtime_premium' }"
                @click="$emit('sort', 'realtime_premium')"
              >
                <span>实时溢价</span>
                <span class="table-sort-indicator">{{ sortIndicator('realtime_premium') }}</span>
              </button>
              <span v-else>实时溢价</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.symbol"
            :data-model="row.model_version"
            :data-note="row.note || ''"
          >
            <td v-if="!mobileMode" class="sticky-col code">
              <button
                class="code-link"
                :class="{ 'valuation-unconfirmed': isUnconfirmed(row.symbol) }"
                type="button"
                @click="$emit('select', row.symbol)"
              >
                {{ row.symbol }}
              </button>
            </td>
            <td
              class="name"
              :class="{ 'valuation-unconfirmed': isUnconfirmed(row.symbol) }"
            >
              <button
                v-if="mobileMode"
                class="name-link estimate-name-text"
                :class="{ 'valuation-unconfirmed': isUnconfirmed(row.symbol) }"
                type="button"
                @click="$emit('select', row.symbol)"
              >
                {{ row.name }}
              </button>
              <span v-else class="estimate-name-text">{{ row.name }}</span>
            </td>
            <td class="purchase-limit-col" :title="row.purchase_status || ''">
              {{ fmtPurchaseLimit(row.purchase_limit, row.purchase_status) }}
            </td>
            <td class="est-value-col">
              {{ fmt(row.official_est, 4) }}
            </td>
            <td class="premium-col" :class="pctClass(row.official_premium)">
              {{ fmtPct(row.official_premium) }}
            </td>
            <td
              v-if="showT1CloseColumns"
              class="est-value-col"
              :class="{ 'valuation-unconfirmed': isUnconfirmed(row.symbol) }"
            >
              {{ showT1CloseEstimate(row) ? fmt(row.fair_est, 4) : '' }}
            </td>
            <td
              v-if="showT1CloseColumns"
              class="premium-col"
              :class="[
                pctClass(showT1CloseEstimate(row) ? row.fair_premium : null),
                { 'valuation-unconfirmed': isUnconfirmed(row.symbol) },
              ]"
            >
              {{ showT1CloseEstimate(row) ? fmtPct(row.fair_premium) : '' }}
            </td>
            <td
              v-if="showRealtimeColumns"
              class="est-value-col"
              :class="{ 'valuation-unconfirmed': isUnconfirmed(row.symbol) }"
            >
              {{ fmt(row.realtime_est, 4) }}
            </td>
            <td
              v-if="showRealtimeColumns"
              class="premium-col"
              :class="[
                pctClass(row.realtime_premium),
                { 'valuation-unconfirmed': isUnconfirmed(row.symbol) },
              ]"
            >
              {{ fmtPct(row.realtime_premium) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.estimate-table-section {
  position: relative;
  overflow: visible;
}

.estimate-detail-hint {
  position: absolute;
  top: 156px;
  right: calc(100% + 18px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: rgba(123, 136, 156, 0.52);
  pointer-events: none;
}

.estimate-detail-hint-label {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 88px;
  padding: 0;
  color: rgba(88, 99, 117, 0.74);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.15;
  text-align: center;
  white-space: normal;
}

.estimate-detail-hint-arrow {
  width: 82px;
  height: 56px;
  overflow: visible;
  filter: drop-shadow(0 6px 10px rgba(148, 163, 184, 0.08));
}

@media (max-width: 1380px) {
  .estimate-detail-hint {
    right: calc(100% + 10px);
    transform: scale(0.94);
    transform-origin: center top;
  }
}

@media (max-width: 1240px) {
  .estimate-detail-hint {
    display: none;
  }
}
</style>
