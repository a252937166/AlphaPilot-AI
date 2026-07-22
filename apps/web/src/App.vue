<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  Bell,
  CandlestickChart,
  ClipboardCheck,
  Grid3x3,
  LayoutDashboard,
  Radar,
  Search,
  Sparkles,
  Star,
} from 'lucide-vue-next'
import { api } from './api'
import NotificationBell from './components/NotificationBell.vue'

const router = useRouter()
const search = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const healthy = ref(false)
const providerLabel = ref('—')
const futuHealthy = ref(false)
const today = new Date().toLocaleDateString('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
})

const NAV = [
  { name: 'overview', path: '/', label: '总览', icon: LayoutDashboard },
  { name: 'screening', path: '/screening', label: 'AI选股', icon: Sparkles },
  { name: 'stock', path: '/stock', label: '个股分析', icon: CandlestickChart },
  { name: 'watchlist', path: '/watchlist', label: '自选追踪', icon: Star },
  { name: 'sectors', path: '/sectors', label: '板块预测', icon: Grid3x3 },
  { name: 'market', path: '/market', label: '大盘监控', icon: Radar },
  { name: 'alerts', path: '/alerts', label: '交易提醒', icon: Bell },
  { name: 'review', path: '/review', label: 'AI复盘', icon: ClipboardCheck },
]

function goSearch() {
  const symbol = search.value.trim()
  if (!symbol) return
  router.push(`/stock/${symbol}`)
  search.value = ''
  searchInput.value?.blur()
}

function onGlobalKey(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    searchInput.value?.focus()
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onGlobalKey)
  try {
    const health = await api.health()
    healthy.value = health.status === 'ok'
    providerLabel.value = health.default_data_provider
    futuHealthy.value = Boolean(health.futu?.healthy)
  } catch {
    healthy.value = false
  }
})

onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="logo"><Activity :size="15" :stroke-width="2.4" /></span>
        <span>AlphaPilot <span class="ai">AI</span></span>
      </div>
      <router-link
        v-for="item in NAV"
        :key="item.name"
        :to="item.path"
        class="nav-item"
        :class="{ active: $route.name === item.name }"
        :aria-label="item.label"
      >
        <span class="icon"><component :is="item.icon" :size="15" :stroke-width="1.8" /></span>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
      <div class="sidebar-foot">
        <b>研究模式</b><br />
        实盘交易硬禁用，所有预测仅供工程验证，不构成投资建议。
      </div>
    </aside>

    <div class="main-area">
      <header class="topbar">
        <div class="search-box">
          <Search :size="13" :stroke-width="2" />
          <input
            ref="searchInput"
            v-model="search"
            placeholder="搜索代码，如 600519"
            @keyup.enter="goSearch"
          />
          <kbd>⌘K</kbd>
        </div>
        <div class="spacer" />
        <div class="status-cluster">
          <span class="item mono market-date">A股 · {{ today }}</span>
          <span class="sep" />
          <span class="item provider-status">数据源 <span class="mono" style="color: var(--text-1)">{{ providerLabel }}</span></span>
          <span class="sep" />
          <span class="item"><span class="status-dot" :class="{ ok: futuHealthy }" />Futu</span>
          <span class="item"><span class="status-dot" :class="{ ok: healthy }" />API</span>
        </div>
        <NotificationBell />
      </header>
      <main class="page">
        <router-view />
      </main>
    </div>
  </div>
</template>
