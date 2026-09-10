<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import BranchTabs from './components/BranchTabs.vue'
import ContactMessagesAdminPage from './components/ContactMessagesAdminPage.vue'
import ContactPage from './components/ContactPage.vue'
import DebugToolsPage from './components/DebugToolsPage.vue'
import EstimateTable from './components/EstimateTable.vue'
import EffectiveRatioHistoryPage from './components/EffectiveRatioHistoryPage.vue'
import FundDetail from './components/FundDetail.vue'
import HomeDashboard from './components/HomeDashboard.vue'
import UserGuidePage from './components/UserGuidePage.vue'
import RedemptionGuidePage from './components/RedemptionGuidePage.vue'
import ShareHistoryPage from './components/ShareHistoryPage.vue'
import SnapshotGuideLinks from './components/SnapshotGuideLinks.vue'
import VisitDashboard from './components/VisitDashboard.vue'
import YesterdayRedemptionBoardPage from './components/YesterdayRedemptionBoardPage.vue'
import {
  getBranches,
  getBranchSnapshot,
  getFundEffectiveRatioHistory,
  getFundShareHistory,
  getFundSnapshot,
  getHomeSnapshot,
  getVisitCounts,
  getYesterdayRedemptionBoard,
  syncDataViewAccessFromURL,
  syncMessageAdminAccessFromURL,
  trackPageView,
} from './lib/api'
import { isPrimaryAutoRefreshTime } from './lib/tradingHours'
import { isValuationConfirmed } from './lib/valuationConfirmation'
import type { Branch, BranchSnapshot, BranchSummary, DebugStatus, EffectiveRatioFitHistoryResponse, FundSnapshot, HomeSnapshot, ShareHistoryResponse, VisitCountResponse, YesterdayRedemptionBoardResponse } from './lib/types'

type NavigationMode = 'push' | 'replace' | 'none'
type LoadOptions = {
  silent?: boolean
}
type EstimateSortField = 'official_premium' | 'fair_premium' | 'realtime_premium'
type SortDirection = 'asc' | 'desc'

