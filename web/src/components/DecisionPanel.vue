<script setup>
import { computed } from 'vue'

const props = defineProps({
  decision: { type: Object, default: null },
  ruleReport: { type: Object, default: null },
  speechText: { type: String, default: '' },
})

const DECISION_META = {
  allow: { label: '允许外出', type: 'success', icon: 'CircleCheck' },
  conditional: { label: '有条件允许', type: 'warning', icon: 'WarnTriangleFilled' },
  deny: { label: '不建议外出', type: 'danger', icon: 'CircleClose' },
}

const meta = computed(() => DECISION_META[props.decision?.decision] || DECISION_META.deny)
const report = computed(() => props.ruleReport || props.decision?.rule_report || null)
const blockHits = computed(() => (report.value?.hits || []).filter((h) => h.level === 'block'))
const warnHits = computed(() => (report.value?.hits || []).filter((h) => h.level === 'warn'))
const speech = computed(() => props.speechText || props.decision?.speech_text || '')
const limits = computed(() => props.decision?.limits || {})

// 智能体的补充输出：落库的决策放在 extras 里，评估预演的结果是平铺字段，两种都认
const EXTRA_KEYS = ['purpose', 'expected_minutes', 'preparation', 'route_hint', 'best_time_hint', 'care_note']
const extras = computed(() => {
  const source = props.decision?.extras || props.decision || {}
  return Object.fromEntries(EXTRA_KEYS.map((key) => [key, source[key]]))
})
const hasExtras = computed(() =>
  Boolean(extras.value.purpose || extras.value.preparation?.length || extras.value.route_hint || extras.value.best_time_hint || extras.value.care_note),
)

// 评估时的天气材料：实况、预警、降水、日落，让人看到判断依据了哪些天气事实
const weather = computed(() => props.decision?.weather_snapshot || props.decision?.weather || null)
const weatherChips = computed(() => {
  const w = weather.value
  if (!w) return []
  const chips = []
  if (w.condition) chips.push({ text: `${w.condition} ${w.temperature ?? '--'}℃`, type: 'info' })
  if (w.feels_like != null && w.feels_like !== w.temperature) chips.push({ text: `体感 ${w.feels_like}℃`, type: 'info' })
  if (w.aqi != null) chips.push({ text: `空气 ${w.aqi_category || ''} ${w.aqi}`.trim(), type: w.aqi >= 150 ? 'danger' : w.aqi >= 100 ? 'warning' : 'success' })
  if (w.wind_level != null) chips.push({ text: `${w.wind_level} 级风`, type: w.wind_level >= 5 ? 'warning' : 'info' })
  const summary = w.minutely?.summary
  if (summary) chips.push({ text: summary, type: w.minutely?.rain_starts_in_minutes != null ? 'warning' : 'success' })
  for (const alert of w.alerts || []) {
    chips.push({ text: alert.headline || `${alert.event}预警`, type: ['orange', 'red'].includes(alert.color) ? 'danger' : 'warning' })
  }
  if (w.today?.sunset) chips.push({ text: `日落 ${w.today.sunset}`, type: 'info' })
  return chips
})
</script>

