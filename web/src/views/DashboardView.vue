<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { dashboardApi, elderApi } from '@/api'
import { usePresentationStore } from '@/stores/presentation'
import BentoCard from '@/components/BentoCard.vue'
import MetricTile from '@/components/MetricTile.vue'
import RealtimeFeed from '@/components/RealtimeFeed.vue'
import LiveVitals from '@/components/LiveVitals.vue'

const router = useRouter()
const presentation = usePresentationStore()
const overview = ref(null)
const trend = ref([])
const elderTotal = ref(null)
const loading = ref(false)
let timer = null
const DASHBOARD_ELDER_NAMES = ['张三', '李四', '王五']
const DASHBOARD_ELDER_LIMIT = DASHBOARD_ELDER_NAMES.length

// 数据未接入：卡片都在，遥测数字全是 --，只有在册老人这类档案数照常
const EMPTY_OVERVIEW = {
  elder_total: null,
  device_online: null,
  device_total: null,
  outing_today: null,
  outing_active: null,
  alarm_pending: null,
  alarm_today: null,
  decision_distribution: {},
  recent_alarms: [],
  mqtt: null,
  ai: null,
}
const live = computed(() => presentation.dataLive)
const view = computed(() => {
  const data = live.value ? overview.value : { ...EMPTY_OVERVIEW, elder_total: elderTotal.value }
  if (!data) return data
  return {
    ...data,
    elder_total: data.elder_total == null ? null : Math.min(data.elder_total, DASHBOARD_ELDER_LIMIT),
  }
})
const dash = (value, fallback = '--') => (value ?? fallback)

const LEVEL_TYPE = { info: 'info', warning: 'warning', critical: 'danger' }
const LEVEL_LABEL = { info: '提示', warning: '注意', critical: '紧急' }
const STATUS_LABEL = {
  pending: '待处理',
  confirming: '语音确认中',
  acked: '已受理',
  resolved: '已处置',
  false_alarm: '误报',
}
const DECISION_LABEL = { allow: '允许外出', conditional: '有条件允许', deny: '不建议外出' }
const DECISION_COLOR = { allow: 'var(--ok)', conditional: 'var(--warn)', deny: 'var(--danger)' }

async function load() {
  loading.value = true
  try {
    const [overviewData, trendData] = await Promise.all([
      dashboardApi.overview(),
      dashboardApi.outingTrend(7),
    ])
    overview.value = overviewData
    trend.value = trendData
    if (!live.value && elderTotal.value == null) {
      elderTotal.value = (await elderApi.list()).length
    }
  } finally {
    loading.value = false
  }
}

const trendMax = computed(() =>
  Math.max(1, ...trend.value.map((d) => d.allow + d.conditional + d.deny)),
)

const decisionTotal = computed(() => {
  const dist = view.value?.decision_distribution || {}
  return Object.values(dist).reduce((sum, value) => sum + value, 0)
})

function decisionPercent(key) {
  if (!decisionTotal.value) return 0
  return Math.round(((view.value.decision_distribution[key] || 0) / decisionTotal.value) * 100)
}

onMounted(() => {
  load()
  timer = setInterval(load, 20000)
})
onUnmounted(() => clearInterval(timer))

/** 用一句话概括平台此刻的状态，作为主视觉卡的核心信息。 */
const headline = computed(() => {
  if (!live.value) {
    return { text: '数据尚未接入', sub: '平台已就位，等待手表与机器狗的数据接入', tone: 'idle' }
  }
  if (!overview.value) return { text: '正在读取守护状态…', tone: 'idle' }
  if (overview.value.alarm_pending) {
    return {
      text: `有 ${overview.value.alarm_pending} 条告警等待处理`,
      sub: '请尽快确认老人当前是否安全',
      tone: 'alert',
    }
  }
  if (overview.value.outing_active) {
    return {
      text: `${overview.value.outing_active} 位老人正在外出`,
      sub: '机器狗跟随中，位置与体征持续回传',
      tone: 'active',
    }
  }
  return {
    text: '所有老人都平安在家',
    sub: '设备在线，健康数据正常回传',
    tone: 'calm',
  }
})
</script>

