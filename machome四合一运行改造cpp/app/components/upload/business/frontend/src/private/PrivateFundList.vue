<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ChinaBroadMarketETF, PrivateFundListItem, PrivateFundListResponse } from '../lib/privateTypes'

const props = defineProps<{
  response: PrivateFundListResponse
  researchFunds: ChinaBroadMarketETF[]
}>()

type DirectoryKey = 'quick-index' | 'all' | 'energy' | 'silver' | 'china-internet' | 'india' | 'nasdaq' | 'sp500' | 'nikkei' | 'germany' | 'a-share-broad-market' | 'other'

type FundDirectory = {
  key: DirectoryKey
  title: string
  description: string
}

type FundListItem = {
  symbol: string
  name: string
  indexName?: string
  kind: 'valuation' | 'research'
  modelVersion: string
  valuationKind?: string
  ready: boolean
  actionable: boolean
  basketBidNAV: number | null
  basketAskNAV: number | null
  buyDirectionPremiumRate: number | null
  sellDirectionPremiumRate: number | null
  settlementNAV?: number | null
  tradingNAV?: number | null
  settlementPremiumRate?: number | null
  tradingPremiumRate?: number | null
  activeContract?: string
  shareDate?: string
  shareChange10K?: number | null
  shareChangePct?: number | null
}

const directories: FundDirectory[] = [
  { key: 'all', title: '全部标的', description: '全部已注册 Private ETF' },
  { key: 'energy', title: '标普油气', description: 'XOP 代理与油气成分篮子' },
  { key: 'silver', title: '白银基金', description: '上期所 AG 结算与交易双估值' },
  { key: 'china-internet', title: '中概互联网', description: '多市场 PCF 成分券估值' },
  { key: 'india', title: '印度基金', description: 'T−2 多市场锚点与 INDA 估值' },
  { key: 'nasdaq', title: '纳斯达克100', description: 'NQ 期货代理估值' },
  { key: 'sp500', title: '标普500', description: 'ES 期货代理估值' },
  { key: 'nikkei', title: '日经225', description: 'N225M 期货代理估值' },
  { key: 'germany', title: '德国DAX', description: 'Xetra 17:35 锚定 FDXM 代理估值' },
  { key: 'a-share-broad-market', title: 'A股宽基', description: '仅跟踪历史份额，不纳入估值' },
  { key: 'other', title: '其他模型', description: '尚未归入既有指数目录' },
]

const quickIndexDirectory: FundDirectory = {
  key: 'quick-index',
  title: '快速索引',
  description: '标普油气、白银基金与印度基金',
}
const quickIndexKeys: Exclude<DirectoryKey, 'quick-index'>[] = ['energy', 'silver', 'india']

const selectedDirectory = ref<DirectoryKey>('quick-index')

const allFunds = computed<FundListItem[]>(() => [
  ...props.response.funds.map((item: PrivateFundListItem) => ({
    symbol: item.symbol,
    name: item.name,
    kind: 'valuation' as const,
    modelVersion: item.model_version,
    valuationKind: item.valuation_kind,
    ready: item.ready,
    actionable: item.actionable,
    basketBidNAV: item.basket_bid_nav,
    basketAskNAV: item.basket_ask_nav,
    buyDirectionPremiumRate: item.buy_direction_premium_rate,
    sellDirectionPremiumRate: item.sell_direction_premium_rate,
    settlementNAV: item.settlement_nav,
    tradingNAV: item.trading_nav,
    settlementPremiumRate: item.settlement_premium_rate,
    tradingPremiumRate: item.trading_premium_rate,
    activeContract: item.active_contract,
    shareDate: item.share_date,
    shareChange10K: item.share_change_10k,
    shareChangePct: item.share_change_pct,
  })),
  ...props.researchFunds.map((item: ChinaBroadMarketETF) => ({
    symbol: item.symbol,
    name: item.name,
    indexName: item.index_name,
    kind: 'research' as const,
    modelVersion: 'research.a-share-broad-market',
    ready: false,
    actionable: false,
    basketBidNAV: null,
    basketAskNAV: null,
    buyDirectionPremiumRate: null,
    sellDirectionPremiumRate: null,
  })),
])

