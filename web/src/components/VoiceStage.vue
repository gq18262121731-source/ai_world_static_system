<script setup>
// 机器狗语音：机器狗在线时一直在听，有人说话就边说边转文字，回答逐字生成后用真实音色播出。
// 浏览器的麦克风在这里扮演机器狗的耳朵；真机器狗接入后走同一条 /ws/dog-audio 协议。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { deviceApi } from '@/api'
import { streamPost } from '@/api/stream'
import { useElders } from '@/composables/useElders'
import { useDogEars } from '@/composables/useDogEars'
import { usePresentationStore } from '@/stores/presentation'
import { useRealtimeStore } from '@/stores/realtime'

const emit = defineEmits(['turn'])

const { elders, currentElderId, load: loadElders } = useElders()
const realtime = useRealtimeStore()
const presentation = usePresentationStore()
const ears = useDogEars()
// 演示第 3 步之前智能体未接入：状态条显示"未接入"，不接耳朵、不收文字
const ready = computed(() => presentation.agentLive)

const dog = ref(null)
const dogLoading = ref(true)
const typed = ref('')
const showTyping = ref(false)
const bubbles = ref([])
const bubbleBox = ref(null)
const lastReply = ref(null)
const busy = ref(false)
let statusTimer = null
let abortStream = null
let currentAudio = null

const SUGGESTIONS = ['我想出去走走', '我最近血压怎么样', '我今天身体怎么样', '今天天气好不好', '我们回家了']

/** 五个环节对应真机上的完整链路，逐个点亮让人看清系统在中间做了什么。 */
const STAGE_DEFS = [
  { key: 'listen', label: '机器狗拾音', icon: 'Microphone', note: '持续监听，有人说话就开始收音' },
  { key: 'asr', label: '语音转文字', icon: 'Document', note: '边说边转，文字实时出现' },
  { key: 'think', label: '智能体分析', icon: 'MagicStick', note: '识别意图并调取健康与外出数据' },
  { key: 'reply', label: '生成回答', icon: 'ChatDotRound', note: '结合数据与规则逐字生成' },
  { key: 'speak', label: '机器狗播报', icon: 'Bell', note: '用真实音色说给老人听' },
]
const stages = ref(STAGE_DEFS.map((item) => ({ ...item, status: 'idle', detail: '', ms: null })))

function resetStages() {
  stages.value = STAGE_DEFS.map((item) => ({ ...item, status: 'idle', detail: '', ms: null }))
}

function setStage(key, patch) {
  const target = stages.value.find((item) => item.key === key)
  if (target) Object.assign(target, patch)
}

const INTENT_LABEL = {
  outing_request: '外出申请',
  end_outing: '结束外出',
  health_query: '健康问询',
  general_qa: '通用问答',
  sos: '紧急求助',
  fall_confirm: '跌倒确认',
  chitchat: '日常闲聊',
}

const elderName = computed(() => elders.value.find((item) => item.id === currentElderId.value)?.name || '老人')
const dogOnline = computed(() => ready.value && Boolean(dog.value?.online))
const earsOn = computed(() => !['idle', 'error'].includes(ears.state.value))

const stateText = computed(() => {
  if (!ready.value) return '智能体尚未接入'
  switch (ears.state.value) {
    case 'connecting':
      return '正在接通机器狗的耳朵…'
    case 'listening':
      return '倾听中，您说话就行'
    case 'hearing':
      return ears.partial.value || '听到您在说…'
    case 'thinking':
      return '机器狗在想…'
    case 'speaking':
      return '机器狗在说…'
    case 'error':
      return ears.errorText.value || '拾音出了点问题'
    default:
      return dogOnline.value ? '机器狗待命' : '机器狗离线'
  }
})

async function scrollBubbles() {
  await nextTick()
  if (bubbleBox.value) bubbleBox.value.scrollTop = bubbleBox.value.scrollHeight
}

