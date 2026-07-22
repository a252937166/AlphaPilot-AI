<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  AlertTriangle,
  Bell,
  Check,
  ClipboardCheck,
  Info,
  RefreshCw,
  X,
} from 'lucide-vue-next'
import { api, type NotificationItem, type NotificationKind } from '../api'
import { fmtTime } from '../format'

type FilterKind = 'all' | NotificationKind

const POLL_INTERVAL_MS = 30_000
const FILTERS: { key: FilterKind; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'alert', label: '提醒' },
  { key: 'event', label: '事件' },
  { key: 'job', label: '任务' },
  { key: 'system', label: '系统' },
]
const KIND_META = {
  alert: { label: '提醒', icon: AlertTriangle, route: '/alerts' },
  event: { label: '事件', icon: Activity, route: '/review' },
  job: { label: '任务', icon: ClipboardCheck, route: '/' },
  system: { label: '系统', icon: Info, route: '/' },
} satisfies Record<NotificationKind, { label: string; icon: typeof Bell; route: string }>

const router = useRouter()
const trigger = ref<HTMLButtonElement | null>(null)
const drawer = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const open = ref(false)
const loading = ref(false)
const markingAll = ref(false)
const countLoading = ref(false)
const pendingReadCount = ref(0)
const failedReadCount = ref(0)
const listError = ref('')
const actionError = ref('')
const countError = ref(false)
const unreadCount = ref<number | null>(null)
const notifications = ref<NotificationItem[]>([])
const activeFilter = ref<FilterKind>('all')
let pollTimer: ReturnType<typeof setInterval> | null = null
let previousBodyOverflow = ''
let backgroundShell: HTMLElement | null = null
let countRequestVersion = 0
let listRequestVersion = 0
let readQueue: Promise<void> = Promise.resolve()
const pendingReadIds = new Set<number>()
const pendingReadAt = new Map<number, string>()
const confirmedReadAt = new Map<number, string>()
const failedReadIds = new Set<number>()

const visibleNotifications = computed(() =>
  activeFilter.value === 'all'
    ? notifications.value
    : notifications.value.filter((item) => item.kind === activeFilter.value),
)
const listedUnreadCount = computed(
  () => notifications.value.filter((item) => !item.read_at).length,
)
const canMarkAll = computed(
  () => (unreadCount.value ?? 0) > 0 || listedUnreadCount.value > 0,
)
const badgeLabel = computed(() => {
  const count = unreadCount.value ?? 0
  return count > 99 ? '99+' : String(count)
})
const triggerLabel = computed(() => {
  if (unreadCount.value === null) return '通知，未读数暂不可用'
  return unreadCount.value ? `通知，${unreadCount.value} 条未读` : '通知，无未读消息'
})
const drawerError = computed(
  () =>
    actionError.value ||
    listError.value ||
    (failedReadCount.value
      ? `${failedReadCount.value} 条通知的已读状态同步失败，仍保持未读。`
      : '') ||
    (countError.value
      ? unreadCount.value === null
        ? '未读数暂时不可用，请检查 API 状态后重试。'
        : '未读数同步失败，正在展示上次确认的结果。'
      : ''),
)

function filterCount(kind: FilterKind): number {
  if (kind === 'all') return notifications.value.length
  return notifications.value.filter((item) => item.kind === kind).length
}

function applyConfirmedUnreadCount(value: number) {
  countRequestVersion += 1
  unreadCount.value = Math.max(0, Number(value) || 0)
  countError.value = false
  countLoading.value = false
}

async function refreshCount(force = false) {
  if ((!force && countLoading.value) || document.visibilityState === 'hidden') return
  const requestVersion = ++countRequestVersion
  countLoading.value = true
  try {
    const payload = await api.notificationUnreadCount()
    if (requestVersion === countRequestVersion) {
      unreadCount.value = Math.max(0, Number(payload.unread_count) || 0)
      countError.value = false
    }
  } catch {
    if (requestVersion === countRequestVersion) countError.value = true
  } finally {
    if (requestVersion === countRequestVersion) countLoading.value = false
  }
}

