<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

type Candidate = {
  rank: number
  symbol: string
  score: number
  p_up_5d: number
  p_up_20d: number
  expected_return_20d: number
  confidence_20d: number
  reasons: string[]
}

const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
const symbols = ref('600000,000001,000333,600519,300750,601318')
const provider = ref('mock')
const loading = ref(false)
const error = ref('')
const health = ref<Record<string, unknown> | null>(null)
const candidates = ref<Candidate[]>([])
const generatedAt = ref('')

const requestedSymbols = computed(() =>
  symbols.value.split(',').map((item) => item.trim()).filter(Boolean),
)

async function loadHealth() {
  const response = await fetch(`${apiBase}/health`)
  health.value = await response.json()
}

async function runScreen() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch(`${apiBase}/v1/screens/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbols: requestedSymbols.value,
        top_n: Math.min(20, requestedSymbols.value.length),
        provider: provider.value,
      }),
    })
    const body = await response.json()
    if (!response.ok) throw new Error(body.detail || '选股请求失败')
    candidates.value = body.candidates
    generatedAt.value = body.generated_at
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    await loadHealth()
    await runScreen()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
})
</script>

<template>
  <main>
    <header class="hero">
      <div>
        <p class="eyebrow">PROBABILISTIC MARKET INTELLIGENCE</p>
        <h1>AlphaPilot AI</h1>
        <p class="subtitle">自动选股、持续追踪、板块预测、大盘监控与受控交易辅助。</p>
      </div>
      <div class="status-card">
        <span class="status-dot" />
        <strong>{{ health ? 'API Online' : 'Connecting' }}</strong>
        <small>实盘下单默认关闭</small>
      </div>
    </header>

    <section class="controls panel">
      <label>
        股票代码，以逗号分隔
        <textarea v-model="symbols" rows="3" />
      </label>
      <label>
        数据源
        <select v-model="provider">
          <option value="mock">Mock（离线）</option>
          <option value="akshare">AKShare</option>
          <option value="futu">富途 OpenD</option>
        </select>
      </label>
      <button :disabled="loading || requestedSymbols.length === 0" @click="runScreen">
        {{ loading ? '计算中…' : '运行 AI 选股' }}
      </button>
    </section>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="metrics">
      <article class="metric panel">
        <span>候选股票</span><strong>{{ candidates.length }}</strong>
      </article>
      <article class="metric panel">
        <span>数据源</span><strong>{{ provider }}</strong>
      </article>
      <article class="metric panel">
        <span>更新时间</span><strong>{{ generatedAt ? new Date(generatedAt).toLocaleString() : '—' }}</strong>
      </article>
    </section>

    <section class="panel table-panel">
      <div class="section-heading">
        <div><p class="eyebrow">SCREENING RESULT</p><h2>预测候选池</h2></div>
        <span>透明基线模型，仅验证工程链路</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>排名</th><th>代码</th><th>综合分</th><th>5日上涨概率</th>
              <th>20日上涨概率</th><th>20日期望收益</th><th>置信度</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in candidates" :key="item.symbol">
              <td>#{{ item.rank }}</td>
              <td><strong>{{ item.symbol }}</strong></td>
              <td>{{ item.score.toFixed(1) }}</td>
              <td>{{ (item.p_up_5d * 100).toFixed(1) }}%</td>
              <td>{{ (item.p_up_20d * 100).toFixed(1) }}%</td>
              <td :class="item.expected_return_20d >= 0 ? 'positive' : 'negative'">
                {{ (item.expected_return_20d * 100).toFixed(2) }}%
              </td>
              <td>{{ (item.confidence_20d * 100).toFixed(1) }}%</td>
            </tr>
            <tr v-if="!candidates.length"><td colspan="7" class="empty">暂无结果</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
