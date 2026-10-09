<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { elderApi, healthApi } from '@/api'
import { usePresentationStore } from '@/stores/presentation'
import { useRealtimeStore } from '@/stores/realtime'
import ScoreRing from '@/components/ScoreRing.vue'
import ScoreDormant from '@/components/ScoreDormant.vue'

const props = defineProps({
  // 轮询间隔，作为 WebSocket 推送的兜底
  pollInterval: { type: Number, default: 10000 },
  points: { type: Number, default: 30 },
  // 嵌入 Bento 卡片时去掉外层卡片样式，避免双重边框
  embedded: { type: Boolean, default: false },
  // 总览页可限制展示人数；不传时仍展示全部老人
  maxItems: { type: Number, default: null },
  // 指定展示名单及顺序，未指定时沿用接口返回顺序
  elderNames: { type: Array, default: null },
})

const router = useRouter()
const realtime = useRealtimeStore()
const presentation = usePresentationStore()

const snapshots = ref([])
const loading = ref(false)
// elder_id -> 时间戳，用于给刚刚更新的卡片加高亮动画
const flashing = ref({})
let timer = null

const RISK_META = {
  normal: { label: '平稳', type: 'success' },
  warning: { label: '需留意', type: 'warning' },
  danger: { label: '异常', type: 'danger' },
  unknown: { label: '无数据', type: 'info' },
  pending: { label: '未接入', type: 'info' },
}

const live = computed(() => presentation.dataLive)
const visibleSnapshots = computed(() => {
  const selected = props.elderNames == null
    ? snapshots.value
    : props.elderNames
      .map((name) => snapshots.value.find((item) => item.elder_name === name))
      .filter(Boolean)
  return props.maxItems == null ? selected : selected.slice(0, props.maxItems)
})

/** 数据未接入：每位老人一张卡，名字照常，体征都是 --。 */
function placeholderSnapshot(elder) {
  return {
    elder_id: elder.id,
    elder_name: elder.name,
    risk_level: 'pending',
    body_statuses: [],
    vitals: {},
    series: [],
    abnormal_items: [],
    seconds_since_update: null,
    latest_at: null,
    is_stale: false,
    watch_online: null,
    dog_online: null,
    steps_today: null,
    watch_battery: null,
    health_score: null,
  }
}

const VITAL_ORDER = ['heart_rate', 'spo2', 'systolic', 'temperature']

function format(key, value) {
  if (value === null || value === undefined) return '--'
  return key === 'temperature' ? Number(value).toFixed(1) : Math.round(value)
}

const VITAL_LABEL = { heart_rate: '心率', spo2: '血氧', systolic: '血压', temperature: '体温' }
const VITAL_UNIT = { heart_rate: '次/分', spo2: '%', systolic: '', temperature: '℃' }

function displayVital(snapshot, key) {
  const stat = snapshot.vitals?.[key]
  if (!stat) return { label: VITAL_LABEL[key], text: '--', unit: VITAL_UNIT[key], status: 'unknown' }
  if (key === 'systolic') {
    const dia = snapshot.vitals.diastolic
    const diaText = dia?.latest != null ? `/${format('diastolic', dia.latest)}` : ''
    return {
      label: '血压',
      text: `${format(key, stat.latest)}${diaText}`,
      // 标签已说明是血压，省略 mmHg 让四项指标在窄屏也能一行排下
      unit: '',
      status: stat.status === 'normal' && dia?.status !== 'normal' ? dia.status : stat.status,
    }
  }
  return {
    label: stat.label,
    text: format(key, stat.latest),
    unit: stat.unit,
    status: stat.status,
  }
}

