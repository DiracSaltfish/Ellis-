<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { DebugAuthStatus, DebugStatus } from '../lib/types'
import ContactMessagesAdminPage from './ContactMessagesAdminPage.vue'
import DebugDashboard from './DebugDashboard.vue'
import IntradayRebuildPage from './IntradayRebuildPage.vue'
import NavSettingsPage from './NavSettingsPage.vue'
import { clearStoredDebugAccess, getDebugAuthStatus, getDebugStatus, postDebugLogin, postDebugLogout } from '../lib/api'

const props = defineProps<{
  page: 'status' | 'valuation-controls' | 'intraday-rebuild' | 'contact-inbox'
}>()

const emit = defineEmits<{
  (event: 'navigate', page: 'status' | 'valuation-controls' | 'intraday-rebuild' | 'contact-inbox'): void
}>()

const auth = ref<DebugAuthStatus | null>(null)
const authLoading = ref(false)
const loginLoading = ref(false)
const pageLoading = ref(false)
const status = ref<DebugStatus | null>(null)
const pageError = ref('')
const loginError = ref('')
const username = ref('')
const password = ref('')
let refreshTimer: number | undefined

const pageTitle = computed(() => {
  if (props.page === 'valuation-controls') return '估值控制'
  if (props.page === 'intraday-rebuild') return '盘中回补'
  if (props.page === 'contact-inbox') return '留言收件箱'
  return '运行状态'
})
const pageNote = computed(() => {
  if (props.page === 'valuation-controls') {
    return '使用浏览器登录后即可直接访问调试页；估值控制页会通过已鉴权接口向网站后端和 uploader 双向同步手工仓位。'
  }
  if (props.page === 'contact-inbox') {
    return '登录 /debug 后即可查看网站留言收件箱，不需要再单独依赖隐藏入口的页面鉴权。'
  }
  if (props.page === 'intraday-rebuild') {
    return '由 Mac-home 专用 Agent 通过 TWS 回拉数据，并使用既有审计导入链重建当日分时估值。'
  }
  return '使用浏览器登录后即可直接访问调试页，并查看当前服务运行状态。'
})
const isAuthenticated = computed(() => auth.value?.authenticated === true)
const authModeLabel = computed(() => {
  if (!auth.value?.authenticated) return ''
  return auth.value.auth_mode === 'legacy_token' ? 'legacy token' : 'debug session'
})

