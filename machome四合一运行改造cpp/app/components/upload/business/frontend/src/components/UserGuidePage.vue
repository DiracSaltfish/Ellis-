<template>
  <section class="section guide-page">
    <section class="table-section">
      <div class="table-section-header">
        <div class="table-section-heading">
          <h2>使用指南</h2>
        </div>
        <button type="button" class="table-action-button" @click="$emit('back-home')">
          返回首页
        </button>
      </div>

      <p class="guide-summary">
        本站提供基金实时估值、参考溢价率与分支快照的浏览入口。你可以从首页进入各市场总览，也可以直接搜索代码进入具体基金页。
      </p>

      <h3>1. 首页快速上手</h3>
      <ol class="guide-list">
        <li>在顶部输入框输入基金代码（如 <span>226</span>、<span>161226</span> 或 <span>SZ161226</span>）后回车查询。</li>
        <li>在分支标签中快速切换不同市场分组（如中证100、QDII 等）。</li>
        <li>首页表格中点击分支名称可进入该分支快照，点击代码可进入具体基金详情。</li>
      </ol>

      <h3>2. 估值与5档交易</h3>
      <ul class="guide-list">
        <li>基金详情页展示实时估值、官方/参考信息、估值时间与分时估值图。</li>
        <li>“当前5档交易”显示前五档买卖盘数量、价格与溢价率，便于观察盘面流动性与溢价偏离。</li>
        <li>5档交易中的数量以“手”为单位展示，便于快速和实际交易口径核对。</li>
      </ul>

      <h3>3. 溢价率计算方式</h3>
      <p class="guide-note">溢价率按下列方式计算并展示：</p>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr>
              <th>字段</th>
              <th>含义</th>
              <th>算法</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>T-1 收盘时点溢价率</td>
              <td>用官方收盘估值（或页面展示的 T-1 基准）与市场报价相比。</td>
              <td>(市场报价 / 官方估值 - 1) × 100%</td>
            </tr>
            <tr>
              <td>实时溢价率</td>
              <td>用当前实时估值与市场报价相比。</td>
              <td>(市场报价 / 实时估值 - 1) × 100%</td>
            </tr>
            <tr>
              <td>说明</td>
              <td>溢价率用于展示偏离程度，不构成投资建议。</td>
              <td>结果保留小数点后两位并显示百分号。</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="guide-note">如果你看到的数字与其他平台有差异，通常由估值时点、基准选择、数据刷新时延或模型更新口径导致。</p>
    </section>
  </section>
</template>

<script setup lang="ts">
defineEmits<{
  'back-home': []
}>()
</script>

<style scoped>
.guide-page h3 {
  margin: 16px 0 8px;
  font-size: 15px;
}

.guide-summary {
  margin: 0 0 12px;
  color: #4b5563;
  font-size: 14px;
  line-height: 1.6;
}

.guide-list {
  margin: 0 0 12px 22px;
  padding: 0;
  color: #445066;
  line-height: 1.55;
}

.guide-list li {
  margin-bottom: 6px;
}

.guide-list span {
  display: inline-flex;
  padding: 0 4px;
  border-radius: 4px;
  background: #eef2ff;
  color: #1f4bd1;
  font-size: 13px;
}

.guide-note {
  margin: 8px 0;
  color: #596579;
  font-size: 13px;
}
</style>
