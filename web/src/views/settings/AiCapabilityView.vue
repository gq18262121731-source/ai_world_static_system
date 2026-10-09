<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { agentApi } from '@/api'

const status = ref(null)
const metrics = ref(null)
const results = ref([])
const testing = ref(false)

const CAPABILITY = {
  llm: { label: '大模型', desc: '负责综合归因、生成结论与口语化播报', icon: 'MagicStick' },
  asr: { label: '语音识别', desc: '把机器狗采集的音频转成文字', icon: 'Microphone' },
  tts: { label: '语音合成', desc: '把回复文本变成机器狗的声音', icon: 'Bell' },
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

async function load() {
  const [statusData, metricsData] = await Promise.all([agentApi.status(), agentApi.metrics()])
  status.value = statusData
  metrics.value = metricsData
}

async function runTest() {
  testing.value = true
  try {
    results.value = await agentApi.testProviders()
    const failed = results.value.filter((item) => !item.ok)
    if (failed.length) ElMessage.warning(`${failed.length} 项能力调用失败，请检查密钥与网络`)
    else ElMessage.success('全部能力调用正常')
    await load()
  } finally {
    testing.value = false
  }
}

const providers = computed(() => {
  if (!status.value) return []
  return ['llm', 'asr', 'tts'].map((key) => ({
    key,
    ...CAPABILITY[key],
    provider: status.value[key].provider,
    model: status.value[key].model || '',
    fallback: status.value[key].fallback_count || 0,
    error: status.value[key].last_error || '',
    result: results.value.find((item) => item.capability === key) || null,
  }))
})

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">AI 能力</h2>
        <p class="page-subtitle">查看当前使用的模型服务，并验证密钥与网络是否可用</p>
      </div>
      <el-button type="primary" :loading="testing" @click="runTest">
        <el-icon><Connection /></el-icon>连通性测试
      </el-button>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      提供方通过 backend/.env 的 LLM_PROVIDER / ASR_PROVIDER / TTS_PROVIDER 切换，修改后重启后端生效。
      未配置密钥时使用内置 Mock 实现；真实模型调用失败会自动降级到 Mock，业务不会中断。
    </el-alert>

    <div class="providers">
      <div v-for="item in providers" :key="item.key" class="provider liftable">
        <div class="provider-top">
          <span class="provider-icon">
            <el-icon :size="19"><component :is="item.icon" /></el-icon>
          </span>
          <div>
            <div class="provider-label">{{ item.label }}</div>
            <div class="muted">{{ item.desc }}</div>
          </div>
        </div>

        <div class="provider-value">{{ item.provider }}</div>
        <div v-if="item.model" class="muted">{{ item.model }}</div>

        <div class="provider-foot">
          <el-tag v-if="item.fallback" size="small" type="warning" effect="plain">
            已降级 {{ item.fallback }} 次
          </el-tag>
          <template v-if="item.result">
            <el-tag size="small" :type="item.result.ok ? 'success' : 'danger'">
              {{ item.result.ok ? '调用正常' : '调用失败' }}
            </el-tag>
            <span class="muted">{{ item.result.latency_ms }}ms</span>
          </template>
        </div>

        <div v-if="item.result?.sample" class="provider-sample">
          {{ item.result.sample }}
        </div>
        <div v-if="item.error" class="provider-error">{{ item.error }}</div>
      </div>
    </div>

    <div v-if="metrics" class="card">
      <h3 class="card-title">对话运行指标</h3>
      <div class="metrics">
        <div class="metric">
          <span class="metric-num">{{ metrics.total_turns }}</span>
          <span class="muted">累计对话轮次</span>
        </div>
        <div class="metric">
          <span class="metric-num">{{ metrics.avg_latency_ms }}<em>ms</em></span>
          <span class="muted">平均响应耗时</span>
        </div>
        <div class="metric">
          <span class="metric-num">{{ metrics.max_latency_ms }}<em>ms</em></span>
          <span class="muted">峰值耗时</span>
        </div>
      </div>

      <h3 class="card-title" style="margin-top: 22px">意图分布</h3>
      <div
        v-for="(count, intent) in metrics.intent_distribution"
        :key="intent"
        class="dist-row"
      >
        <span class="dist-label">{{ INTENT_LABEL[intent] || intent }}</span>
        <el-progress :percentage="Math.round((count / Math.max(1, metrics.total_turns)) * 100)" />
        <span class="dist-count">{{ count }} 次</span>
      </div>
      <el-empty
        v-if="!Object.keys(metrics.intent_distribution).length"
        description="还没有对话记录"
        :image-size="60"
      />
    </div>
  </div>
</template>

<style scoped>
.providers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}

.provider {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 18px;
  background: var(--surface);
}

.provider-top {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.provider-icon {
  width: 38px;
  height: 38px;
  border-radius: 13px;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.provider-label {
  font-size: 16px;
  font-weight: 600;
}

.provider-value {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.provider-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 12px;
  min-height: 24px;
}

.provider-sample {
  margin-top: 10px;
  font-size: 14.5px;
  line-height: 1.7;
  background: var(--surface-tint);
  border-radius: var(--radius-sm);
  padding: 9px 11px;
  color: var(--ink-mid);
  word-break: break-all;
}

.provider-error {
  margin-top: 8px;
  font-size: 13.5px;
  color: var(--danger);
  word-break: break-all;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 14px;
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
  background: var(--surface-tint);
  border-radius: var(--radius);
  padding: 14px;
}

.metric-num {
  font-size: 25px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.metric-num em {
  font-style: normal;
  font-size: 15px;
  font-weight: 400;
  color: var(--ink-soft);
  margin-left: 2px;
}

.dist-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.dist-label {
  width: 88px;
  font-size: 15px;
}

.dist-row :deep(.el-progress) {
  flex: 1;
}

.dist-count {
  width: 56px;
  text-align: right;
  font-size: 14px;
  color: var(--ink-soft);
}
</style>
