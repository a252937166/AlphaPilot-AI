import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

import OverviewView from './views/OverviewView.vue'
import ScreeningView from './views/ScreeningView.vue'
import StockView from './views/StockView.vue'
import WatchlistView from './views/WatchlistView.vue'
import SectorsView from './views/SectorsView.vue'
import MarketView from './views/MarketView.vue'
import AlertsView from './views/AlertsView.vue'
import ReviewView from './views/ReviewView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewView, meta: { title: '总览' } },
    { path: '/screening', name: 'screening', component: ScreeningView, meta: { title: 'AI选股' } },
    { path: '/stock/:symbol?', name: 'stock', component: StockView, meta: { title: '个股分析' } },
    { path: '/watchlist', name: 'watchlist', component: WatchlistView, meta: { title: '自选追踪' } },
    { path: '/sectors', name: 'sectors', component: SectorsView, meta: { title: '板块预测' } },
    { path: '/market', name: 'market', component: MarketView, meta: { title: '大盘监控' } },
    { path: '/alerts', name: 'alerts', component: AlertsView, meta: { title: '交易提醒' } },
    { path: '/review', name: 'review', component: ReviewView, meta: { title: 'AI复盘' } },
  ],
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title || '')} · AlphaPilot AI`
})

createApp(App).use(router).mount('#app')
