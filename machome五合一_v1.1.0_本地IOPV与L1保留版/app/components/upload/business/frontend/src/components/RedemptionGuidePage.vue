<script setup lang="ts">
import { computed, ref } from 'vue'

type RedeemMode = 'buy' | 'subscribe'

defineEmits<{
  'back-home': []
}>()

const selectedMode = ref<RedeemMode>('buy')
const selectedStartIndex = ref(0)

const weekdays = ['本周一', '本周二', '本周三', '本周四', '本周五'] as const
const tradingDays = [
  '本周一',
  '本周二',
  '本周三',
  '本周四',
  '本周五',
  '下周一',
  '下周二',
  '下周三',
  '下周四',
  '下周五',
] as const

const modeText: Record<RedeemMode, { label: string; firstCloseOffset: number; note: string }> = {
  buy: {
    label: '场内买入',
    firstCloseOffset: 0,
    note: '成交日收盘后的账户实际持仓，就是第一个日终观察点。',
  },
  subscribe: {
    label: '场内申购',
    firstCloseOffset: 2,
    note: '国外基金常见 T+2 交收，申购份额通常从 T+2 日终开始进入持仓观察。',
  },
}

const selectedModeText = computed(() => modeText[selectedMode.value])
const selectedStartDay = computed(() => weekdays[selectedStartIndex.value])
const firstCloseIndex = computed(
  () => selectedStartIndex.value + selectedModeText.value.firstCloseOffset,
)
const closeCheckpoints = computed(() =>
  [0, 1, 2].map((offset, index) => ({
    label: `第 ${index + 1} 个日终`,
    day: tradingDays[firstCloseIndex.value + offset],
  })),
)
const earliestRedeemDay = computed(() => tradingDays[firstCloseIndex.value + 3])
const scheduleRows = weekdays.map((day, index) => ({
  day,
  buyRedeemDay: tradingDays[index + 3],
  subscribeRedeemDay: tradingDays[index + 5],
}))
</script>

