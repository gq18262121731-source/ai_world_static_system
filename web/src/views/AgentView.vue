<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { agentApi } from '@/api'
import { useElders } from '@/composables/useElders'
import { usePresentationStore } from '@/stores/presentation'
import VoiceStage from '@/components/VoiceStage.vue'
import AgentChat from '@/components/AgentChat.vue'
import TurnDetail from '@/components/TurnDetail.vue'
import BentoCard from '@/components/BentoCard.vue'

const { elders, load: loadElders } = useElders()
const presentation = usePresentationStore()

const activeTab = ref('chat')
const intents = ref([])

// 对话现场
const liveTurn = ref(null)

// 对话记录
const filters = ref({ elder_id: null, intent: null, keyword: '' })
const turns = ref([])
const total = ref(0)
const page = ref(1)
const turnsLoading = ref(false)
const drawerVisible = ref(false)
const drawerTurn = ref(null)

const INTENT_LABEL = computed(() =>
  Object.fromEntries(intents.value.map((item) => [item.value, item.label])),
)

async function loadTurns() {
  turnsLoading.value = true
  try {
    const data = await agentApi.turns({
      elder_id: filters.value.elder_id || undefined,
      intent: filters.value.intent || undefined,
      keyword: filters.value.keyword || undefined,
      page: page.value,
      page_size: 20,
    })
    turns.value = data.items
    total.value = data.total
  } finally {
    turnsLoading.value = false
  }
}

function onTurn(reply) {
  liveTurn.value = {
    message_id: reply.conversation_id,
    conversation_id: reply.conversation_id,
    elder_id: reply.elder_id,
    intent: reply.intent,
    question: reply.request_text,
    answer: reply.reply_text,
    audio_url: reply.audio_url,
    tools: reply.tools_used,
    stages: reply.stages || {},
    llm_provider: reply.llm_provider,
    decision: reply.decision,
    task_id: reply.task_id,
    latency_ms: reply.latency_ms,
    created_at: new Date().toISOString(),
  }
}

watch(activeTab, (tab) => {
  if (tab === 'turns') loadTurns()
})

onMounted(async () => {
  await loadElders()
  intents.value = await agentApi.intents()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">智能体</h2>
        <p class="page-subtitle">
          两个模块：老人在系统里打字咨询自己的身体，回答报出具体读数并说清高低；机器狗在线时持续倾听，老人开口就自动转文字、分析并用真实音色回答
        </p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="tabs">
      <el-tab-pane name="chat">
        <template #label>
          <span class="tab-label"><el-icon><ChatLineSquare /></el-icon>健康咨询</span>
        </template>
        <div class="card">
          <AgentChat />
        </div>
      </el-tab-pane>

      <el-tab-pane name="voice">
        <template #label>
          <span class="tab-label"><el-icon><Microphone /></el-icon>机器狗语音</span>
        </template>
        <div class="live-grid">
          <BentoCard :span="8" :padded="false" class="stage-card">
            <div class="stage-inner">
              <VoiceStage v-if="activeTab === 'voice'" @turn="onTurn" />
            </div>
          </BentoCard>

          <BentoCard title="本轮分析过程" icon="Operation" :span="4">
            <TurnDetail :turn="liveTurn" />
          </BentoCard>
        </div>
      </el-tab-pane>

      <el-tab-pane label="对话记录" name="turns">
        <div class="card">
          <div class="filters">
            <el-select
              v-model="filters.elder_id"
              placeholder="全部老人"
              clearable
              style="width: 140px"
              @change="loadTurns"
            >
              <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-select
              v-model="filters.intent"
              placeholder="全部意图"
              clearable
              style="width: 150px"
              @change="loadTurns"
            >
              <el-option
                v-for="item in intents"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
            <el-input
              v-model="filters.keyword"
              placeholder="搜索对话内容"
              clearable
              style="width: 220px"
              @keyup.enter="loadTurns"
              @clear="loadTurns"
            />
            <el-button @click="loadTurns"><el-icon><Search /></el-icon>查询</el-button>
          </div>

          <el-table
            :data="turns"
            v-loading="turnsLoading"
            :empty-text="presentation.agentLive ? '没有匹配的对话' : '智能体尚未接入'"
            @row-click="(row) => { drawerTurn = row; drawerVisible = true }"
          >
            <el-table-column label="时间" width="150">
              <template #default="{ row }">
                {{ dayjs(row.created_at).format('MM-DD HH:mm:ss') }}
              </template>
            </el-table-column>
            <el-table-column prop="elder_name" label="老人" width="90" />
            <el-table-column label="意图" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">
                  {{ INTENT_LABEL[row.intent] || row.intent }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="question" label="老人说" min-width="160" show-overflow-tooltip />
            <el-table-column prop="answer" label="机器狗答" min-width="280" show-overflow-tooltip />
            <el-table-column label="依据" width="90">
              <template #default="{ row }">
                <span v-if="row.tools?.length" class="muted">{{ row.tools.length }} 项数据</span>
                <span v-else class="muted">--</span>
              </template>
            </el-table-column>
            <el-table-column label="决策" width="110">
              <template #default="{ row }">
                <el-tag
                  v-if="row.decision"
                  size="small"
                  :type="{ allow: 'success', conditional: 'warning', deny: 'danger' }[row.decision.decision]"
                >
                  {{ { allow: '允许外出', conditional: '有条件', deny: '不建议' }[row.decision.decision] }}
                </el-tag>
                <span v-else class="muted">--</span>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            v-model:current-page="page"
            :page-size="20"
            :total="total"
            layout="total, prev, pager, next"
            style="margin-top: 12px; justify-content: flex-end"
            @current-change="loadTurns"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerVisible" title="对话分析过程" size="620px">
      <TurnDetail :turn="drawerTurn" />
    </el-drawer>
  </div>
</template>

<style scoped>
.tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.live-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 18px;
  align-items: start;
}

.stage-inner {
  padding: 20px;
}

.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

@media (max-width: 1000px) {
  .live-grid {
    grid-template-columns: 1fr;
  }
}
</style>
