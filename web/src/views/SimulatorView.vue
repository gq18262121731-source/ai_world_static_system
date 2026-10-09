<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { simulatorApi } from '@/api'
import { useElders } from '@/composables/useElders'
import { useRealtimeStore } from '@/stores/realtime'
import DecisionPanel from '@/components/DecisionPanel.vue'
import TrackMap from '@/components/TrackMap.vue'
import RealtimeFeed from '@/components/RealtimeFeed.vue'

const realtime = useRealtimeStore()
const { elders, currentElderId, load: loadElders } = useElders()

const state = ref(null)
const busy = ref('')
const speakText = ref('我想出去走走')
const lastReply = ref(null)
const track = ref([])
const speakEnabled = ref(true)

const PROFILE_META = {
  normal: { label: '健康平稳', tone: 'success', hint: '各项指标正常，预期可以外出' },
  tired: { label: '疲劳缺觉', tone: 'warning', hint: '睡眠不足，预期有条件允许' },
  hypoxia: { label: '血氧偏低', tone: 'danger', hint: '血氧低于安全线，预期被拒绝' },
  hypertension: { label: '血压偏高', tone: 'danger', hint: '收缩压超红线，预期被拒绝' },
  tachycardia: { label: '心率过快', tone: 'danger', hint: '心率超红线，预期被拒绝' },
  fever: { label: '发热', tone: 'danger', hint: '体温超红线，预期被拒绝' },
}

const QUICK_PHRASES = [
  '我想出去走走',
  '现在能下楼买菜吗',
  '我最近血压怎么样',
  '今天天气好不好',
  '我昨晚睡得怎么样',
  '我们回家了',
  '我没事，能自己起来',
  '快帮我叫人，我起不来了',
]

async function refresh() {
  if (!currentElderId.value) return
  state.value = await simulatorApi.state(currentElderId.value)
  track.value = []
}

async function run(key, action, successMessage) {
  busy.value = key
  try {
    const result = await action()
    if (successMessage) ElMessage.success(successMessage)
    await refresh()
    return result
  } finally {
    busy.value = ''
  }
}

function applyProfile(profile) {
  return run(
    `profile-${profile}`,
    async () => {
      const result = await simulatorApi.healthProfile({
        elder_id: currentElderId.value,
        profile,
        // 覆盖最近一小时的读数，避免旧的异常值继续影响判定
        points: 14,
      })
      ElMessage.success(
        result.abnormal_items?.length
          ? `已注入「${result.label}」数据，检测到 ${result.abnormal_items.length} 项异常`
          : `已注入「${result.label}」数据，各项指标正常`,
      )
      return result
    },
  )
}

function setBattery(deviceType, battery) {
  return run(`battery-${deviceType}`, () =>
    simulatorApi.deviceState({ elder_id: currentElderId.value, device_type: deviceType, battery }),
  )
}

function setOnline(deviceType, online) {
  return run(
    `online-${deviceType}`,
    () =>
      simulatorApi.deviceState({ elder_id: currentElderId.value, device_type: deviceType, online }),
    online ? '设备已上线' : '设备已离线',
  )
}

async function speak(text) {
  const content = (text || speakText.value || '').trim()
  if (!content) return
  const reply = await run('speak', () =>
    simulatorApi.speak({ elder_id: currentElderId.value, text: content }),
  )
  lastReply.value = reply
  if (speakEnabled.value) speakOut(reply.reply_text)
  if (reply.task_id) await loadTrack()
}

/** 用浏览器语音合成把机器狗的回复念出来，直观还原"机器狗说话"。 */
function speakOut(text) {
  if (!window.speechSynthesis || !text) return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 0.95
  window.speechSynthesis.speak(utterance)
}