function directoryFor(item: FundListItem): Exclude<DirectoryKey, 'quick-index'> {
  if (item.kind === 'research') return 'a-share-broad-market'
  const model = item.modelVersion.toLowerCase()
  if (model.includes('.xop-') || model.includes('.xop.')) return 'energy'
  if (item.valuationKind === 'silver_settlement' || model.includes('.ag-settlement.')) return 'silver'
  if (model.includes('full-cash-substitution')) return 'china-internet'
  if (model.includes('.t2-multimarket.inda-')) return 'india'
  if (model.includes('.n225m-') || model.includes('.n225m.')) return 'nikkei'
  if (model.includes('.nq-') || model.includes('.nq.')) return 'nasdaq'
  if (model.includes('.es-') || model.includes('.es.')) return 'sp500'
  if (model.includes('.fdxm-') || model.includes('.fdxm.')) return 'germany'
  return 'other'
}

function itemsFor(key: DirectoryKey) {
  if (key === 'all') return allFunds.value
  if (key === 'quick-index') {
    return allFunds.value
      .filter((item) => quickIndexKeys.includes(directoryFor(item)))
      .sort((left, right) => quickIndexKeys.indexOf(directoryFor(left)) - quickIndexKeys.indexOf(directoryFor(right)))
  }
  return allFunds.value.filter((item) => directoryFor(item) === key)
}

const visibleDirectories = computed(() => directories.filter((directory) => (
  directory.key === 'all' || itemsFor(directory.key).length > 0
)))

const activeDirectory = computed(() => (
  selectedDirectory.value === 'quick-index'
    ? quickIndexDirectory
    : directories.find((directory) => directory.key === selectedDirectory.value) || directories[0]
))

const visibleFunds = computed(() => itemsFor(selectedDirectory.value))

function readyCount(key: DirectoryKey) {
  return itemsFor(key).filter((item) => item.kind === 'valuation' && item.ready).length
}

function usesSilver(item: FundListItem) {
  return item.valuationKind === 'silver_settlement' || item.modelVersion.includes('.ag-settlement.')
}

function firstNAV(item: FundListItem) {
  return usesSilver(item) ? item.settlementNAV : item.basketBidNAV
}

function secondNAV(item: FundListItem) {
  return usesSilver(item) ? item.tradingNAV : item.basketAskNAV
}

function firstPremium(item: FundListItem) {
  return usesSilver(item) ? item.settlementPremiumRate : item.buyDirectionPremiumRate
}

function secondPremium(item: FundListItem) {
  return usesSilver(item) ? item.tradingPremiumRate : item.sellDirectionPremiumRate
}

function formatNumber(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(4)}%`
}

function formatShareChange(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatNumber(value, 2)} 万份`
}

function formatShareChangePercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function premiumClass(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  if (value > 0) return 'private-rate-positive'
  if (value < 0) return 'private-rate-negative'
  return 'private-rate-flat'
}

function shareChangeClass(value: number | null | undefined) {
  return premiumClass(value)
}

</script>

