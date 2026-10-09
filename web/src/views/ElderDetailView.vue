<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import {
  agentApi,
  deviceApi,
  elderApi,
  healthApi,
  outingApi,
} from '@/api'
import { usePresentationStore } from '@/stores/presentation'
import { useRealtimeStore } from '@/stores/realtime'
import VitalGrid from '@/components/VitalGrid.vue'
import VitalTrendChart from '@/components/VitalTrendChart.vue'
import DecisionPanel from '@/components/DecisionPanel.vue'
import TrackMap from '@/components/TrackMap.vue'
import RealtimeFeed from '@/components/RealtimeFeed.vue'
import ScoreRing from '@/components/ScoreRing.vue'
import ScoreDormant from '@/components/ScoreDormant.vue'

const route = useRoute()
const router = useRouter()
const realtime = useRealtimeStore()
const presentation = usePresentationStore()
const elderId = computed(() => Number(route.params.id))

const GRADE_TAG = { 优: 'success', 良: 'primary', 中: 'warning', 差: 'danger' }

function dimTone(value) {
  if (value == null) return 'none'
  if (value >= 85) return 'ok'
  if (value >= 60) return 'warn'
  return 'danger'
}

// 体征这类会持续变的数据轮询刷新，作为 WebSocket 推送的兜底
const LIVE_POLL_MS = 10000

const elder = ref(null)
const assessment = ref(null)
const summaries = ref([])
const stats = ref(null)
const ruleReport = ref(null)
const devices = ref(null)
const activeTask = ref(null)
const track = ref([])
const conversations = ref([])
const requests = ref([])
const loading = ref(false)
const updatedAt = ref(null)
let timer = null

// 健康评分随评估一起返回，评估每 10 秒刷新一次
const score = computed(() => assessment.value?.health_score || null)

// 四项体征默认同时展示；每个范围都固定为 6 分钟一个时间桶
const TREND_RANGES = [
  { hours: 1, label: '近 1 小时' },
  { hours: 6, label: '近 6 小时' },
  { hours: 24, label: '近 24 小时' },
]
const TREND_METRICS = ['heart_rate', 'spo2', 'temperature', 'systolic', 'diastolic']
const TREND_POINTS_PER_HOUR = 10
const trendHours = ref(24)
const trends = ref({})
const trendLoading = ref(false)
let trendRequest = 0

async function refreshTrend() {
  const id = elderId.value
  if (!id) return
  const ticket = ++trendRequest
  trendLoading.value = true
  try {
    const points = trendHours.value * TREND_POINTS_PER_HOUR
    const data = await Promise.all(
      TREND_METRICS.map((metric) => healthApi.trend(id, metric, trendHours.value, points)),
    )
    if (ticket === trendRequest) {
      trends.value = Object.fromEntries(TREND_METRICS.map((metric, index) => [metric, data[index]]))
    }
  } finally {
    if (ticket === trendRequest) trendLoading.value = false
  }
}

watch(trendHours, () => {
  trends.value = {}
  refreshTrend()
})

/** 会持续变化的部分：体征、规则判定、设备状态、外出轨迹。 */
async function refreshLive(silent = true) {
  const id = elderId.value
  if (!id) return
  if (!silent) loading.value = true
  try {
    const [assess, statsData, reportData, deviceData, taskData] = await Promise.all([
      healthApi.assessment(id),
      outingApi.stats(id),
      outingApi.ruleReport(id),
      deviceApi.snapshot(id),
      outingApi.activeTask(id),
      refreshTrend(),
    ])
    assessment.value = assess
    stats.value = statsData
    ruleReport.value = reportData
    devices.value = deviceData
    activeTask.value = taskData
    track.value = taskData ? await outingApi.track(taskData.id) : []
    updatedAt.value = new Date()
  } finally {
    if (!silent) loading.value = false
  }
}