const defaultBranchKey = 'qdiimix'
const yesterdayRedemptionBoardTabKey = 'yesterday-redemption-board'
const yesterdayRedemptionBoardPath = '/funds/yesterday-redemption-board'
const topbarExtraTabs = [
  { key: yesterdayRedemptionBoardTabKey, label: '昨日赎回榜', after_key: 'qdiieu' },
]
const branches = ref<Branch[]>([])
const activeKey = ref('home')
const routePath = ref(location.pathname)
const home = ref<HomeSnapshot | null>(null)
const snapshot = ref<BranchSnapshot | null>(null)
const fund = ref<FundSnapshot | null>(null)
const ratioHistory = ref<EffectiveRatioFitHistoryResponse | null>(null)
const shareHistory = ref<ShareHistoryResponse | null>(null)
const yesterdayRedemptionBoard = ref<YesterdayRedemptionBoardResponse | null>(null)
const debugStatus = ref<DebugStatus | null>(null)
const visits = ref<VisitCountResponse | null>(null)
const visitDate = ref('')
const navSettings = ref(false)
const guide = ref(false)
const contactPage = ref(false)
const contactMessagesAdmin = ref(false)
const loading = ref(false)
const error = ref('')
const query = ref('')
const hideUnconfirmed = ref(false)
const estimateSortField = ref<EstimateSortField | null>(null)
const estimateSortDirection = ref<SortDirection>('desc')
const uaClass = detectMobileUserAgent() ? 'ua-mobile' : 'ua-desktop'
const searchPlaceholder = computed(() =>
  uaClass === 'ua-mobile'
    ? '输入代码，如 226 / SZ161226'
    : '输入代码，可用 226 / 161226 / SZ161226',
)
const debugSubpage = computed(() => {
  if (routePath.value === '/debug') return 'status'
  if (routePath.value === '/debug/valuation-controls') return 'valuation-controls'
  if (routePath.value === '/debug/intraday-rebuild') return 'intraday-rebuild'
  if (routePath.value === '/debug/contact-inbox') return 'contact-inbox'
  return ''
})
const activeDebugPage = computed<'status' | 'valuation-controls' | 'intraday-rebuild' | 'contact-inbox'>(() => {
  if (debugSubpage.value === 'valuation-controls') return 'valuation-controls'
  if (debugSubpage.value === 'intraday-rebuild') return 'intraday-rebuild'
  if (debugSubpage.value === 'contact-inbox') return 'contact-inbox'
  return 'status'
})
const currentViewSymbols = computed(() => {
  const symbols: string[] = []
  if (fund.value?.symbol) {
    symbols.push(fund.value.symbol)
  } else if (ratioHistory.value?.symbol) {
    symbols.push(ratioHistory.value.symbol)
  } else if (shareHistory.value?.symbol) {
    symbols.push(shareHistory.value.symbol)
  } else if (snapshot.value) {
    symbols.push(...snapshot.value.estimate_rows.map((row) => row.symbol))
  } else if (home.value) {
    symbols.push(...home.value.branches.map((summary) => summary.max_fair_premium_symbol))
  }
  return Array.from(
    new Set(
      symbols
        .map((symbol) => symbol?.trim().toUpperCase())
        .filter((symbol): symbol is string => Boolean(symbol)),
    ),
  )
})
const showValuationDisclaimer = computed(() =>
  currentViewSymbols.value.some((symbol) => !isValuationConfirmed(symbol)),
)
const redemptionGuide = computed(() => routePath.value === '/redemption-guide')
const snapshotHeaderNote = computed(() => {
  if (!snapshot.value || !snapshotHasUnconfirmed.value) return ''
  if (hideUnconfirmed.value) {
    return '未确认标的已隐藏，点击“显示未确认标的”可恢复查看。'
  }
  return '表示该标的估值方案尚未确认，代码、名称、T-1收盘时点/实时估值及对应溢价率仅供参考，数据准确性不明。'
})
const snapshotHeaderNoteShowLegend = computed(() =>
  Boolean(snapshot.value && snapshotHasUnconfirmed.value && !hideUnconfirmed.value),
)
const snapshotHasUnconfirmed = computed(() =>
  Boolean(
    snapshot.value &&
      snapshot.value.estimate_rows.some((row) => !isValuationConfirmed(row.symbol)),
  ),
)
const filteredEstimateRows = computed(() => {
  if (!snapshot.value) return []
  if (!hideUnconfirmed.value) return snapshot.value.estimate_rows
  return snapshot.value.estimate_rows.filter((row) => isValuationConfirmed(row.symbol))
})
const hasRealtimePremium = computed(() =>
  Boolean(
    snapshot.value?.estimate_rows.some(
      (row) => row.realtime_est !== null && row.realtime_premium !== null,
    ),
  ),
)
const sortedEstimateRows = computed(() => {
  const rows = filteredEstimateRows.value
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const sortField = estimateSortField.value
      if (!sortField) {
        const leftUnconfirmed = !isValuationConfirmed(left.row.symbol)
        const rightUnconfirmed = !isValuationConfirmed(right.row.symbol)
        if (leftUnconfirmed !== rightUnconfirmed) return leftUnconfirmed ? 1 : -1
        return left.index - right.index
      }
      const valueDiff = compareNullableNumbers(
        left.row[sortField],
        right.row[sortField],
        estimateSortDirection.value,
      )
      if (valueDiff !== 0) return valueDiff
      return left.index - right.index
    })
    .map(({ row }) => row)
})
let timer: number | undefined
let refreshInFlight = false

syncDataViewAccessFromURL()
syncMessageAdminAccessFromURL()

