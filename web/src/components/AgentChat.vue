<script setup>
// 健康咨询：老人自己在屏幕上打字问自己的身体情况，回答直接对老人说、逐项引用读数。
// 只回答文字，不播报、不开跟随；问外出时后端只做预演并附上规则报告。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { streamPost } from '@/api/stream'
import { useElders } from '@/composables/useElders'
import { usePresentationStore } from '@/stores/presentation'
import DecisionPanel from '@/components/DecisionPanel.vue'

const { elders, currentElderId } = useElders()
const presentation = usePresentationStore()

const draft = ref('')
const busy = ref(false)
const messages = ref([])
const conversationId = ref(null)
const box = ref(null)
let abortStream = null

const INTENT_LABEL = {
  outing_request: '外出评估',
  health_query: '健康问询',
  general_qa: '通用问答',
  chitchat: '闲聊',
}

const BASE_SUGGESTIONS = [
  '我今天身体怎么样，有没有哪项不正常',
  '我最近血压控制得怎么样',
  '我今天适合出门吗',
  '我今天走了多少步，昨晚睡得怎么样',
]
// 健康评分还没揭示时，不在这里先把"评分"这个词露出来
const SUGGESTIONS = computed(() =>
  presentation.scoreLive ? [...BASE_SUGGESTIONS, '我今天的健康评分为什么扣分了'] : BASE_SUGGESTIONS,
)

const elderName = computed(() => elders.value.find((e) => e.id === currentElderId.value)?.name || '老人')
// 演示第 3 步之前智能体未接入：界面都在，但不能发问
const ready = computed(() => presentation.agentLive)

async function scrollToEnd() {
  await nextTick()
  if (box.value) box.value.scrollTop = box.value.scrollHeight
}

function ask(text) {
  if (!ready.value) return
  const question = (text || draft.value).trim()
  if (!question || busy.value || !currentElderId.value) return
  draft.value = ''
  busy.value = true
  messages.value.push({ role: 'user', text: question, at: dayjs() })
  const answer = { role: 'assistant', text: '', at: dayjs(), streaming: true, stage: '正在识别意图…' }
  messages.value.push(answer)
  scrollToEnd()

  abortStream = streamPost(
    '/agent/chat/stream',
    { elder_id: currentElderId.value, question, conversation_id: conversationId.value },
    (event) => {
      if (event.event === 'intent') {
        answer.intent = event.intent
        answer.stage = `意图：${INTENT_LABEL[event.intent] || event.intent}，正在调取数据…`
      } else if (event.event === 'stage' && event.detail) {
        answer.stage = event.detail
      } else if (event.event === 'token') {
        answer.text += event.text
        scrollToEnd()
      } else if (event.event === 'final') {
        const reply = event.reply
        answer.text = reply.reply_text
        answer.streaming = false
        answer.tools = reply.tools_used
        answer.decision = reply.decision
        answer.latency = reply.latency_ms
        answer.provider = reply.llm_provider
        conversationId.value = reply.conversation_id
        scrollToEnd()
      } else if (event.event === 'error') {
        answer.text = answer.text || `这一轮没有走通：${event.detail || '未知错误'}`
        answer.streaming = false
        answer.error = true
      }
    },
    () => {
      busy.value = false
      answer.streaming = false
    },
    (error) => {
      busy.value = false
      answer.streaming = false
      answer.error = true
      answer.text = answer.text || `请求失败：${error.message}`
    },
  )
}

function clearChat() {
  abortStream?.()
  messages.value = []
  conversationId.value = null
  busy.value = false
}

watch(currentElderId, clearChat)
onUnmounted(() => abortStream?.())
</script>