function pushBubble(from, text, extra = {}) {
  const bubble = { from, text, at: dayjs(), ...extra }
  bubbles.value.push(bubble)
  scrollBubbles()
  return bubble
}

// ---------- 机器狗状态 ----------

async function loadDog() {
  if (!currentElderId.value) return
  if (!ready.value) {
    // 未接入时不去查机器狗状态，也不自动开始听
    dog.value = null
    dogLoading.value = false
    return
  }
  try {
    const snapshot = await deviceApi.snapshot(currentElderId.value)
    dog.value = snapshot?.dog || null
  } catch {
    dog.value = null
  } finally {
    dogLoading.value = false
  }
}

// 机器狗上线就自动开始听，掉线就停
watch([dogOnline, dogLoading], ([online, loading]) => {
  if (loading) return
  if (online && !earsOn.value) startEars()
  if (!online && earsOn.value) ears.stop()
})

watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (latest?.event === 'device_status' && latest.elder_id === currentElderId.value) loadDog()
  },
)

// ---------- 事件驱动的链路 ----------

let activeBubble = null

function handleAgentEvent(event) {
  if (event.event === 'transcript') {
    resetStages()
    setStage('listen', { status: 'done', detail: '收音完成' })
    setStage('asr', { status: 'done', detail: event.text })
    setStage('think', { status: 'active', detail: '正在识别意图…' })
    pushBubble('elder', event.text)
    activeBubble = pushBubble('dog', '', { streaming: true })
    busy.value = true
  } else if (event.event === 'partial') {
    setStage('listen', { status: 'active', detail: '有人在说话' })
    setStage('asr', { status: 'active', detail: event.text })
  } else if (event.event === 'intent') {
    setStage('think', { status: 'active', detail: `意图：${INTENT_LABEL[event.intent] || event.intent}，正在调取数据…` })
    if (activeBubble) activeBubble.intent = event.intent
  } else if (event.event === 'stage') {
    if (event.stage === 'reasoning') {
      setStage('think', { status: event.ms != null ? 'done' : 'active', detail: event.detail, ms: event.ms })
      if (event.ms != null) setStage('reply', { status: 'active', detail: '正在组织回答…' })
    } else if (event.stage === 'tts') {
      setStage('reply', { status: 'done', detail: '回答已生成' })
      setStage('speak', { status: 'active', detail: event.detail, ms: event.ms })
    } else if (event.stage === 'speak') {
      setStage('speak', { status: 'active', detail: event.detail })
    }
  } else if (event.event === 'token') {
    if (activeBubble) activeBubble.text += event.text
    setStage('reply', { status: 'active', detail: '正在组织回答…' })
    scrollBubbles()
  } else if (event.event === 'final') {
    const reply = event.reply
    if (activeBubble) {
      activeBubble.text = reply.reply_text
      activeBubble.streaming = false
      activeBubble.tools = reply.tools_used
      activeBubble.decision = reply.decision
      activeBubble.latency = reply.latency_ms
    }
    lastReply.value = reply
    emit('turn', reply)
    setStage('think', {
      status: 'done',
      detail: reply.tools_used?.length ? `调取了 ${reply.tools_used.length} 项数据` : '直接由模型作答',
      ms: reply.stages?.intent_ms,
    })
    setStage('reply', { status: 'done', detail: `意图：${INTENT_LABEL[reply.intent] || reply.intent}`, ms: reply.stages?.reasoning_ms })
    setStage('speak', { status: 'active', detail: '正在播报…', ms: reply.stages?.tts_ms })
    speakOut(reply.reply_text, reply.audio_url)
    busy.value = false
  } else if (event.event === 'error') {
    if (activeBubble && !activeBubble.text) bubbles.value = bubbles.value.filter((b) => b !== activeBubble)
    activeBubble = null
    busy.value = false
    setStage('think', { status: 'error', detail: event.detail || '这一轮没有走通' })
    ElMessage.warning(event.detail || '这一轮没有走通')
  }
}

