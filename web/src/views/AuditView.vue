<script setup>
import { onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { dashboardApi } from '@/api'

const logs = ref([])
const loading = ref(false)

const ACTION_LABEL = {
  login: '登录',
  create_user: '创建用户',
  create_elder: '新建老人档案',
  update_elder: '更新老人档案',
  delete_elder: '删除老人档案',
  add_contact: '新增紧急联系人',
  create_device: '注册设备',
  update_device: '更新设备',
  device_command: '下发设备指令',
  outing_request: '发起外出申请',
  end_outing_task: '结束外出任务',
  handle_alarm: '处置告警',
  upsert_rule: '调整规则阈值',
}

async function load() {
  loading.value = true
  try {
    logs.value = await dashboardApi.auditLogs(200)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">操作审计</h2>
        <p class="page-subtitle">记录所有涉及老人数据与设备控制的关键操作，便于事后追溯</p>
      </div>
      <el-button @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>

    <div class="card">
      <el-table :data="logs" v-loading="loading" empty-text="暂无操作记录">
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ dayjs(row.created_at).format('YYYY-MM-DD HH:mm:ss') }}</template>
        </el-table-column>
        <el-table-column prop="username" label="操作人" width="120" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">{{ ACTION_LABEL[row.action] || row.action }}</template>
        </el-table-column>
        <el-table-column label="对象" width="160">
          <template #default="{ row }">
            <span v-if="row.target_type">{{ row.target_type }} #{{ row.target_id }}</span>
            <span v-else class="muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="详情" min-width="300">
          <template #default="{ row }">
            <code class="code">{{ JSON.stringify(row.detail) }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="来源 IP" width="140" />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.code {
  font-size: 14px;
  color: var(--ink-soft);
  word-break: break-all;
}
</style>
