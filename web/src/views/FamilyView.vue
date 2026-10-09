<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { alarmApi, deviceApi, healthApi, outingApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useElders } from '@/composables/useElders'
import { usePresentationStore } from '@/stores/presentation'
import { useRealtimeStore } from '@/stores/realtime'
import ScoreRing from '@/components/ScoreRing.vue'
import VitalGrid from '@/components/VitalGrid.vue'
import TrackMap from '@/components/TrackMap.vue'

// 在设置页里作为预览嵌入时，隐藏“返回后台”这类只对真实使用场景有意义的入口
const props = defineProps({
  preview: { type: Boolean, default: false },
})

const router = useRouter()
const auth = useAuthStore()
const realtime = useRealtimeStore()
const presentation = usePresentationStore()
const { elders, currentElderId, load: loadElders } = useElders()

const assessment = ref(null)
const devices = ref(null)
const activeTask = ref(null)
const track = ref([])
const alarms = ref([])
const stats = ref(null)
const loading = ref(false)
let timer = null

const LEVEL_META = {
  info: { label: '提示', type: 'info' },
  warning: { label: '注意', type: 'warning' },
  critical: { label: '紧急', type: 'danger' },
}

async function load() {
  if (!currentElderId.value) return
  loading.value = true
  try {
    const id = currentElderId.value
    const [assess, deviceData, task, alarmData, statsData] = await Promise.all([
      healthApi.assessment(id),
      deviceApi.snapshot(id),
      outingApi.activeTask(id),
      alarmApi.list({ elder_id: id, page_size: 8 }),
      outingApi.stats(id),
    ])
    assessment.value = assess
    devices.value = deviceData
    activeTask.value = task
    alarms.value = alarmData.items
    stats.value = statsData
    track.value = task ? await outingApi.track(task.id) : []
  } finally {
    loading.value = false
  }
}

const elder = computed(() => elders.value.find((item) => item.id === currentElderId.value))
const score = computed(() => assessment.value?.health_score || null)

const overallStatus = computed(() => {
  if (!presentation.dataLive) return { text: '数据尚未接入', tone: 'info' }
  if (!assessment.value) return { text: '数据加载中', tone: 'info' }
  const pending = alarms.value.filter((a) => ['pending', 'confirming'].includes(a.status))
  if (pending.length) {
    return { text: `有 ${pending.length} 条告警待处理`, tone: 'danger' }
  }
  if (assessment.value.abnormal_items?.length) {
    return { text: '健康指标有异常，请留意', tone: 'warning' }
  }
  if (activeTask.value) {
    return { text: '正在外出，机器狗跟随中', tone: 'success' }
  }
  return { text: '一切正常，老人在家', tone: 'success' }
})

async function handleAlarm(alarm) {
  await alarmApi.handle(alarm.id, { status: 'acked', note: '家属已知晓并联系老人' })
  ElMessage.success('已标记为知晓')
  await load()
}

watch(currentElderId, load)
watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (latest && ['alarm', 'alarm_updated', 'outing_started', 'outing_ended'].includes(latest.event)) {
      load()
    }
  },
)

