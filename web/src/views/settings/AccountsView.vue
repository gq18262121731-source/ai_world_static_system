<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api'
import { useElders } from '@/composables/useElders'

const { elders, load: loadElders, elderName } = useElders()

const users = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)

const ROLE_META = {
  admin: { label: '平台管理员', type: 'danger', desc: '全部功能，含规则阈值与审计' },
  staff: { label: '社区服务人员', type: 'warning', desc: '全部老人的日常运营与告警处置' },
  family: { label: '家属', type: 'success', desc: '仅能查看绑定老人，默认进入家属端' },
}

const form = reactive({
  username: '',
  password: '',
  real_name: '',
  phone: '',
  role: 'family',
  elder_ids: [],
  relation: '子女',
})

async function load() {
  loading.value = true
  try {
    users.value = await authApi.users()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    username: '',
    password: '',
    real_name: '',
    phone: '',
    role: 'family',
    elder_ids: [],
    relation: '子女',
  })
  dialogVisible.value = true
}

async function save() {
  if (form.username.trim().length < 3) {
    ElMessage.warning('用户名至少 3 个字符')
    return
  }
  if (form.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  if (form.role === 'family' && !form.elder_ids.length) {
    ElMessage.warning('家属账号需要绑定至少一位老人，否则登录后看不到任何数据')
    return
  }
  saving.value = true
  try {
    await authApi.createUser(form)
    ElMessage.success('账号已创建')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await loadElders()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="card">
      <div class="head">
        <div>
          <h3 class="card-title" style="margin-bottom: 6px">账号管理</h3>
          <p class="muted" style="margin: 0">
            家属账号只能看到与自己绑定的老人；管理员与服务人员可以看到全部老人。
          </p>
        </div>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新建账号
        </el-button>
      </div>

      <div class="role-cards">
        <div v-for="(meta, key) in ROLE_META" :key="key" class="role-card">
          <el-tag :type="meta.type" size="small">{{ meta.label }}</el-tag>
          <div class="role-desc">{{ meta.desc }}</div>
          <div class="role-count">
            {{ users.filter((u) => u.role === key).length }} 个账号
          </div>
        </div>
      </div>

      <el-table :data="users" v-loading="loading" empty-text="暂无账号">
        <el-table-column prop="username" label="登录名" width="130" />
        <el-table-column prop="real_name" label="姓名" width="150" />
        <el-table-column prop="phone" label="联系电话" width="140" />
        <el-table-column label="角色" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="ROLE_META[row.role]?.type">
              {{ ROLE_META[row.role]?.label || row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="绑定老人" min-width="180">
          <template #default="{ row }">
            <template v-if="row.elder_ids?.length">
              <el-tag
                v-for="id in row.elder_ids"
                :key="id"
                size="small"
                effect="plain"
                style="margin-right: 4px"
              >
                {{ elderName(id) }}
              </el-tag>
            </template>
            <span v-else class="muted">{{ row.role === 'family' ? '未绑定' : '全部老人' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'info'" effect="plain">
              {{ row.is_active ? '正常' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" title="新建账号" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="登录名" required>
          <el-input v-model="form.username" placeholder="至少 3 个字符" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="form.real_name" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="form.phone" placeholder="告警通知会发送到这个号码" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option
              v-for="(meta, key) in ROLE_META"
              :key="key"
              :label="meta.label"
              :value="key"
            />
          </el-select>
          <div class="muted" style="margin-top: 4px">{{ ROLE_META[form.role]?.desc }}</div>
        </el-form-item>
        <template v-if="form.role === 'family'">
          <el-form-item label="绑定老人" required>
            <el-select v-model="form.elder_ids" multiple style="width: 100%">
              <el-option
                v-for="item in elders"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="与老人关系">
            <el-input v-model="form.relation" placeholder="例如 子女、配偶" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 18px;
  flex-wrap: wrap;
}

.role-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.role-card {
  background: var(--surface-tint);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  padding: 13px;
}

.role-desc {
  font-size: 14.5px;
  color: var(--ink-soft);
  margin: 7px 0 6px;
  line-height: 1.6;
}

.role-count {
  font-size: 16.5px;
  font-weight: 600;
}
</style>
