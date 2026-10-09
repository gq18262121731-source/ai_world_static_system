<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { alarmApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePresentationStore } from '@/stores/presentation'
import { useRealtimeStore } from '@/stores/realtime'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const realtime = useRealtimeStore()
const presentation = usePresentationStore()

const pendingAlarms = ref([])
const railRef = ref(null)
// 选中指示条的位置，随路由滑动而不是直接跳变
const indicator = ref({ top: 0, height: 0, ready: false })
let timer = null

const menus = computed(() =>
  router.options.routes
    .find((item) => item.path === '/')
    .children.filter((item) => !item.meta?.hidden)
    .filter((item) => !item.meta?.adminOnly || auth.isAdmin),
)

const activeKey = computed(() => `/${route.path.split('/')[1] || 'dashboard'}`)

const greeting = computed(() => {
  const hour = dayjs().hour()
  if (hour < 6) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

function syncIndicator() {
  const rail = railRef.value
  if (!rail) return
  const active = rail.querySelector('.rail-item.is-active')
  if (!active) return
  indicator.value = {
    top: active.offsetTop,
    height: active.offsetHeight,
    ready: true,
  }
}

async function loadAlarms() {
  // 数据未接入时告警接口直接返回空，铃铛上不会出现数字
  try {
    const data = await alarmApi.list({ status: 'pending', page_size: 8 })
    pendingAlarms.value = data.items
  } catch {
    pendingAlarms.value = []
  }
}

function logout() {
  realtime.disconnect()
  auth.logout()
  router.push({ name: 'login' })
}

watch(activeKey, () => requestAnimationFrame(syncIndicator))
watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (latest && ['alarm', 'alarm_updated'].includes(latest.event)) loadAlarms()
  },
)
onMounted(() => {
  realtime.connect()
  // 数据接入那一步起，确保演示模拟器在跑（幂等）
  presentation.syncSimulator()
  loadAlarms()
  requestAnimationFrame(syncIndicator)
  window.addEventListener('resize', syncIndicator)
  timer = setInterval(loadAlarms, 30000)
})
onUnmounted(() => {
  realtime.disconnect()
  window.removeEventListener('resize', syncIndicator)
  clearInterval(timer)
})
</script>

<template>
  <div class="shell">
    <!-- 图标导航条：不用常规的竖列文字菜单 -->
    <aside class="rail">
      <router-link to="/dashboard" class="mark" v-ripple="'light'">
        <el-icon :size="22"><Sunny /></el-icon>
      </router-link>

      <nav ref="railRef" class="rail-nav">
        <span
          v-show="indicator.ready"
          class="rail-indicator"
          :style="{ top: `${indicator.top}px`, height: `${indicator.height}px` }"
        />
        <router-link
          v-for="menu in menus"
          :key="menu.path"
          :to="`/${menu.path}`"
          class="rail-item"
          :class="{ 'is-active': activeKey === `/${menu.path}` }"
          v-ripple
        >
          <span class="rail-icon">
            <el-icon :size="20"><component :is="menu.meta.icon" /></el-icon>
          </span>
          <span class="rail-label">{{ menu.meta.title }}</span>
        </router-link>
      </nav>

      <div class="rail-foot">
        <div class="date-chip">
          <span class="date-day">{{ dayjs().format('D') }}</span>
          <span class="date-month">{{ dayjs().format('M月') }}</span>
        </div>
      </div>
    </aside>

    <div class="stage">
      <header class="topbar">
        <div class="topbar-left">
          <h1 class="topbar-title">{{ route.meta.title }}</h1>
          <span class="topbar-hello">
            {{ greeting }}，{{ auth.user?.real_name || auth.user?.username }}
          </span>
        </div>

        <div class="topbar-right">
          <span v-if="presentation.dormant" class="pulse-chip">
            <i class="pulse-dot"></i>
            等待数据接入
          </span>
          <span v-else class="pulse-chip" :class="{ on: realtime.connected }">
            <i class="pulse-dot" :class="{ breathing: realtime.connected }"></i>
            {{ realtime.connected ? '实时守护中' : '连接已断开' }}
          </span>

          <el-popover placement="bottom-end" :width="330" trigger="click">
            <template #reference>
              <button
                class="bell"
                :class="{ alerting: pendingAlarms.length }"
                v-ripple
                aria-label="待处理告警"
              >
                <el-icon :size="19"><Bell /></el-icon>
                <span v-if="pendingAlarms.length" class="bell-count">
                  {{ pendingAlarms.length > 99 ? '99+' : pendingAlarms.length }}
                </span>
              </button>
            </template>
            <div class="pop-head">
              待处理告警
              <el-button link type="primary" size="small" @click="router.push('/settings/alarms')">
                全部处理
              </el-button>
            </div>
            <div v-if="pendingAlarms.length" class="pop-list rise-stagger">
              <div
                v-for="alarm in pendingAlarms"
                :key="alarm.id"
                class="pop-item pressable"
                v-ripple
                @click="router.push('/settings/alarms')"
              >
                <el-tag
                  size="small"
                  :type="{ info: 'info', warning: 'warning', critical: 'danger' }[alarm.level]"
                >
                  {{ { info: '提示', warning: '注意', critical: '紧急' }[alarm.level] }}
                </el-tag>
                <div>
                  <div class="pop-title">{{ alarm.title }}</div>
                  <div class="muted">{{ dayjs(alarm.triggered_at).format('MM-DD HH:mm') }}</div>
                </div>
              </div>
            </div>
            <el-empty v-else description="一切平安，没有待处理告警" :image-size="58" />
          </el-popover>

          <el-dropdown @command="logout">
            <div class="account pressable" v-ripple>
              <span class="avatar">
                {{ (auth.user?.real_name || auth.user?.username || '?').slice(0, 1) }}
              </span>
              <span class="account-text">
                <span class="account-name">{{ auth.user?.real_name || auth.user?.username }}</span>
                <span class="account-role">{{ auth.roleLabel }}</span>
              </span>
              <el-icon class="account-caret"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="stage-body">
        <!-- 页面永远正常挂载；数据未接入时遥测接口在 api 层直接给空结果，各处显示 -- 与空态 -->
        <div class="live-stage">
          <router-view v-slot="{ Component }">
            <transition name="page" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--canvas);
  gap: 0;
}