/** 把心率序列画成迷你折线，直观体现"数据在动"。 */
function sparkline(snapshot, key = 'heart_rate') {
  const values = (snapshot.series || [])
    .map((p) => p[key])
    .filter((v) => v !== null && v !== undefined)
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100
      const y = 92 - ((value - min) / span) * 84
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

function relativeTime(seconds) {
  if (seconds === null || seconds === undefined) return '暂无数据'
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${Math.floor(seconds / 3600)} 小时前`
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    if (!live.value) {
      if (!snapshots.value.length) snapshots.value = (await elderApi.list()).map(placeholderSnapshot)
      return
    }
    snapshots.value = await healthApi.live(props.points)
  } finally {
    loading.value = false
  }
}

/**
 * 收到 WebSocket 推送时先就地更新数值（保证"实时"观感），
 * 再拉一次接口对齐后端的状态判定结果。
 */
function applyPush(elderId, data) {
  const snapshot = snapshots.value.find((item) => item.elder_id === elderId)
  if (!snapshot) {
    load(true)
    return
  }
  for (const [key, value] of Object.entries({
    heart_rate: data.heart_rate,
    spo2: data.spo2,
    systolic: data.systolic,
    diastolic: data.diastolic,
    temperature: data.temperature,
  })) {
    if (value !== null && value !== undefined && snapshot.vitals[key]) {
      snapshot.vitals[key].latest = value
    }
  }
  if (data.steps !== null && data.steps !== undefined) snapshot.steps_today = data.steps
  if (data.battery !== null && data.battery !== undefined) snapshot.watch_battery = data.battery
  snapshot.latest_at = data.recorded_at
  snapshot.seconds_since_update = 0
  snapshot.is_stale = false
  snapshot.series = [
    ...(snapshot.series || []).slice(-(props.points - 1)),
    {
      ts: data.recorded_at,
      heart_rate: data.heart_rate,
      spo2: data.spo2,
      systolic: data.systolic,
    },
  ]
  flashing.value = { ...flashing.value, [elderId]: Date.now() }
  setTimeout(() => {
    const next = { ...flashing.value }
    delete next[elderId]
    flashing.value = next
  }, 1200)
}

watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (!latest) return
    if (latest.event === 'health_record') {
      applyPush(latest.elder_id, latest.data)
    } else if (['outing_started', 'outing_ended', 'device_status'].includes(latest.event)) {
      load(true)
    }
  },
)

const hasLiveSource = computed(() => visibleSnapshots.value.some((item) => !item.is_stale))

onMounted(() => {
  load()
  timer = setInterval(() => load(true), props.pollInterval)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div :class="embedded ? 'embedded' : 'card'">
    <h3 class="vitals-title">
      <span class="title-icon"><el-icon :size="16"><Odometer /></el-icon></span>
      老人实时体征
      <el-tag :type="realtime.connected ? 'success' : 'info'" size="small" effect="plain">
        {{ !live ? '未接入' : realtime.connected ? '实时推送中' : '轮询模式' }}
      </el-tag>
      <span class="muted" style="margin-left: auto; font-weight: 400">
        {{ live ? `每 ${pollInterval / 1000} 秒自动刷新` : '等待手表数据接入' }}
      </span>
    </h3>

    <el-alert
      v-if="live && visibleSnapshots.length && !hasLiveSource"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      当前没有设备在持续上报数据。前往
      <el-button link type="primary" @click="router.push('/settings/simulator')">
        设备模拟器
      </el-button>
      切换健康档位，或启动手表模拟器进程，即可看到数值实时变化。
    </el-alert>

    <div v-loading="loading && !snapshots.length" class="live-grid">
      <div
        v-for="snapshot in visibleSnapshots"
        :key="snapshot.elder_id"
        class="live-card liftable"
        :class="[`risk-${snapshot.risk_level}`, { flash: flashing[snapshot.elder_id], stale: snapshot.is_stale }]"
        v-ripple
        @click="router.push(`/elders/${snapshot.elder_id}`)"
      >
        <div class="live-head">
          <div class="name">{{ snapshot.elder_name }}</div>
          <el-tag size="small" :type="RISK_META[snapshot.risk_level]?.type">
            {{ RISK_META[snapshot.risk_level]?.label }}
          </el-tag>
          <el-tag v-if="snapshot.active_task_id" size="small" type="success" effect="plain">
            外出中
          </el-tag>
          <ScoreDormant v-if="presentation.scoreDormant" class="head-score" compact :size="52" />
          <ScoreRing
            v-else
            class="head-score"
            :score="snapshot.health_score"
            :grade="snapshot.health_grade"
            :delta="snapshot.score_delta"
            :reference="snapshot.score_reference"
            :size="52"
            :stroke="5"
          />
        </div>

        <div class="vital-row">
          <div v-for="key in VITAL_ORDER" :key="key" class="vital">
            <div class="vital-label">{{ displayVital(snapshot, key).label }}</div>
            <div class="vital-value" :class="`status-${displayVital(snapshot, key).status}`">
              {{ displayVital(snapshot, key).text }}
              <small>{{ displayVital(snapshot, key).unit }}</small>
            </div>
          </div>
        </div>

        <svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none">
          <polyline
            v-if="sparkline(snapshot)"
            :points="sparkline(snapshot)"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            vector-effect="non-scaling-stroke"
          />
        </svg>

        <div class="live-foot">
          <span :class="{ 'status-warning': snapshot.is_stale }">
            {{ live ? `${relativeTime(snapshot.seconds_since_update)}更新` : '等待数据接入' }}
          </span>
          <span v-if="snapshot.latest_at" class="muted">
            {{ dayjs(snapshot.latest_at).format('HH:mm:ss') }}
          </span>
          <span class="chips">
            <el-tag size="small" :type="snapshot.watch_online ? 'success' : 'info'" effect="plain">
              手表{{ snapshot.watch_online == null ? '未接入' : snapshot.watch_online ? '在线' : '离线' }}
            </el-tag>
            <el-tag size="small" :type="snapshot.dog_online ? 'success' : 'info'" effect="plain">
              机器狗{{ snapshot.dog_online == null ? '未接入' : snapshot.dog_following ? '跟随中' : snapshot.dog_online ? '待命' : '离线' }}
            </el-tag>
          </span>
        </div>

        <!-- 身体状态及异常反馈与健康评分同步揭示，数据接入阶段先只展示实时体征。 -->
        <template v-if="presentation.scoreLive">
          <div v-if="snapshot.body_statuses?.length" class="status-list">
            <el-tag
              v-for="status in snapshot.body_statuses"
              :key="status"
              size="small"
              :type="status === '健康平稳' ? 'success' : status === '疲劳缺觉' ? 'warning' : 'danger'"
              effect="light"
            >
              {{ status }}
            </el-tag>
          </div>

          <div v-if="snapshot.abnormal_items.length" class="abnormal">
            {{ snapshot.abnormal_items.join('；') }}
          </div>
          <div v-else class="extra muted">
            今日步数 {{ snapshot.steps_today ?? '--' }} · 手表电量 {{ snapshot.watch_battery ?? '--' }}%
          </div>
        </template>
      </div>
    </div>

    <el-empty v-if="!loading && !visibleSnapshots.length" description="暂无老人档案" :image-size="60" />
  </div>
</template>

<style scoped>
.embedded {
  padding: 20px;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.vitals-title {
  font-size: 16.5px;
  font-weight: 600;
  margin: 0 0 15px;
  display: flex;
  align-items: center;
  gap: 9px;
}

.title-icon {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.live-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(258px, 1fr));
  gap: 14px;
  flex: 1;
  align-content: start;
}

.status-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 9px;
}

.live-card {
  position: relative;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px;
  cursor: pointer;
  color: var(--brand);
  background: var(--surface);
  overflow: hidden;
}

/* 顶部一道渐隐色条表示风险等级，比整条左边框更轻盈 */
.live-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, currentColor, transparent 78%);
}

.live-card.risk-normal {
  color: var(--ok);
}

.live-card.risk-warning {
  color: var(--warn);
}

.live-card.risk-danger {
  color: var(--danger);
}

.live-card.risk-unknown {
  color: var(--ink-soft);
}

.live-card.stale {
  background: #fafbfc;
}

.live-card.flash {
  background: var(--brand-soft);
}

.live-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.name {
  font-size: 16.5px;
  font-weight: 600;
  color: var(--ink);
}

.head-score {
  margin-left: auto;
}

/* 2x2 而非一行四项: 数值可以放大，阅读更轻松 */
.vital-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
}

.vital-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-mid);
}

.vital-value {
  font-size: 21px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--ink);
  white-space: nowrap;
}

.vital-value small {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-mid);
  margin-left: 3px;
}

.vital-value.status-warning {
  color: var(--warn);
}

.vital-value.status-danger {
  color: var(--danger);
}

.spark {
  width: 100%;
  height: 38px;
  margin: 10px 0 8px;
  display: block;
}

.live-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ink-soft);
  flex-wrap: wrap;
}

.chips {
  margin-left: auto;
  display: flex;
  gap: 4px;
}

.abnormal {
  margin-top: 10px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--accent);
  background: var(--warn-soft);
  border-radius: 6px;
  padding: 7px 9px;
}

.extra {
  margin-top: 10px;
  font-size: 14px;
}
</style>