<template>
  <div v-if="decision" class="decision">
    <div class="head">
      <el-tag :type="meta.type" size="large" effect="dark">
        <el-icon><component :is="meta.icon" /></el-icon>
        {{ meta.label }}
      </el-tag>
      <span v-if="decision.confidence" class="muted">
        置信度 {{ (decision.confidence * 100).toFixed(0) }}%
      </span>
      <el-tag v-if="decision.overridden_by_rules" type="danger" size="small" effect="plain">
        模型结论被规则层否决
      </el-tag>
      <el-tag v-if="decision.llm_provider" size="small" effect="plain">
        推理来源 {{ decision.llm_provider }}
      </el-tag>
    </div>

    <div v-if="speech" class="speech-bubble">
      <strong>机器狗播报：</strong>{{ speech }}
    </div>

    <div v-if="hasExtras" class="agent-extras">
      <div v-if="extras.purpose" class="extra-row">
        <span class="extra-label">听出来的意图</span>
        <span>
          {{ extras.purpose }}
          <template v-if="extras.expected_minutes">，大概 {{ extras.expected_minutes }} 分钟</template>
        </span>
      </div>
      <div v-if="extras.preparation?.length" class="extra-row">
        <span class="extra-label">出门带上</span>
        <span class="chips">
          <el-tag v-for="item in extras.preparation" :key="item" size="small" effect="plain">{{ item }}</el-tag>
        </span>
      </div>
      <div v-if="extras.route_hint" class="extra-row">
        <span class="extra-label">路线提示</span>
        <span>{{ extras.route_hint }}</span>
      </div>
      <div v-if="extras.best_time_hint" class="extra-row">
        <span class="extra-label">再看看的时间</span>
        <span>{{ extras.best_time_hint }}</span>
      </div>
      <div v-if="extras.care_note" class="extra-row care">
        <span class="extra-label">一句关心</span>
        <span>{{ extras.care_note }}</span>
      </div>
    </div>

    <div v-if="weatherChips.length" class="weather-chips">
      <span class="extra-label">评估时的天气</span>
      <el-tag v-for="(chip, index) in weatherChips" :key="index" size="small" :type="chip.type" effect="light">
        {{ chip.text }}
      </el-tag>
    </div>

    <div v-if="limits.duration_minutes" class="limits">
      <el-tag type="info" effect="plain">限时 {{ limits.duration_minutes }} 分钟</el-tag>
      <el-tag type="info" effect="plain">活动半径 {{ limits.radius_meters }} 米</el-tag>
      <el-tag v-if="limits.return_before" type="info" effect="plain">
        建议 {{ limits.return_before }} 前返回
      </el-tag>
      <el-tag v-if="limits.need_follow" type="info" effect="plain">机器狗全程跟随</el-tag>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <div class="section-title">判定理由</div>
        <ul class="reason-list">
          <li v-for="(reason, index) in decision.reasons" :key="index">{{ reason }}</li>
        </ul>
      </el-col>
      <el-col :xs="24" :md="12">
        <div class="section-title">给老人的建议</div>
        <ul class="reason-list">
          <li v-for="(advice, index) in decision.advice" :key="index">{{ advice }}</li>
        </ul>
      </el-col>
    </el-row>

    <template v-if="report">
      <div class="section-title">规则引擎判定明细</div>
      <div v-if="blockHits.length" class="hits">
        <div v-for="(hit, index) in blockHits" :key="`b${index}`" class="hit is-block">
          <el-tag type="danger" size="small">拦截</el-tag>
          <div>
            <div class="hit-title">{{ hit.title }}</div>
            <div class="muted">{{ hit.detail }}</div>
            <div v-if="hit.threshold" class="muted">阈值：{{ hit.threshold }}</div>
          </div>
        </div>
      </div>
      <div v-if="warnHits.length" class="hits">
        <div v-for="(hit, index) in warnHits" :key="`w${index}`" class="hit is-warn">
          <el-tag type="warning" size="small">提醒</el-tag>
          <div>
            <div class="hit-title">{{ hit.title }}</div>
            <div class="muted">{{ hit.detail }}</div>
          </div>
        </div>
      </div>
      <div v-if="report.passed_rules?.length" class="passed">
        <span class="muted">通过检查：</span>
        <el-tag
          v-for="rule in report.passed_rules"
          :key="rule"
          size="small"
          type="success"
          effect="plain"
        >
          {{ rule }}
        </el-tag>
      </div>
    </template>
  </div>
  <el-empty v-else description="暂无评估结果" :image-size="70" />
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.limits {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0;
}

.agent-extras {
  display: grid;
  gap: 8px;
  margin: 12px 0;
  padding: 12px 14px;
  border-radius: var(--radius);
  background: var(--surface-tint);
  border: 1px solid var(--line-soft);
}

.extra-row {
  display: flex;
  gap: 12px;
  align-items: baseline;
  line-height: 1.6;
}

.extra-row.care {
  color: var(--brand-deep);
}

.extra-label {
  flex-shrink: 0;
  min-width: 96px;
  font-size: 14px;
  color: var(--ink-soft);
}

.chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.weather-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin: 10px 0;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 14px 0 6px;
}

.hits {
  display: grid;
  gap: 8px;
  margin-bottom: 8px;
}

.hit {
  display: flex;
  gap: 10px;
  padding: 9px 11px;
  border-radius: 8px;
  align-items: flex-start;
}

.hit.is-block {
  background: var(--danger-soft);
}

.hit.is-warn {
  background: var(--warn-soft);
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
  margin-top: 8px;
}
</style>
