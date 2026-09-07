<script setup lang="ts">
import type { QuoteTableRow } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'

withDefaults(
  defineProps<{
    rows: QuoteTableRow[]
    title?: string
    markValuationStatus?: boolean
  }>(),
  { title: '参考数据', markValuationStatus: false },
)

defineEmits<{
  select: [symbol: string]
}>()

function pctClass(value: number) {
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function fmt(value: number, digits = 3) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(digits)
}

function statusText(row: QuoteTableRow) {
  return row.realtime_status || (row.is_realtime ? 'realtime' : 'stale')
}
</script>

<template>
  <section class="table-section">
    <h2>{{ title }}</h2>
    <div class="table-wrap">
      <table class="quote-table">
        <thead>
          <tr>
            <th class="sticky-col">代码</th>
            <th>价格</th>
            <th>涨幅</th>
            <th class="quote-col-date">日期</th>
            <th class="quote-col-time">时间</th>
            <th>名称</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.symbol"
            :data-source="row.source"
            :data-source-symbol="row.source_symbol || ''"
            :data-status="statusText(row)"
            :data-stale-reason="row.stale_reason || ''"
          >
            <td class="sticky-col code">
              <button
                class="code-link"
                :class="{
                  'valuation-unconfirmed': markValuationStatus && !isValuationConfirmed(row.symbol),
                }"
                type="button"
                @click="$emit('select', row.symbol)"
              >
                {{ row.symbol }}
              </button>
            </td>
            <td>{{ fmt(row.price, 3) }}</td>
            <td :class="pctClass(row.change_pct)">{{ fmt(row.change_pct, 2) }}%</td>
            <td class="quote-col-date">{{ row.quote_date }}</td>
            <td class="quote-col-time">{{ row.quote_time }}</td>
            <td
              class="name"
              :class="{
                'valuation-unconfirmed': markValuationStatus && !isValuationConfirmed(row.symbol),
              }"
            >
              {{ row.name }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
