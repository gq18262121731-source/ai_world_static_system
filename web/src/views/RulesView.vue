<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ruleApi } from '@/api'
import { useElders } from '@/composables/useElders'

const { elders, load: loadElders } = useElders()

const scopeElderId = ref(null)
const defaults = ref({ rules: {}, titles: {} })
const effective = ref(null)
const configs = ref([])
const loading = ref(false)

const editVisible = ref(false)
const editingKey = ref('')
const editingValue = ref({})

async function load() {
  loading.value = true
  try {
    const [defaultData, effectiveData, configData] = await Promise.all([
      ruleApi.defaults(),
      ruleApi.effective(scopeElderId.value || undefined),
      ruleApi.configs(scopeElderId.value || undefined),
    ])
    defaults.value = defaultData
    effective.value = effectiveData
    configs.value = configData
  } finally {
    loading.value = false
  }
}

const ruleKeys = computed(() => Object.keys(defaults.value.rules || {}))

function titleOf(key) {
  return defaults.value.titles?.[key] || key
}

function descriptionOf(key) {
  return defaults.value.rules?.[key]?._desc || ''
}

function fieldLabel(field) {
  return defaults.value.field_labels?.[field] || field
}

/** 只展示可配置的数值项，_desc 是说明文字不参与编辑。 */
function entriesOf(key) {
  const values = effective.value?.values?.[key] || {}
  return Object.entries(values).filter(([field]) => !field.startsWith('_'))
}

function isOverridden(key) {
  return effective.value?.overridden_keys?.includes(key)
}

function openEdit(key) {
  editingKey.value = key
  editingValue.value = Object.fromEntries(entriesOf(key))
  editVisible.value = true
}

async function save() {
  await ruleApi.upsert(
    {
      rule_key: editingKey.value,
      value: editingValue.value,
      enabled: true,
      description: titleOf(editingKey.value),
    },
    scopeElderId.value || undefined,
  )
  ElMessage.success(
    scopeElderId.value ? '个性化阈值已保存，仅对该老人生效' : '全局阈值已保存',
  )
  editVisible.value = false
  await load()
}

async function removeConfig(config) {
  await ElMessageBox.confirm(
    `删除后「${titleOf(config.rule_key)}」将恢复为上一级默认值，确定吗？`,
    '恢复默认',
    { type: 'warning' },
  )
  await ruleApi.remove(config.id)
  ElMessage.success('已恢复默认值')
  await load()
}

onMounted(async () => {
  await loadElders()
  await load()
})
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h2 class="page-title">外出评估规则阈值</h2>
        <p class="page-subtitle">
          三级生效顺序：系统默认值 → 全局配置 → 老人个性化配置，慢性病还会自动收紧对应阈值
        </p>
      </div>
      <el-select v-model="scopeElderId" placeholder="全局配置" clearable style="width: 190px" @change="load">
        <el-option v-for="item in elders" :key="item.id" :label="`${item.name} 的个性化配置`" :value="item.id" />
      </el-select>
    </div>

    <el-alert
      v-if="scopeElderId"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      当前正在编辑单个老人的个性化阈值，保存后仅对该老人生效，优先级高于全局配置。
    </el-alert>

    <div class="rules">
      <div v-for="key in ruleKeys" :key="key" class="card rule">
        <div class="rule-head">
          <div>
            <h3 class="card-title" style="margin-bottom: 4px">
              {{ titleOf(key) }}
              <el-tag v-if="isOverridden(key)" size="small" type="warning" effect="plain">已覆盖</el-tag>
            </h3>
            <div class="muted">{{ descriptionOf(key) }}</div>
          </div>
          <el-button link type="primary" @click="openEdit(key)">调整</el-button>
        </div>

        <div class="fields">
          <div v-for="[field, value] in entriesOf(key)" :key="field" class="field">
            <span class="field-name">{{ fieldLabel(field) }}</span>
            <span class="field-value">
              <template v-if="typeof value === 'boolean'">{{ value ? '是' : '否' }}</template>
              <template v-else>{{ Array.isArray(value) ? value.join('、') : value }}</template>
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 class="card-title">
        {{ scopeElderId ? '该老人的覆盖记录' : '全局覆盖记录' }}
      </h3>
      <el-table :data="configs" size="small" empty-text="暂无覆盖，全部使用系统默认值">
        <el-table-column label="规则项" width="180">
          <template #default="{ row }">{{ titleOf(row.rule_key) }}</template>
        </el-table-column>
        <el-table-column label="覆盖内容" min-width="320">
          <template #default="{ row }">
            <code class="code">{{ JSON.stringify(row.value) }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="180" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeConfig(row)">恢复默认</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="editVisible" :title="`调整「${titleOf(editingKey)}」阈值`" width="520px">
      <el-form label-width="190px">
        <el-form-item
          v-for="(value, field) in editingValue"
          :key="field"
          :label="fieldLabel(field)"
        >
          <el-input-number
            v-if="typeof value === 'number'"
            v-model="editingValue[field]"
            :precision="Number.isInteger(value) ? 0 : 1"
            :step="Number.isInteger(value) ? 1 : 0.1"
            controls-position="right"
            style="width: 100%"
          />
          <el-switch v-else-if="typeof value === 'boolean'" v-model="editingValue[field]" />
          <el-select
            v-else-if="Array.isArray(value)"
            v-model="editingValue[field]"
            multiple
            filterable
            allow-create
            style="width: 100%"
          >
            <el-option v-for="item in value" :key="item" :label="item" :value="item" />
          </el-select>
          <el-input v-else v-model="editingValue[field]" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.rules {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.rule + .rule {
  margin-top: 0;
}

.rule-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}

.fields {
  display: grid;
  gap: 6px;
}

.field {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 15px;
  padding: 5px 8px;
  background: var(--surface-tint);
  border-radius: 6px;
}

.field-name {
  color: var(--ink-soft);
}

.field-value {
  font-weight: 600;
  text-align: right;
  word-break: break-all;
}

.code {
  font-size: 14px;
  background: var(--surface-tint);
  padding: 2px 6px;
  border-radius: 4px;
  word-break: break-all;
}
</style>
