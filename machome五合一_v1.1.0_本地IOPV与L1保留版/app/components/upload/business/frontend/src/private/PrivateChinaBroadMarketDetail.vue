<script setup lang="ts">
import ShareHistoryPage from '../components/ShareHistoryPage.vue'
import { getPrivateChinaBroadMarketShareHistory } from '../lib/privateApi'
import type { ShareHistoryResponse } from '../lib/types'

defineProps<{
  history: ShareHistoryResponse
}>()

defineEmits<{
  back: []
}>()
</script>

<template>
  <section class="private-page-stack">
    <section class="private-panel">
      <div class="private-section-heading">
        <div>
          <p class="private-kicker">A-SHARE BROAD MARKET RESEARCH</p>
          <h2>仅跟踪历史份额，不纳入实时估值</h2>
          <p class="private-detail-note">该标的不会进入普通基金目录、实时估值模型或昨日赎回榜。</p>
        </div>
        <button type="button" class="private-back-button" @click="$emit('back')">返回 A股宽基</button>
      </div>
      <ShareHistoryPage
        :history="history"
        :load-history="getPrivateChinaBroadMarketShareHistory"
        show-extended-windows
        @back-to-fund="$emit('back')"
      />
    </section>
  </section>
</template>
