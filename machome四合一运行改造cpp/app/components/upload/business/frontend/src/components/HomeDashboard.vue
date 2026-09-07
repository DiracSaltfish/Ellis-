<script setup lang="ts">
import { computed } from 'vue'
import type { BranchSummary, HomeSnapshot } from '../lib/types'
import { isValuationConfirmed } from '../lib/valuationConfirmation'

const props = defineProps<{ home: HomeSnapshot }>()

defineEmits<{
  selectBranch: [key: string]
  selectFund: [symbol: string]
  openGuide: []
}>()

const totals = computed(() => {
  return props.home.branches.reduce(
    (acc, item) => {
      acc.references += item.reference_count
      acc.estimates += item.estimate_count
      return acc
    },
    { references: 0, estimates: 0 },
  )
})

function fmt(value: number, digits = 2) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')
}

function pctClass(value: number) {
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}
</script>

<template>
  <section class="section">
    <section class="table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>使用指南</h2>
        </div>
        <button type="button" class="table-action-button" @click="$emit('openGuide')">
          查看使用指南 / 溢价率说明
        </button>
      </div>
    </section>

    <section class="summary-grid">
      <div class="metric-card">
        <span>分支数量</span>
        <strong>{{ home.branches.length }}</strong>
      </div>
      <div class="metric-card">
        <span>参考行情</span>
        <strong>{{ totals.references }}</strong>
      </div>
      <div class="metric-card">
        <span>估值行数</span>
        <strong>{{ totals.estimates }}</strong>
      </div>
    </section>

    <section class="table-section">
      <h2>分支总览</h2>
      <div class="table-wrap compact">
        <table class="home-branch-table">
          <thead>
            <tr>
              <th class="sticky-col home-branch-name-col">分支</th>
              <th>参考</th>
              <th>估值</th>
              <th class="home-branch-premium-col">最大T-1收盘时点溢价</th>
              <th class="home-branch-avg-col">平均绝对溢价</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="summary in home.branches"
              :key="summary.branch.key"
              :data-models="JSON.stringify(summary.model_counts)"
              :data-as-of="summary.as_of"
              :data-realtime-count="summary.realtime_count"
              :data-stale-count="summary.stale_count"
              :data-unsupported-count="summary.unsupported_count"
              :data-demo-count="summary.demo_count"
            >
              <td class="sticky-col code">
                <button
                  class="code-link"
                  type="button"
                  @click="$emit('selectBranch', summary.branch.key)"
                >
                  {{ summary.branch.name_cn }}
                </button>
              </td>
              <td>{{ summary.reference_count }}</td>
              <td>{{ summary.estimate_count }}</td>
              <td class="home-branch-premium-col" :class="pctClass(summary.max_fair_premium)">
                <button
                  v-if="summary.max_fair_premium_symbol"
                  class="inline-link"
                  :class="{
                    'valuation-unconfirmed': !isValuationConfirmed(summary.max_fair_premium_symbol),
                  }"
                  type="button"
                  @click="$emit('selectFund', summary.max_fair_premium_symbol)"
                >
                  {{ summary.max_fair_premium_symbol }}
                </button>
                <span
                  :class="{
                    'valuation-unconfirmed': !isValuationConfirmed(summary.max_fair_premium_symbol),
                  }"
                >
                  {{ fmt(summary.max_fair_premium) }}%
                </span>
              </td>
              <td class="home-branch-avg-col">{{ fmt(summary.avg_abs_fair_premium) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