async function loadNotifications() {
  const requestVersion = ++listRequestVersion
  loading.value = true
  listError.value = ''
  try {
    const payload = await api.notifications(false, 100)
    if (requestVersion !== listRequestVersion) return
    for (const item of payload.notifications) {
      if (item.read_at) {
        failedReadIds.delete(item.id)
        confirmedReadAt.set(item.id, item.read_at)
      }
    }
    notifications.value = payload.notifications.map((item) => {
      const pendingAt = pendingReadAt.get(item.id)
      if (pendingAt) return item.read_at ? item : { ...item, read_at: pendingAt }
      if (failedReadIds.has(item.id)) return { ...item, read_at: null }
      const localReadAt = confirmedReadAt.get(item.id)
      return localReadAt && !item.read_at ? { ...item, read_at: localReadAt } : item
    })
    failedReadCount.value = failedReadIds.size
  } catch {
    if (requestVersion === listRequestVersion) {
      listError.value = '通知列表暂时不可用，请检查 API 状态后重试。'
    }
  } finally {
    if (requestVersion === listRequestVersion) loading.value = false
  }
}

async function showDrawer() {
  if (open.value) return
  open.value = true
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  backgroundShell = document.querySelector<HTMLElement>('#app > .app-shell')
  backgroundShell?.setAttribute('inert', '')
  await nextTick()
  closeButton.value?.focus()
  void loadNotifications()
  void refreshCount()
}

function hideDrawer({ restoreFocus = true } = {}) {
  if (!open.value) return
  open.value = false
  document.body.style.overflow = previousBodyOverflow
  backgroundShell?.removeAttribute('inert')
  backgroundShell = null
  if (restoreFocus) nextTick(() => trigger.value?.focus())
}

async function toggleDrawer() {
  if (open.value) hideDrawer()
  else await showDrawer()
}

async function markAllRead() {
  if (!canMarkAll.value || markingAll.value || loading.value) return
  markingAll.value = true
  actionError.value = ''
  try {
    const payload = await api.readNotifications({ all: true })
    listRequestVersion += 1
    loading.value = false
    const readAt = new Date().toISOString()
    notifications.value = notifications.value.map((item) => ({
      ...item,
      read_at: item.read_at || readAt,
    }))
    for (const item of notifications.value) {
      if (item.read_at) confirmedReadAt.set(item.id, item.read_at)
    }
    failedReadIds.clear()
    failedReadCount.value = 0
    applyConfirmedUnreadCount(payload.unread_count)
    void loadNotifications()
  } catch {
    actionError.value = '未能标记全部已读，请稍后重试。'
  } finally {
    markingAll.value = false
  }
}

async function syncNotificationRead(item: NotificationItem) {
  try {
    const payload = await api.readNotifications({ ids: [item.id] })
    const readAt = pendingReadAt.get(item.id) ?? item.read_at ?? new Date().toISOString()
    item.read_at = readAt
    notifications.value = notifications.value.map((current) =>
      current.id === item.id ? { ...current, read_at: readAt } : current,
    )
    confirmedReadAt.set(item.id, readAt)
    failedReadIds.delete(item.id)
    failedReadCount.value = failedReadIds.size
    applyConfirmedUnreadCount(payload.unread_count)
  } catch {
    pendingReadIds.delete(item.id)
    pendingReadAt.delete(item.id)
    pendingReadCount.value = pendingReadIds.size
    item.read_at = null
    notifications.value = notifications.value.map((current) =>
      current.id === item.id ? { ...current, read_at: null } : current,
    )
    confirmedReadAt.delete(item.id)
    failedReadIds.add(item.id)
    failedReadCount.value = failedReadIds.size
    countError.value = true
    void refreshCount(true)
  } finally {
    pendingReadIds.delete(item.id)
    pendingReadAt.delete(item.id)
    pendingReadCount.value = pendingReadIds.size
  }
}

function openNotification(item: NotificationItem) {
  const wasUnread = !item.read_at
  if (wasUnread && !pendingReadIds.has(item.id)) {
    const readAt = new Date().toISOString()
    item.read_at = readAt
    failedReadIds.delete(item.id)
    failedReadCount.value = failedReadIds.size
    pendingReadIds.add(item.id)
    pendingReadAt.set(item.id, readAt)
    pendingReadCount.value = pendingReadIds.size
    if (unreadCount.value !== null) {
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    }
    readQueue = readQueue.then(() => syncNotificationRead(item))
    void readQueue
  }
  const target = KIND_META[item.kind].route
  hideDrawer({ restoreFocus: false })
  void router.push(target)
}

