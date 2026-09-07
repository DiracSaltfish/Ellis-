<script setup lang="ts">
import type { YesterdayRedemptionBoardResponse } from '../lib/types'

const props = defineProps<{
  board: YesterdayRedemptionBoardResponse
}>()

defineEmits<{
  selectFund: [symbol: string]
}>()

function fmt(value: number | null | undefined, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) return '-'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function fmtPct(value: number | null | undefined, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) return '-'
  return `${fmt(value, digits)}%`
}

function pctClass(value: number | null | undefined) {
  if (value === undefined || value === null || !Number.isFinite(value)) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function branchText(names: string[] | undefined) {
  return names?.length ? names.join(' / ') : '-'
}
</script>

<template>
  <section class="section redemption-board-page">
    <section class="table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>最新披露日排序</h2>
          <p class="table-inline-note">
            <span>披露日：{{ board.share_date || '-' }}</span>
            <span>延迟同步：{{ board.stale_symbols }}</span>
            <span>缺少前值：{{ board.missing_change_rows }}</span>
          </p>
        </div>
      </div>

      <p v-if="!board.rows.length" class="model-note">
        暂无可展示的昨日赎回榜记录。
      </p>
      <div v-else class="table-wrap">
        <table class="redemption-board-table">
          <thead>
            <tr>
              <th class="sticky-col">排名</th>
              <th class="redemption-board-code-col">代码</th>
              <th class="redemption-board-name-col">基金简称</th>
              <th class="redemption-board-branch-col">所属分组</th>
              <th class="redemption-board-share-col">昨日份额({{ board.unit || '万份' }})</th>
              <th class="redemption-board-change-col">新增({{ board.unit || '万份' }})</th>
              <th class="redemption-board-ratio-col">新增比例</th>
              <th class="redemption-board-premium-col">收盘溢价</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in board.rows" :key="row.symbol">
              <td class="sticky-col" data-label="排名">{{ index + 1 }}</td>
              <td class="redemption-board-code-col" data-label="代码">
                <button type="button" class="code-link" @click="$emit('selectFund', row.symbol)">
                  {{ row.symbol }}
                </button>
              </td>
              <td class="name redemption-board-name-col" data-label="基金简称">
                <button type="button" class="name-link" @click="$emit('selectFund', row.symbol)">
                  {{ row.name || row.symbol }}
                </button>
              </td>
              <td class="branch-name redemption-board-branch-col" data-label="所属分组">{{ branchText(row.branch_names) }}</td>
              <td class="redemption-board-share-col" data-label="昨日份额">{{ fmt(row.shares_10k) }}</td>
              <td class="redemption-board-change-col" :class="pctClass(row.share_change_10k)" data-label="新增">
                {{ fmt(row.share_change_10k) }}
              </td>
              <td class="redemption-board-ratio-col" :class="pctClass(row.share_change_pct)" data-label="新增比例">
                {{ fmtPct(row.share_change_pct) }}
              </td>
              <td class="redemption-board-premium-col" :class="pctClass(row.close_premium_pct)" data-label="收盘溢价">
                {{ fmtPct(row.close_premium_pct) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<style scoped>
.redemption-board-page {
  width: 100%;
}
</style>
