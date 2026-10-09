<script setup>
import { computed } from 'vue'
import dayjs from 'dayjs'
import DecisionPanel from '@/components/DecisionPanel.vue'

const props = defineProps({
  turn: { type: Object, default: null },
})

const INTENT_LABEL = {
  outing_request: '外出申请',
  end_outing: '结束外出',
  health_query: '健康问询',
  general_qa: '通用问答',
  sos: '紧急求助',
  fall_confirm: '跌倒确认',
  chitchat: '日常闲聊',
}

const TOOL_LABEL = {
  get_elder_profile: '读取老人档案',
  get_recent_health: '查询近期健康数据',
  get_outing_stats: '统计外出频次',
  get_weather: '获取天气',
  get_device_status: '检查设备状态',
  evaluate_outing: '执行外出评估',
  start_following: '启动机器狗跟随',
  end_following: '结束跟随任务',
  raise_alarm: '触发告警',
}

const STAGE_LABEL = {
  intent_ms: '意图识别',
  reasoning_ms: '工具调用与生成',
  tts_ms: '语音合成',
}

const STAGE_COLOR = {
  intent_ms: 'var(--brand)',
  reasoning_ms: 'var(--ok)',
  tts_ms: 'var(--warn)',
}

const stages = computed(() => {
  const source = props.turn?.stages || {}
  const entries = Object.entries(source).filter(([, value]) => value >= 0)
  const total = entries.reduce((sum, [, value]) => sum + value, 0) || 1
  return entries.map(([key, value]) => ({
    key,
    label: STAGE_LABEL[key] || key,
    value,
    percent: (value / total) * 100,
    color: STAGE_COLOR[key] || 'var(--ink-soft)',
  }))
})

const stageTotal = computed(() => stages.value.reduce((sum, s) => sum + s.value, 0))
</script>

<template>
  <div v-if="turn" class="detail">
    <div class="chat">
      <div class="bubble elder">
        <div class="who">老人说</div>
        <div class="text">{{ turn.question || '（无记录）' }}</div>
      </div>
      <div class="bubble dog">
        <div class="who">机器狗回答</div>
        <div class="text">{{ turn.answer }}</div>
        <audio v-if="turn.audio_url" :src="turn.audio_url" controls class="audio" />
      </div>
    </div>

    <div class="meta-row">
      <el-tag size="small" type="primary">{{ INTENT_LABEL[turn.intent] || turn.intent }}</el-tag>
      <el-tag v-if="turn.intent_confidence" size="small" effect="plain">
        置信度 {{ (turn.intent_confidence * 100).toFixed(0) }}%
      </el-tag>
      <el-tag size="small" effect="plain">总耗时 {{ turn.latency_ms }}ms</el-tag>
      <el-tag v-if="turn.llm_provider" size="small" effect="plain">
        推理来源 {{ turn.llm_provider }}
      </el-tag>
      <el-tag v-if="turn.task_id" size="small" type="success" effect="plain">
        外出任务 #{{ turn.task_id }}
      </el-tag>
      <span class="muted" style="margin-left: auto">
        {{ dayjs(turn.created_at).format('MM-DD HH:mm:ss') }}
      </span>
    </div>

    <div class="section-title">工具调用链</div>
    <div v-if="turn.tools?.length" class="tools">
      <template v-for="(tool, index) in turn.tools" :key="tool">
        <span class="tool">{{ TOOL_LABEL[tool] || tool }}</span>
        <el-icon v-if="index < turn.tools.length - 1" class="arrow"><Right /></el-icon>
      </template>
    </div>
    <div v-else class="muted">本轮未调用工具，直接由模型作答</div>

    <template v-if="stages.length">
      <div class="section-title">耗时分解（合计 {{ stageTotal }}ms）</div>
      <div class="stage-bar">
        <div
          v-for="stage in stages"
          :key="stage.key"
          class="stage-seg"
          :style="{ width: `${stage.percent}%`, background: stage.color }"
          :title="`${stage.label} ${stage.value}ms`"
        />
      </div>
      <div class="stage-legend">
        <span v-for="stage in stages" :key="stage.key">
          <i class="dot" :style="{ background: stage.color }"></i>
          {{ stage.label }} {{ stage.value }}ms
        </span>
      </div>
    </template>

    <template v-if="turn.decision">
      <div class="section-title">本轮产生的外出决策</div>
      <DecisionPanel :decision="turn.decision" :speech-text="turn.answer" />
    </template>
  </div>
  <el-empty v-else description="选择一轮对话查看推理过程" :image-size="70" />
</template>

<style scoped>
.chat {
  display: grid;
  gap: 10px;
}

.bubble {
  padding: 10px 12px;
  border-radius: 9px;
}

.bubble.elder {
  background: var(--surface-tint);
}

.bubble.dog {
  background: var(--brand-soft);
  border-left: 3px solid var(--brand);
}

.who {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-soft);
  margin-bottom: 4px;
}

.bubble .text {
  font-size: 15.5px;
  line-height: 1.8;
}

.audio {
  width: 100%;
  height: 32px;
  margin-top: 8px;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 14px 0;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 16px 0 8px;
}

.tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tool {
  background: var(--brand-tint);
  border-radius: 6px;
  padding: 4px 9px;
  font-size: 14px;
}

.arrow {
  color: var(--ink-soft);
  font-size: 14px;
}

.stage-bar {
  display: flex;
  height: 14px;
  border-radius: 7px;
  overflow: hidden;
  background: var(--line-soft);
}

.stage-seg {
  height: 100%;
  min-width: 2px;
}

.stage-legend {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 8px;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
}
</style>