<template>
  <section class="private-page-stack">
    <section class="private-panel">
      <div class="private-section-heading">
        <div>
          <p class="private-kicker">PRIVATE FUND LIST</p>
          <h2>独立 ETF 估值列表</h2>
        </div>
      </div>

      <a class="private-tool-entry" href="/private/hk-connect-fx" target="_blank" rel="noopener noreferrer">
        <span>
          <small>交易工具 · HK CONNECT FX</small>
          <strong>港股通结算汇率实时估值</strong>
          <em>沪深港股通成交额 + CFETS HKD/CNY BID/ASK · 14:30–16:00 决策窗口</em>
        </span>
        <b>打开 →</b>
      </a>

      <div v-if="allFunds.length" class="private-directory-layout">
        <aside class="private-directory-panel" aria-label="Private ETF 分类目录">
          <div class="private-quick-index">
            <div class="private-directory-heading">
              <span>快速索引</span>
              <strong>1 项</strong>
            </div>
            <nav class="private-directory-nav" aria-label="Private ETF 快速索引">
              <button
                type="button"
                class="private-directory-button private-quick-index-button"
                :class="{ 'is-active': selectedDirectory === quickIndexDirectory.key }"
                :aria-pressed="selectedDirectory === quickIndexDirectory.key"
                @click="selectedDirectory = quickIndexDirectory.key"
              >
                <span>
                  <strong>{{ quickIndexDirectory.title }}</strong>
                  <small>{{ quickIndexDirectory.description }}</small>
                </span>
                <em>{{ itemsFor(quickIndexDirectory.key).length }}</em>
              </button>
            </nav>
          </div>
          <div class="private-directory-heading">
            <span>分类目录</span>
            <strong>{{ allFunds.length }} 只</strong>
          </div>
          <nav class="private-directory-nav">
            <button
              v-for="directory in visibleDirectories"
              :key="directory.key"
              type="button"
              class="private-directory-button"
              :class="{ 'is-active': selectedDirectory === directory.key }"
              :aria-pressed="selectedDirectory === directory.key"
              @click="selectedDirectory = directory.key"
            >
              <span>
                <strong>{{ directory.title }}</strong>
                <small>{{ directory.description }}</small>
              </span>
              <em>{{ itemsFor(directory.key).length }}</em>
            </button>
          </nav>
        </aside>

        <div class="private-directory-content">
          <div class="private-directory-result">
            <div>
              <span>当前目录</span>
              <strong>{{ activeDirectory.title }}</strong>
            </div>
            <p>
              共 {{ visibleFunds.length }} 只
              <span>·</span>
              <template v-if="selectedDirectory === 'a-share-broad-market'">仅历史份额研究</template>
              <template v-else>{{ readyCount(selectedDirectory) }} 只 READY</template>
            </p>
          </div>

          <div class="private-table-wrap">
            <table class="private-data-table private-list-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>计算状态</th>
                  <th>可操作</th>
                  <th>{{ selectedDirectory === 'silver' ? '结算口径 NAV' : selectedDirectory === 'quick-index' ? '第一口径 NAV' : '篮子 Bid NAV' }}</th>
                  <th>{{ selectedDirectory === 'silver' ? '交易口径 NAV' : selectedDirectory === 'quick-index' ? '第二口径 NAV' : '篮子 Ask NAV' }}</th>
                  <th class="private-buy-column">{{ selectedDirectory === 'silver' ? '相对结算估值' : selectedDirectory === 'quick-index' ? '第一口径估值' : '买入方向折溢价' }}</th>
                  <th class="private-sell-column">{{ selectedDirectory === 'silver' ? '相对交易估值' : selectedDirectory === 'quick-index' ? '第二口径估值' : '卖出方向折溢价' }}</th>
                  <th class="private-share-change-column">份额变动情况</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in visibleFunds" :key="item.symbol">
                  <td class="private-symbol-cell">
                    <a
                      :href="item.kind === 'research' ? `/private/a-share-broad-market/${item.symbol}` : `/private/${item.symbol}`"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <strong>{{ item.symbol }}</strong>
                      <span>{{ item.name }}<template v-if="item.indexName"> · {{ item.indexName }}</template><template v-if="item.activeContract"> · {{ item.activeContract }}</template></span>
                    </a>
                  </td>
                  <td>
                    <span class="private-state-pill" :class="item.kind === 'research' ? 'state-warn' : (item.ready ? 'state-good' : 'state-bad')">
                      {{ item.kind === 'research' ? 'RESEARCH' : (item.ready ? 'READY' : 'NOT READY') }}
                    </span>
                  </td>
                  <td>
                    <span
                      class="private-state-pill"
                      :class="item.kind === 'research' ? 'state-good' : (item.actionable ? 'state-good' : 'state-warn')"
                    >
                      {{ item.kind === 'research' ? 'HISTORY' : (item.actionable ? 'ACTIONABLE' : 'BLOCKED') }}
                    </span>
                  </td>
                  <td>{{ formatNumber(firstNAV(item), 6) }}</td>
                  <td>{{ formatNumber(secondNAV(item), 6) }}</td>
                  <td class="private-buy-column private-rate-strong" :class="premiumClass(firstPremium(item))">
                    {{ formatPercent(firstPremium(item)) }}
                  </td>
                  <td class="private-sell-column private-rate-strong" :class="premiumClass(secondPremium(item))">
                    {{ formatPercent(secondPremium(item)) }}
                  </td>
                  <td class="private-share-change-column" :class="shareChangeClass(item.shareChange10K)">
                    <strong>{{ formatShareChange(item.shareChange10K) }}</strong>
                    <span v-if="item.shareChangePct !== null && item.shareChangePct !== undefined">
                      {{ formatShareChangePercent(item.shareChangePct) }} · {{ item.shareDate || '—' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div v-else class="private-inline-empty">
        Private API 尚未返回已注册列表项。
      </div>
    </section>
  </section>
</template>