async function walk() {
  const result = await run('walk', () =>
    simulatorApi.walk({ elder_id: currentElderId.value, steps: 5, meters_per_step: 80 }),
  )
  if (result.ok === false) {
    ElMessage.warning(result.message)
    return
  }
  ElMessage.success(`已走到离家 ${result.distance_from_origin_m} 米`)
  await loadTrack()
}

async function triggerFall(source) {
  const result = await run(`fall-${source}`, () =>
    simulatorApi.fall({ elder_id: currentElderId.value, source, confidence: 0.9 }),
  )
  ElMessage[result.is_new_alarm ? 'warning' : 'info'](
    result.is_new_alarm
      ? `已创建跌倒告警 #${result.alarm_id}，机器狗正在语音确认`
      : `已融合进告警 #${result.alarm_id}，来源 ${result.sources.join('+')}`,
  )
}

async function reset() {
  await ElMessageBox.confirm(
    '将清空该老人的外出任务、告警与对话记录（保留历史健康数据），用于重新演示。',
    '重置演示数据',
    { type: 'warning' },
  )
  await run('reset', () => simulatorApi.reset({ elder_id: currentElderId.value }), '演示数据已重置')
  lastReply.value = null
  track.value = []
}

async function restoreBaseline() {
  await ElMessageBox.confirm(
    '将张三、李四、王五、陈二恢复为预设的健康与设备状态，之后仍可继续手动调整。',
    '恢复四人演示基线',
    { type: 'warning' },
  )
  await run(
    'baseline',
    () => simulatorApi.restoreBaseline(),
    '四位老人的演示状态与健康评分已恢复',
  )
}

async function loadTrack() {
  await refresh()
  if (!state.value?.active_task) {
    track.value = []
    return
  }
  const { outingApi } = await import('@/api')
  track.value = await outingApi.track(state.value.active_task.id)
}

const dogSpeeches = computed(() =>
  realtime.events
    .filter(
      (item) =>
        item.event === 'device_command' &&
        ['tts_speak', 'ask_confirm', 'alarm_broadcast'].includes(item.data?.command),
    )
    .slice(0, 8),
)

watch(currentElderId, refresh)