<template>
  <div class="chat">
    <div class="chat-head">
      <div>
        <div class="chat-title">健康咨询</div>
        <div class="muted">{{ elderName }}可以在这里直接问自己的身体和外出情况，回答会报出具体读数并说清偏高还是偏低</div>
      </div>
      <div class="chat-actions">
        <el-select v-model="currentElderId" style="width: 136px">
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button text :disabled="!messages.length" @click="clearChat">清空</el-button>
      </div>
    </div>

    <div ref="box" class="messages">
      <div v-if="!messages.length" class="empty">
        <div class="empty-title">
          {{ ready ? `${elderName}，想问什么就直接打字问我` : '智能体尚未接入' }}
        </div>
        <div class="suggestions">
          <button
            v-for="item in SUGGESTIONS"
            :key="item"
            class="chip pressable"
            :class="{ disabled: !ready }"
            :disabled="!ready"
            v-ripple
            @click="ask(item)"
          >
            {{ item }}
          </button>
        </div>
      </div>

      <div v-for="(message, index) in messages" :key="index" class="msg" :class="message.role">
        <div class="msg-bubble" :class="{ error: message.error }">
          <div v-if="message.role === 'assistant' && message.streaming && !message.text" class="thinking">
            <span class="dots"><i></i><i></i><i></i></span>{{ message.stage }}
          </div>
          <div v-else class="msg-text">
            {{ message.text }}<span v-if="message.streaming" class="caret"></span>
          </div>
          <div v-if="message.role === 'assistant' && !message.streaming && (message.tools?.length || message.intent)" class="msg-meta">
            <el-tag v-if="message.intent" size="small" effect="plain">{{ INTENT_LABEL[message.intent] || message.intent }}</el-tag>
            <span v-if="message.tools?.length" class="muted">依据 {{ message.tools.length }} 项数据</span>
            <span v-if="message.latency" class="muted">{{ (message.latency / 1000).toFixed(1) }} 秒</span>
          </div>
          <div v-if="message.decision" class="msg-decision">
            <DecisionPanel :decision="message.decision" />
          </div>
        </div>
        <span class="msg-time">{{ message.at.format('HH:mm:ss') }}</span>
      </div>
    </div>

    <div class="composer">
      <el-input
        v-model="draft"
        :placeholder="ready ? '比如：我今天血压怎么样 / 我能出门吗' : '智能体尚未接入'"
        :disabled="busy || !ready"
        @keyup.enter="ask()"
      />
      <el-button type="primary" :loading="busy" :disabled="!ready" @click="ask()">
        <el-icon><Promotion /></el-icon>
        发送
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 640px;
}

.chat-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line-soft);
}

.chat-title {
  font-size: 17px;
  font-weight: 600;
  margin-bottom: 3px;
}

.chat-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 4px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.empty {
  margin: auto;
  text-align: center;
  max-width: 560px;
}

.empty-title {
  font-size: 16px;
  color: var(--ink-mid);
  margin-bottom: 14px;
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.chip {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: var(--radius-pill);
  padding: 7px 14px;
  font: inherit;
  font-size: 14.5px;
  color: var(--ink-mid);
  cursor: pointer;
}

.chip:hover {
  border-color: var(--brand);
  color: var(--brand);
  background: var(--brand-tint);
}

.chip.disabled,
.chip.disabled:hover {
  cursor: not-allowed;
  color: var(--ink-soft);
  border-color: var(--line);
  border-style: dashed;
  background: var(--surface-tint);
  opacity: 0.8;
}

.msg {
  display: flex;
  flex-direction: column;
  max-width: 82%;
}

.msg.user {
  align-self: flex-end;
  align-items: flex-end;
}

.msg-bubble {
  padding: 11px 15px;
  border-radius: 16px;
  line-height: 1.75;
  font-size: 15.5px;
  background: var(--surface-tint);
  border: 1px solid var(--line-soft);
  white-space: pre-wrap;
}

.msg.user .msg-bubble {
  background: var(--brand);
  color: #fff;
  border-color: transparent;
}

.msg-bubble.error {
  background: var(--danger-soft);
  color: var(--danger);
}

.msg-time {
  font-size: 12.5px;
  color: var(--ink-soft);
  margin: 4px 6px 0;
}

.msg-meta {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 8px;
  font-size: 13.5px;
}

.msg-decision {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
  white-space: normal;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-soft);
}

.dots {
  display: inline-flex;
  gap: 3px;
}

.dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand);
  animation: dot-bounce 1.2s infinite;
}

.dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.dots i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes dot-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
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

.composer {
  display: flex;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}
</style>