function fmtDate(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function clearRefreshTimer() {
  if (refreshTimer !== undefined) {
    window.clearInterval(refreshTimer)
    refreshTimer = undefined
  }
}

function ensureRefreshTimer() {
  clearRefreshTimer()
  if (!isAuthenticated.value || props.page !== 'status') return
  refreshTimer = window.setInterval(() => {
    void loadStatus(true)
  }, 5000)
}

async function loadAuthStatus() {
  authLoading.value = true
  try {
    auth.value = await getDebugAuthStatus()
  } catch (err) {
    pageError.value = err instanceof Error ? err.message : String(err)
  } finally {
    authLoading.value = false
  }
}

async function loadStatus(silent = false) {
  if (!isAuthenticated.value || props.page !== 'status') {
    status.value = null
    return
  }
  if (!silent) {
    pageLoading.value = true
    pageError.value = ''
  }
  try {
    status.value = await getDebugStatus()
  } catch (err) {
    status.value = null
    pageError.value = err instanceof Error ? err.message : String(err)
    await loadAuthStatus()
  } finally {
    if (!silent) {
      pageLoading.value = false
    }
  }
}

async function submitLogin() {
  loginLoading.value = true
  loginError.value = ''
  pageError.value = ''
  try {
    auth.value = await postDebugLogin({
      username: username.value,
      password: password.value,
    })
    password.value = ''
    if (props.page === 'status') {
      await loadStatus()
    }
    ensureRefreshTimer()
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loginLoading.value = false
  }
}

async function logout() {
  pageError.value = ''
  loginError.value = ''
  try {
    clearStoredDebugAccess()
    await postDebugLogout()
    auth.value = await getDebugAuthStatus()
    status.value = null
    clearRefreshTimer()
  } catch (err) {
    pageError.value = err instanceof Error ? err.message : String(err)
  }
}

watch(
  () => props.page,
  async () => {
    pageError.value = ''
    if (props.page === 'status') {
      await loadStatus()
    } else {
      status.value = null
    }
    ensureRefreshTimer()
  },
)

watch(isAuthenticated, async (authenticated) => {
  if (!authenticated) {
    status.value = null
    clearRefreshTimer()
    return
  }
  if (props.page === 'status' && !status.value) {
    await loadStatus()
  }
  ensureRefreshTimer()
})

onMounted(async () => {
  await loadAuthStatus()
  if (isAuthenticated.value && props.page === 'status') {
    await loadStatus()
  }
  ensureRefreshTimer()
})

onBeforeUnmount(() => {
  clearRefreshTimer()
})
</script>

<template>
  <section class="section debug-tools-page">
    <section class="detail-panel debug-tools-panel">
      <div class="debug-tools-header">
        <div>
          <p class="eyebrow">Debug</p>
          <h2>{{ pageTitle }}</h2>
          <p class="model-note">{{ pageNote }}</p>
        </div>
        <div class="debug-tools-nav">
          <button
            type="button"
            class="secondary-action"
            :class="{ active: page === 'status' }"
            @click="emit('navigate', 'status')"
          >
            运行状态
          </button>
          <button
            type="button"
            class="secondary-action"
            :class="{ active: page === 'valuation-controls' }"
            @click="emit('navigate', 'valuation-controls')"
          >
            估值控制
          </button>
          <button
            type="button"
            class="secondary-action"
            :class="{ active: page === 'intraday-rebuild' }"
            @click="emit('navigate', 'intraday-rebuild')"
          >
            盘中回补
          </button>
          <button
            type="button"
            class="secondary-action"
            :class="{ active: page === 'contact-inbox' }"
            @click="emit('navigate', 'contact-inbox')"
          >
            留言收件箱
          </button>
        </div>
      </div>

      <div class="detail-metrics debug-tools-metrics">
        <div>
          <span>登录状态</span>
          <strong>{{ isAuthenticated ? '已登录' : '未登录' }}</strong>
        </div>
        <div>
          <span>认证方式</span>
          <strong>{{ authModeLabel || '-' }}</strong>
        </div>
        <div>
          <span>当前身份</span>
          <strong>{{ auth?.username || '-' }}</strong>
        </div>
        <div>
          <span>到期时间</span>
          <strong>{{ fmtDate(auth?.expires_at) || '-' }}</strong>
        </div>
      </div>

      <p v-if="pageError" class="notice error">{{ pageError }}</p>
      <p v-else-if="authLoading" class="notice">正在校验调试登录状态...</p>

      <form v-if="!isAuthenticated && !authLoading" class="debug-login-form" @submit.prevent="submitLogin">
        <label>
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="输入调试用户名" />
        </label>
        <label>
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="输入调试密码"
          />
        </label>
        <button type="submit" :disabled="loginLoading">
          {{ loginLoading ? '登录中...' : '登录 /debug' }}
        </button>
      </form>

      <p v-if="loginError" class="notice error">{{ loginError }}</p>

      <div v-if="isAuthenticated" class="debug-session-actions">
        <button type="button" class="secondary-action" @click="logout">
          退出调试登录
        </button>
      </div>
    </section>

    <p v-if="isAuthenticated && page === 'status' && pageLoading" class="notice">正在加载调试状态...</p>

    <DebugDashboard
      v-if="isAuthenticated && page === 'status' && status"
      :status="status"
    />

    <ContactMessagesAdminPage v-else-if="isAuthenticated && page === 'contact-inbox'" />

    <NavSettingsPage v-else-if="isAuthenticated && page === 'valuation-controls'" />

    <IntradayRebuildPage v-else-if="isAuthenticated && page === 'intraday-rebuild'" />
  </section>
</template>
