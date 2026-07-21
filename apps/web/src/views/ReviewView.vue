<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Sparkles } from 'lucide-vue-next'
import { api } from '../api'
import { actionMeta, fmtDate, fmtPct, fmtTime, pctClass } from '../format'

const router = useRouter()
const loading = ref(true)
const generating = ref(false)
const error = ref('')
const report = ref<any>(null)

const hitStats = computed(() => report.value?.forecast_hit_stats)
const gainers = computed(() => report.value?.watchlist_gainers ?? [])
const losers = computed(() => report.value?.watchlist_losers ?? [])

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.dailyReport()
  } catch {
    report.value = null
  } finally {
    loading.value = false
  }
}

async function generate() {
  generating.value = true
  error.value = ''
  try {
    report.value = await api.generateDailyReport()
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    generating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>AI复盘</h1>
      <span class="sub mono" v-if="report">{{ report.report_date }}</span>
      <span class="sub">收益归因 + 预测评分 + 明日关注</span>
      <div style="margin-left: auto">
        <button class="btn primary" :disabled="generating" @click="generate">
          <Sparkles :size="12" /> {{ generating ? '生成中…' : '生成今日复盘' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">{{ error }}</div>

    <div v-if="report" class="grid" style="grid-template-columns: minmax(0, 1fr) 300px; align-items: start">
      <div class="grid">
        <!-- 结论 -->
        <div class="banner">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px">
            <b style="display: inline-flex; align-items: center; gap: 6px; color: var(--accent-hi)">
              <Sparkles :size="13" /> 今日复盘结论
            </b>
            <span class="xs dim mono">
              {{ report.ai_summary?.source === 'llm' ? 'LLM' : '规则模板' }} · {{ fmtTime(report.generated_at).slice(0, -3) }}
            </span>
          </div>
          <span class="muted">{{ report.ai_summary?.text }}</span>
        </div>

        <!-- 统计卡 -->
        <div class="grid" style="grid-template-columns: repeat(4, 1fr)">
          <div class="stat-card">
            <div class="label">1日预测命中率</div>
            <div class="value" :class="hitStats?.hit_rate >= 0.5 ? 'up' : hitStats?.hit_rate === null || hitStats?.hit_rate === undefined ? '' : 'down'">
              {{ hitStats?.hit_rate === null || hitStats?.hit_rate === undefined ? '—' : (hitStats.hit_rate * 100).toFixed(1) + '%' }}
            </div>
            <div class="delta">样本 {{ hitStats?.evaluated ?? 0 }} 个，随运行天数累计</div>
          </div>
          <div class="stat-card">
            <div class="label">今日提醒</div>
            <div class="value">{{ (report.alerts || []).length }}</div>
            <div class="delta">来自自选追踪信号</div>
          </div>
          <div class="stat-card">
            <div class="label">自选最强</div>
            <div class="value up" style="font-size: 17px">
              {{ gainers[0] ? fmtPct(gainers[0].change_pct) : '—' }}
            </div>
            <div class="delta">{{ gainers[0]?.display_name || gainers[0]?.symbol || '—' }}</div>
          </div>
          <div class="stat-card">
            <div class="label">自选最弱</div>
            <div class="value down" style="font-size: 17px">
              {{ losers[0] ? fmtPct(losers[0].change_pct) : '—' }}
            </div>
            <div class="delta">{{ losers[0]?.display_name || losers[0]?.symbol || '—' }}</div>
          </div>
        </div>

        <!-- 预测 vs 实际 -->
        <div class="panel" style="padding-bottom: 6px">
          <div class="panel-title">预测 vs 实际 <span class="extra">1日方向</span></div>
          <table class="tbl" v-if="hitStats?.samples?.length">
            <thead>
              <tr><th>标的</th><th>预测时间</th><th class="r">1日上涨概率</th><th class="r">实际收益</th><th>命中</th></tr>
            </thead>
            <tbody>
              <tr v-for="(sample, index) in hitStats.samples" :key="index">
                <td class="sym" @click="router.push(`/stock/${sample.symbol}`)">
                  <span class="name num">{{ sample.symbol }}</span>
                </td>
                <td class="xs dim mono">{{ fmtDate(sample.as_of) }}</td>
                <td class="r num">{{ (sample.p_up_1d * 100).toFixed(1) }}%</td>
                <td class="r num" :class="pctClass(sample.realized_return_1d)">
                  {{ fmtPct(sample.realized_return_1d, 2, false) }}
                </td>
                <td>
                  <span class="badge" :class="sample.hit ? 'green' : 'red'">{{ sample.hit ? '命中' : '未中' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-hint">
            暂无可评估样本 —— 预测快照需与后续真实K线配对，连续运行数日后自动累计
          </div>
        </div>

        <!-- 涨跌榜 -->
        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="panel" style="padding-bottom: 6px">
            <div class="panel-title">自选涨幅榜</div>
            <table class="tbl" v-if="gainers.length">
              <tbody>
                <tr v-for="row in gainers" :key="row.symbol">
                  <td class="sym" @click="router.push(`/stock/${row.symbol}`)">
                    <span class="name" style="font-size: 12px">{{ row.display_name || row.symbol }}</span>
                  </td>
                  <td class="r num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</td>
                  <td style="text-align: right">
                    <span class="badge" :class="actionMeta(row.alert_action).cls">{{ actionMeta(row.alert_action).label }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-hint">无数据</div>
          </div>
          <div class="panel" style="padding-bottom: 6px">
            <div class="panel-title">自选跌幅榜</div>
            <table class="tbl" v-if="losers.length">
              <tbody>
                <tr v-for="row in losers" :key="row.symbol">
                  <td class="sym" @click="router.push(`/stock/${row.symbol}`)">
                    <span class="name" style="font-size: 12px">{{ row.display_name || row.symbol }}</span>
                  </td>
                  <td class="r num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</td>
                  <td style="text-align: right">
                    <span class="badge" :class="actionMeta(row.alert_action).cls">{{ actionMeta(row.alert_action).label }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-hint">无数据</div>
          </div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <div class="panel">
          <div class="panel-title">明日关注</div>
          <div v-if="report.tomorrow_focus?.length">
            <div v-for="(item, index) in report.tomorrow_focus" :key="index" class="feed-row">
              <span class="num dim" style="width: 14px">{{ Number(index) + 1 }}</span>
              <div>
                <div style="font-weight: 600; font-size: 12.5px">{{ item.display_name || item.symbol }}</div>
                <div class="xs dim">{{ item.reason }}</div>
              </div>
            </div>
          </div>
          <div v-else class="empty-hint">暂无重点关注项</div>
        </div>

        <div class="panel">
          <div class="panel-title">今日公告时间线</div>
          <ul class="timeline" v-if="report.disclosures?.length">
            <li v-for="item in report.disclosures.slice(0, 8)" :key="item.id">
              <div class="xs dim mono">{{ fmtDate(item.published_at) }} · {{ item.symbol }}</div>
              <a :href="item.url" target="_blank" rel="noopener" class="xs">{{ item.title }}</a>
            </li>
          </ul>
          <div v-else class="empty-hint">暂无公告缓存</div>
        </div>

        <div class="panel">
          <div class="xs dim" style="line-height: 1.7">{{ report.disclaimer }}</div>
        </div>
      </div>
    </div>

    <div v-else-if="!loading" class="panel empty-hint" style="padding: 56px">
      还没有今日复盘报告<br /><br />
      <button class="btn primary" :disabled="generating" @click="generate">
        <Sparkles :size="12" /> {{ generating ? '生成中…' : '立即生成' }}
      </button>
    </div>
  </div>
</template>
