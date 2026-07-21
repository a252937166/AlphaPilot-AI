<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import { api } from '../api'
import { fmtAmount, fmtPct, fmtTime, heatColor, pctClass } from '../format'
import EChart from '../components/EChart.vue'
import { SERIES_PALETTE, tooltipStyle } from '../chartTheme'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref<any>(null)

const sectors = computed(() => data.value?.sectors ?? [])

const turnoverDonut = computed(() => {
  const total = sectors.value.reduce((sum: number, s: any) => sum + (s.turnover || 0), 0)
  return {
    animation: false,
    tooltip: {
      ...tooltipStyle,
      formatter: (params: any) => `${params.name}  ${((params.value / total) * 100).toFixed(1)}%`,
    },
    legend: { show: false },
    series: [
      {
        type: 'pie',
        radius: ['52%', '76%'],
        label: { color: '#9aa7c4', fontSize: 10, formatter: '{b}' },
        labelLine: { lineStyle: { color: 'rgba(148,163,198,0.3)' } },
        itemStyle: { borderColor: '#0a0f1c', borderWidth: 2 },
        data: sectors.value.slice(0, 8).map((s: any, index: number) => ({
          name: s.plate_name,
          value: s.turnover,
          itemStyle: { color: SERIES_PALETTE[index % SERIES_PALETTE.length] },
        })),
      },
    ],
  }
})

async function load(refresh = false) {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.sectors(refresh)
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

onMounted(() => load(false))
</script>

<template>
  <div>
    <div class="page-head">
      <h1>板块预测</h1>
      <span class="sub">板块抽样强度 · 观察型启发式排名，非校准预测</span>
      <div style="margin-left: auto">
        <button class="btn ghost" :disabled="loading" @click="load(true)">
          <RefreshCw :size="12" :class="{ spin: loading }" /> 强制刷新
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">
      板块数据不可用：{{ error }}（需要 Futu OpenD 行情）
    </div>

    <div v-if="loading && !sectors.length" class="grid" style="grid-template-columns: 1fr 300px">
      <div class="grid">
        <div class="skeleton" style="height: 180px" />
        <div class="skeleton" style="height: 320px" />
      </div>
      <div class="grid"><div class="skeleton" style="height: 300px" /></div>
    </div>

    <div v-if="sectors.length" class="grid" style="grid-template-columns: minmax(0, 1fr) 300px; align-items: start">
      <div class="grid">
        <!-- 热力图 -->
        <div class="panel">
          <div class="panel-title">
            板块热力图 · 按强度
            <span class="extra mono">{{ data.cached ? 'cache' : 'live' }} · {{ fmtTime(data.as_of).slice(-8, -3) }}</span>
          </div>
          <div class="heat-grid" style="grid-template-columns: repeat(auto-fill, minmax(118px, 1fr))">
            <div
              v-for="sector in sectors"
              :key="sector.plate_code"
              class="heat-tile"
              style="min-height: 72px"
              :style="{ background: heatColor(Number(sector.avg_change_pct)) }"
            >
              <div class="t-name">{{ sector.plate_name }}</div>
              <div class="t-val">{{ fmtPct(sector.avg_change_pct) }}</div>
              <div class="t-sub">强度 {{ sector.strength }} · 涨 {{ (sector.up_ratio * 100).toFixed(0) }}%</div>
            </div>
          </div>
        </div>

        <!-- 排行榜 -->
        <div class="panel" style="padding-bottom: 6px">
          <div class="panel-title">板块强度排行榜</div>
          <table class="tbl">
            <thead>
              <tr>
                <th>#</th><th>板块</th><th>强度</th><th class="r">平均涨跌</th>
                <th class="r">上涨占比</th><th class="r">抽样</th><th class="r">成交额</th><th>龙头股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="sector in sectors" :key="sector.plate_code">
                <td class="num dim">{{ sector.rank }}</td>
                <td style="font-weight: 600">{{ sector.plate_name }}</td>
                <td>
                  <div style="display: flex; align-items: center; gap: 8px">
                    <span class="num" style="color: var(--cyan); font-weight: 650; width: 30px">{{ sector.strength }}</span>
                    <span class="score-bar" style="width: 70px"><i :style="{ width: sector.strength * 10 + '%' }" /></span>
                  </div>
                </td>
                <td class="r num" :class="pctClass(sector.avg_change_pct)">{{ fmtPct(sector.avg_change_pct) }}</td>
                <td class="r num">{{ (sector.up_ratio * 100).toFixed(0) }}%</td>
                <td class="r num dim">{{ sector.sampled }}</td>
                <td class="r num dim">{{ fmtAmount(sector.turnover) }}</td>
                <td class="sym" @click="router.push(`/stock/${String(sector.leader_code).split('.').pop()}`)">
                  <span class="name" style="font-size: 12px">{{ sector.leader_name }}</span>
                  <span class="xs num" :class="pctClass(sector.leader_change_pct)" style="margin-left: 6px">
                    {{ fmtPct(sector.leader_change_pct) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <div class="panel">
          <div class="panel-title">TOP 3 看好板块</div>
          <div v-for="sector in sectors.slice(0, 3)" :key="sector.plate_code" class="feed-row">
            <span class="num dim" style="width: 14px">{{ sector.rank }}</span>
            <div style="flex: 1">
              <div style="font-weight: 600; font-size: 12.5px">{{ sector.plate_name }}</div>
              <div class="xs dim">龙头 {{ sector.leader_name }}</div>
            </div>
            <div style="text-align: right">
              <div class="num" style="color: var(--cyan); font-weight: 650">{{ sector.strength }}</div>
              <div class="xs num" :class="pctClass(sector.avg_change_pct)">{{ fmtPct(sector.avg_change_pct) }}</div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-title">成交额分布</div>
          <EChart :option="turnoverDonut" height="210px" />
        </div>

        <div class="panel">
          <div class="panel-title">方法说明</div>
          <div class="xs dim" style="line-height: 1.7">
            每板块抽样至多 30 只成份股，单次富途快照计算平均涨跌、上涨占比与成交额，
            合成 0-10 强度分。当前覆盖 {{ sectors.length }} 个行业板块，属观察型指标；
            资金流、拥挤度与预测模型在路线图 M2 加入。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.spin {
  animation: rotate 0.9s linear infinite;
}
@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}
</style>
