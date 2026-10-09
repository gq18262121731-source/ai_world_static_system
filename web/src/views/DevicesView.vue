<script setup>
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deviceApi } from '@/api'
import { useElders } from '@/composables/useElders'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { elders, load: loadElders, elderName } = useElders()

const devices = ref([])
const mqtt = ref(null)
const loading = ref(false)
const checking = ref(false)
const report = ref(null)
const dialogVisible = ref(false)
const commandVisible = ref(false)
const currentDevice = ref(null)

const form = reactive({ device_id: '', device_type: 'watch', name: '', elder_id: null })
const command = reactive({ command: 'tts_speak', text: '' })

const PROBE_LABEL = { ping: '主动探活', passive: '心跳判定', skipped: '已跳过' }
const SOURCE_LABEL = { serial: '真实手环', simulator: '模拟器', watch: '手表', manual: '手动录入' }
// 整体样本数只要有一只手环在发就一直在涨，断了哪一只必须逐只看
const BAND_STATE = { streaming: '采集中', stalled: '已断流', never: '从未上报' }

function relative(seconds) {
  if (seconds == null) return '--'
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${Math.floor(seconds / 3600)} 小时前`
}

const COMMANDS = [
  { value: 'tts_speak', label: '语音播报' },
  { value: 'stop_follow', label: '退出跟随' },
  { value: 'return_home', label: '返回家中' },
  { value: 'alarm_broadcast', label: '现场告警播报' },
]

async function load() {
  loading.value = true
  try {
    const [deviceList, mqttStatus] = await Promise.all([deviceApi.list(), deviceApi.mqttStatus()])
    devices.value = deviceList
    mqtt.value = mqttStatus
  } finally {
    loading.value = false
  }
}

/** 自检不是重新读数据库：机器狗会真的收到一条探活指令并回应。 */
async function runSelfCheck() {
  checking.value = true
  try {
    report.value = await deviceApi.selfCheck(3)
    const { online, total, with_issues: withIssues, status_changed: changed } = report.value.summary
    if (withIssues) {
      ElMessage.warning(`自检完成：${online}/${total} 台在线，${withIssues} 台需要处理`)
    } else {
      ElMessage.success(`自检完成：${online}/${total} 台设备全部正常`)
    }
    if (changed) {
      ElMessage.info(`已修正 ${changed} 台设备的在线状态`)
    }
    await load()
  } finally {
    checking.value = false
  }
}

function issueRowClass({ row }) {
  if (row.issues?.length) return row.status === 'online' ? 'row-warn' : 'row-bad'
  return ''
}

function openCreate() {
  Object.assign(form, { device_id: '', device_type: 'watch', name: '', elder_id: null })
  dialogVisible.value = true
}

async function save() {
  if (!form.device_id.trim()) {
    ElMessage.warning('请填写设备编号')
    return
  }
  await deviceApi.create(form)
  ElMessage.success('设备已注册')
  dialogVisible.value = false
  await load()
}

async function bindElder(row, elderId) {
  await deviceApi.update(row.device_id, { elder_id: elderId })
  ElMessage.success('绑定已更新')
  await load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除设备 ${row.device_id} 吗？`, '删除确认', { type: 'warning' })
  await deviceApi.remove(row.device_id)
  ElMessage.success('已删除')
  await load()
}

function openCommand(row) {
  currentDevice.value = row
  Object.assign(command, { command: 'tts_speak', text: '' })
  commandVisible.value = true
}

async function sendCommand() {
  const payload =
    command.command === 'tts_speak' || command.command === 'alarm_broadcast'
      ? { text: command.text, level: 'warning' }
      : {}
  await deviceApi.command(currentDevice.value.device_id, { command: command.command, payload })
  ElMessage.success('指令已下发')
  commandVisible.value = false
}