onMounted(async () => {
  realtime.connect()
  presentation.syncSimulator()
  await loadElders()
  await load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="family">
    <header class="top">
      <div>
        <div class="hello">您好，{{ auth.user?.real_name || auth.user?.username }}</div>
        <div class="sub">家属端 · 实时守护</div>
      </div>
      <div class="top-actions">
        <el-select v-if="elders.length > 1" v-model="currentElderId" size="small" style="width: 110px">
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button
          v-if="auth.isAdmin && !props.preview"
          size="small"
          @click="router.push('/dashboard')"
        >
          返回后台
        </el-button>
      </div>
    </header>

    <div class="body" v-loading="loading && presentation.dataLive && !assessment">
      <div class="status-card" :class="`tone-${overallStatus.tone}`">
        <div class="status-main">
          <div class="status-body">
            <div class="status-name">{{ elder?.name }}</div>
            <div class="status-text">{{ overallStatus.text }}</div>
            <div class="status-meta">
              <span>今日外出 {{ stats?.today_count ?? 0 }} 次</span>
              <span>在外 {{ stats?.today_total_minutes ?? 0 }} 分钟</span>
              <span v-if="assessment?.latest_at">
                数据更新于 {{ dayjs(assessment.latest_at).format('HH:mm') }}
              </span>
            </div>
          </div>
          <ScoreRing
            v-if="(presentation.scoreLive || preview) && score"
            :score="score.score"
            :grade="score.grade"
            :delta="score.delta_vs_yesterday"
            :reference="score.is_reference"
            :size="76"
            :stroke="7"
            inverted
          />
        </div>
        <!-- 给家属看的说法：先说好消息，最多点一条需要留意的事 -->
        <div v-if="(presentation.scoreLive || preview) && score?.friendly_text" class="status-friendly">{{ score.friendly_text }}</div>
      </div>

      <section class="panel">
        <h3>当前身体状况</h3>
        <VitalGrid :assessment="assessment" :empty-tag="presentation.dataLive ? '无数据' : '未接入'" />
        <div v-if="assessment?.abnormal_items?.length" class="warn-box">
          <div v-for="(item, index) in assessment.abnormal_items" :key="index">· {{ item }}</div>
        </div>
        <div class="extra">
          <span>昨夜睡眠 {{ assessment?.sleep_hours_last_night ?? '--' }} 小时</span>
          <span>今日步数 {{ assessment?.steps_today ?? '--' }}</span>
        </div>
      </section>

      <section class="panel">
        <h3>外出实时位置</h3>
        <template v-if="activeTask">
          <TrackMap
            :origin="{ lat: activeTask.origin_lat, lng: activeTask.origin_lng }"
            :points="track"
            :radius="activeTask.geofence_radius_m"
            :size="280"
          />
          <div class="task-meta">
            <span>{{ dayjs(activeTask.started_at).format('HH:mm') }} 出门</span>
            <span>约定 {{ activeTask.planned_duration_minutes }} 分钟内回家</span>
            <span>最远 {{ Math.round(activeTask.max_distance_m) }} 米</span>
          </div>
        </template>
        <el-empty v-else :description="presentation.dataLive ? '老人当前在家' : '等待数据接入'" :image-size="70" />
      </section>

      <section class="panel">
        <h3>设备状态</h3>
        <div v-if="devices" class="devices">
          <div v-for="key in ['watch', 'dog']" :key="key" class="device">
            <el-icon :size="18"><component :is="key === 'watch' ? 'Watch' : 'Guide'" /></el-icon>
            <div class="device-info">
              <div>{{ key === 'watch' ? '健康手表' : '陪护机器狗' }}</div>
              <div class="muted">
                {{ devices[key] ? (devices[key].online ? '在线' : '离线') : '未绑定' }}
                <span v-if="devices[key]"> · 电量 {{ devices[key].battery }}%</span>
              </div>
            </div>
            <el-tag
              size="small"
              :type="devices[key]?.online ? 'success' : 'info'"
              effect="plain"
            >
              {{ devices[key]?.online ? '正常' : '异常' }}
            </el-tag>
          </div>
        </div>
      </section>

      <section class="panel">
        <h3>最近告警</h3>
        <div v-if="alarms.length" class="alarms">
          <div v-for="alarm in alarms" :key="alarm.id" class="alarm">
            <div class="alarm-head">
              <el-tag size="small" :type="LEVEL_META[alarm.level]?.type">
                {{ LEVEL_META[alarm.level]?.label }}
              </el-tag>
              <b>{{ alarm.title }}</b>
              <span class="time">{{ dayjs(alarm.triggered_at).format('MM-DD HH:mm') }}</span>
            </div>
            <div class="alarm-desc">{{ alarm.description }}</div>
            <div class="alarm-foot">
              <span class="muted">状态：{{ alarm.status }}</span>
              <el-button
                v-if="['pending', 'confirming'].includes(alarm.status)"
                size="small"
                type="primary"
                @click="handleAlarm(alarm)"
              >
                我已知晓
              </el-button>
            </div>
          </div>
        </div>
        <el-empty v-else :description="presentation.dataLive ? '近期没有告警' : '等待数据接入'" :image-size="70" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.family {
  min-height: 100%;
  background: var(--bg);
  max-width: 520px;
  margin: 0 auto;
  box-shadow: 0 0 30px rgba(20, 40, 80, 0.06);
}

.top {
  background: linear-gradient(140deg, var(--brand-deep), #46b7a6);
  color: #fff;
  padding: 18px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.hello {
  font-size: 18px;
  font-weight: 600;
}

.sub {
  font-size: 14px;
  opacity: 0.85;
  margin-top: 2px;
}

.top-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.body {
  padding: 14px;
}

.status-card {
  border-radius: 12px;
  padding: 16px;
  color: #fff;
  margin-bottom: 14px;
}

.status-card.tone-success {
  background: linear-gradient(135deg, var(--ok), #5fc496);
}

.status-card.tone-warning {
  background: linear-gradient(135deg, var(--warn), #eab971);
}

.status-card.tone-danger {
  background: linear-gradient(135deg, var(--danger), #e58a85);
}

.status-card.tone-info {
  background: linear-gradient(135deg, #8a938d, #a8b0aa);
}

.status-main {
  display: flex;
  align-items: center;
  gap: 14px;
}

.status-body {
  flex: 1;
  min-width: 0;
}

.status-friendly {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.28);
  font-size: 15.5px;
  line-height: 1.6;
  opacity: 0.95;
}

.status-name {
  font-size: 16.5px;
  opacity: 0.9;
}

.status-text {
  font-size: 20px;
  font-weight: 600;
  margin: 6px 0 10px;
}

.status-meta {
  display: flex;
  gap: 14px;
  font-size: 14px;
  opacity: 0.9;
  flex-wrap: wrap;
}

.panel {
  background: #fff;
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 14px;
}

.panel h3 {
  font-size: 16.5px;
  margin: 0 0 12px;
}

.warn-box {
  background: var(--warn-soft);
  border-radius: 8px;
  padding: 10px;
  margin-top: 12px;
  font-size: 15px;
  line-height: 1.8;
  color: var(--accent);
}

.extra {
  display: flex;
  gap: 16px;
  font-size: 15px;
  color: var(--ink-soft);
  margin-top: 12px;
  flex-wrap: wrap;
}

.task-meta {
  display: flex;
  gap: 12px;
  justify-content: center;
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 10px;
  flex-wrap: wrap;
}

.devices {
  display: grid;
  gap: 10px;
}

.device {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  background: var(--surface-tint);
  border-radius: 9px;
}

.device-info {
  flex: 1;
  font-size: 15px;
}

.alarms {
  display: grid;
  gap: 10px;
}

.alarm {
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 10px;
}

.alarm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  flex-wrap: wrap;
}

.alarm-head .time {
  margin-left: auto;
  font-size: 13px;
  color: var(--ink-soft);
}

.alarm-desc {
  font-size: 15px;
  line-height: 1.7;
  margin: 6px 0;
}

.alarm-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
