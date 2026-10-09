<script setup>
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { outingApi } from '@/api'
import { useElders } from '@/composables/useElders'
import DecisionPanel from '@/components/DecisionPanel.vue'
import TrackMap from '@/components/TrackMap.vue'

const { elders, load: loadElders, elderName } = useElders()

const activeTab = ref('requests')
const filters = ref({ elder_id: null, decision: null })
const requests = ref([])
const requestTotal = ref(0)
const page = ref(1)
const tasks = ref([])
const taskTotal = ref(0)
const taskPage = ref(1)
const loading = ref(false)

const drawerVisible = ref(false)
const detail = ref(null)
const trackVisible = ref(false)
const currentTask = ref(null)
const currentTrack = ref([])

const DECISION_META = {
  allow: { label: '允许外出', type: 'success' },
  conditional: { label: '有条件允许', type: 'warning' },
  deny: { label: '不建议外出', type: 'danger' },
}
const STATUS_META = {
  active: { label: '进行中', type: 'success' },
  completed: { label: '已完成', type: 'info' },
  aborted: { label: '已中止', type: 'danger' },
}

async function loadRequests() {
  loading.value = true
  try {
    const data = await outingApi.requests({
      elder_id: filters.value.elder_id || undefined,
      decision: filters.value.decision || undefined,
      page: page.value,
      page_size: 20,
    })
    requests.value = data.items
    requestTotal.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadTasks() {
  loading.value = true
  try {
    const data = await outingApi.tasks({
      elder_id: filters.value.elder_id || undefined,
      page: taskPage.value,
      page_size: 20,
    })
    tasks.value = data.items
    taskTotal.value = data.total
  } finally {
    loading.value = false
  }
}

function refresh() {
  return activeTab.value === 'requests' ? loadRequests() : loadTasks()
}

function openDetail(row) {
  detail.value = row
  drawerVisible.value = true
}

async function openTrack(task) {
  currentTask.value = task
  currentTrack.value = await outingApi.track(task.id)
  trackVisible.value = true
}

async function endTask(task) {
  await ElMessageBox.confirm(
    `确定手动结束 ${elderName(task.elder_id)} 的外出任务 #${task.id} 吗？机器狗会退出跟随模式。`,
    '结束外出',
    { type: 'warning' },
  )
  await outingApi.endTask(task.id)
  ElMessage.success('外出任务已结束')
  await loadTasks()
}

const decisionOptions = computed(() =>
  Object.entries(DECISION_META).map(([value, meta]) => ({ value, label: meta.label })),
)

onMounted(async () => {
  await loadElders()
  await loadRequests()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">外出评估与任务</h2>
        <p class="page-subtitle">每一次外出申请的判定依据、模型输出与跟随过程都可追溯</p>
      </div>
      <div class="filters">
        <el-select
          v-model="filters.elder_id"
          placeholder="全部老人"
          clearable
          style="width: 150px"
          @change="refresh"
        >
          <el-option v-for="item in elders" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select
          v-if="activeTab === 'requests'"
          v-model="filters.decision"
          placeholder="全部结论"
          clearable
          style="width: 150px"
          @change="refresh"
        >
          <el-option v-for="item in decisionOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>刷新</el-button>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="activeTab" @tab-change="refresh">
        <el-tab-pane label="外出申请与决策日志" name="requests">
          <el-table :data="requests" v-loading="loading" empty-text="暂无外出申请">
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ dayjs(row.requested_at).format('MM-DD HH:mm:ss') }}</template>
            </el-table-column>
            <el-table-column label="老人" width="100">
              <template #default="{ row }">{{ elderName(row.elder_id) }}</template>
            </el-table-column>
            <el-table-column prop="raw_text" label="老人原话" min-width="150" show-overflow-tooltip />
            <el-table-column label="来源" width="90">
              <template #default="{ row }">{{ row.channel === 'voice' ? '语音' : '后台' }}</template>
            </el-table-column>
            <el-table-column label="结论" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.decision" size="small" :type="DECISION_META[row.decision.decision]?.type">
                  {{ DECISION_META[row.decision.decision]?.label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="主要理由" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">{{ (row.decision?.reasons || [])[0] || '--' }}</template>
            </el-table-column>
            <el-table-column label="限制" width="140">
              <template #default="{ row }">
                <span v-if="row.decision?.limits?.duration_minutes">
                  {{ row.decision.limits.duration_minutes }}分钟 / {{ row.decision.limits.radius_meters }}米
                </span>
                <span v-else class="muted">--</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.decision" link type="primary" @click="openDetail(row)">
                  查看详情
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="page"
            :page-size="20"
            :total="requestTotal"
            layout="total, prev, pager, next"
            style="margin-top: 12px; justify-content: flex-end"
            @current-change="loadRequests"
          />
        </el-tab-pane>

        <el-tab-pane label="外出任务与轨迹" name="tasks">
          <el-table :data="tasks" v-loading="loading" empty-text="暂无外出任务">
            <el-table-column prop="id" label="#" width="70" />
            <el-table-column label="老人" width="100">
              <template #default="{ row }">{{ elderName(row.elder_id) }}</template>
            </el-table-column>
            <el-table-column label="开始时间" width="160">
              <template #default="{ row }">{{ dayjs(row.started_at).format('MM-DD HH:mm:ss') }}</template>
            </el-table-column>
            <el-table-column label="用时" width="90">
              <template #default="{ row }">
                {{ dayjs(row.ended_at || undefined).diff(dayjs(row.started_at), 'minute') }} 分钟
              </template>
            </el-table-column>
            <el-table-column label="限制" width="140">
              <template #default="{ row }">
                {{ row.planned_duration_minutes }}分钟 / {{ row.geofence_radius_m }}米
              </template>
            </el-table-column>
            <el-table-column label="最远/累计" width="140">
              <template #default="{ row }">
                <span :class="row.max_distance_m > row.geofence_radius_m ? 'status-danger' : ''">
                  {{ Math.round(row.max_distance_m) }}
                </span>
                / {{ Math.round(row.total_distance_m) }} 米
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="STATUS_META[row.status]?.type">
                  {{ STATUS_META[row.status]?.label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="summary" label="外出小结" min-width="240" show-overflow-tooltip />
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openTrack(row)">轨迹</el-button>
                <el-button v-if="row.status === 'active'" link type="danger" @click="endTask(row)">
                  结束
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="taskPage"
            :page-size="20"
            :total="taskTotal"
            layout="total, prev, pager, next"
            style="margin-top: 12px; justify-content: flex-end"
            @current-change="loadTasks"
          />
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-drawer v-model="drawerVisible" title="外出决策详情" size="640px">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small" style="margin-bottom: 16px">
          <el-descriptions-item label="老人">{{ elderName(detail.elder_id) }}</el-descriptions-item>
          <el-descriptions-item label="申请时间">
            {{ dayjs(detail.requested_at).format('YYYY-MM-DD HH:mm:ss') }}
          </el-descriptions-item>
          <el-descriptions-item label="老人原话" :span="2">{{ detail.raw_text }}</el-descriptions-item>
        </el-descriptions>

        <DecisionPanel :decision="detail.decision" />

        <el-collapse style="margin-top: 16px">
          <el-collapse-item title="评估时的健康快照" name="health">
            <pre class="raw">{{ JSON.stringify(detail.decision?.health_snapshot, null, 2) }}</pre>
          </el-collapse-item>
          <el-collapse-item title="天气与外出统计" name="context">
            <pre class="raw">{{ JSON.stringify({ weather: detail.decision?.weather_snapshot, stats: detail.decision?.outing_stats }, null, 2) }}</pre>
          </el-collapse-item>
          <el-collapse-item title="模型原始输出" name="llm">
            <pre class="raw">{{ detail.decision?.llm_raw || '（未调用外部模型）' }}</pre>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-drawer>

    <el-dialog v-model="trackVisible" title="外出轨迹" width="460px">
      <template v-if="currentTask">
        <TrackMap
          :origin="{ lat: currentTask.origin_lat, lng: currentTask.origin_lng }"
          :points="currentTrack"
          :radius="currentTask.geofence_radius_m"
          :size="380"
        />
        <p class="muted" style="text-align: center; margin-top: 10px">
          {{ currentTask.summary || '任务进行中' }}
        </p>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.raw {
  background: var(--surface-tint);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  font-size: 14px;
  line-height: 1.6;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
