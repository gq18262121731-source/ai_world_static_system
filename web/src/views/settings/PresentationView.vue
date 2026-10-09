<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { simulatorApi } from '@/api'
import {
  DEFAULT_RESET_HOTKEY,
  MAX_STAGE,
  STAGES,
  hotkeyFromEvent,
  usePresentationStore,
} from '@/stores/presentation'

const presentation = usePresentationStore()

const recording = ref(false)
const draft = ref('')
const runtimeLog = ref([])
let timer = null

const runtime = computed(() => presentation.runtime)

async function refresh() {
  try {
    const data = await presentation.refreshRuntime(24)
    runtimeLog.value = data?.log || []
  } catch {
    // 接口失败时 http 层已经弹过提示
  }
}

function startRecording() {
  recording.value = true
  draft.value = ''
}

function onRecordKeydown(event) {
  if (!recording.value) return
  event.preventDefault()
  event.stopPropagation()
  if (event.code === 'Escape') {
    recording.value = false
    return
  }
  const combo = hotkeyFromEvent(event)
  // 只按了修饰键时先显示已按下的部分，等主键按下再定稿
  draft.value = combo || ['Ctrl', 'Alt', 'Shift', 'Meta']
    .filter((name) => event[`${name.toLowerCase()}Key`])
    .join('+')
  if (!combo) return
  if (!combo.includes('+')) {
    ElMessage.warning('请至少搭配一个修饰键（Ctrl / Alt / Shift），单个字母会干扰正常输入')
    return
  }
  presentation.setResetHotkey(combo)
  recording.value = false
  ElMessage.success(`重置快捷键已改为 ${combo}`)
}

async function startSimulator() {
  presentation.runtime = await simulatorApi.runtimeStart({})
  await refresh()
}

async function stopSimulator() {
  presentation.runtime = await simulatorApi.runtimeStop()
  await refresh()
}