// ---------- 耳朵 ----------

async function startEars() {
  if (!dog.value?.device_id) return
  await ears.start(dog.value.device_id, {
    ready: (event) => {
      setStage('listen', { status: 'active', detail: event.streaming ? '倾听中（讯飞流式识别）' : '倾听中（整句识别）' })
    },
    partial: handleAgentEvent,
    transcript: handleAgentEvent,
    intent: handleAgentEvent,
    stage: handleAgentEvent,
    token: handleAgentEvent,
    final: handleAgentEvent,
    error: (detail) => {
      if (typeof detail === 'string') {
        ElMessage.warning(detail)
        showTyping.value = true
      } else {
        handleAgentEvent(detail)
      }
    },
    idle: () => {
      if (!currentAudio) setStage('listen', { status: 'active', detail: '倾听中' })
    },
    ignored: (event) => {
      setStage('listen', { status: 'active', detail: `只听到“${event.text}”，没当成是在跟我说话` })
      setStage('asr', { status: 'idle', detail: '' })
    },
  })
}

/** 机器狗开口：优先放真实合成的音频，期间闭麦；没有音频就用浏览器朗读。 */
function speakOut(text, audioUrl) {
  if (!text) {
    setStage('speak', { status: 'done', detail: '已下发播报指令' })
    return
  }
  let settled = false
  const finish = (detail) => {
    if (settled) return
    settled = true
    currentAudio = null
    ears.mute(false)
    setStage('speak', { status: 'done', detail })
    setStage('listen', { status: earsOn.value ? 'active' : 'idle', detail: earsOn.value ? '倾听中' : '' })
  }
  ears.mute(true)
  if (audioUrl) {
    currentAudio = new Audio(audioUrl)
    currentAudio.onended = () => finish('播报完成（聆小璇）')
    currentAudio.onerror = () => speakWithBrowser(text, finish)
    currentAudio.play().catch(() => speakWithBrowser(text, finish))
    return
  }
  speakWithBrowser(text, finish)
}

function speakWithBrowser(text, finish) {
  if (!window.speechSynthesis) {
    finish('已下发播报指令')
    return
  }
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 0.95
  utterance.onend = () => finish('播报完成（浏览器朗读）')
  utterance.onerror = () => finish('已下发播报指令')
  window.speechSynthesis.speak(utterance)
}

// ---------- 打字兜底 ----------

function submitTyped() {
  if (!ready.value) return
  const text = typed.value.trim()
  if (!text || busy.value) return
  typed.value = ''
  if (earsOn.value) {
    // 耳朵连着时，文字也走同一条通道，机器狗照样播报
    ears.sendText(text)
    return
  }
  // 机器狗离线：走普通流式接口，界面上模拟整条链路
  handleAgentEvent({ event: 'transcript', text })
  setStage('listen', { status: 'done', detail: '手动输入（机器狗离线）' })
  abortStream = streamPost(
    '/agent/talk/stream',
    { elder_id: currentElderId.value, text },
    handleAgentEvent,
    () => {
      busy.value = false
    },
    (error) => handleAgentEvent({ event: 'error', detail: error.message }),
  )
}

function useSuggestion(text) {
  typed.value = text
  showTyping.value = true
}

watch(currentElderId, async () => {
  ears.stop()
  bubbles.value = []
  lastReply.value = null
  resetStages()
  dogLoading.value = true
  await loadDog()
})

onMounted(async () => {
  await loadElders()
  await loadDog()
  statusTimer = setInterval(loadDog, 10000)
})
onUnmounted(() => {
  clearInterval(statusTimer)
  abortStream?.()
  currentAudio?.pause()
  ears.stop()
})

defineExpose({ lastReply })
</script>

