<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PrivateChinaBroadMarketDetail from './private/PrivateChinaBroadMarketDetail.vue'
import PrivateFundDetail from './private/PrivateFundDetail.vue'
import PrivateFundList from './private/PrivateFundList.vue'
import PrivateHKConnectFX from './private/PrivateHKConnectFX.vue'
import PrivateHKConnectFXCloseHistory from './private/PrivateHKConnectFXCloseHistory.vue'
import PrivateIndiaHistoryReview from './private/PrivateIndiaHistoryReview.vue'
import PrivateIndiaNiftyBridgeHistoryReview from './private/PrivateIndiaNiftyBridgeHistoryReview.vue'
import PrivateSilverCloseHistory from './private/PrivateSilverCloseHistory.vue'
import { getPrivateChinaBroadMarketFunds, getPrivateChinaBroadMarketShareHistory, getPrivateFund, getPrivateFundList, getPrivateIndiaHistoryReview, getPrivateIndiaNiftyReview, getPrivateSilverCloseHistory } from './lib/privateApi'
import type { ChinaBroadMarketETF, PrivateFundListResponse, PrivateFundSnapshot, PrivateFundSymbol, PrivateIndiaHistoryReviewResponse, PrivateIndiaNiftyReviewResponse, PrivateSilverCloseHistoryResponse } from './lib/privateTypes'
import type { ShareHistoryResponse } from './lib/types'

type PrivateRoute =
  | { kind: 'list'; path: '/private' }
  | { kind: 'hk-connect-fx'; path: '/private/hk-connect-fx' }
  | { kind: 'hk-connect-fx-close-history'; path: '/private/hk-connect-fx/close-history' }
  | { kind: 'fund'; path: string; symbol: PrivateFundSymbol }
  | { kind: 'china-broad-market-history'; path: string; symbol: string }
  | { kind: 'india-history-review'; path: string; symbol: 'SZ164824' }
  | { kind: 'india-nifty-history-review'; path: string; symbol: 'SZ164824' }
  | { kind: 'silver-close-history'; path: string; symbol: 'SZ161226' }
  | { kind: 'not-found'; path: string }

const routePath = ref(window.location.pathname)
const list = ref<PrivateFundListResponse | null>(null)
const chinaBroadMarketFunds = ref<ChinaBroadMarketETF[]>([])
const fund = ref<PrivateFundSnapshot | null>(null)
const chinaBroadMarketHistory = ref<ShareHistoryResponse | null>(null)
const indiaHistoryReview = ref<PrivateIndiaHistoryReviewResponse | null>(null)
const indiaNiftyHistoryReview = ref<PrivateIndiaNiftyReviewResponse | null>(null)
const silverCloseHistory = ref<PrivateSilverCloseHistoryResponse | null>(null)
const loading = ref(false)
const error = ref('')
let refreshTimer: number | undefined
let activeRequest: AbortController | null = null

const route = computed<PrivateRoute>(() => {
  if (routePath.value === '/private') return { kind: 'list', path: '/private' }
  if (routePath.value === '/private/hk-connect-fx') return { kind: 'hk-connect-fx', path: '/private/hk-connect-fx' }
  if (routePath.value === '/private/hk-connect-fx/close-history') return { kind: 'hk-connect-fx-close-history', path: '/private/hk-connect-fx/close-history' }
  const chinaBroadMarketHistoryMatch = routePath.value.match(/^\/private\/a-share-broad-market\/(S[HZ]\d{6})$/i)
  if (chinaBroadMarketHistoryMatch) {
    const symbol = chinaBroadMarketHistoryMatch[1].toUpperCase()
    return { kind: 'china-broad-market-history', path: `/private/a-share-broad-market/${symbol}`, symbol }
  }
  const indiaHistoryMatch = routePath.value.match(/^\/private\/(SZ164824)\/india-history-review$/i)
  if (indiaHistoryMatch) return { kind: 'india-history-review', path: `/private/${indiaHistoryMatch[1].toUpperCase()}/india-history-review`, symbol: 'SZ164824' }
  const indiaNiftyHistoryMatch = routePath.value.match(/^\/private\/(SZ164824)\/nifty-bridge-history-review$/i)
  if (indiaNiftyHistoryMatch) return { kind: 'india-nifty-history-review', path: `/private/${indiaNiftyHistoryMatch[1].toUpperCase()}/nifty-bridge-history-review`, symbol: 'SZ164824' }
  const silverCloseHistoryMatch = routePath.value.match(/^\/private\/(SZ161226)\/close-history$/i)
  if (silverCloseHistoryMatch) return { kind: 'silver-close-history', path: `/private/${silverCloseHistoryMatch[1].toUpperCase()}/close-history`, symbol: 'SZ161226' }
  const match = routePath.value.match(/^\/private\/([A-Z]{2}\d{6})$/)
  if (match) {
    const symbol = match[1] as PrivateFundSymbol
    return { kind: 'fund', path: `/private/${symbol}`, symbol }
  }
  return { kind: 'not-found', path: routePath.value }
})