function uptimeText(seconds) {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 8000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page presentation">
    <div class="grid-2 grid">
      <section class="card state-card" :class="presentation.dataLive ? 'is-live' : 'is-idle'">
        <h3 class="card-title">演示进度</h3>
        <p class="muted intro">
          新开标签页时界面都在、数据是空的；每刷新一次或单独按一次 Ctrl 往前走一步。进度只记在当前标签页里，关掉浏览器重开就回到第 0 步。
        </p>
        <ol class="steps">
          <li
            v-for="item in STAGES"
            :key="item.value"
            class="step"
            :class="{ done: item.value < presentation.stage, current: item.value === presentation.stage }"
          >
            <span class="step-index">{{ item.value }}</span>
            <div class="step-body">
              <div class="step-label">
                {{ item.label }}
                <el-tag v-if="item.value === presentation.stage" size="small" type="success" effect="dark">当前</el-tag>
              </div>
              <div class="muted">{{ item.detail }}</div>
            </div>
          </li>
        </ol>
        <div class="state-actions">
          <el-button
            type="primary"
            :disabled="presentation.stage >= MAX_STAGE"
            @click="presentation.advance()"
          >
            <el-icon><Refresh /></el-icon>
            {{ presentation.nextStageInfo ? `推进到「${presentation.nextStageInfo.label}」（等同刷新/Ctrl）` : '已是最后一步' }}
          </el-button>
          <el-button :loading="presentation.busy" @click="presentation.reset()">
            <el-icon><RefreshLeft /></el-icon>
            重置到第 0 步
          </el-button>
        </div>
      </section>

      <section class="card">
        <h3 class="card-title">重置快捷键</h3>
        <p class="muted intro">单独按 Ctrl 会推进一步；按下这组完整快捷键时只会重置，不会误推进，界面将回到“未接入”。</p>
        <div class="hotkey-row">
          <button
            class="hotkey-box pressable"
            :class="{ recording }"
            v-ripple
            @click="startRecording"
            @keydown="onRecordKeydown"
            @blur="recording = false"
          >
            <template v-if="recording">
              <span class="kbd-hint">{{ draft || '请按下组合键…' }}</span>
            </template>
            <template v-else>
              <kbd v-for="part in presentation.resetHotkey.split('+')" :key="part">{{ part }}</kbd>
            </template>
          </button>
          <el-button
            link
            type="primary"
            :disabled="presentation.resetHotkey === DEFAULT_RESET_HOTKEY"
            @click="presentation.restoreResetHotkey()"
          >
            恢复默认 {{ DEFAULT_RESET_HOTKEY }}
          </el-button>
        </div>
        <p class="muted tip">
          点击按键区域后直接按下新的组合键（Esc 取消）。默认组合避开了浏览器和中文输入法常用的键；
          改键时请不要用 Ctrl + Shift 开头的组合，容易和输入法切换冲突。
        </p>
        <el-divider />
        <div class="option-row">
          <div>
            <div class="option-title">数据接入时自动启动模拟器</div>
            <div class="muted">第 1 步起确保模拟器在跑，重置时顺带停掉。关掉后只切换界面，适合现场只接真机的情况。</div>
          </div>
          <el-switch
            :model-value="presentation.autostartSimulator"
            @change="presentation.setAutostart($event)"
          />
        </div>
      </section>
    </div>

    <section class="card runtime-card">
      <div class="runtime-head">
        <h3 class="card-title" style="margin: 0">模拟器进程</h3>
        <div class="runtime-actions">
          <el-tag v-if="runtime?.running" type="success" effect="light">
            运行中 · PID {{ runtime.pid }} · 已运行 {{ uptimeText(runtime.uptime_seconds) }}
          </el-tag>
          <el-tag v-else-if="runtime && !runtime.available" type="danger" effect="light">找不到模拟器入口</el-tag>
          <el-tag v-else-if="runtime?.external?.length" type="warning" effect="light">
            别处已在运行 · PID {{ runtime.external.map((item) => item.pid).join(', ') }}
          </el-tag>
          <el-tag v-else type="info" effect="light">未运行</el-tag>
          <el-button size="small" :disabled="runtime?.running" @click="startSimulator">启动</el-button>
          <el-button size="small" :disabled="!runtime?.running" @click="stopSimulator">停止</el-button>
          <el-button size="small" text @click="refresh">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </div>
      <p class="muted" style="margin: 8px 0 12px">
        由后端托管的 simulator/run_all.py 进程，为张三、李四两位演示老人的手表与机器狗持续上报数据。
        真机手环的数据不经过它，随时都在。
        <template v-if="runtime?.last_error">
          <span class="status-danger">最近一次错误：{{ runtime.last_error }}</span>
        </template>
      </p>
      <pre v-if="runtimeLog.length" class="log">{{ runtimeLog.join('\n') }}</pre>
      <el-empty v-else description="还没有运行日志" :image-size="56" />
      <div v-if="runtime?.started_at" class="muted" style="margin-top: 8px">
        本次启动于 {{ dayjs(runtime.started_at).format('MM-DD HH:mm:ss') }}
      </div>
    </section>
  </div>
</template>

<style scoped>
.presentation .grid {
  margin-bottom: 18px;
}

.intro {
  margin: 0 0 14px;
  line-height: 1.7;
}

.state-card {
  transition: background-color var(--dur) var(--ease-out), border-color var(--dur) var(--ease-out);
}

.state-card.is-live {
  background: linear-gradient(135deg, var(--ok-soft), var(--surface));
}

.state-card.is-idle {
  background: linear-gradient(135deg, var(--brand-tint), var(--surface));
}

.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.step {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: var(--radius);
  border: 1px dashed var(--line);
  background: var(--surface);
}

.step.done {
  border-style: solid;
  border-color: var(--ok-soft);
}

.step.current {
  border-style: solid;
  border-color: var(--ok);
  box-shadow: 0 0 0 1px var(--ok) inset;
}

.step-index {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 14.5px;
  background: var(--surface-tint);
  border: 1px solid var(--line);
  color: var(--ink-soft);
  flex-shrink: 0;
}

.step.done .step-index,
.step.current .step-index {
  background: var(--ok);
  border-color: var(--ok);
  color: #fff;
}

.step-body {
  min-width: 0;
}

.step-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15.5px;
  font-weight: 600;
  margin-bottom: 2px;
}

.state-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.hotkey-row {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.hotkey-box {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 190px;
  min-height: 46px;
  padding: 8px 14px;
  border-radius: var(--radius);
  border: 1px dashed var(--line);
  background: var(--surface-tint);
  cursor: pointer;
  font: inherit;
  color: var(--ink);
}

.hotkey-box:focus-visible,
.hotkey-box.recording {
  outline: none;
  border-style: solid;
  border-color: var(--brand);
  background: var(--brand-tint);
}

kbd {
  font-family: inherit;
  font-size: 15.5px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-bottom-width: 2px;
  color: var(--ink);
}

kbd + kbd::before {
  content: '+';
  margin-right: 6px;
  color: var(--ink-soft);
  font-weight: 400;
}

.kbd-hint {
  color: var(--brand);
  font-size: 15.5px;
}

.tip {
  margin: 12px 0 0;
  line-height: 1.7;
}

.option-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.option-title {
  font-weight: 500;
  margin-bottom: 3px;
}

.runtime-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.runtime-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.log {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  padding: 12px 14px;
  border-radius: var(--radius);
  background: #2f3833;
  color: #d8e2dc;
  font-size: 14.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