/** 变化较慢的部分：档案、日摘要、对话与决策日志，只在进入页面或相关事件后取。 */
async function refreshContext() {
  const id = elderId.value
  if (!id) return
  const [elderData, summaryData, convData, requestData] = await Promise.all([
    elderApi.detail(id),
    healthApi.summaries(id, 7),
    agentApi.conversations(id, 5),
    outingApi.requests({ elder_id: id, page_size: 10 }),
  ])
  elder.value = elderData
  summaries.value = summaryData
  conversations.value = convData
  requests.value = requestData.items
}

async function load() {
  loading.value = true
  try {
    await Promise.all([refreshLive(), refreshContext()])
  } finally {
    loading.value = false
  }
}

const latestDecision = computed(() => requests.value.find((item) => item.decision)?.decision || null)

// 曲线说明：读数条数、剔除的坏读数、固定 6 分钟桶宽
function trendCaption(data, secondary = null) {
  if (!data || !data.points.length) return ''
  const parts = secondary
    ? [`收缩压 ${data.total} 条`, `舒张压 ${secondary.total} 条`]
    : [`${data.total} 条读数`]
  const dropped = data.dropped + (secondary?.dropped || 0)
  if (dropped) parts.push(`已剔除 ${dropped} 条疑似坏读数`)
  parts.push('每 6 分钟一个点')
  return parts.join(' · ')
}

const messages = computed(() =>
  conversations.value.flatMap((conv) => conv.messages || []).slice(-14).reverse(),
)

// 收到与本位老人相关的推送就立刻刷新，比等下一次轮询更及时
watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (!latest || latest.elder_id !== elderId.value) return

    if (latest.event === 'track_point' && activeTask.value) {
      // 轨迹点直接追加，不用为一个点重新拉整条轨迹
      track.value = [
        ...track.value,
        {
          ts: latest.data.ts,
          lat: latest.data.lat,
          lng: latest.data.lng,
          speed: latest.data.speed ?? 0,
          distance_from_origin_m: latest.data.distance_from_origin_m,
        },
      ]
      return
    }

    if (['health_record', 'alarm', 'alarm_updated', 'device_status'].includes(latest.event)) {
      refreshLive()
      return
    }
    if (
      ['outing_decision', 'outing_started', 'outing_ended', 'conversation'].includes(latest.event)
    ) {
      refreshLive()
      refreshContext()
    }
  },
)

watch(elderId, load)