<template>
  <section class="section redemption-page">
    <section class="detail-panel redemption-hero">
      <div class="detail-header-top">
        <div>
          <p class="eyebrow">赎回规则科普</p>
          <h2>什么时候可以赎回？</h2>
        </div>
        <button type="button" class="table-action-button" @click="$emit('back-home')">
          返回首页
        </button>
      </div>
      <p class="redemption-lead">
        简化判断：赎回日前连续 3 个交易日的收盘后账户实际持仓都存在，才形成对应数量的可赎回份额。
        可赎回数量取这 3 个日终持仓数量的最小值。
      </p>
      <p class="redemption-note">
        遇到节假日、暂停申赎、基金公告特殊安排或券商可用份额显示不一致时，以基金公告和券商最终可赎数量为准。
      </p>
    </section>

    <section class="summary-grid redemption-summary">
      <article class="metric-card">
        <span>核心观察窗</span>
        <strong>T-3 / T-2 / T-1</strong>
        <small>赎回日前 3 个交易日日终</small>
      </article>
      <article class="metric-card">
        <span>可赎数量</span>
        <strong>三天最小值</strong>
        <small>缺任一天持仓表则不能可靠计算</small>
      </article>
      <article class="metric-card">
        <span>申购份额</span>
        <strong>T+2 起算</strong>
        <small>交收后进入日终持仓观察</small>
      </article>
    </section>

    <section class="table-section redemption-calculator">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>点选日期看最早赎回日</h2>
        </div>
      </div>

      <div class="redemption-controls">
        <div class="segmented-control" aria-label="份额来源">
          <button
            type="button"
            :class="{ active: selectedMode === 'buy' }"
            :aria-pressed="selectedMode === 'buy'"
            @click="selectedMode = 'buy'"
          >
            场内买入
          </button>
          <button
            type="button"
            :class="{ active: selectedMode === 'subscribe' }"
            :aria-pressed="selectedMode === 'subscribe'"
            @click="selectedMode = 'subscribe'"
          >
            场内申购
          </button>
        </div>

        <div class="weekday-control" aria-label="发生日期">
          <button
            v-for="(day, index) in weekdays"
            :key="day"
            type="button"
            :class="{ active: selectedStartIndex === index }"
            :aria-pressed="selectedStartIndex === index"
            @click="selectedStartIndex = index"
          >
            {{ day }}
          </button>
        </div>
      </div>

      <p class="redemption-note calculator-note">
        {{ selectedStartDay }}{{ selectedModeText.label }}：{{ selectedModeText.note }}
      </p>

      <div class="redemption-timeline">
        <div
          v-for="item in closeCheckpoints"
          :key="item.label"
          class="timeline-step"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.day }}</strong>
        </div>
        <div class="timeline-step redeem">
          <span>最早赎回</span>
          <strong>{{ earliestRedeemDay }}</strong>
        </div>
      </div>
    </section>

    <section class="table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>常见周内对照</h2>
        </div>
      </div>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr>
              <th>发生日</th>
              <th>买入后最早赎回</th>
              <th>申购后最早赎回</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in scheduleRows" :key="row.day">
              <td>{{ row.day }}</td>
              <td>{{ row.buyRedeemDay }}</td>
              <td>{{ row.subscribeRedeemDay }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="redemption-grid">
      <section class="table-section">
        <h2>深圳基金：看日终净持仓</h2>
        <ul class="redemption-list">
          <li>深市基金持有时长允许买卖轧差，核心判断点是当日收盘后的账户实际持仓。</li>
          <li>当天存在做 T 行为时，只要卖出后当天买回，且收盘持仓数量没有减少，对应数量仍可连续计算持有时间。</li>
          <li>实操可简化为：连续交易日日终持仓数量，决定未来可赎回数量。</li>
        </ul>
      </section>

      <section class="table-section">
        <h2>上海基金：看份额来源和可用方向</h2>
        <ul class="redemption-list">
          <li>沪市 ETF 规则会区分当日申购、当日买入、卖出和赎回方向，不能只看一天的买卖净额。</li>
          <li>常见规则是当日申购份额可卖出但不得赎回，当日买入份额可赎回但不得卖出；跨境产品和具体基金还可能有交收差异。</li>
          <li>做 T 后如果券商显示可赎份额不足，应以券商可赎数量、登记结算结果和基金公告为准。</li>
        </ul>
      </section>
    </section>

    <section class="table-section">
      <h2>做 T 示例</h2>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>动作</th>
              <th>收盘后持仓</th>
              <th>深市连续性判断</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>本周一</td>
              <td>原有 10,000 份</td>
              <td>10,000</td>
              <td>第 1 个日终有效</td>
            </tr>
            <tr>
              <td>本周二</td>
              <td>卖出 10,000 后买回 10,000</td>
              <td>10,000</td>
              <td>日终数量未减少，连续</td>
            </tr>
            <tr>
              <td>本周三</td>
              <td>无变化</td>
              <td>10,000</td>
              <td>第 3 个日终有效</td>
            </tr>
            <tr>
              <td>本周四</td>
              <td>提交赎回</td>
              <td>按赎回后变化</td>
              <td>最多可按 10,000 份计算</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="table-section">
      <h2>计算器口径</h2>
      <p class="redemption-note">
        自动计算程序读取每日持仓文件，按账户、代码汇总收盘数量；对一个赎回日，取前三个交易日的日终持仓。
        如果三天分别是 12,000、10,000、11,000 份，则可赎数量按 10,000 份计算。
        已登记的赎回记录会反向扣减对应三天窗口，避免重复计算同一批可赎份额。
      </p>
      <div class="reference-links">
        <a
          href="https://docs.static.szse.cn/www/disclosure/notice/W020201204583453961510.pdf"
          target="_blank"
          rel="noreferrer"
        >
          深交所基金交易和申购赎回实施细则
        </a>
        <a
          href="https://www.sse.com.cn/assortment/fund/etf/rules/c/c_20150911_3985181.shtml"
          target="_blank"
          rel="noreferrer"
        >
          上交所 ETF 业务实施细则
        </a>
      </div>
    </section>
  </section>
</template>

<style scoped>
.redemption-hero {
  gap: 10px;
}

.redemption-lead {
  max-width: 920px;
  margin: 0;
  color: #263449;
  font-size: 15px;
  line-height: 1.65;
}

.redemption-note {
  margin: 0;
  color: #596579;
  font-size: 13px;
  line-height: 1.65;
}

.redemption-summary .metric-card {
  display: grid;
  gap: 6px;
}

.redemption-summary small {
  color: #596579;
  font-size: 12px;
  line-height: 1.35;
}

.redemption-controls {
  display: grid;
  gap: 12px;
  margin-bottom: 12px;
}

.segmented-control,
.weekday-control {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.segmented-control button,
.weekday-control button {
  min-height: 34px;
  border: 1px solid #cfd7e6;
  border-radius: 6px;
  padding: 7px 12px;
  background: white;
  color: #263449;
  cursor: pointer;
  line-height: 1.2;
}

.segmented-control button.active,
.weekday-control button.active {
  border-color: #2462b8;
  background: #2462b8;
  color: white;
}

.calculator-note {
  margin-bottom: 12px;
}

.redemption-timeline {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 10px;
}

.timeline-step {
  min-height: 72px;
  border: 1px solid #dbe2ee;
  border-radius: 6px;
  padding: 12px;
  background: #fbfcff;
}

.timeline-step span {
  display: block;
  margin-bottom: 8px;
  color: #657084;
  font-size: 12px;
}

.timeline-step strong {
  color: #172033;
  font-size: 18px;
}

.timeline-step.redeem {
  border-color: #a7d8bc;
  background: #f3fbf6;
}

.timeline-step.redeem strong {
  color: #166534;
}

.redemption-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.redemption-list {
  margin: 0 0 0 20px;
  padding: 0;
  color: #445066;
  line-height: 1.6;
}

.redemption-list li {
  margin-bottom: 8px;
}

.reference-links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}

.reference-links a {
  border: 1px solid #cfd7e6;
  border-radius: 6px;
  padding: 7px 10px;
  background: #f8fafc;
  color: #2456b8;
  font-size: 13px;
  text-decoration: none;
}

.reference-links a:hover {
  border-color: #9bb9ea;
  background: #eef5ff;
}

@media (max-width: 760px) {
  .redemption-timeline,
  .redemption-grid {
    grid-template-columns: 1fr;
  }

  .segmented-control button,
  .weekday-control button {
    flex: 1 1 120px;
  }
}
</style>