<template>
  <div class="page" v-loading="loading && live && !overview">
    <div class="bento-grid rise-stagger">
      <!-- 顶部总览横排：主视觉与四个操作指标卡同处第一行 -->
      <div class="overview-strip">
        <BentoCard tone="brand" class="hero">
          <div class="hero-top">
            <span class="hero-badge">
              <i class="hero-dot breathing"></i>
              {{ dayjs().format('M月D日 HH:mm') }}
            </span>
          </div>

          <div class="hero-main">
            <div class="hero-eyebrow">此刻</div>
            <div class="hero-headline">{{ headline.text }}</div>
            <div class="hero-sub">{{ headline.sub }}</div>
          </div>

          <div class="hero-stats">
            <div class="hero-stat">
              <span class="hero-num">{{ dash(view?.elder_total) }}</span>
              <span class="hero-cap">在管老人</span>
            </div>
            <div class="hero-stat">
              <span class="hero-num">
                {{ dash(view?.device_online) }}<em>/{{ dash(view?.device_total) }}</em>
              </span>
              <span class="hero-cap">设备在线</span>
            </div>
            <div class="hero-stat">
              <span class="hero-num">{{ dash(view?.outing_today) }}</span>
              <span class="hero-cap">今日外出</span>
            </div>
          </div>

          <svg class="hero-arc" viewBox="0 0 400 200" preserveAspectRatio="none">
            <path d="M-20 170 Q 110 60 220 130 T 430 70" fill="none" stroke="#fff" stroke-width="1.4" opacity="0.28" />
            <path d="M-20 195 Q 130 100 250 160 T 430 110" fill="none" stroke="#fff" stroke-width="1.4" opacity="0.16" />
          </svg>
        </BentoCard>

        <div class="lead-metrics">
          <MetricTile
            class="metric"
            label="进行中的外出"
            :value="live ? (overview?.outing_active ?? 0) : '--'"
            suffix="次"
            icon="Guide"
            tone="ok"
            hint="点击查看外出与轨迹"
            clickable
            @click="router.push('/outings')"
          />
          <MetricTile
            class="metric"
            label="待处理告警"
            :value="live ? (overview?.alarm_pending ?? 0) : '--'"
            suffix="条"
            icon="Warning"
            :tone="overview?.alarm_pending ? 'danger' : 'ok'"
            :hint="live ? `今日累计 ${overview?.alarm_today ?? 0} 条` : '等待数据接入'"
            clickable
            @click="router.push('/settings/alarms')"
          />
          <MetricTile
            class="metric"
            label="累计外出评估"
            :value="live ? decisionTotal : '--'"
            suffix="次"
            icon="ChatDotRound"
            tone="brand"
            hint="点击进入智能体运营台"
            clickable
            @click="router.push('/agent')"
          />
          <MetricTile
            class="metric"
            label="设备模拟器"
            :value="live ? (overview?.device_total ?? 0) : '--'"
            suffix="台"
            icon="Monitor"
            tone="warn"
            hint="造数据、说话、触发跌倒"
            clickable
            @click="router.push('/settings/simulator')"
          />
        </div>
      </div>

      <!-- 老人卡片独占第二行，张三、李四、王五横向排满一行 -->
      <BentoCard :span="12" :padded="false" class="vitals-wrap">
        <LiveVitals
          :poll-interval="10000"
          :points="30"
          :max-items="DASHBOARD_ELDER_LIMIT"
          :elder-names="DASHBOARD_ELDER_NAMES"
          embedded
        />
      </BentoCard>

      <!-- 决策趋势 -->
      <BentoCard title="近 7 天外出决策" icon="TrendCharts" :span="5" hint="悬停查看当日次数">
        <div v-if="!trend.length" class="trend-empty muted">{{ live ? '近 7 天还没有外出评估' : '等待数据接入' }}</div>
        <div v-else class="trend">
          <div v-for="day in trend" :key="day.date" class="trend-col">
            <div class="trend-bars">
              <div
                v-for="key in ['allow', 'conditional', 'deny']"
                :key="key"
                class="trend-bar"
                :class="key"
                :style="{ height: `${(day[key] / trendMax) * 100}%` }"
                :title="`${DECISION_LABEL[key]} ${day[key]} 次`"
              />
            </div>
            <span class="trend-label">{{ dayjs(day.date).format('MM-DD') }}</span>
          </div>
        </div>
        <div class="legend">
          <span v-for="key in ['allow', 'conditional', 'deny']" :key="key">
            <i :style="{ background: DECISION_COLOR[key] }"></i>{{ DECISION_LABEL[key] }}
          </span>
        </div>

        <template v-if="decisionTotal">
          <div class="dist">
            <div v-for="key in ['allow', 'conditional', 'deny']" :key="key" class="dist-row">
              <span class="dist-label">{{ DECISION_LABEL[key] }}</span>
              <span class="dist-track">
                <i
                  :style="{
                    width: `${decisionPercent(key)}%`,
                    background: DECISION_COLOR[key],
                  }"
                />
              </span>
              <span class="dist-count">{{ view.decision_distribution[key] || 0 }}</span>
            </div>
          </div>
        </template>
      </BentoCard>

      <!-- 实时事件流 -->
      <BentoCard title="实时事件流" icon="Bell" :span="4">
        <RealtimeFeed :limit="18" />
      </BentoCard>

      <!-- 运行环境 -->
      <BentoCard v-if="view" title="运行状态" icon="Cpu" :span="3" tone="tint">
        <div class="env">
          <div class="env-row">
            <span class="env-key">MQTT</span>
            <span class="env-val">
              <i class="env-dot" :class="{ on: view.mqtt?.connected }"></i>
              {{ view.mqtt ? (view.mqtt.connected ? '已连接' : '未连接') : '等待接入' }}
            </span>
          </div>
          <div class="env-row">
            <span class="env-key">收 / 发</span>
            <span class="env-val">{{ dash(view.mqtt?.received) }} / {{ dash(view.mqtt?.published) }}</span>
          </div>
          <div class="env-row">
            <span class="env-key">大模型</span>
            <span class="env-val">{{ dash(view.ai?.llm?.provider) }}</span>
          </div>
          <div class="env-row">
            <span class="env-key">语音识别</span>
            <span class="env-val">{{ dash(view.ai?.asr?.provider) }}</span>
          </div>
          <div class="env-row">
            <span class="env-key">语音合成</span>
            <span class="env-val">{{ dash(view.ai?.tts?.provider) }}</span>
          </div>
          <div class="env-row">
            <span class="env-key">模型降级</span>
            <span class="env-val">{{ dash(view.ai?.llm?.fallback_count) }} 次</span>
          </div>
        </div>
      </BentoCard>

      <!-- 最近告警 -->
      <BentoCard v-if="view" title="最近告警" icon="WarnTriangleFilled" :span="12">
        <template #head>
          <el-button link type="primary" @click="router.push('/settings/alarms')">
            前往告警中心
          </el-button>
        </template>
        <el-table :data="view.recent_alarms" size="small" :empty-text="live ? '近期没有告警，一切平安' : '等待数据接入'">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">
              {{ dayjs(row.triggered_at).format('MM-DD HH:mm:ss') }}
            </template>
          </el-table-column>
          <el-table-column prop="title" label="告警" min-width="150" />
          <el-table-column prop="description" label="说明" min-width="300" show-overflow-tooltip />
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="LEVEL_TYPE[row.level]">
                {{ LEVEL_LABEL[row.level] || row.level }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">{{ STATUS_LABEL[row.status] || row.status }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="router.push({ name: 'alarms', query: { id: row.id } })"
              >
                处理
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </BentoCard>
    </div>
  </div>
</template>

<style scoped>
/* 12 栅格 + 不同跨度，构成不对称的 bento 布局 */
.bento-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 18px;
  grid-auto-rows: minmax(132px, auto);
}