onMounted(() => {
  realtime.connect()
  load()
  timer = setInterval(() => refreshLive(), LIVE_POLL_MS)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page" v-loading="loading && !elder">
    <div class="page-head">
      <div>
        <h2 class="page-title">
          {{ elder?.name || '健康看板' }}
          <el-tag v-if="elder" size="small" effect="plain">{{ elder.age }} 岁 · {{ elder.gender }}</el-tag>
          <el-tag v-if="elder?.live_alone" size="small" type="warning" effect="plain">独居</el-tag>
        </h2>
      </div>
      <div class="head-actions">
        <span class="live-hint" :class="{ on: realtime.connected }">
          <i class="live-dot" :class="{ breathing: realtime.connected }"></i>
          <template v-if="!presentation.dataLive">等待数据接入</template>
          <template v-else>
            {{ realtime.connected ? '实时更新中' : `每 ${LIVE_POLL_MS / 1000} 秒轮询` }}
            <template v-if="updatedAt">· {{ dayjs(updatedAt).format('HH:mm:ss') }}</template>
          </template>
        </span>
        <el-button @click="router.back()">返回</el-button>
        <el-button type="primary" :loading="loading" @click="refreshLive(false)">
          立即刷新
        </el-button>
      </div>
    </div>

    <div class="card vitals-card">
      <h3 class="card-title">
        当前体征
        <el-tag v-if="assessment?.latest_at" size="small" effect="plain">
          采集于 {{ dayjs(assessment.latest_at).format('MM-DD HH:mm') }}
        </el-tag>
        <el-tag
          v-if="presentation.scoreLive && score?.score != null"
          size="small"
          :type="GRADE_TAG[score.grade] || 'info'"
        >
          健康评分 {{ score.score }} · {{ score.grade }}
        </el-tag>
        <span class="muted card-hint">四项体征曲线同步展示</span>
      </h3>
      <VitalGrid
        :assessment="assessment"
        :empty-tag="presentation.dataLive ? '无数据' : '未接入'"
      />

      <div class="trend-head">
        <div class="trend-title">
          <span class="trend-name">生命体征曲线</span>
          <span class="muted">心率、血氧、体温、血压 · 每 6 分钟一个点</span>
        </div>
        <el-radio-group v-model="trendHours" size="small">
          <el-radio-button v-for="item in TREND_RANGES" :key="item.hours" :value="item.hours">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>
      </div>
      <div class="trend-grid">
        <section class="trend-panel">
          <div class="trend-panel-head">
            <b>心率曲线</b>
            <span class="muted">{{ trendCaption(trends.heart_rate) }}</span>
          </div>
          <VitalTrendChart :trend="trends.heart_rate" metric="heart_rate" :loading="trendLoading" :height="230" :empty-text="presentation.dataLive ? '' : '等待数据接入'" />
        </section>
        <section class="trend-panel">
          <div class="trend-panel-head">
            <b>血氧曲线</b>
            <span class="muted">{{ trendCaption(trends.spo2) }}</span>
          </div>
          <VitalTrendChart :trend="trends.spo2" metric="spo2" :loading="trendLoading" :height="230" :empty-text="presentation.dataLive ? '' : '等待数据接入'" />
        </section>
        <section class="trend-panel">
          <div class="trend-panel-head">
            <b>体温曲线</b>
            <span class="muted">{{ trendCaption(trends.temperature) }}</span>
          </div>
          <VitalTrendChart :trend="trends.temperature" metric="temperature" :loading="trendLoading" :height="230" :empty-text="presentation.dataLive ? '' : '等待数据接入'" />
        </section>
        <section class="trend-panel">
          <div class="trend-panel-head">
            <b>血压曲线</b>
            <span class="muted">{{ trendCaption(trends.systolic, trends.diastolic) }}</span>
          </div>
          <VitalTrendChart :trend="trends.systolic" metric="systolic" :secondary-trend="trends.diastolic" secondary-metric="diastolic" :loading="trendLoading" :height="230" :empty-text="presentation.dataLive ? '' : '等待数据接入'" />
        </section>
      </div>

      <div v-if="assessment?.abnormal_items?.length" class="notes">
        <div class="notes-title status-danger">异常项</div>
        <ul class="reason-list">
          <li v-for="(item, index) in assessment.abnormal_items" :key="index">{{ item }}</li>
        </ul>
      </div>
      <div v-if="assessment?.trend_notes?.length" class="notes">
        <div class="notes-title">趋势提示</div>
        <ul class="reason-list">
          <li v-for="(item, index) in assessment.trend_notes" :key="index">{{ item }}</li>
        </ul>
      </div>
      <div v-if="assessment" class="extra">
        <span>昨夜睡眠 {{ assessment.sleep_hours_last_night ?? '--' }} 小时</span>
        <span>今日步数 {{ assessment.steps_today ?? '--' }}</span>
        <span>手表电量 {{ assessment.watch_battery ?? '--' }}%</span>
      </div>

      <!-- 先展示生命体征及趋势，再在同一卡片内给出综合健康评分 -->
      <section class="score-section">
        <h3 class="card-title">
          健康评分
          <template v-if="presentation.scoreLive">
            <span v-if="score?.coverage" class="muted coverage">
              基于 {{ score.coverage }} 项维度 · 置信度 {{ Math.round((score.confidence ?? 1) * 100) }}%
            </span>
            <el-tag v-if="score?.is_reference" size="small" type="info" effect="plain">数据不全，仅供参考</el-tag>
          </template>
        </h3>
        <template v-if="presentation.scoreDormant">
          <ScoreDormant />
        </template>
        <template v-else>
          <div v-if="score?.score != null" class="score-body rise-stagger">
            <div class="score-main">
              <ScoreRing
                :score="score.score"
                :grade="score.grade"
                :delta="score.delta_vs_yesterday"
                :reference="score.is_reference"
                :size="118"
                :stroke="9"
              />
              <div class="score-text">
                <div class="score-headline">{{ score.summary.split('；')[0] }}</div>
                <div v-if="score.cap_reason" class="cap-note">
                  <el-icon><WarningFilled /></el-icon>
                  {{ score.cap_reason }}，按 NEWS2 临床响应阈值限制了总分
                </div>
                <ul v-if="score.deductions.length" class="reason-list">
                  <li v-for="(item, index) in score.deductions" :key="index">{{ item }}</li>
                </ul>
                <div v-else class="muted">没有扣分项，各项指标都在个人正常范围内</div>
                <div v-if="score.delta_vs_yesterday != null" class="muted">
                  较昨日{{ score.delta_vs_yesterday > 0 ? '上升' : score.delta_vs_yesterday < 0 ? '下降' : '持平' }}
                  <template v-if="score.delta_vs_yesterday">{{ Math.abs(score.delta_vs_yesterday) }} 分</template>
                </div>
              </div>
            </div>
            <div class="dims">
              <div v-for="dim in score.dimensions" :key="dim.key" class="dim" :class="{ missing: dim.score == null }">
                <div class="dim-head">
                  <span class="dim-label">{{ dim.label }}</span>
                  <span class="muted">权重 {{ dim.weight }}</span>
                  <span class="dim-score">{{ dim.score == null ? '缺项' : Math.round(dim.score) }}</span>
                </div>
                <div class="dim-bar">
                  <span :style="{ width: `${dim.score ?? 0}%` }" :class="dimTone(dim.score)"></span>
                </div>
                <div v-if="dim.reasons.length" class="dim-reason muted">{{ dim.reasons[0] }}</div>
              </div>
            </div>
          </div>
          <el-empty v-else :description="score?.summary || '暂无足够数据评分'" :image-size="60" />
        </template>
      </section>
    </div>

    <div class="card outing-card">
        <h3 class="card-title">当前是否适合外出</h3>
        <el-empty v-if="!presentation.dataLive" description="等待数据接入后评估" :image-size="56" />
        <div v-else-if="ruleReport" class="rule-summary">
          <el-alert
            :type="ruleReport.blocked ? 'error' : ruleReport.hits.length ? 'warning' : 'success'"
            :closable="false"
            show-icon
          >
            <template #title>
              {{
                ruleReport.blocked
                  ? '当前不满足外出条件'
                  : ruleReport.hits.length
                    ? '可以外出，但需要收紧限制'
                    : '各项条件均满足，可以外出'
              }}
            </template>
            建议时长 {{ ruleReport.suggested_duration_minutes }} 分钟 / 活动半径
            {{ ruleReport.suggested_radius_m }} 米
          </el-alert>

          <div v-for="(hit, index) in ruleReport.hits" :key="index" class="hit">
            <el-tag size="small" :type="hit.level === 'block' ? 'danger' : 'warning'">
              {{ hit.level === 'block' ? '拦截' : '提醒' }}
            </el-tag>
            <div>
              <div class="hit-title">{{ hit.title }}</div>
              <div class="muted">{{ hit.detail }}</div>
            </div>
          </div>
          <div v-if="ruleReport.passed_rules?.length" class="passed">
            <span class="muted">通过：</span>
            <el-tag
              v-for="rule in ruleReport.passed_rules"
              :key="rule"
              size="small"
              type="success"
              effect="plain"
            >
              {{ rule }}
            </el-tag>
          </div>
        </div>

        <el-divider />
        <div class="stat-row">
          <div><span class="muted">今日外出</span><b>{{ stats?.today_count ?? '--' }} 次</b></div>
          <div><span class="muted">本周外出</span><b>{{ stats?.week_count ?? '--' }} 次</b></div>
          <div><span class="muted">今日在外</span><b>{{ stats?.today_total_minutes ?? '--' }} 分钟</b></div>
          <div><span class="muted">平均时长</span><b>{{ stats?.avg_duration_minutes ?? '--' }} 分钟</b></div>
        </div>
        <div v-if="devices" class="devices">
          <div v-for="key in ['watch', 'dog']" :key="key" class="device">
            <el-icon><component :is="key === 'watch' ? 'Watch' : 'Guide'" /></el-icon>
            <span>{{ key === 'watch' ? '健康手表' : '陪护机器狗' }}</span>
            <template v-if="devices[key]">
              <el-tag size="small" :type="devices[key].online ? 'success' : 'info'" effect="plain">
                {{ devices[key].online ? '在线' : '离线' }}
              </el-tag>
              <span class="muted">电量 {{ devices[key].battery }}%</span>
            </template>
            <el-tag v-else size="small" type="info" effect="plain">未绑定</el-tag>
          </div>
        </div>
    </div>

    <div class="card">
      <h3 class="card-title">近 7 天健康日摘要</h3>
      <el-table :data="summaries" size="small" empty-text="暂无日摘要">
        <el-table-column label="日期" width="110">
          <template #default="{ row }">{{ dayjs(row.summary_date).format('MM-DD') }}</template>
        </el-table-column>
        <el-table-column label="心率(均/低/高)" min-width="150">
          <template #default="{ row }">
            {{ row.hr_avg ?? '--' }} / {{ row.hr_min ?? '--' }} / {{ row.hr_max ?? '--' }}
          </template>
        </el-table-column>
        <el-table-column label="静息心率" width="100">
          <template #default="{ row }">{{ row.resting_hr != null ? Math.round(row.resting_hr) : '--' }}</template>
        </el-table-column>
        <el-table-column label="血氧(均/最低)" min-width="130">
          <template #default="{ row }">{{ row.spo2_avg ?? '--' }} / {{ row.spo2_min ?? '--' }}</template>
        </el-table-column>
        <el-table-column label="血压均值" min-width="120">
          <template #default="{ row }">
            {{ row.systolic_avg ?? '--' }}/{{ row.diastolic_avg ?? '--' }}
          </template>
        </el-table-column>
        <el-table-column prop="steps_total" label="步数" width="90" />
        <el-table-column prop="sleep_hours" label="睡眠(h)" width="90" />
        <el-table-column prop="anomaly_count" label="异常点" width="90" />
        <el-table-column label="健康评分" width="120">
          <template #default="{ row }">
            <span v-if="presentation.scoreDormant" class="muted">——</span>
            <el-tag v-else-if="row.health_score != null" size="small" :type="GRADE_TAG[row.health_grade] || 'info'">
              {{ Math.round(row.health_score) }} · {{ row.health_grade }}
            </el-tag>
            <span v-else class="muted">--</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3 class="card-title">
          外出跟随
          <el-tag v-if="activeTask" type="success" size="small">进行中 #{{ activeTask.id }}</el-tag>
        </h3>
        <div v-if="activeTask">
          <TrackMap
            :origin="{ lat: activeTask.origin_lat, lng: activeTask.origin_lng }"
            :points="track"
            :radius="activeTask.geofence_radius_m"
          />
          <div class="task-meta">
            <span>开始 {{ dayjs(activeTask.started_at).format('HH:mm') }}</span>
            <span>限时 {{ activeTask.planned_duration_minutes }} 分钟</span>
            <span>累计行走 {{ Math.round(activeTask.total_distance_m) }} 米</span>
          </div>
        </div>
        <el-empty v-else description="当前没有进行中的外出" :image-size="70" />
      </div>

      <div class="card">
        <h3 class="card-title">最近一次外出决策</h3>
        <DecisionPanel :decision="latestDecision" />
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3 class="card-title">最近语音对话</h3>
        <div v-if="messages.length" class="dialog">
          <div v-for="msg in messages" :key="msg.id" class="msg" :class="msg.role">
            <div class="who">{{ msg.role === 'elder' ? '老人' : '机器狗' }}</div>
            <div class="text">{{ msg.text }}</div>
            <div class="time">
              {{ dayjs(msg.created_at).format('MM-DD HH:mm:ss') }}
              <span v-if="msg.intent"> · {{ msg.intent }}</span>
              <span v-if="msg.meta?.tools?.length"> · 工具 {{ msg.meta.tools.join('/') }}</span>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无对话记录" :image-size="70" />
      </div>

      <div class="card">
        <h3 class="card-title">该老人的实时事件</h3>
        <RealtimeFeed :elder-id="elderId" :limit="20" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.live-hint {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14.5px;
  color: var(--ink-soft);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  padding: 6px 13px;
}

.live-hint.on {
  color: var(--ok);
  border-color: var(--ok-soft);
  background: var(--ok-soft);
}

.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.vitals-card {
  margin-bottom: 18px;
}

.card-hint {
  font-weight: 400;
  font-size: 14.5px;
  margin-left: auto;
}

.trend-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin: 18px 0 10px;
}

.trend-title {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.trend-name {
  font-size: 16.5px;
  font-weight: 600;
}

.trend-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.trend-panel {
  min-width: 0;
}

.trend-panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  min-height: 28px;
  padding: 0 3px;
  font-size: 14px;
}

.trend-panel-head b {
  color: var(--ink);
  font-size: 15px;
}

.trend-panel-head .muted {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 980px) {
  .trend-grid {
    grid-template-columns: 1fr;
  }
}

.notes {
  margin-top: 14px;
}

.notes-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 4px;
}