function retryDrawer() {
  actionError.value = ''
  void loadNotifications()
  void refreshCount()
}

function onDrawerKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    hideDrawer()
    return
  }
  if (event.key !== 'Tab' || !drawer.value) return
  const focusable = Array.from(
    drawer.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  )
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') void refreshCount()
}

onMounted(() => {
  void refreshCount()
  pollTimer = window.setInterval(() => void refreshCount(), POLL_INTERVAL_MS)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (open.value) {
    document.body.style.overflow = previousBodyOverflow
    backgroundShell?.removeAttribute('inert')
  }
})
</script>

<template>
  <div class="notification-bell">
    <button
      ref="trigger"
      type="button"
      class="bell-trigger"
      :class="{ active: open }"
      :aria-expanded="open"
      aria-controls="notification-drawer"
      :aria-label="triggerLabel"
      aria-haspopup="dialog"
      @click="toggleDrawer"
    >
      <Bell :size="16" :stroke-width="1.9" />
      <span
        v-if="unreadCount !== null && unreadCount > 0"
        class="unread-badge num"
        aria-hidden="true"
      >{{ badgeLabel }}</span>
    </button>

    <Teleport to="body">
      <Transition name="notification-drawer">
        <div v-if="open" class="notification-layer" @mousedown.self="hideDrawer()">
          <aside
            id="notification-drawer"
            ref="drawer"
            class="notification-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="notification-title"
            @keydown="onDrawerKeydown"
          >
            <header class="drawer-head">
              <div>
                <div class="drawer-title-row">
                  <h2 id="notification-title">通知中心</h2>
                  <span class="unread-summary num">
                    {{ unreadCount === null ? '—' : unreadCount }} 未读
                    <small v-if="countError">· 待同步</small>
                  </span>
                </div>
              </div>
              <button
                ref="closeButton"
                type="button"
                class="icon-button"
                aria-label="关闭通知中心"
                @click="hideDrawer()"
              >
                <X :size="17" />
              </button>
            </header>

            <div class="drawer-toolbar">
              <div class="kind-filters" role="group" aria-label="按通知类型筛选">
                <button
                  v-for="filter in FILTERS"
                  :key="filter.key"
                  type="button"
                  :class="{ on: activeFilter === filter.key }"
                  :aria-pressed="activeFilter === filter.key"
                  @click="activeFilter = filter.key"
                >
                  {{ filter.label }}
                  <span class="num">{{ filterCount(filter.key) }}</span>
                </button>
              </div>
              <button
                type="button"
                class="mark-all"
                :disabled="!canMarkAll || markingAll || loading || pendingReadCount > 0"
                @click="markAllRead"
              >
                <Check :size="13" />
                {{ markingAll ? '同步中' : '全部已读' }}
              </button>
            </div>

            <div v-if="drawerError" class="drawer-error" role="status">
              <AlertTriangle :size="14" />
              <span>{{ drawerError }}</span>
              <button type="button" @click="retryDrawer">
                <RefreshCw :size="12" />重试
              </button>
            </div>

            <div class="notification-list" :aria-busy="loading" aria-live="polite">
              <template v-if="loading">
                <div v-for="item in 5" :key="item" class="notification-skeleton">
                  <span class="skeleton" />
                  <span class="skeleton" />
                  <span class="skeleton" />
                </div>
              </template>

              <div v-else-if="!visibleNotifications.length" class="notification-empty">
                <Bell :size="24" :stroke-width="1.4" />
                <strong>{{ activeFilter === 'all' ? '暂无通知' : '这个分类暂无通知' }}</strong>
                <span>新的提醒、事件和任务状态会自动出现在这里。</span>
              </div>

              <button
                v-for="item in visibleNotifications"
                v-else
                :key="item.id"
                type="button"
                class="notification-item"
                :class="[`level-${item.level}`, { unread: !item.read_at }]"
                @click="openNotification(item)"
              >
                <span class="signal-rail" aria-hidden="true" />
                <span class="item-content">
                  <span class="item-meta">
                    <span class="kind-label" :class="`kind-${item.kind}`">
                      <component :is="KIND_META[item.kind].icon" :size="11" />
                      {{ KIND_META[item.kind].label }}
                    </span>
                    <time class="num" :datetime="item.created_at">{{ fmtTime(item.created_at) }}</time>
                  </span>
                  <span class="item-title">
                    <i v-if="!item.read_at" aria-label="未读" />
                    {{ item.title }}
                  </span>
                  <span class="item-body">{{ item.body }}</span>
                  <span class="item-ref mono">{{ item.ref_id }}</span>
                </span>
              </button>
            </div>

            <footer class="drawer-foot">
              <span><i />每 30 秒同步未读数</span>
              <button type="button" :disabled="countLoading" @click="refreshCount()">
                <RefreshCw :size="12" :class="{ spin: countLoading }" />立即同步
              </button>
            </footer>
          </aside>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.notification-bell {
  display: grid;
  place-items: center;
}