.overview-strip {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(380px, 1.7fr) minmax(0, 3.3fr);
  gap: 18px;
  min-width: 0;
}

.overview-strip > .hero {
  grid-column: auto;
}

.lead-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
  min-width: 0;
}

.lead-metrics .metric {
  min-height: 220px;
  flex-direction: column;
  justify-content: center;
}

/* ---------- 主视觉卡 ---------- */

.hero {
  justify-content: flex-start;
  gap: 14px;
  min-height: 220px;
  padding: 20px;
}

.hero-arc {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 42%;
  pointer-events: none;
}

.hero-top,
.hero-main,
.hero-stats {
  position: relative;
  z-index: 1;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.18);
  border-radius: var(--radius-pill);
  padding: 6px 14px;
  font-size: 14.5px;
}

.hero-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #fff;
  display: inline-block;
  color: #fff;
}

.hero-main {
  margin: 2px 0 0;
}

.hero-eyebrow {
  font-size: 14.5px;
  opacity: 0.9;
  letter-spacing: 3px;
}

.hero-headline {
  font-size: clamp(21px, 2.1vw, 28px);
  font-weight: 600;
  line-height: 1.45;
  margin: 6px 0 8px;
  letter-spacing: 0.5px;
}

.hero-sub {
  font-size: 15.5px;
  opacity: 0.85;
  line-height: 1.7;
}