/* ---------- 图标导航条 ---------- */

.rail {
  width: 96px;
  flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 0 14px;
}

.mark {
  width: 46px;
  height: 46px;
  border-radius: 16px;
  background: linear-gradient(140deg, var(--brand), #4ec2ac);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 16px rgba(47, 158, 147, 0.32);
  margin-bottom: 20px;
  transition: transform var(--dur) var(--spring);
}

.mark:hover {
  transform: rotate(-8deg) scale(1.06);
}

.rail-nav {
  position: relative;
  flex: 1;
  width: 100%;
  padding: 0 10px;
}

/* 跟随选中项滑动的指示块，替代静态高亮 */
.rail-indicator {
  position: absolute;
  left: 10px;
  right: 10px;
  border-radius: 18px;
  background: linear-gradient(150deg, var(--brand), #45b9a4);
  box-shadow: 0 8px 18px rgba(47, 158, 147, 0.3);
  transition:
    top 0.42s var(--spring),
    height 0.42s var(--spring);
  z-index: 0;
}

.rail-item {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 11px 4px 10px;
  border-radius: 18px;
  text-decoration: none;
  color: var(--ink-soft);
  margin-bottom: 6px;
  transition: color var(--dur) var(--ease-out), transform var(--dur-fast) var(--spring-snap);
}

.rail-item:hover {
  color: var(--brand);
}

.rail-item:active {
  transform: scale(0.94);
}

.rail-item.is-active {
  color: #fff;
}

.rail-icon {
  display: flex;
  transition: transform var(--dur) var(--spring);
}

.rail-item:hover .rail-icon {
  transform: translateY(-2px) scale(1.08);
}

.rail-label {
  font-size: 13.5px;
  font-weight: 500;
  letter-spacing: 0.2px;
  white-space: nowrap;
}

.rail-foot {
  padding-top: 10px;
}

.date-chip {
  width: 52px;
  height: 52px;
  border-radius: 18px;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  line-height: 1.1;
}

.date-day {
  font-size: 19px;
  font-weight: 700;
}

.date-month {
  font-size: 12px;
}

/* ---------- 顶栏 ---------- */

.stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 74px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 26px;
  gap: 16px;
}

.topbar-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.topbar-title {
  font-size: 21px;
  font-weight: 600;
  margin: 0;
  letter-spacing: 0.4px;
}

.topbar-hello {
  font-size: 15px;
  color: var(--ink-soft);
  white-space: nowrap;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pulse-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14.5px;
  color: var(--ink-soft);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  padding: 7px 14px;
}

.pulse-chip.on {
  color: var(--ok);
  border-color: var(--ok-soft);
  background: var(--ok-soft);
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.bell {
  position: relative;
  width: 42px;
  height: 42px;
  border-radius: 15px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink-mid);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    transform var(--dur-fast) var(--spring-snap),
    background-color var(--dur) var(--ease-out),
    color var(--dur) var(--ease-out);
}

.bell:hover {
  background: var(--brand-soft);
  color: var(--brand);
  transform: translateY(-2px);
}

.bell:active {
  transform: scale(0.93);
}

.bell.alerting {
  color: var(--danger);
  background: var(--danger-soft);
  border-color: transparent;
  animation: bell-shake 3.4s var(--ease-out) infinite;
}

@keyframes bell-shake {
  0%, 88%, 100% { transform: rotate(0); }
  90% { transform: rotate(-11deg); }
  93% { transform: rotate(9deg); }
  96% { transform: rotate(-5deg); }
}

.bell-count {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 19px;
  height: 19px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--danger);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid var(--canvas);
}

.pop-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15.5px;
  font-weight: 600;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line-soft);
}

.pop-list {
  max-height: 320px;
  overflow-y: auto;
}

.pop-item {
  display: flex;
  gap: 9px;
  padding: 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  align-items: flex-start;
}

.pop-item:hover {
  background: var(--surface-tint);
}

.pop-title {
  font-size: 15.5px;
  line-height: 1.5;
}

.account {
  display: flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
  padding: 5px 12px 5px 5px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  border: 1px solid var(--line);
}

.account:hover {
  border-color: var(--brand-soft);
  background: var(--brand-tint);
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(140deg, var(--brand), #57c2b1);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 15.5px;
}

.account-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.account-name {
  font-size: 15px;
  font-weight: 500;
}

.account-role {
  font-size: 13px;
  color: var(--ink-soft);
}

.account-caret {
  color: var(--ink-soft);
  font-size: 14px;
}

.stage-body {
  flex: 1;
  overflow-y: auto;
}

.live-stage {
  min-height: 100%;
}

.page-enter-active,
.page-leave-active {
  transition: opacity 0.2s var(--ease-out), transform 0.24s var(--spring);
}

.page-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 860px) {
  .rail {
    width: 68px;
  }
  .rail-label,
  .rail-foot,
  .account-text,
  .pulse-chip {
    display: none;
  }
  .topbar {
    padding: 0 16px;
  }
}
</style>