.bell-trigger,
.icon-button {
  border: 1px solid var(--line-1);
  background: var(--surface-1);
  color: var(--text-2);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
}

.bell-trigger {
  position: relative;
  width: 32px;
  height: 32px;
  border-radius: 9px;
}

.bell-trigger:hover,
.bell-trigger.active,
.icon-button:hover {
  color: var(--text-1);
  border-color: rgba(96, 165, 250, 0.42);
  background: var(--surface-2);
}

.unread-badge {
  position: absolute;
  top: -5px;
  right: -6px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  color: #fff;
  background: #dc2626;
  border: 2px solid var(--bg);
  box-shadow: 0 0 12px rgba(248, 113, 113, 0.4);
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
}

.notification-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: flex;
  justify-content: flex-end;
  padding: 10px;
  background: rgba(1, 3, 9, 0.58);
  backdrop-filter: blur(4px);
}

.notification-drawer {
  width: min(392px, calc(100vw - 20px));
  height: calc(100dvh - 20px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--text-1);
  background: linear-gradient(165deg, rgba(37, 99, 235, 0.1), transparent 28%),
    linear-gradient(180deg, #0d1528, #080e1a);
  border: 1px solid var(--line-2);
  border-radius: var(--r-lg);
  box-shadow: -18px 0 70px rgba(0, 0, 0, 0.48), 0 0 0 1px rgba(96, 165, 250, 0.05);
}

.drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  padding: 18px 18px 14px;
  border-bottom: 1px solid var(--line-1);
}

.drawer-title-row {
  display: flex;
  align-items: baseline;
  gap: 9px;
}

.drawer-title-row h2 {
  font-size: 17px;
  font-weight: 680;
  line-height: 1.35;
  letter-spacing: -0.015em;
}

.unread-summary {
  color: var(--text-2);
  font-size: 10.5px;
}

.unread-summary small {
  color: var(--warn);
  font-size: 9.5px;
}

.icon-button {
  width: 30px;
  height: 30px;
  border-radius: 8px;
}

.drawer-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-1);
}

.kind-filters {
  min-width: 0;
  display: flex;
  gap: 2px;
  padding: 2px;
  overflow-x: auto;
  background: rgba(4, 7, 15, 0.5);
  border: 1px solid var(--line-1);
  border-radius: 8px;
}

.kind-filters button,
.mark-all,
.drawer-error button,
.drawer-foot button {
  border: 0;
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  font-family: inherit;
}

.kind-filters button {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 7px;
  border-radius: 6px;
  font-size: 10.5px;
}

.kind-filters button span {
  opacity: 0.82;
  font-size: 9.5px;
}

.kind-filters button:hover,
.kind-filters button.on {
  color: var(--text-1);
  background: rgba(59, 130, 246, 0.16);
}

.mark-all {
  margin-left: auto;
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
}

.mark-all:hover:not(:disabled),
.drawer-foot button:hover:not(:disabled),
.drawer-error button:hover {
  color: var(--accent-hi);
}

.mark-all:disabled,
.drawer-foot button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.drawer-error {
  margin: 10px 12px 0;
  padding: 8px 10px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 7px;
  color: #fca5a5;
  background: rgba(248, 113, 113, 0.07);
  border: 1px solid rgba(248, 113, 113, 0.24);
  border-radius: 8px;
  font-size: 11px;
}

.drawer-error button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: #fca5a5;
  font-size: 10px;
}

.notification-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px 12px;
}

