<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Play } from 'lucide-vue-next'
import { api } from '../api'
import { fmtNum, fmtPct, pctClass } from '../format'
import EChart from '../components/EChart.vue'
import { categoryAxis, tooltipStyle, valueAxis } from '../chartTheme'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const symbolsText = ref('')
const topN = ref(10)
const result = ref<any>(null)

const FACTOR_WEIGHTS = [
  { name: '趋势动量', weight: 60 },
  { name: '风险（波动率）', weight: 25 },
  { name: '质量（占位）', weight: 15 },
]

const candidates = computed(() => result.value?.candidates ?? [])
const avgScore = computed(() => {
  if (!candidates.value.length) return null
  return candidates.value.reduce((sum: number, c: any) => sum + c.score, 0) / candidates.value.length
})
const avgPUp = computed(() => {
  if (!candidates.value.length) return null
  return (
    candidates.value.reduce((sum: number, c: any) => sum + c.p_up_20d, 0) / candidates.value.length
  )
})

const distOption = computed(() => {
  const buckets = [
    { label: '<-5%', min: -Infinity, max: -0.05 },
    { label: '-5~0', min: -0.05, max: 0 },
    { label: '0~5', min: 0, max: 0.05 },
    { label: '5~10', min: 0.05, max: 0.1 },
    { label: '>10%', min: 0.1, max: Infinity },
  ]
  const counts = buckets.map(
    (bucket) =>
      candidates.value.filter(
        (c: any) => c.expected_return_20d >= bucket.min && c.expected_return_20d < bucket.max,
      ).length,
  )
  return {
    animation: false,
    tooltip: { ...tooltipStyle },
    grid: { left: 28, right: 6, top: 12, bottom: 22 },
    xAxis: categoryAxis(buckets.map((bucket) => bucket.label)),
    yAxis: valueAxis({ minInterval: 1 }),
    series: [
      {
        type: 'bar',
        data: counts,
        barWidth: '52%',
        itemStyle: { color: '#3b82f6', borderRadius: [3, 3, 0, 0] },
      },
    ],
  }
})

async function loadUniverse() {
  try {
    const universe = await api.screenUniverse()
    symbolsText.value = universe.symbols.join(', ')
  } catch {
    symbolsText.value = '600519, 300750, 002594, 600000, 000333'
  }
}

async function run() {
  const symbols = symbolsText.value
    .split(/[\s,，、]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (!symbols.length) return
  loading.value = true
  error.value = ''
  try {
    result.value = await api.runScreen({ symbols, top_n: topN.value })
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadUniverse()
  try {
    result.value = await api.latestScreen()
  } catch {
    /* no previous run */
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>AI选股</h1>
      <span class="sub">多因子基线评分排序 · 质量因子为占位权重</span>
    </div>

    <div class="panel" style="margin-bottom: 12px">
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
        <input
          v-model="symbolsText"
          class="input mono"
          style="flex: 1; min-width: 300px"
          placeholder="股票代码列表，逗号分隔"
        />
        <label class="xs dim" style="display: inline-flex; align-items: center; gap: 6px">
          Top
          <input v-model.number="topN" type="number" class="input num" style="width: 62px" min="1" max="100" />
        </label>
        <button class="btn primary" :disabled="loading" @click="run">
          <Play :size="12" /> {{ loading ? '评分中…' : '运行选股' }}
        </button>
      </div>
      <div v-if="error" class="xs down" style="margin-top: 8px">{{ error }}</div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) 300px; align-items: start" v-if="result">
      <div class="grid">
        <div class="grid" style="grid-template-columns: repeat(4, 1fr)">
          <div class="stat-card">
            <div class="label">本次入选</div>
            <div class="value" style="color: var(--cyan)">{{ candidates.length }}</div>
            <div class="delta">请求 {{ result.requested }} · 成功 {{ result.succeeded }}</div>
          </div>
          <div class="stat-card">
            <div class="label">平均综合评分</div>
            <div class="value">{{ avgScore === null ? '—' : fmtNum(avgScore, 1) }}</div>
            <div class="delta">0-100 分</div>
          </div>
          <div class="stat-card">
            <div class="label">平均20日上涨概率</div>
            <div class="value up">{{ avgPUp === null ? '—' : (avgPUp * 100).toFixed(1) + '%' }}</div>
            <div class="delta">概率化输出</div>
          </div>
          <div class="stat-card">
            <div class="label">数据源 / 模型</div>
            <div class="value" style="font-size: 15px; font-family: var(--font-mono)">{{ result.provider }}</div>
            <div class="delta mono xs">{{ result.model_version }}</div>
          </div>
        </div>

        <div class="panel" style="padding-bottom: 6px">
          <div class="panel-title">
            候选列表
            <span class="extra mono">{{ (result.generated_at || result.created_at || '').slice(0, 19) }}</span>
          </div>
          <table class="tbl">
            <thead>
              <tr>
                <th>#</th><th>代码</th><th>综合评分</th><th class="r">趋势分</th>
                <th class="r">5日概率</th><th class="r">20日概率</th><th class="r">20日预期</th><th class="r">置信</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in candidates" :key="c.symbol">
                <td class="dim num">{{ c.rank }}</td>
                <td class="sym" @click="router.push(`/stock/${c.symbol}`)">
                  <span class="name num">{{ c.symbol }}</span>
                </td>
                <td>
                  <div style="display: flex; align-items: center; gap: 8px">
                    <span class="num" style="font-weight: 650; color: var(--cyan); width: 34px">{{ fmtNum(c.score, 1) }}</span>
                    <span class="score-bar" style="width: 64px"><i :style="{ width: c.score + '%' }" /></span>
                  </div>
                </td>
                <td class="r num">{{ fmtNum(c.trend_score, 1) }}</td>
                <td class="r num">{{ (c.p_up_5d * 100).toFixed(1) }}%</td>
                <td class="r num">{{ (c.p_up_20d * 100).toFixed(1) }}%</td>
                <td class="r num" :class="pctClass(c.expected_return_20d)">
                  {{ fmtPct(c.expected_return_20d, 2, false) }}
                </td>
                <td class="r num dim">{{ (c.confidence_20d * 100).toFixed(0) }}%</td>
              </tr>
            </tbody>
          </table>
          <div v-if="Object.keys(result.failed || {}).length" class="xs dim" style="margin: 8px 0 4px">
            失败标的：{{ Object.keys(result.failed).join('、') }}
          </div>
        </div>
      </div>

      <div class="grid">
        <div class="panel">
          <div class="panel-title">本次选股逻辑 <span class="extra">基线权重</span></div>
          <div v-for="factor in FACTOR_WEIGHTS" :key="factor.name" style="margin-bottom: 10px">
            <div class="kv" style="padding: 0 0 4px">
              <span class="k">{{ factor.name }}</span>
              <span class="num">{{ factor.weight }}%</span>
            </div>
            <div class="score-bar"><i :style="{ width: factor.weight + '%' }" /></div>
          </div>
          <div class="xs dim" style="margin-top: 10px; line-height: 1.7">
            当前为透明工程基线：趋势动量 + 波动风险 + 质量占位分。财务质量因子与
            Walk-Forward 回测在路线图 M2 落地后替换此权重。
          </div>
        </div>

        <div class="panel">
          <div class="panel-title">预期收益分布 <span class="extra">20日</span></div>
          <EChart :option="distOption" height="160px" />
        </div>
      </div>
    </div>

    <div v-else class="empty-hint">尚无选股结果，点击「运行选股」开始</div>
  </div>
</template>