.hero-stats {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  margin-top: auto;
}

.hero-stat {
  display: flex;
  flex-direction: column;
}

.hero-num {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.hero-num em {
  font-style: normal;
  font-size: 16.5px;
  opacity: 0.85;
}

.hero-cap {
  font-size: 14px;
  opacity: 0.8;
  margin-top: 2px;
}

/* ---------- 趋势 ---------- */

.trend {
  display: flex;
  gap: 8px;
  height: 128px;
  align-items: flex-end;
}

.trend-empty {
  height: 128px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--line);
  border-radius: var(--radius);
}

.trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.trend-bars {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 3px;
}

.trend-bar {
  width: 9px;
  min-height: 3px;
  border-radius: 5px 5px 2px 2px;
  transition: transform var(--dur) var(--spring), filter var(--dur) var(--ease-out);
  transform-origin: bottom;
}

.trend-bar:hover {
  transform: scaleX(1.5);
  filter: brightness(1.08);
}

.trend-bar.allow {
  background: var(--ok);
}
.trend-bar.conditional {
  background: var(--warn);
}
.trend-bar.deny {
  background: var(--danger);
}

.trend-label {
  font-size: 13px;
  color: var(--ink-soft);
  margin-top: 7px;
}

.legend {
  display: flex;
  gap: 15px;
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 12px;
}

.legend i {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 3px;
  margin-right: 5px;
}

.dist {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed var(--line);
}

.dist-row {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 8px;
}

.dist-label {
  width: 74px;
  font-size: 14.5px;
  color: var(--ink-mid);
}

.dist-track {
  flex: 1;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--line-soft);
  overflow: hidden;
}

.dist-track i {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  transition: width 0.7s var(--spring);
}

.dist-count {
  width: 34px;
  text-align: right;
  font-size: 14px;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}

/* ---------- 运行状态 ---------- */

.env {
  display: grid;
  gap: 9px;
}

.env-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15px;
  padding: 7px 10px;
  background: var(--surface);
  border-radius: var(--radius-sm);
}

.env-key {
  color: var(--ink-soft);
}

.env-val {
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 6px;
}

.env-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ink-soft);
}

.env-dot.on {
  background: var(--ok);
}

@media (max-width: 1400px) {
  .overview-strip {
    grid-template-columns: minmax(320px, 0.9fr) minmax(0, 1.6fr);
  }

  .lead-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .lead-metrics .metric {
    min-height: 142px;
    flex-direction: row;
    justify-content: flex-start;
  }
}

@media (max-width: 1000px) {
  .overview-strip {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .bento-grid {
    grid-auto-rows: auto;
  }

  .lead-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