onMounted(async () => {
  await loadElders()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">设备管理</h2>
        <p class="page-subtitle">
          设备按 MQTT 主题接入，真机只要实现相同主题即可替换模拟器
        </p>
      </div>
      <div>
        <el-button @click="load"><el-icon><Refresh /></el-icon>刷新列表</el-button>
        <el-button type="primary" :loading="checking" @click="runSelfCheck">
          <el-icon><Aim /></el-icon>{{ checking ? '正在自检…' : '设备自检' }}
        </el-button>
        <el-button v-if="auth.isAdmin" @click="openCreate">
          <el-icon><Plus /></el-icon>注册设备
        </el-button>
      </div>
    </div>

    <div v-if="mqtt" class="card">
      <h3 class="card-title">MQTT 接入状态</h3>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="Broker">{{ mqtt.host }}:{{ mqtt.port }}</el-descriptions-item>
        <el-descriptions-item label="连接状态">
          <el-tag size="small" :type="mqtt.connected ? 'success' : 'info'">
            {{ mqtt.connected ? '已连接' : '未连接' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="已接收">{{ mqtt.received }} 条</el-descriptions-item>
        <el-descriptions-item label="已下发">{{ mqtt.published }} 条</el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="!mqtt.connected"
        type="info"
        :closable="false"
        show-icon
        style="margin-top: 10px"
      >
        未连接 MQTT Broker，平台正以 REST 直连模式运行：设备模拟器面板的操作会直接作用于后端，
        全流程功能不受影响。启动 Broker 后会自动重连。
      </el-alert>
    </div>

    <!-- 自检结果 -->
    <div v-if="report" class="card check-card">
      <h3 class="card-title">
        自检结果
        <el-tag size="small" effect="plain">
          {{ dayjs(report.checked_at).format('HH:mm:ss') }} · 耗时 {{ report.duration_ms }}ms
        </el-tag>
        <el-button link type="primary" style="margin-left: auto" @click="report = null">
          收起
        </el-button>
      </h3>

      <div class="check-summary">
        <div class="sum-item ok">
          <span class="sum-num">{{ report.summary.online }}</span>
          <span class="sum-label">在线</span>
        </div>
        <div class="sum-item" :class="report.summary.offline ? 'bad' : ''">
          <span class="sum-num">{{ report.summary.offline }}</span>
          <span class="sum-label">离线</span>
        </div>
        <div class="sum-item" :class="report.summary.with_issues ? 'warn' : ''">
          <span class="sum-num">{{ report.summary.with_issues }}</span>
          <span class="sum-label">需处理</span>
        </div>
        <div class="sum-item" :class="report.summary.unbound ? 'warn' : ''">
          <span class="sum-num">{{ report.summary.unbound }}</span>
          <span class="sum-label">未绑定</span>
        </div>
        <div class="sum-item">
          <span class="sum-num">{{ report.summary.status_changed }}</span>
          <span class="sum-label">状态修正</span>
        </div>
      </div>

      <el-alert
        v-if="!report.mqtt.connected"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      >
        MQTT 未连接，无法对机器狗做主动探活，本次只按心跳时间判定。
      </el-alert>

      <div v-if="report.gateways.length" class="gateways">
        <div
          v-for="gw in report.gateways"
          :key="gw.gateway_id"
          class="gateway"
          :class="{ bad: gw.issues.length }"
        >
          <el-icon :size="16"><Connection /></el-icon>
          <div>
            <div class="gw-title">
              采集网关 {{ gw.gateway_id }}
              <el-tag size="small" :type="gw.online ? 'success' : 'danger'">
                {{ gw.online ? '在线' : '离线' }}
              </el-tag>
            </div>
            <div class="muted">
              串口 {{ gw.serial_port || '未知' }} ·
              {{ gw.serial_connected ? '已连接' : '未连接' }} ·
              已上报 {{ gw.published }} 条
              <template v-if="gw.seconds_since_sample != null">
                · 最近样本 {{ Math.round(gw.seconds_since_sample) }} 秒前
              </template>
            </div>
            <div v-if="gw.wristbands?.length" class="bands">
              <span
                v-for="band in gw.wristbands"
                :key="band.mac_address"
                class="band"
                :class="band.state"
              >
                {{ band.mac_address }} · {{ band.sample_count }} 条 ·
                {{ BAND_STATE[band.state] || band.state }}
              </span>
            </div>
            <div v-for="(issue, i) in gw.issues" :key="i" class="status-danger issue-line">
              {{ issue }}
            </div>
          </div>
        </div>
      </div>

      <el-table :data="report.devices" size="small" :row-class-name="issueRowClass">
        <el-table-column label="设备" min-width="180">
          <template #default="{ row }">
            <div class="dev-id">{{ row.device_id }}</div>
            <div class="muted">
              {{ row.elder_name || '未绑定' }}
              <template v-if="row.data_source">
                · {{ SOURCE_LABEL[row.data_source] || row.data_source }}
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="检测结果" width="180">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'online' ? 'success' : 'danger'">
              {{ row.status === 'online' ? '在线' : '离线' }}
            </el-tag>
            <el-tag
              v-if="row.status_changed"
              size="small"
              type="warning"
              effect="plain"
              style="margin-left: 4px"
            >
              已修正
            </el-tag>
            <div class="muted">
              {{ PROBE_LABEL[row.probe] }} ·
              {{ row.seconds_since_seen != null ? relative(row.seconds_since_seen) : '从未上报' }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="发现的问题与建议" min-width="300">
          <template #default="{ row }">
            <div v-if="row.issues.length">
              <div v-for="(issue, i) in row.issues" :key="i" class="issue-line">· {{ issue }}</div>
              <div v-if="row.advice" class="muted advice">建议：{{ row.advice }}</div>
            </div>
            <span v-else class="status-normal">一切正常</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <h3 class="card-title">设备列表</h3>
      <el-table :data="devices" v-loading="loading" empty-text="暂无设备">
        <el-table-column prop="device_id" label="设备编号" width="170" />
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ row.device_type === 'watch' ? '健康手表' : '陪护机器狗' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="绑定老人" width="160">
          <template #default="{ row }">
            <el-select
              v-if="auth.isAdmin"
              :model-value="row.elder_id"
              placeholder="未绑定"
              clearable
              size="small"
              @change="(value) => bindElder(row, value)"
            >
              <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <span v-else>{{ row.elder_id ? elderName(row.elder_id) : '未绑定' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'online' ? 'success' : 'info'">
              {{ row.status === 'online' ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="电量" width="120">
          <template #default="{ row }">
            <el-progress
              :percentage="row.battery"
              :stroke-width="10"
              :color="row.battery < 25 ? 'var(--danger)' : row.battery < 45 ? 'var(--warn)' : 'var(--ok)'"
            />
          </template>
        </el-table-column>
        <el-table-column label="跟随模式" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.device_type === 'dog'" size="small" :type="row.extra?.follow_mode ? 'success' : 'info'" effect="plain">
              {{ row.extra?.follow_mode ? '跟随中' : '待命' }}
            </el-tag>
            <span v-else class="muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="最后心跳" width="160">
          <template #default="{ row }">
            {{ row.last_seen_at ? dayjs(row.last_seen_at).format('MM-DD HH:mm:ss') : '--' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.device_type === 'dog' && auth.isAdmin"
              link
              type="primary"
              @click="openCommand(row)"
            >
              下发指令
            </el-button>
            <el-button v-if="auth.isAdmin" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" title="注册设备" width="460px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="设备编号" required>
          <el-input v-model="form.device_id" placeholder="例如 WATCH-LJG-002" />
        </el-form-item>
        <el-form-item label="设备类型">
          <el-radio-group v-model="form.device_type">
            <el-radio value="watch">健康手表</el-radio>
            <el-radio value="dog">陪护机器狗</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="设备名称">
          <el-input v-model="form.name" placeholder="留空则使用设备编号" />
        </el-form-item>
        <el-form-item label="绑定老人">
          <el-select v-model="form.elder_id" clearable placeholder="可稍后绑定" style="width: 100%">
            <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="commandVisible" title="向机器狗下发指令" width="460px">
      <el-form label-width="90px">
        <el-form-item label="设备">
          <span>{{ currentDevice?.name }}（{{ currentDevice?.device_id }}）</span>
        </el-form-item>
        <el-form-item label="指令">
          <el-select v-model="command.command" style="width: 100%">
            <el-option v-for="item in COMMANDS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="['tts_speak', 'alarm_broadcast'].includes(command.command)"
          label="播报内容"
        >
          <el-input v-model="command.text" type="textarea" :rows="3" placeholder="机器狗将朗读这段话" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="commandVisible = false">取消</el-button>
        <el-button type="primary" @click="sendCommand">下发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.check-card {
  border-color: var(--brand-soft);
}

.check-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.sum-item {
  background: var(--surface-tint);
  border-radius: var(--radius);
  padding: 11px 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sum-item.ok {
  background: var(--ok-soft);
}

.sum-item.warn {
  background: var(--warn-soft);
}

.sum-item.bad {
  background: var(--danger-soft);
}

.sum-num {
  font-size: 23px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.sum-label {
  font-size: 14px;
  color: var(--ink-soft);
}

.gateways {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}

.gateway {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 11px 13px;
  border-radius: var(--radius);
  background: var(--brand-tint);
  color: var(--brand);
}

.gateway.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

.bands {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.band {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid currentColor;
}

.band.stalled,
.band.never {
  color: var(--danger);
}

.gw-title {
  font-size: 15.5px;
  font-weight: 600;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 7px;
}

.dev-id {
  font-size: 15.5px;
  font-weight: 500;
}

.issue-line {
  font-size: 14.5px;
  line-height: 1.7;
}

.advice {
  margin-top: 3px;
}

:deep(.row-warn) {
  background: var(--warn-soft);
}

:deep(.row-bad) {
  background: var(--danger-soft);
}
</style>