function detectMobileUserAgent() {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false
  const override = new URLSearchParams(window.location.search).get('ua')
  if (override === 'mobile') return true
  if (override === 'desktop') return false
  const ua = navigator.userAgent || ''
  if (/Android.*Mobile|iPhone|iPod|Windows Phone|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(ua)) {
    return true
  }
  const coarsePointer = window.matchMedia?.('(pointer: coarse)').matches === true
  const hasTouch = navigator.maxTouchPoints > 0
  const shortEdge = Math.min(window.screen.width || window.innerWidth, window.screen.height || window.innerHeight)
  return coarsePointer && hasTouch && shortEdge <= 820
}

function asArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : []
}

function replaceArray<T>(target: T[], source: T[] | null | undefined) {
  target.splice(0, target.length, ...asArray(source))
}

function compareNullableNumbers(
  left: number | null | undefined,
  right: number | null | undefined,
  direction: SortDirection,
) {
  const leftMissing = left === null || left === undefined || !Number.isFinite(left)
  const rightMissing = right === null || right === undefined || !Number.isFinite(right)
  if (leftMissing && rightMissing) return 0
  if (leftMissing) return 1
  if (rightMissing) return -1
  return direction === 'desc' ? right - left : left - right
}

function patchArrayByKey<T extends object>(
  target: T[] | null | undefined,
  source: T[] | null | undefined,
  getKey: (item: T) => string,
  patchItem: (targetItem: T, sourceItem: T) => void = Object.assign,
) {
  const targetArray = asArray(target)
  const sourceArray = asArray(source)
  const currentByKey = new Map(targetArray.map((item) => [getKey(item), item]))
  const nextItems = sourceArray.map((sourceItem) => {
    const currentItem = currentByKey.get(getKey(sourceItem))
    if (!currentItem) return sourceItem
    patchItem(currentItem, sourceItem)
    return currentItem
  })
  replaceArray(targetArray, nextItems)
  return targetArray
}

function normalizeBranchSnapshot(snapshot: BranchSnapshot) {
  snapshot.branch.symbols = asArray(snapshot.branch.symbols)
  snapshot.estimate_rows = asArray(snapshot.estimate_rows)
  snapshot.warnings = asArray(snapshot.warnings)
  return snapshot
}

function normalizeBranchSummary(summary: BranchSummary) {
  summary.branch.symbols = asArray(summary.branch.symbols)
  summary.model_counts = summary.model_counts || {}
  summary.warnings = asArray(summary.warnings)
  return summary
}

function normalizeHomeSnapshot(snapshot: HomeSnapshot) {
  snapshot.branches = asArray(snapshot.branches).map(normalizeBranchSummary)
  snapshot.warnings = asArray(snapshot.warnings)
  return snapshot
}

function normalizeFundSnapshot(snapshot: FundSnapshot) {
  snapshot.branches = asArray(snapshot.branches).map((branch) => {
    branch.symbols = asArray(branch.symbols)
    return branch
  })
  snapshot.valuation_inputs = asArray(snapshot.valuation_inputs)
  return snapshot
}

function patchBranchSnapshot(current: BranchSnapshot, next: BranchSnapshot) {
  normalizeBranchSnapshot(current)
  normalizeBranchSnapshot(next)
  current.snapshot_key = next.snapshot_key
  current.schema_version = next.schema_version
  current.as_of = next.as_of
  current.ttl_seconds = next.ttl_seconds
  current.stale_after_seconds = next.stale_after_seconds
  Object.assign(current.branch, next.branch)
  current.estimate_rows = patchArrayByKey(current.estimate_rows, next.estimate_rows, (row) => row.symbol)
  replaceArray(current.warnings, next.warnings)
}

function patchBranchSummary(current: BranchSummary, next: BranchSummary) {
  normalizeBranchSummary(current)
  normalizeBranchSummary(next)
  const branch = current.branch
  const warnings = current.warnings
  Object.assign(current, next)
  current.branch = branch
  current.warnings = warnings
  Object.assign(current.branch, next.branch)
  current.model_counts = { ...next.model_counts }
  replaceArray(current.warnings, next.warnings)
}