.extra {
  display: flex;
  gap: 18px;
  margin-top: 12px;
  font-size: 15px;
  color: var(--ink-soft);
  flex-wrap: wrap;
}

.hit {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 10px;
}

.hit-title {
  font-size: 15px;
  font-weight: 600;
}

.passed {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 10px;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 10px;
}

.stat-row div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.stat-row b {
  font-size: 18px;
}

.devices {
  margin-top: 14px;
  display: grid;
  gap: 8px;
}

.device {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}

.coverage {
  font-weight: 400;
  margin-left: 2px;
}

.score-section {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid var(--line-soft);
}

.score-section .dims {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.outing-card {
  margin-bottom: 18px;
}

/* 健康评分并入当前体征卡后，分数环与维度条上下排开 */
.score-body {
  display: grid;
  grid-template-columns: 1fr;
  gap: 20px;
  align-items: start;
}

.score-main {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

.score-text {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.score-headline {
  font-size: 16.5px;
  font-weight: 600;
  line-height: 1.5;
}

.cap-note {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 14.5px;
  line-height: 1.5;
}

.dims {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 18px;
}

.dim {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--surface-tint);
  border: 1px solid var(--line-soft);
}

.dim.missing {
  opacity: 0.8;
  border-style: dashed;
}

.dim-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 15px;
  margin-bottom: 7px;
}

.dim-label {
  font-weight: 600;
}

.dim-score {
  margin-left: auto;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.dim-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--line-soft);
  overflow: hidden;
}

.dim-bar span {
  display: block;
  height: 100%;
  border-radius: 3px;
  transition: width 0.8s var(--spring);
}

.dim-bar .ok { background: var(--ok); }
.dim-bar .warn { background: var(--warn); }
.dim-bar .danger { background: var(--danger); }
.dim-bar .none { background: transparent; }

.dim-reason {
  margin-top: 6px;
  font-size: 14.5px;
  line-height: 1.5;
}

.task-meta {
  display: flex;
  gap: 16px;
  justify-content: center;
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 10px;
  flex-wrap: wrap;
}

.dialog {
  max-height: 380px;
  overflow-y: auto;
}

.msg {
  padding: 9px 11px;
  border-radius: 8px;
  margin-bottom: 8px;
}

.msg.elder {
  background: var(--surface-tint);
}

.msg.system {
  background: var(--brand-soft);
}

.who {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-soft);
}

.msg .text {
  font-size: 15px;
  line-height: 1.7;
  margin: 3px 0;
}

.msg .time {
  font-size: 13px;
  color: var(--ink-soft);
}

@media (max-width: 980px) {
  .score-section .dims {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .score-section .dims {
    grid-template-columns: 1fr;
  }
}
</style>
