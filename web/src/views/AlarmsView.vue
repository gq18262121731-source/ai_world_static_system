<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { alarmApi } from '@/api'
import { useElders } from '@/composables/useElders'
import { useRealtimeStore } from '@/stores/realtime'

const route = useRoute()
const realtime = useRealtimeStore()
const { elders, load: loadElders, elderName } = useElders()

const alarms = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const filters = ref({ elder_id: null, status: null, level: null, alarm_type: null })

const drawerVisible = ref(false)
const detail = ref(null)
const handleForm = ref({ status: 'acked', note: '' })

const TYPE_LABEL = {
  fall: '跌倒',
  sos: '主动求助',
  geofence: '越界',
  timeout: '超时未归',
  health: '健康异常',
  follow_lost: '跟随丢失',
  device_offline: '设备异常',
}
const LEVEL_META = {
  info: { label: '提示', type: 'info' },
  warning: { label: '警告', type: 'warning' },
  critical: { label: '紧急', type: 'danger' },
}
const STATUS_META = {
  pending: { label: '待处理', type: 'danger' },
  confirming: { label: '语音确认中', type: 'warning' },
  acked: { label: '已受理', type: 'primary' },
  resolved: { label: '已处置', type: 'success' },
  false_alarm: { label: '误报', type: 'info' },
}

async function load() {
  loading.value = true
  try {
    const data = await alarmApi.list({
      elder_id: filters.value.elder_id || undefined,
      status: filters.value.status || undefined,
      level: filters.value.level || undefined,
      alarm_type: filters.value.alarm_type || undefined,
      page: page.value,
      page_size: 20,
    })
    alarms.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  detail.value = await alarmApi.detail(row.id)
  handleForm.value = { status: 'acked', note: '' }
  drawerVisible.value = true
}

async function submitHandle() {
  await alarmApi.handle(detail.value.id, handleForm.value)
  ElMessage.success('处置已记录')
  drawerVisible.value = false
  await load()
}

// 收到新告警时自动刷新列表
watch(
  () => realtime.events.length,
  () => {
    const latest = realtime.events[0]
    if (latest && ['alarm', 'alarm_updated'].includes(latest.event)) load()
  },
)

onMounted(async () => {
  await loadElders()
  if (route.query.id) {
    await openDetail({ id: Number(route.query.id) })
  }
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">告警中心</h2>
        <p class="page-subtitle">跌倒双源融合、越界与超时、健康异常的统一处置入口</p>
      </div>
      <div class="filters">
        <el-select v-model="filters.elder_id" placeholder="全部老人" clearable style="width: 130px" @change="load">
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="filters.alarm_type" placeholder="全部类型" clearable style="width: 130px" @change="load">
          <el-option v-for="(label, value) in TYPE_LABEL" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="filters.level" placeholder="全部级别" clearable style="width: 120px" @change="load">
          <el-option v-for="(meta, value) in LEVEL_META" :key="value" :label="meta.label" :value="value" />
        </el-select>
        <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 130px" @change="load">
          <el-option v-for="(meta, value) in STATUS_META" :key="value" :label="meta.label" :value="value" />
        </el-select>
        <el-button @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
      </div>
    </div>

    <div class="card">
      <el-table :data="alarms" v-loading="loading" empty-text="暂无告警">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ dayjs(row.triggered_at).format('MM-DD HH:mm:ss') }}</template>
        </el-table-column>
        <el-table-column label="老人" width="100">
          <template #default="{ row }">{{ elderName(row.elder_id) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ TYPE_LABEL[row.alarm_type] || row.alarm_type }}</template>
        </el-table-column>
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="LEVEL_META[row.level]?.type">{{ LEVEL_META[row.level]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="告警标题" min-width="150" />
        <el-table-column prop="description" label="说明" min-width="280" show-overflow-tooltip />
        <el-table-column label="升级次数" width="100">
          <template #default="{ row }">{{ row.escalation_level }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_META[row.status]?.type">
              {{ STATUS_META[row.status]?.label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情/处置</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        :page-size="20"
        :total="total"
        layout="total, prev, pager, next"
        style="margin-top: 12px; justify-content: flex-end"
        @current-change="load"
      />
    </div>

    <el-drawer v-model="drawerVisible" title="告警详情与处置" size="560px">
      <template v-if="detail">
        <el-alert
          :type="LEVEL_META[detail.level]?.type === 'danger' ? 'error' : LEVEL_META[detail.level]?.type"
          :title="detail.title"
          :description="detail.description"
          :closable="false"
          show-icon
        />

        <el-descriptions :column="2" border size="small" style="margin-top: 16px">
          <el-descriptions-item label="老人">{{ detail.elder_name }}</el-descriptions-item>
          <el-descriptions-item label="类型">
            {{ TYPE_LABEL[detail.alarm_type] || detail.alarm_type }}
          </el-descriptions-item>
          <el-descriptions-item label="触发时间">
            {{ dayjs(detail.triggered_at).format('YYYY-MM-DD HH:mm:ss') }}
          </el-descriptions-item>
          <el-descriptions-item label="当前状态">
            {{ STATUS_META[detail.status]?.label }}
          </el-descriptions-item>
          <el-descriptions-item label="关联外出任务">
            {{ detail.task_id ? `#${detail.task_id}` : '无' }}
          </el-descriptions-item>
          <el-descriptions-item label="升级次数">{{ detail.escalation_level }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.handled_by" label="处置人">
            {{ detail.handled_by }}
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.handle_note" label="处置说明" :span="2">
            {{ detail.handle_note }}
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="detail.data?.sources">
          <h4 class="section">检测来源融合</h4>
          <el-tag
            v-for="source in detail.data.sources"
            :key="source"
            size="small"
            type="danger"
            effect="plain"
            style="margin-right: 6px"
          >
            {{ { watch: '健康手表', dog: '机器狗' }[source] || source }}
          </el-tag>
          <span v-if="detail.data.fused_confidence" class="muted" style="margin-left: 8px">
            融合置信度 {{ (detail.data.fused_confidence * 100).toFixed(0) }}%
          </span>
          <div v-if="detail.data.elder_reply" class="speech-bubble" style="margin-top: 10px">
            <strong>老人回应：</strong>{{ detail.data.elder_reply }}
          </div>
        </template>

        <h4 class="section">通知记录</h4>
        <el-timeline v-if="detail.notifications?.length">
          <el-timeline-item
            v-for="item in detail.notifications"
            :key="item.id"
            :timestamp="dayjs(item.sent_at).format('MM-DD HH:mm:ss')"
          >
            <b>{{ { app_push: 'App 推送', sms: '短信', voice_call: '语音外呼', websocket: '家属端推送', dog_speak: '机器狗播报' }[item.channel] || item.channel }}</b>
            → {{ item.target_name }} {{ item.target }}
            <div class="muted">{{ item.content }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无通知记录" :image-size="60" />

        <template v-if="!['resolved', 'false_alarm'].includes(detail.status)">
          <h4 class="section">处置</h4>
          <el-form label-width="80px">
            <el-form-item label="处置结果">
              <el-radio-group v-model="handleForm.status">
                <el-radio value="acked">已受理</el-radio>
                <el-radio value="resolved">已处置</el-radio>
                <el-radio value="false_alarm">误报</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="说明">
              <el-input v-model="handleForm.note" type="textarea" :rows="3" placeholder="记录处置过程，例如已电话联系家属确认" />
            </el-form-item>
            <el-button type="primary" @click="submitHandle">提交处置</el-button>
          </el-form>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.section {
  margin: 20px 0 10px;
  font-size: 15.5px;
}
</style>