function patchHomeSnapshot(current: HomeSnapshot, next: HomeSnapshot) {
  normalizeHomeSnapshot(current)
  normalizeHomeSnapshot(next)
  current.snapshot_key = next.snapshot_key
  current.schema_version = next.schema_version
  current.as_of = next.as_of
  current.ttl_seconds = next.ttl_seconds
  current.branches = patchArrayByKey(current.branches, next.branches, (summary) => summary.branch.key, patchBranchSummary)
  replaceArray(current.warnings, next.warnings)
}

function patchFundSnapshot(current: FundSnapshot, next: FundSnapshot) {
  normalizeFundSnapshot(current)
  normalizeFundSnapshot(next)
  current.symbol = next.symbol
  current.as_of = next.as_of
  current.branches = patchArrayByKey(current.branches, next.branches, (branch) => branch.key)
  Object.assign(current.quote, next.quote)
  if (current.estimate && next.estimate) {
    Object.assign(current.estimate, next.estimate)
  } else {
    current.estimate = next.estimate
  }
  if (current.valuation_reference && next.valuation_reference) {
    Object.assign(current.valuation_reference, next.valuation_reference)
  } else {
    current.valuation_reference = next.valuation_reference
  }
  if (current.valuation_inputs && next.valuation_inputs) {
    current.valuation_inputs = patchArrayByKey(
      current.valuation_inputs,
      next.valuation_inputs,
      (row) => `${row.role}:${row.symbol}:${row.reference_symbol || ''}:${row.base_date || ''}:${row.holding_date || ''}`,
    )
  } else {
    current.valuation_inputs = next.valuation_inputs
  }
}