onMounted(async () => {
  await loadElders()
  await refresh()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">设备模拟器</h2>
        <p class="page-subtitle">
          在没有真机的情况下驱动完整业务闭环：造健康数据 → 对机器狗说话 → 跟随行走 → 触发跌倒
        </p>
      </div>
      <div class="head-actions">
        <el-select v-model="currentElderId" placeholder="选择老人" style="width: 150px">
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>刷新状态</el-button>
        <el-button type="primary" plain :loading="busy === 'baseline'" @click="restoreBaseline">
          <el-icon><RefreshLeft /></el-icon>恢复四人演示基线
        </el-button>
        <el-button type="danger" plain :loading="busy === 'reset'" @click="reset">
          重置演示数据
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="state && !state.mqtt.connected"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      当前未连接 MQTT Broker，面板操作直接作用于后端服务，演示流程完全可用。
      若启动了 EMQX 与独立模拟器进程，这些操作会通过 MQTT 控制主题下发到模拟器，链路与真机一致。
    </el-alert>

    <div class="grid grid-2">
      <div class="card">
        <h3 class="card-title">1. 设定老人当前身体状况</h3>
        <p class="muted" style="margin-top: -6px">
          点击档位会写入一段最新的手表读数，随后的外出评估将基于这些数据判定
        </p>
        <div class="profiles">
          <div
            v-for="(meta, key) in PROFILE_META"
            :key="key"
            class="profile liftable"
            :class="`tone-${meta.tone}`"
            v-ripple
            @click="applyProfile(key)"
          >
            <div class="profile-name">
              {{ meta.label }}
              <el-icon v-if="busy === `profile-${key}`" class="is-loading"><Loading /></el-icon>
            </div>
            <div class="muted">{{ meta.hint }}</div>
          </div>
        </div>

        <template v-if="state?.latest_health">
          <h4 class="sub-title">手表最新读数</h4>
          <div class="readings">
            <span>心率 {{ state.latest_health.heart_rate ?? '--' }}</span>
            <span>血氧 {{ state.latest_health.spo2 ?? '--' }}%</span>
            <span>血压 {{ state.latest_health.systolic ?? '--' }}/{{ state.latest_health.diastolic ?? '--' }}</span>
            <span>体温 {{ state.latest_health.temperature ?? '--' }}℃</span>
            <span>步数 {{ state.latest_health.steps ?? '--' }}</span>
            <span class="muted">{{ dayjs(state.latest_health.recorded_at).format('HH:mm:ss') }}</span>
          </div>
        </template>
      </div>

      <div class="card">
        <h3 class="card-title">2. 设备状态</h3>
        <div v-if="state" class="device-panel">
          <div v-for="key in ['watch', 'dog']" :key="key" class="device-block">
            <div class="device-head">
              <el-icon><component :is="key === 'watch' ? 'Watch' : 'Guide'" /></el-icon>
              <b>{{ key === 'watch' ? '健康手表' : '陪护机器狗' }}</b>
              <span class="muted">{{ state[key].device_id || '未绑定' }}</span>
              <el-tag
                v-if="key === 'dog' && state.dog.follow_mode"
                size="small"
                type="success"
              >
                跟随中
              </el-tag>
            </div>
            <div class="device-controls">
              <span class="control-label">在线</span>
              <el-switch
                :model-value="state[key].status === 'online'"
                :loading="busy === `online-${key}`"
                @change="(value) => setOnline(key, value)"
              />
              <span class="control-label">电量 {{ state[key].battery }}%</span>
              <el-slider
                :model-value="state[key].battery"
                :min="0"
                :max="100"
                :step="5"
                style="flex: 1; min-width: 140px"
                @change="(value) => setBattery(key, value)"
              />
            </div>
            <div class="quick-battery">
              <el-button size="small" @click="setBattery(key, key === 'watch' ? 10 : 20)">
                调到低电量（触发拦截）
              </el-button>
              <el-button size="small" @click="setBattery(key, 90)">充满</el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3 class="card-title">3. 老人对机器狗说话</h3>
        <el-input
          v-model="speakText"
          type="textarea"
          :rows="2"
          placeholder="输入老人说的话，例如：我想出去走走"
        />
        <div class="speak-actions">
          <el-button type="primary" :loading="busy === 'speak'" @click="speak()">
            <el-icon><Microphone /></el-icon>说给机器狗听
          </el-button>
          <el-checkbox v-model="speakEnabled">浏览器朗读机器狗的回复</el-checkbox>
        </div>
        <div class="phrases">
          <el-tag
            v-for="phrase in QUICK_PHRASES"
            :key="phrase"
            class="phrase pressable"
            effect="plain"
            v-ripple
            @click="speakText = phrase"
          >
            {{ phrase }}
          </el-tag>
        </div>

        <template v-if="lastReply">
          <h4 class="sub-title">机器狗回复</h4>
          <div class="speech-bubble">{{ lastReply.reply_text }}</div>
          <div class="reply-meta">
            <el-tag size="small" effect="plain">意图 {{ lastReply.intent }}</el-tag>
            <el-tag size="small" effect="plain">耗时 {{ lastReply.latency_ms }}ms</el-tag>
            <el-tag v-for="tool in lastReply.tools_used" :key="tool" size="small" type="info" effect="plain">
              {{ tool }}
            </el-tag>
            <el-tag v-if="lastReply.task_id" size="small" type="success">
              外出任务 #{{ lastReply.task_id }}
            </el-tag>
          </div>
        </template>
      </div>

      <div class="card">
        <h3 class="card-title">4. 外出跟随与应急</h3>
        <div v-if="state?.active_task" class="task-box">
          <TrackMap
            :origin="state.active_task.origin"
            :points="track"
            :radius="state.active_task.geofence_radius_m"
            :size="260"
          />
          <div class="muted" style="text-align: center; margin-top: 8px">
            任务 #{{ state.active_task.id }} · 限时 {{ state.active_task.planned_duration_minutes }} 分钟 ·
            围栏 {{ state.active_task.geofence_radius_m }} 米
          </div>
        </div>
        <el-empty v-else description="当前没有进行中的外出任务" :image-size="70" />

        <div class="ops">
          <el-button :loading="busy === 'walk'" :disabled="!state?.active_task" @click="walk">
            <el-icon><Position /></el-icon>向外走 5 步（约 400 米）
          </el-button>
          <el-button type="danger" plain :loading="busy === 'fall-dog'" @click="triggerFall('dog')">
            <el-icon><WarnTriangleFilled /></el-icon>机器狗检测到跌倒
          </el-button>
          <el-button type="danger" plain :loading="busy === 'fall-watch'" @click="triggerFall('watch')">
            <el-icon><Watch /></el-icon>手表检测到跌倒
          </el-button>
        </div>
        <p class="muted">
          30 秒内先后触发两个来源，会融合成同一条告警；触发后对机器狗说"我没事"可自动关闭告警，
          不回应则在 30 秒后自动升级通知家属。
        </p>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3 class="card-title">机器狗扬声器（下行指令）</h3>
        <div v-if="dogSpeeches.length" class="speaker">
          <div v-for="(item, index) in dogSpeeches" :key="index" class="speech-item">
            <el-tag
              size="small"
              :type="item.data.command === 'alarm_broadcast' ? 'danger' : item.data.command === 'ask_confirm' ? 'warning' : 'primary'"
            >
              {{ { tts_speak: '播报', ask_confirm: '确认询问', alarm_broadcast: '告警播报' }[item.data.command] }}
            </el-tag>
            <div>
              <div class="speech-text">{{ item.data.payload?.text }}</div>
              <div class="muted">
                {{ item.data.device_id }} · {{ dayjs(item.ts).format('HH:mm:ss') }}
                <el-button link type="primary" size="small" @click="speakOut(item.data.payload?.text)">
                  朗读
                </el-button>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else description="等待平台下发语音指令…" :image-size="60" />
      </div>

      <div class="card">
        <h3 class="card-title">实时事件流</h3>
        <RealtimeFeed :elder-id="currentElderId" :limit="25" />
      </div>
    </div>

    <div v-if="lastReply?.decision" class="card">
      <h3 class="card-title">本次外出评估详情</h3>
      <DecisionPanel :decision="lastReply.decision" :speech-text="lastReply.reply_text" />
    </div>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.profiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.profile {
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: var(--radius);
  padding: 12px 14px;
  cursor: pointer;
  background: var(--surface);
  position: relative;
  overflow: hidden;
}

.profile.tone-success {
  border-left-color: var(--ok);
}

.profile.tone-warning {
  border-left-color: var(--warn);
}

.profile.tone-danger {
  border-left-color: var(--danger);
}

.profile-name {
  font-size: 15.5px;
  font-weight: 600;
  margin-bottom: 3px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.sub-title {
  font-size: 15px;
  margin: 16px 0 8px;
}

.readings {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 15px;
}

.device-block + .device-block {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--line);
}

.device-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 15.5px;
}

.device-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.control-label {
  font-size: 15px;
  color: var(--ink-soft);
}

.quick-battery {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.speak-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 12px 0;
  flex-wrap: wrap;
}

.phrases {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.phrase {
  cursor: pointer;
}

.reply-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.ops {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 14px 0 10px;
}

.speaker {
  max-height: 360px;
  overflow-y: auto;
}

.speech-item {
  display: flex;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--line);
  align-items: flex-start;
}

.speech-item:last-child {
  border-bottom: none;
}

.speech-text {
  font-size: 15px;
  line-height: 1.7;
}
</style>
