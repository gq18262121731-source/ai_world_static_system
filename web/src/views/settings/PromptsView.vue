<script setup>
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const templates = ref([])
const loading = ref(false)
const editVisible = ref(false)
const editing = ref(null)
const editContent = ref('')

const CATEGORY_LABEL = {
  system_prompt: '大模型提示词',
  speech: '机器狗话术',
}

const CATEGORY_DESC = {
  system_prompt: '决定大模型如何思考与表达，改动会影响所有对话的风格与边界',
  speech: '机器狗在固定场景下会说的话，可以按老人的接受度调整措辞',
}

function placeholderText(name) {
  return `{${name}}`
}

async function load() {
  loading.value = true
  try {
    templates.value = await agentApi.templates()
  } finally {
    loading.value = false
  }
}

const groups = computed(() => {
  const result = {}
  for (const item of templates.value) {
    ;(result[item.category] ||= []).push(item)
  }
  return result
})

const overriddenCount = computed(() => templates.value.filter((t) => t.overridden).length)

function openEdit(template) {
  editing.value = template
  editContent.value = template.content
  editVisible.value = true
}

async function save() {
  await agentApi.updateTemplate(editing.value.template_key, {
    content: editContent.value,
    enabled: true,
  })
  ElMessage.success('已保存，机器狗下一次说话就会使用新话术')
  editVisible.value = false
  await load()
}

async function reset(template) {
  await ElMessageBox.confirm(
    `将「${template.name}」恢复为系统默认内容，确定吗？`,
    '恢复默认',
    { type: 'warning' },
  )
  await agentApi.resetTemplate(template.template_key)
  ElMessage.success('已恢复默认')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">提示词与话术</h2>
        <p class="page-subtitle">
          共 {{ templates.length }} 条，其中 {{ overriddenCount }} 条已自定义
        </p>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      修改后立即对下一次对话生效。内容留空或点「恢复默认」都会回落到系统内置版本，
      所以改坏了也不会影响系统运行。
    </el-alert>

    <div v-loading="loading">
      <div v-for="(items, category) in groups" :key="category" class="group">
        <div class="group-head">
          <h3 class="group-title">{{ CATEGORY_LABEL[category] || category }}</h3>
          <span class="muted">{{ CATEGORY_DESC[category] }}</span>
        </div>

        <div class="grid-cards">
          <div v-for="item in items" :key="item.template_key" class="tpl liftable">
            <div class="tpl-head">
              <div>
                <span class="tpl-name">{{ item.name }}</span>
                <el-tag v-if="item.overridden" size="small" type="warning" effect="plain">
                  已自定义
                </el-tag>
              </div>
              <div v-if="auth.isAdmin" class="tpl-actions">
                <el-button link type="primary" @click="openEdit(item)">编辑</el-button>
                <el-button v-if="item.overridden" link type="danger" @click="reset(item)">
                  恢复默认
                </el-button>
              </div>
            </div>

            <div class="muted tpl-desc">{{ item.description }}</div>
            <div class="tpl-content">{{ item.content }}</div>

            <div v-if="item.placeholders.length" class="tpl-holders">
              可用占位符：
              <code v-for="p in item.placeholders" :key="p">{{ placeholderText(p) }}</code>
            </div>
            <div v-if="item.overridden" class="muted tpl-foot">
              由 {{ item.updated_by || '未知' }} 修改于
              {{ item.updated_at ? dayjs(item.updated_at).format('MM-DD HH:mm') : '--' }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="editVisible" :title="`编辑「${editing?.name}」`" width="660px">
      <p class="muted" style="margin-top: 0">{{ editing?.description }}</p>
      <div v-if="editing?.placeholders?.length" class="tpl-holders" style="margin-bottom: 10px">
        可用占位符：
        <code v-for="p in editing.placeholders" :key="p">{{ placeholderText(p) }}</code>
      </div>
      <el-input
        v-model="editContent"
        type="textarea"
        :rows="editing?.category === 'system_prompt' ? 16 : 5"
      />
      <template #footer>
        <el-button @click="editContent = editing.default_content">填入默认内容</el-button>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.group + .group {
  margin-top: 26px;
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.group-title {
  font-size: 16.5px;
  margin: 0;
}

.grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 14px;
}

.tpl {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 16px;
  background: var(--surface);
}

.tpl-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}

.tpl-name {
  font-size: 16px;
  font-weight: 600;
  margin-right: 6px;
}

.tpl-actions {
  flex-shrink: 0;
}

.tpl-desc {
  margin: 7px 0 11px;
  line-height: 1.65;
}

.tpl-content {
  background: var(--surface-tint);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  font-size: 15px;
  line-height: 1.85;
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
}

.tpl-holders {
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 9px;
}

.tpl-holders code {
  background: var(--brand-soft);
  color: var(--brand-deep);
  border-radius: 4px;
  padding: 1px 6px;
  margin-right: 4px;
}

.tpl-foot {
  font-size: 13px;
  margin-top: 9px;
}
</style>