async function loadHome(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    activeKey.value = 'home'
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    const next = normalizeHomeSnapshot(await getHomeSnapshot())
    if (silent && !home.value) return
    if (home.value) {
      patchHomeSnapshot(home.value, next)
    } else {
      home.value = next
    }
    updatePath('/', mode)
    if (!silent) trackPageView('home')
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadBranch(key: string, mode: NavigationMode = 'push', options: LoadOptions = {}) {
  if (key === 'home') {
    await loadHome(mode, options)
    return
  }
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    if (!silent) activeKey.value = key
    const next = normalizeBranchSnapshot(await getBranchSnapshot(key))
    if (silent && (activeKey.value !== key || !snapshot.value || home.value || fund.value)) return
    if (snapshot.value) {
      patchBranchSnapshot(snapshot.value, next)
    } else {
      snapshot.value = next
    }
    updatePath(branchPath(key), mode)
    if (!silent) trackPageView(`branch:${key}`)
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadFund(symbol: string, mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const clean = symbol.trim().toUpperCase()
  if (!clean) return
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    snapshot.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    const next = normalizeFundSnapshot(await getFundSnapshot(clean))
    if (silent && (!fund.value || fund.value.symbol !== clean || home.value || snapshot.value)) return
    if (fund.value) {
      patchFundSnapshot(fund.value, next)
    } else {
      fund.value = next
    }
    activeKey.value = fund.value.branches[0]?.key || ''
    query.value = clean
    updatePath(`/funds/${clean}`, mode)
    if (!silent) trackPageView(`fund:${clean}`)
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadFundEffectiveRatioHistory(symbol: string, mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const clean = symbol.trim().toUpperCase()
  if (!clean) return
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    ratioHistory.value = await getFundEffectiveRatioHistory(clean, 180)
    activeKey.value = ''
    query.value = clean
    updatePath(`/funds/${clean}/effective-ratio-history`, mode)
    if (!silent) trackPageView(`fund-ratio-history:${clean}`)
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadFundShareHistory(symbol: string, mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const clean = symbol.trim().toUpperCase()
  if (!clean) return
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    shareHistory.value = await getFundShareHistory(clean, 60)
    activeKey.value = ''
    query.value = clean
    updatePath(`/funds/${clean}/share-history`, mode)
    if (!silent) trackPageView(`fund-share-history:${clean}`)
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadYesterdayRedemptionBoard(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
    activeKey.value = yesterdayRedemptionBoardTabKey
  }
  try {
    yesterdayRedemptionBoard.value = await getYesterdayRedemptionBoard()
    activeKey.value = yesterdayRedemptionBoardTabKey
    updatePath(yesterdayRedemptionBoardPath, mode)
    if (!silent) trackPageView('yesterday-redemption-board')
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadDebug(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    visits.value = null
    activeKey.value = ''
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  debugStatus.value = null
  updatePath('/debug', mode)
  if (!silent) trackPageView('debug')
}

async function loadDebugValuationControls(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    activeKey.value = ''
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  updatePath('/debug/valuation-controls', mode)
  if (!silent) trackPageView('debug-valuation-controls')
}

async function loadDebugContactInbox(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    activeKey.value = ''
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  updatePath('/debug/contact-inbox', mode)
  if (!silent) trackPageView('debug-contact-inbox')
}

async function loadDebugIntradayRebuild(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    activeKey.value = ''
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  updatePath('/debug/intraday-rebuild', mode)
  if (!silent) trackPageView('debug-intraday-rebuild')
}

async function loadVisits(mode: NavigationMode = 'push', options: LoadOptions = {}, date = '') {
  const silent = options.silent === true
  const cleanDate = normalizeVisitDate(date)
  if (!silent) {
    loading.value = true
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    activeKey.value = ''
    navSettings.value = false
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
  }
  try {
    visits.value = await getVisitCounts(30)
    visitDate.value = cleanDate
    updatePath(cleanDate ? `/visits/${cleanDate}` : '/visits', mode)
    if (!silent) trackPageView(cleanDate ? `visits:${cleanDate}` : 'visits')
  } catch (err) {
    if (!silent) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

function loadVisitDate(date: string) {
  const cleanDate = normalizeVisitDate(date)
  visitDate.value = cleanDate
  updatePath(cleanDate ? `/visits/${cleanDate}` : '/visits', 'push')
  trackPageView(cleanDate ? `visits:${cleanDate}` : 'visits')
}

function clearVisitDate() {
  visitDate.value = ''
  updatePath('/visits', 'push')
  trackPageView('visits')
}

async function loadNavSettings(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  await loadDebugValuationControls(mode, options)
}

async function loadGuide(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    activeKey.value = ''
    guide.value = true
    contactPage.value = false
    contactMessagesAdmin.value = false
    updatePath('/guide', mode)
    trackPageView('guide')
    return
  }
  guide.value = true
  contactPage.value = false
  contactMessagesAdmin.value = false
}

async function loadRedemptionGuide(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    activeKey.value = ''
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = false
    updatePath('/redemption-guide', mode)
    trackPageView('redemption-guide')
    return
  }
  guide.value = false
  contactPage.value = false
  contactMessagesAdmin.value = false
}

async function loadContact(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    activeKey.value = ''
    guide.value = false
    contactPage.value = true
    contactMessagesAdmin.value = false
    updatePath('/contact-admin', mode)
    trackPageView('contact-admin')
    return
  }
  contactPage.value = true
  contactMessagesAdmin.value = false
}

async function loadContactInbox(mode: NavigationMode = 'push', options: LoadOptions = {}) {
  const silent = options.silent === true
  if (!silent) {
    loading.value = false
    error.value = ''
    home.value = null
    snapshot.value = null
    fund.value = null
    ratioHistory.value = null
    shareHistory.value = null
    debugStatus.value = null
    visits.value = null
    navSettings.value = false
    activeKey.value = ''
    guide.value = false
    contactPage.value = false
    contactMessagesAdmin.value = true
    updatePath('/contact-admin/inbox', mode)
    trackPageView('contact-admin-inbox')
    return
  }
  contactPage.value = false
  contactMessagesAdmin.value = true
}

function handleTopTabSelect(key: string) {
  if (key === yesterdayRedemptionBoardTabKey) {
    void loadYesterdayRedemptionBoard()
    return
  }
  void loadBranch(key)
}

function handleDebugPageNavigate(page: 'status' | 'valuation-controls' | 'intraday-rebuild' | 'contact-inbox') {
  if (page === 'valuation-controls') {
    void loadDebugValuationControls()
    return
  }
  if (page === 'contact-inbox') {
    void loadDebugContactInbox()
    return
  }
  if (page === 'intraday-rebuild') {
    void loadDebugIntradayRebuild()
    return
  }
  void loadDebug()
}

async function submitSearch() {
  const resolved = resolveSearchSymbol(query.value)
  if (!resolved) {
    error.value = `没有找到匹配标的：${query.value.trim()}`
    return
  }
  await loadFund(resolved)
}

function resolveSearchSymbol(input: string) {
  const raw = input.trim().toUpperCase()
  if (!raw) return ''
  if (/^[A-Z]{2}\d{5,6}$/.test(raw)) return raw

  const symbols = allBranchSymbols()
  const activeSymbols = activeKey.value
    ? branches.value.find((branch) => branch.key === activeKey.value)?.symbols || []
    : []
  const needle = raw.replace(/[^A-Z0-9]/g, '')
  const digits = raw.replace(/\D/g, '')
  const candidates = uniqueSymbols([
    ...activeSymbols,
    ...symbols,
  ])

  const exactBody = candidates.filter((symbol) => symbol.slice(2) === needle)
  if (exactBody.length) return exactBody[0]

  if (digits) {
    const suffix = candidates.filter((symbol) => symbol.endsWith(digits))
    if (suffix.length) return suffix[0]
    const bodyIncludes = candidates.filter((symbol) => symbol.slice(2).includes(digits))
    if (bodyIncludes.length) return bodyIncludes[0]
  }

  const textIncludes = candidates.filter((symbol) => symbol.includes(needle))
  return textIncludes[0] || ''
}

function allBranchSymbols() {
  return branches.value.flatMap((branch) => branch.symbols)
}

function uniqueSymbols(symbols: string[]) {
  return Array.from(new Set(symbols))
}

function updatePath(path: string, mode: NavigationMode) {
  routePath.value = path
  if (mode === 'none' || location.pathname === path) return
  if (mode === 'replace') {
    history.replaceState(null, '', path)
  } else {
    history.pushState(null, '', path)
  }
}

function branchPath(key: string) {
  const branch = branches.value.find((item) => item.key === key)
  return branch?.new_path || `/funds/${key}`
}

function inferInitialBranch() {
  const path = location.pathname
  const byNewPath = branches.value.find((item) => item.new_path === path)
  if (byNewPath) return byNewPath.key
  const byOldPath = branches.value.find((item) => item.old_path === path)
  if (byOldPath) return byOldPath.key
  const keyPath = path.match(/^\/funds\/([a-z0-9-]+)$/i)?.[1]
  if (keyPath) {
    const normalizedKey = keyPath.replace(/-/g, '').toLowerCase()
    const byKey = branches.value.find((item) => item.key.toLowerCase() === normalizedKey)
    if (byKey) return byKey.key
  }
  return ''
}

async function loadCurrentRoute(mode: NavigationMode = 'replace') {
  if (location.pathname === '/contact-admin/inbox') {
    await loadContactInbox(mode)
    return
  }
  if (location.pathname === '/contact-admin') {
    await loadContact(mode)
    return
  }
  if (location.pathname === '/guide') {
    await loadGuide(mode)
    return
  }
  if (location.pathname === '/redemption-guide') {
    await loadRedemptionGuide(mode)
    return
  }
  if (location.pathname === '/debug/valuation-controls') {
    await loadDebugValuationControls(mode)
    return
  }
  if (location.pathname === '/debug/contact-inbox') {
    await loadDebugContactInbox(mode)
    return
  }
  if (location.pathname === '/debug/intraday-rebuild') {
    await loadDebugIntradayRebuild(mode)
    return
  }
  if (location.pathname === '/debug') {
    await loadDebug(mode)
    return
  }
  const visitsMatch = location.pathname.match(/^\/visits(?:\/(\d{4}-\d{2}-\d{2}))?$/)
  if (visitsMatch) {
    await loadVisits(mode, {}, visitsMatch[1] || '')
    return
  }
  if (location.pathname === '/navsettings') {
    await loadNavSettings(mode)
    return
  }
  if (location.pathname === yesterdayRedemptionBoardPath) {
    await loadYesterdayRedemptionBoard(mode)
    return
  }
  const shareHistoryMatch = location.pathname.match(/^\/funds\/([a-z]{2}\d{5,6})\/share-history$/i)
  if (shareHistoryMatch) {
    await loadFundShareHistory(shareHistoryMatch[1], mode)
    return
  }
  const ratioHistoryMatch = location.pathname.match(/^\/funds\/([a-z]{2}\d{5,6})\/effective-ratio-history$/i)
  if (ratioHistoryMatch) {
    await loadFundEffectiveRatioHistory(ratioHistoryMatch[1], mode)
    return
  }
  const fundMatch = location.pathname.match(/^\/funds\/([a-z]{2}\d{5,6})$/i)
  if (fundMatch) {
    await loadFund(fundMatch[1], mode)
    return
  }
  const branchKey = inferInitialBranch()
  if (branchKey) {
    await loadBranch(branchKey, mode)
    return
  }
  await loadBranch(defaultBranchKey, mode)
}

function routeNeedsBranches(path: string) {
  if (/^\/debug(?:\/.*)?$/.test(path)) {
    return false
  }
  if (/^\/visits(?:\/\d{4}-\d{2}-\d{2})?$/.test(path)) {
    return false
  }
  switch (path) {
    case '/guide':
    case '/redemption-guide':
    case '/debug':
    case '/visits':
    case '/navsettings':
    case '/contact-admin':
    case '/contact-admin/inbox':
      return false
    default:
      return true
  }
}

function normalizeVisitDate(value: string) {
  const clean = value.trim()
  return /^\d{4}-\d{2}-\d{2}$/.test(clean) ? clean : ''
}

function refreshCurrentView() {
  if (refreshInFlight || loading.value) return
  if (!isPrimaryAutoRefreshTime()) return
  if (
    debugSubpage.value ||
    visits.value ||
    navSettings.value ||
    ratioHistory.value ||
    shareHistory.value ||
    routePath.value === yesterdayRedemptionBoardPath ||
    guide.value ||
    redemptionGuide.value ||
    contactPage.value ||
    contactMessagesAdmin.value
  ) return
  refreshInFlight = true
  let request: Promise<void>
  if (fund.value) {
    request = loadFund(fund.value.symbol, 'none', { silent: true })
  } else if (snapshot.value) {
    request = loadBranch(activeKey.value, 'none', { silent: true })
  } else {
    request = loadHome('none', { silent: true })
  }
  void request.finally(() => {
    refreshInFlight = false
  })
}

onMounted(async () => {
  try {
    if (routeNeedsBranches(location.pathname)) {
      branches.value = await getBranches()
    } else {
      void getBranches()
        .then((next) => {
          branches.value = next
        })
        .catch(() => {})
    }
    await loadCurrentRoute('replace')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
  window.addEventListener('popstate', handlePopState)
  timer = window.setInterval(refreshCurrentView, 5000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  window.removeEventListener('popstate', handlePopState)
})

function handlePopState() {
  void loadCurrentRoute('none')
}

function toggleHideUnconfirmed() {
  hideUnconfirmed.value = !hideUnconfirmed.value
}

function toggleEstimateSort(field: EstimateSortField) {
  if (estimateSortField.value !== field) {
    estimateSortField.value = field
    estimateSortDirection.value = 'desc'
    return
  }
  if (estimateSortDirection.value === 'desc') {
    estimateSortDirection.value = 'asc'
    return
  }
  estimateSortField.value = null
  estimateSortDirection.value = 'desc'
}

watch(hasRealtimePremium, (available) => {
  if (!available && estimateSortField.value === 'realtime_premium') {
    estimateSortField.value = null
    estimateSortDirection.value = 'desc'
  }
})
</script>

<template>
  <main class="app-shell" :class="uaClass">
    <header class="topbar">
      <BranchTabs
        :branches="branches"
        :active-key="activeKey"
        :extra-tabs="topbarExtraTabs"
        @select="handleTopTabSelect"
      />
      <form class="symbol-search" @submit.prevent="submitSearch">
        <input
          v-model="query"
          :placeholder="searchPlaceholder"
          @keydown.enter.prevent="submitSearch"
        />
        <button type="submit" @click.prevent="submitSearch">查询</button>
      </form>
    </header>

    <p v-if="showValuationDisclaimer && !snapshot" class="valuation-disclaimer">
      <span class="valuation-unconfirmed">删除线</span>
      <span>表示该标的估值方案尚未确认，代码、名称、T-1收盘时点/实时估值及对应溢价率仅供参考，数据准确性不明。</span>
    </p>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="loading" class="notice">正在刷新数据...</p>

    <HomeDashboard
      v-if="home"
      :home="home"
      @select-branch="loadBranch"
      @select-fund="loadFund"
      @open-guide="loadGuide"
    />

    <FundDetail
      v-else-if="fund"
      :fund="fund"
      @select-branch="loadBranch"
      @select-fund="loadFund"
      @show-ratio-history="loadFundEffectiveRatioHistory"
      @show-share-history="loadFundShareHistory"
    />

    <EffectiveRatioHistoryPage
      v-else-if="ratioHistory"
      :history="ratioHistory"
      @back-to-fund="loadFund"
    />

    <ShareHistoryPage
      v-else-if="shareHistory"
      :history="shareHistory"
      show-extended-windows
      @back-to-fund="loadFund"
    />

    <DebugToolsPage
      v-else-if="debugSubpage"
      :page="activeDebugPage"
      @navigate="handleDebugPageNavigate"
    />

    <VisitDashboard
      v-else-if="visits"
      :visits="visits"
      :selected-date="visitDate"
      @select-date="loadVisitDate"
      @clear-date="clearVisitDate"
    />

    <UserGuidePage
      v-else-if="guide"
      @back-home="loadHome"
    />

    <RedemptionGuidePage
      v-else-if="redemptionGuide"
      @back-home="loadHome"
    />

    <ContactPage
      v-else-if="contactPage"
      @back-home="loadHome"
    />

    <ContactMessagesAdminPage v-else-if="contactMessagesAdmin" />

    <section v-else-if="snapshot" class="section">
      <EstimateTable
        :rows="sortedEstimateRows"
        :header-note="snapshotHeaderNote"
        :header-note-show-legend="snapshotHeaderNoteShowLegend"
        :toggle-label="snapshotHasUnconfirmed ? (hideUnconfirmed ? '显示未确认标的' : '隐藏未确认标的') : ''"
        :sort-field="estimateSortField"
        :sort-direction="estimateSortDirection"
        :allow-realtime-sort="hasRealtimePremium"
        :mobile-mode="uaClass === 'ua-mobile'"
        :show-detail-hint="true"
        @select="loadFund"
        @toggle-visibility="toggleHideUnconfirmed"
        @sort="toggleEstimateSort"
      />
      <SnapshotGuideLinks
        @open-redemption-guide="loadRedemptionGuide"
        @open-guide="loadGuide"
        @open-contact="loadContact"
      />
    </section>

    <YesterdayRedemptionBoardPage
      v-else-if="yesterdayRedemptionBoard"
      :board="yesterdayRedemptionBoard"
      @select-fund="loadFund"
    />
  </main>
</template>