async function loadRoute(silent = false) {
  activeRequest?.abort()
  activeRequest = new AbortController()
  const request = activeRequest
  if (!silent) loading.value = true
  error.value = ''

  try {
    if (route.value.kind === 'list') {
      const [response, research] = await Promise.all([
        getPrivateFundList(request.signal),
        getPrivateChinaBroadMarketFunds(request.signal),
      ])
      list.value = response
      chinaBroadMarketFunds.value = research.funds
      fund.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'hk-connect-fx') {
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'hk-connect-fx-close-history') {
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'fund') {
      fund.value = await getPrivateFund(route.value.symbol, request.signal)
      list.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'china-broad-market-history') {
      chinaBroadMarketHistory.value = await getPrivateChinaBroadMarketShareHistory(route.value.symbol, 60, request.signal)
      list.value = null
      fund.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'india-history-review') {
      indiaHistoryReview.value = await getPrivateIndiaHistoryReview(route.value.symbol, 120, request.signal)
      indiaNiftyHistoryReview.value = null
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'india-nifty-history-review') {
      indiaNiftyHistoryReview.value = await getPrivateIndiaNiftyReview(route.value.symbol, request.signal)
      indiaHistoryReview.value = null
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      silverCloseHistory.value = null
    } else if (route.value.kind === 'silver-close-history') {
      silverCloseHistory.value = await getPrivateSilverCloseHistory(route.value.symbol, 365, request.signal)
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
    } else {
      list.value = null
      fund.value = null
      chinaBroadMarketHistory.value = null
      indiaHistoryReview.value = null
      indiaNiftyHistoryReview.value = null
      silverCloseHistory.value = null
    }
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === 'AbortError') return
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    if (activeRequest === request) {
      activeRequest = null
      loading.value = false
    }
  }
}

function navigate(path: string) {
  if (window.location.pathname !== path) window.history.pushState(null, '', path)
  routePath.value = path
  void loadRoute()
}

function openIndiaHistoryReview(symbol: string) {
  if (symbol !== 'SZ164824') return
  navigate(`/private/${symbol}/india-history-review`)
}

function openIndiaNiftyHistoryReview(symbol: string) {
  if (symbol !== 'SZ164824') return
  navigate(`/private/${symbol}/nifty-bridge-history-review`)
}

function openSilverCloseHistory(symbol: string) {
  if (symbol !== 'SZ161226') return
  navigate(`/private/${symbol}/close-history`)
}

function handlePopState() {
  routePath.value = window.location.pathname
  void loadRoute()
}

onMounted(() => {
  if (window.location.pathname === '/private/') {
    window.history.replaceState(null, '', '/private')
    routePath.value = '/private'
  }
  void loadRoute()
  window.addEventListener('popstate', handlePopState)
  refreshTimer = window.setInterval(() => {
    if (route.value.kind === 'fund') void loadRoute(true)
  }, 3_000)
})

onBeforeUnmount(() => {
  activeRequest?.abort()
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
  window.removeEventListener('popstate', handlePopState)
})
</script>

<template>
  <div class="private-shell app-shell">
    <main>
      <div v-if="error" class="private-alert private-alert-error" role="alert">
        <strong>数据加载失败</strong>
        <span>{{ error }}</span>
        <button type="button" @click="loadRoute()">重试</button>
      </div>

      <div v-if="loading && route.kind !== 'not-found'" class="private-loading">
        正在读取独立估值数据…
      </div>

      <PrivateFundList
        v-else-if="route.kind === 'list' && list"
        :response="list"
        :research-funds="chinaBroadMarketFunds"
      />

      <PrivateChinaBroadMarketDetail
        v-else-if="route.kind === 'china-broad-market-history' && chinaBroadMarketHistory"
        :history="chinaBroadMarketHistory"
        @back="navigate('/private')"
      />

      <PrivateHKConnectFX
        v-else-if="route.kind === 'hk-connect-fx'"
        @back="navigate('/private')"
        @open-close-history="navigate('/private/hk-connect-fx/close-history')"
      />

      <PrivateHKConnectFXCloseHistory
        v-else-if="route.kind === 'hk-connect-fx-close-history'"
        @back="navigate('/private/hk-connect-fx')"
      />

      <PrivateFundDetail
        v-else-if="route.kind === 'fund' && fund"
        :snapshot="fund"
        @back="navigate('/private')"
        @open-india-history-review="openIndiaHistoryReview($event)"
        @open-india-nifty-history-review="openIndiaNiftyHistoryReview($event)"
        @open-silver-close-history="openSilverCloseHistory($event)"
      />

      <PrivateIndiaHistoryReview
        v-else-if="route.kind === 'india-history-review' && indiaHistoryReview"
        :history="indiaHistoryReview"
        @back-to-fund="navigate(`/private/${$event}`)"
        @open-nifty-bridge-review="openIndiaNiftyHistoryReview($event)"
      />

      <PrivateIndiaNiftyBridgeHistoryReview
        v-else-if="route.kind === 'india-nifty-history-review' && indiaNiftyHistoryReview"
        :review="indiaNiftyHistoryReview"
        @back-to-fund="navigate(`/private/${$event}`)"
        @open-final-review="openIndiaHistoryReview($event)"
      />

      <PrivateSilverCloseHistory
        v-else-if="route.kind === 'silver-close-history' && silverCloseHistory"
        :history="silverCloseHistory"
        @back-to-fund="navigate(`/private/${$event}`)"
      />

      <section v-else-if="route.kind === 'not-found'" class="private-empty-card">
        <p class="private-kicker">PRIVATE ROUTE NOT FOUND</p>
        <h2>此 Private 页面不存在</h2>
        <p>当前仅提供已注册的 Private 独立估值页。</p>
        <a href="/private" @click.prevent="navigate('/private')">返回 Private 列表</a>
      </section>
    </main>
  </div>
</template>