<template>
  <div class="stage">
    <!-- 机器狗状态 -->
    <div class="dog-bar" :class="{ online: dogOnline, offline: ready && !dogOnline && !dogLoading }">
      <span class="dog-badge">
        <el-icon :size="22"><Guide /></el-icon>
      </span>
      <div class="dog-info">
        <div class="dog-title">
          {{ dog?.name || '陪护机器狗' }}
          <el-tag v-if="!ready" size="small" type="info" effect="dark">未接入</el-tag>
          <el-tag v-else size="small" :type="dogLoading ? 'info' : dogOnline ? 'success' : 'danger'" effect="dark">
            {{ dogLoading ? '查询中' : dogOnline ? '在线' : '离线' }}
          </el-tag>
          <el-tag v-if="dog?.battery != null" size="small" effect="plain">电量 {{ dog.battery }}%</el-tag>
          <el-tag v-if="ears.streaming.value && earsOn" size="small" type="success" effect="plain">流式识别</el-tag>
        </div>
        <div class="muted dog-sub">
          <template v-if="!ready">智能体尚未接入，机器狗的语音链路暂不可用。</template>
          <template v-else-if="!dog && !dogLoading">{{ elderName }}还没有绑定机器狗，到「设置 → 设备管理」绑定后这里会自动开始倾听。</template>
          <template v-else-if="dogOnline">机器狗持续监听，{{ elderName }}开口说话就会自动转文字并回答，不需要按任何按钮。</template>
          <template v-else-if="!dogLoading">机器狗离线时无法拾音；可以先用下面的文字输入模拟老人说话。</template>
        </div>
      </div>
      <div class="dog-actions">
        <el-select v-model="currentElderId" style="width: 136px">
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button v-if="dogOnline && earsOn" size="small" @click="ears.stop()">暂停倾听</el-button>
        <el-button v-else-if="dogOnline" size="small" type="primary" @click="startEars">开始倾听</el-button>
      </div>
    </div>

    <!-- 倾听界面：老人与机器狗面对面 -->
    <div class="scene" :class="{ busy, hearing: ears.state.value === 'hearing', off: !dogOnline }">
      <div class="actor elder">
        <span class="actor-avatar">
          <el-icon :size="26"><Avatar /></el-icon>
        </span>
        <span class="actor-name">{{ elderName }}</span>
      </div>

      <div class="link">
        <div class="wave" :class="{ on: earsOn }" :style="{ '--level': ears.level.value }">
          <i v-for="n in 13" :key="n" :style="{ animationDelay: `${n * 0.07}s` }"></i>
        </div>
        <div class="link-text" :class="{ partial: ears.state.value === 'hearing' && ears.partial.value }">
          {{ stateText }}
        </div>
      </div>

      <div class="actor dog">
        <span class="actor-avatar dog-avatar" :class="{ talking: ears.state.value === 'speaking', off: !dogOnline }">
          <el-icon :size="26"><Guide /></el-icon>
        </span>
        <span class="actor-name">陪护机器狗</span>
      </div>
    </div>

    <!-- 文字兜底 -->
    <div class="controls">
      <el-button link type="primary" :disabled="!ready" @click="showTyping = !showTyping">
        {{ showTyping ? '收起文字输入' : '用文字模拟老人说话' }}
      </el-button>
      <template v-if="showTyping && ready">
        <el-input v-model="typed" placeholder="输入老人说的话" style="width: 280px" @keyup.enter="submitTyped" />
        <el-button type="primary" :loading="busy" @click="submitTyped">发送</el-button>
      </template>
    </div>

    <div v-if="showTyping" class="suggestions">
      <span class="muted">试试说：</span>
      <el-tag v-for="item in SUGGESTIONS" :key="item" class="phrase pressable" effect="plain" v-ripple @click="useSuggestion(item)">
        {{ item }}
      </el-tag>
    </div>

    <!-- 链路流水线 -->
    <div class="pipeline">
      <div v-for="(item, index) in stages" :key="item.key" class="step" :class="`is-${item.status}`">
        <span class="step-index">{{ index + 1 }}</span>
        <span class="step-icon">
          <el-icon :size="17"><component :is="item.icon" /></el-icon>
        </span>
        <div class="step-body">
          <div class="step-label">
            {{ item.label }}
            <em v-if="item.ms != null">{{ item.ms }}ms</em>
          </div>
          <div class="step-note">{{ item.detail || item.note }}</div>
        </div>
      </div>
    </div>

    <!-- 对话气泡 -->
    <div ref="bubbleBox" class="dialogue">
      <el-empty
        v-if="!bubbles.length"
        :description="!ready ? '智能体尚未接入' : dogOnline ? '机器狗正在听，对着麦克风说一句话试试' : '机器狗离线，可用文字模拟老人说话'"
        :image-size="70"
      />
      <div v-for="(item, index) in bubbles" :key="index" class="bubble-row" :class="item.from">
        <div class="bubble" :class="{ streaming: item.streaming }">
          <div class="bubble-text">
            {{ item.text || (item.streaming ? '正在想…' : '') }}<span v-if="item.streaming" class="caret"></span>
          </div>
          <div v-if="item.from === 'dog' && item.tools?.length" class="bubble-meta">
            <el-tag size="small" effect="plain">{{ INTENT_LABEL[item.intent] || item.intent }}</el-tag>
            <el-tag
              v-if="item.decision"
              size="small"
              :type="{ allow: 'success', conditional: 'warning', deny: 'danger' }[item.decision.decision]"
            >
              {{ { allow: '允许外出', conditional: '有条件允许', deny: '不建议外出' }[item.decision.decision] }}
            </el-tag>
            <span class="muted">依据 {{ item.tools.length }} 项数据 · {{ item.latency }}ms</span>
          </div>
        </div>
        <span class="bubble-time">{{ item.at.format('HH:mm:ss') }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---------- 机器狗状态条 ---------- */

.dog-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: var(--surface);
}

