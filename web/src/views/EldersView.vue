<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { elderApi, healthApi } from '@/api'
import { useElders } from '@/composables/useElders'
import { useAuthStore } from '@/stores/auth'
import { usePresentationStore } from '@/stores/presentation'
import ScoreRing from '@/components/ScoreRing.vue'
import ScoreDormant from '@/components/ScoreDormant.vue'

const router = useRouter()
const auth = useAuthStore()
const presentation = usePresentationStore()
const { elders, load } = useElders()

const loading = ref(false)
// elder_id -> 首页快照（含健康评分），列表只用分数这一列
const scores = ref({})

async function loadScores() {
  try {
    const live = await healthApi.live(1)
    scores.value = Object.fromEntries(live.map((item) => [item.elder_id, item]))
  } catch {
    scores.value = {}
  }
}
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)

const DISEASE_OPTIONS = [
  '高血压', '2型糖尿病', '冠心病', '心律失常', '慢阻肺', '哮喘', '帕金森', '骨质疏松', '脑卒中后遗症',
]
const MOBILITY_OPTIONS = [
  { value: 'good', label: '良好' },
  { value: 'limited', label: '行动稍有不便' },
  { value: 'poor', label: '行动困难' },
]

const form = reactive({
  name: '',
  gender: '男',
  birth_date: null,
  phone: '',
  address: '',
  city: '杭州',
  home_lat: 30.2795,
  home_lng: 120.133,
  chronic_diseases: [],
  mobility_level: 'good',
  live_alone: true,
  note: '',
  emergency_contacts: [],
})

function resetForm() {
  Object.assign(form, {
    name: '',
    gender: '男',
    birth_date: null,
    phone: '',
    address: '',
    city: '杭州',
    home_lat: 30.2795,
    home_lng: 120.133,
    chronic_diseases: [],
    mobility_level: 'good',
    live_alone: true,
    note: '',
    emergency_contacts: [],
  })
}

async function refresh() {
  loading.value = true
  try {
    await Promise.all([load(true), loadScores()])
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  resetForm()
  form.emergency_contacts = [{ name: '', phone: '', relation: '子女', priority: 1 }]
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    ...row,
    chronic_diseases: [...(row.chronic_diseases || [])],
    emergency_contacts: [],
  })
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写老人姓名')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      const { emergency_contacts, id, age, ...payload } = form
      await elderApi.update(editingId.value, payload)
      ElMessage.success('档案已更新')
    } else {
      await elderApi.create({
        ...form,
        emergency_contacts: form.emergency_contacts.filter((c) => c.name && c.phone),
      })
      ElMessage.success('档案已创建')
    }
    dialogVisible.value = false
    await refresh()
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  await ElMessageBox.confirm(
    `确定删除 ${row.name} 的档案吗？相关健康数据、外出与告警记录会一并删除。`,
    '删除确认',
    { type: 'warning' },
  )
  await elderApi.remove(row.id)
  ElMessage.success('已删除')
  await refresh()
}

function addContact() {
  form.emergency_contacts.push({
    name: '',
    phone: '',
    relation: '子女',
    priority: form.emergency_contacts.length + 1,
  })
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">老人档案</h2>
        <p class="page-subtitle">档案里的慢病与行动能力会自动收紧该老人的外出评估阈值</p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>新建档案
      </el-button>
    </div>

    <div class="card">
      <el-table :data="elders" v-loading="loading" empty-text="暂无老人档案">
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column label="健康评分" width="110">
          <template #default="{ row }">
            <ScoreDormant v-if="presentation.scoreDormant" compact />
            <ScoreRing
              v-else
              :score="scores[row.id]?.health_score ?? null"
              :grade="scores[row.id]?.health_grade || ''"
              :delta="scores[row.id]?.score_delta ?? null"
              :reference="scores[row.id]?.score_reference ?? false"
              :size="46"
              :stroke="4"
              :show-grade="false"
            />
          </template>
        </el-table-column>
        <el-table-column label="年龄/性别" width="110">
          <template #default="{ row }">{{ row.age ?? '--' }} 岁 · {{ row.gender }}</template>
        </el-table-column>
        <el-table-column prop="phone" label="联系电话" width="130" />
        <el-table-column label="慢性病" min-width="180">
          <template #default="{ row }">
            <el-tag
              v-for="disease in row.chronic_diseases"
              :key="disease"
              size="small"
              type="warning"
              effect="plain"
              style="margin-right: 4px"
            >
              {{ disease }}
            </el-tag>
            <span v-if="!row.chronic_diseases?.length" class="muted">无</span>
          </template>
        </el-table-column>
        <el-table-column label="行动能力" width="120">
          <template #default="{ row }">
            {{ MOBILITY_OPTIONS.find((m) => m.value === row.mobility_level)?.label }}
          </template>
        </el-table-column>
        <el-table-column label="紧急联系人" min-width="180">
          <template #default="{ row }">
            <span v-if="!row.emergency_contacts?.length" class="muted">未配置</span>
            <span v-else>
              {{ row.emergency_contacts.map((c) => `${c.name}(${c.relation})`).join('、') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="住址" min-width="120">
          <template #default="{ row }">
            <span class="muted">{{ row.address ? '已隐藏' : '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/elders/${row.id}`)">
              健康看板
            </el-button>
            <template v-if="auth.isAdmin">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑老人档案' : '新建老人档案'"
      width="680px"
    >
      <el-form :model="form" label-width="96px">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="姓名" required>
              <el-input v-model="form.name" placeholder="请输入姓名" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="性别">
              <el-radio-group v-model="form.gender">
                <el-radio value="男">男</el-radio>
                <el-radio value="女">女</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="出生日期">
              <el-date-picker
                v-model="form.birth_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系电话">
              <el-input v-model="form.phone" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="居住地址">
              <el-input v-model="form.address" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="城市">
              <el-input v-model="form.city" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="住所纬度">
              <el-input-number v-model="form.home_lat" :precision="4" :step="0.001" controls-position="right" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="住所经度">
              <el-input-number v-model="form.home_lng" :precision="4" :step="0.001" controls-position="right" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="慢性病">
              <el-select
                v-model="form.chronic_diseases"
                multiple
                filterable
                allow-create
                placeholder="选择或输入慢性病"
                style="width: 100%"
              >
                <el-option v-for="item in DISEASE_OPTIONS" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="行动能力">
              <el-select v-model="form.mobility_level" style="width: 100%">
                <el-option
                  v-for="item in MOBILITY_OPTIONS"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="是否独居">
              <el-switch v-model="form.live_alone" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.note" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>

        <template v-if="!editingId">
          <el-divider content-position="left">紧急联系人</el-divider>
          <div v-for="(contact, index) in form.emergency_contacts" :key="index" class="contact-row">
            <el-input v-model="contact.name" placeholder="姓名" style="width: 120px" />
            <el-input v-model="contact.phone" placeholder="手机号" style="width: 150px" />
            <el-input v-model="contact.relation" placeholder="关系" style="width: 110px" />
            <el-input-number v-model="contact.priority" :min="1" :max="9" controls-position="right" style="width: 110px" />
            <el-button link type="danger" @click="form.emergency_contacts.splice(index, 1)">
              移除
            </el-button>
          </div>
          <el-button link type="primary" @click="addContact">+ 添加联系人</el-button>
        </template>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.contact-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
</style>