.notification-item {
  position: relative;
  width: 100%;
  display: block;
  overflow: hidden;
  margin: 0 0 6px;
  padding: 11px 12px 10px 15px;
  text-align: left;
  color: var(--text-2);
  background: rgba(10, 16, 30, 0.76);
  border: 1px solid var(--line-1);
  border-radius: 9px;
  cursor: pointer;
  transition: transform var(--t-fast), background var(--t-fast), border-color var(--t-fast);
}

.notification-item:hover {
  transform: translateX(-2px);
  color: var(--text-1);
  background: rgba(18, 32, 54, 0.72);
  border-color: rgba(96, 165, 250, 0.28);
}

.notification-item.unread {
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.11), rgba(10, 16, 30, 0.82));
  border-color: rgba(96, 165, 250, 0.2);
}

.signal-rail {
  position: absolute;
  inset: 0 auto 0 0;
  width: 2px;
  background: var(--accent-hi);
  box-shadow: 0 0 10px rgba(96, 165, 250, 0.5);
}

.level-warn .signal-rail {
  background: var(--warn);
  box-shadow: 0 0 10px rgba(251, 191, 36, 0.45);
}

.level-error .signal-rail {
  background: var(--down);
  box-shadow: 0 0 10px rgba(248, 113, 113, 0.45);
}

.item-content {
  display: block;
  min-width: 0;
}

.item-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--text-2);
  font-size: 10.25px;
}

.item-meta time {
  white-space: nowrap;
}

.kind-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 650;
}

.kind-alert {
  color: #fcd34d;
}
.kind-event {
  color: #67e8f9;
}
.kind-job {
  color: #c4b5fd;
}
.kind-system {
  color: #93c5fd;
}

.item-title,
.item-body,
.item-ref {
  display: block;
}

.item-title {
  margin-top: 6px;
  color: var(--text-1);
  font-size: 12.75px;
  font-weight: 620;
  line-height: 1.45;
}

.item-title i {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin: 0 5px 2px 0;
  border-radius: 50%;
  background: var(--accent-hi);
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.65);
}

.item-body {
  display: -webkit-box;
  margin-top: 4px;
  overflow: hidden;
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.item-ref {
  margin-top: 7px;
  color: var(--text-2);
  font-size: 10px;
}

.notification-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-2);
  text-align: center;
}

.notification-empty strong {
  color: var(--text-2);
  font-size: 12.5px;
  font-weight: 600;
}

.notification-empty span {
  max-width: 230px;
  font-size: 11.5px;
  line-height: 1.6;
}

.notification-skeleton {
  height: 104px;
  margin-bottom: 6px;
  padding: 14px;
  border: 1px solid var(--line-1);
  border-radius: 9px;
}

.notification-skeleton .skeleton {
  display: block;
  height: 9px;
  margin-bottom: 11px;
}

.notification-skeleton .skeleton:nth-child(1) {
  width: 34%;
}
.notification-skeleton .skeleton:nth-child(2) {
  width: 72%;
}
.notification-skeleton .skeleton:nth-child(3) {
  width: 92%;
}

.drawer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 14px;
  color: var(--text-2);
  border-top: 1px solid var(--line-1);
  background: rgba(4, 7, 15, 0.42);
  font-size: 10.5px;
}

.drawer-foot > span,
.drawer-foot button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.drawer-foot > span i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--up);
  box-shadow: 0 0 7px rgba(52, 211, 153, 0.5);
}

.drawer-foot button {
  font-size: 10.5px;
}

.spin {
  animation: notification-spin 0.9s linear infinite;
}

.notification-drawer-enter-active,
.notification-drawer-leave-active {
  transition: opacity var(--t-med);
}

.notification-drawer-enter-active .notification-drawer,
.notification-drawer-leave-active .notification-drawer {
  transition: transform var(--t-med), opacity var(--t-med);
}

.notification-drawer-enter-from,
.notification-drawer-leave-to {
  opacity: 0;
}

.notification-drawer-enter-from .notification-drawer,
.notification-drawer-leave-to .notification-drawer {
  opacity: 0;
  transform: translateX(24px);
}

@keyframes notification-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .notification-layer {
    padding: 0;
  }

  .notification-drawer {
    width: 100vw;
    height: 100dvh;
    border-radius: 0;
    border-block: 0;
    border-right: 0;
  }

  .drawer-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .kind-filters {
    width: 100%;
  }

  .mark-all {
    margin-left: 2px;
  }
}
</style>