.dog-bar.online {
  border-color: var(--ok-soft);
  background: linear-gradient(135deg, var(--ok-soft), var(--surface) 60%);
}

.dog-bar.offline {
  border-color: var(--danger-soft);
  background: linear-gradient(135deg, var(--danger-soft), var(--surface) 60%);
}

.dog-badge {
  width: 46px;
  height: 46px;
  border-radius: 15px;
  display: grid;
  place-items: center;
  color: #fff;
  background: linear-gradient(140deg, var(--brand), #4ec2ac);
  flex-shrink: 0;
}

.dog-bar.offline .dog-badge {
  background: linear-gradient(140deg, #9aa39d, #b8bfba);
}

.dog-info {
  flex: 1;
  min-width: 0;
}

.dog-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  flex-wrap: wrap;
}

.dog-sub {
  margin-top: 3px;
  line-height: 1.6;
}

.dog-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* ---------- 场景 ---------- */

.scene {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 20px;
  padding: 26px 24px;
  border-radius: var(--radius-lg);
  background: linear-gradient(150deg, var(--brand-tint), var(--surface-tint));
  border: 1px solid var(--line);
  transition: background var(--dur) var(--ease-out);
}

.scene.hearing {
  background: linear-gradient(150deg, var(--brand-soft), var(--surface-tint));
}

.scene.off {
  filter: saturate(0.4);
}

.actor {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.actor-avatar {
  width: 60px;
  height: 60px;
  border-radius: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(140deg, #8aa0b5, #a9bccc);
  box-shadow: var(--shadow);
}

.dog-avatar {
  background: linear-gradient(140deg, var(--brand), #4ec2ac);
}

.dog-avatar.off {
  background: linear-gradient(140deg, #9aa39d, #b8bfba);
}

.dog-avatar.talking {
  animation: nod 1.1s var(--ease-out) infinite;
}

@keyframes nod {
  0%, 100% { transform: translateY(0) rotate(0); }
  50% { transform: translateY(-4px) rotate(-5deg); }
}

.actor-name {
  font-size: 14.5px;
  color: var(--ink-mid);
  font-weight: 500;
}

.link {
  text-align: center;
}

/* 波形随麦克风音量起伏：--level 由拾音实时写入 */
.wave {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 44px;
}

.wave i {
  width: 4px;
  height: 6px;
  border-radius: 3px;
  background: var(--line);
  transition: background-color var(--dur) var(--ease-out), height 0.12s var(--ease-out);
}

.wave.on i {
  background: var(--brand);
  height: calc(6px + var(--level, 0) * 34px);
  animation: breathe-bar 1.6s var(--ease-out) infinite;
}

.wave.on i:nth-child(even) {
  height: calc(4px + var(--level, 0) * 26px);
}

@keyframes breathe-bar {
  0%, 100% { opacity: 0.75; }
  50% { opacity: 1; }
}

.link-text {
  font-size: 15.5px;
  color: var(--ink-soft);
  margin-top: 8px;
  min-height: 22px;
}

.link-text.partial {
  color: var(--ink);
  font-weight: 500;
}

.scene.busy .link-text {
  color: var(--brand-deep);
}

/* ---------- 操作 ---------- */

.controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.suggestions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

/* ---------- 流水线 ---------- */

.pipeline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.step {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 13px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--surface);
  transition:
    border-color var(--dur) var(--ease-out),
    background-color var(--dur) var(--ease-out),
    transform var(--dur) var(--spring);
}

.step.is-active {
  border-color: var(--brand);
  background: var(--brand-tint);
  transform: translateY(-3px);
}

.step.is-done {
  border-color: var(--ok-soft);
  background: var(--ok-soft);
}

.step.is-error {
  border-color: var(--danger);
  background: var(--danger-soft);
}

.step-index {
  position: absolute;
  top: 7px;
  right: 10px;
  font-size: 13px;
  color: var(--ink-soft);
  font-weight: 600;
}

.step-icon {
  width: 30px;
  height: 30px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--line-soft);
  color: var(--ink-soft);
  transition: background-color var(--dur) var(--ease-out), color var(--dur) var(--ease-out);
}

.step.is-active .step-icon {
  background: var(--brand);
  color: #fff;
  animation: step-throb 1.2s var(--ease-out) infinite;
}

@keyframes step-throb {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.12); }
}

.step.is-done .step-icon {
  background: var(--ok);
  color: #fff;
}

.step.is-error .step-icon {
  background: var(--danger);
  color: #fff;
}

.step-body {
  min-width: 0;
}

.step-label {
  font-size: 15.5px;
  font-weight: 600;
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.step-label em {
  font-style: normal;
  font-size: 13px;
  font-weight: 400;
  color: var(--ink-soft);
}

.step-note {
  font-size: 13.5px;
  color: var(--ink-soft);
  line-height: 1.6;
  margin-top: 2px;
  word-break: break-word;
}

/* ---------- 对话气泡 ---------- */

.dialogue {
  max-height: 300px;
  overflow-y: auto;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bubble-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  animation: rise-in 0.4s var(--spring) both;
}

.bubble-row.dog {
  flex-direction: row-reverse;
}

.bubble {
  max-width: 78%;
  padding: 11px 15px;
  border-radius: 16px;
  line-height: 1.75;
  font-size: 15.5px;
}

.caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -0.15em;
  background: currentColor;
  animation: caret-blink 0.9s steps(2) infinite;
}

@keyframes caret-blink {
  to {
    opacity: 0;
  }
}

.bubble-row.elder .bubble {
  background: var(--surface-tint);
  border: 1px solid var(--line);
  border-bottom-left-radius: 5px;
}

.bubble-row.dog .bubble {
  background: linear-gradient(140deg, var(--brand), #45b9a4);
  color: #fff;
  border-bottom-right-radius: 5px;
}

.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.28);
}

.bubble-row.dog .bubble-meta .muted {
  color: rgba(255, 255, 255, 0.85);
}

.bubble-time {
  font-size: 13px;
  color: var(--ink-soft);
  flex-shrink: 0;
}
</style>
