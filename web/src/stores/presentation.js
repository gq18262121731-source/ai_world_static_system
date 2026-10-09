import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { simulatorApi } from '@/api'
import { TOKEN_KEY } from '@/api/http'
import { useRealtimeStore } from '@/stores/realtime'

// 演示进度靠“整页刷新”或单独按下 Ctrl 推进：
//   新开标签页是第 0 步：数据为空；
//   刷新/Ctrl 一次 → 第 1 步 数据接入；再刷新/Ctrl → 第 2 步健康评分与健康状态；
//   再刷新/Ctrl → 第 3 步智能体；之后保持。
//   Ctrl+Alt+S 一键重置回第 0 步。
// 使用新版本键，避免此前“固定完整展示”写入的阶段 3 残留影响本次恢复。
const STAGE_KEY = 'aiot_presentation_stage_v2'
const RESET_HOTKEY_KEY = 'aiot_presentation_reset_hotkey'
const AUTOSTART_KEY = 'aiot_presentation_autostart'

export const DEFAULT_RESET_HOTKEY = 'Ctrl+Alt+S'
export const MAX_STAGE = 3
export const STAGES = [
  { value: 0, label: '未接入', detail: '界面都在，数据位置全是空的，不请求、不轮询、不推送' },
  { value: 1, label: '数据接入', detail: '体征、告警、外出、事件流开始显示，模拟器随之启动' },
  { value: 2, label: '健康评分与健康状态', detail: '评分卡、分数环、身体状态标签及异常反馈一起显示' },
  { value: 3, label: '智能体', detail: '健康咨询与机器狗语音可用' },
]

const MODIFIERS = ['Ctrl', 'Alt', 'Shift', 'Meta']

/** 'Ctrl+Alt+S' -> { ctrl, alt, shift, meta, key: 'S' } */
export function parseHotkey(text) {
  const parts = String(text || '')
    .split('+')
    .map((item) => item.trim())
    .filter(Boolean)
  const spec = { ctrl: false, alt: false, shift: false, meta: false, key: '' }
  for (const part of parts) {
    const lower = part.toLowerCase()
    if (lower === 'ctrl' || lower === 'control') spec.ctrl = true
    else if (lower === 'alt' || lower === 'option') spec.alt = true
    else if (lower === 'shift') spec.shift = true
    else if (lower === 'meta' || lower === 'cmd' || lower === 'win') spec.meta = true
    else spec.key = part.length === 1 ? part.toUpperCase() : part
  }
  return spec
}

/** 键盘事件 -> 'Ctrl+Alt+S' 这样的可读写法；只按了修饰键时返回空串 */
export function hotkeyFromEvent(event) {
  const parts = []
  if (event.ctrlKey) parts.push('Ctrl')
  if (event.altKey) parts.push('Alt')
  if (event.shiftKey) parts.push('Shift')
  if (event.metaKey) parts.push('Meta')
  const key = keyName(event)
  if (!key || MODIFIERS.includes(key)) return ''
  parts.push(key)
  return parts.join('+')
}

function keyName(event) {
  // 用物理键位而不是 event.key：按住 Alt/Shift 时 event.key 会变成别的字符，中文输入法下也会变
  const code = event.code || ''
  if (code.startsWith('Key')) return code.slice(3)
  if (code.startsWith('Digit')) return code.slice(5)
  if (/^F\d{1,2}$/.test(code)) return code
  const named = { Space: 'Space', Enter: 'Enter', Escape: 'Esc', Backquote: '`' }
  if (named[code]) return named[code]
  if (['ControlLeft', 'ControlRight'].includes(code)) return 'Ctrl'
  if (['AltLeft', 'AltRight'].includes(code)) return 'Alt'
  if (['ShiftLeft', 'ShiftRight'].includes(code)) return 'Shift'
  if (['MetaLeft', 'MetaRight'].includes(code)) return 'Meta'
  return ''
}

export function matchesHotkey(event, text) {
  const spec = parseHotkey(text)
  if (!spec.key) return false
  return (
    event.ctrlKey === spec.ctrl &&
    event.altKey === spec.alt &&
    event.shiftKey === spec.shift &&
    event.metaKey === spec.meta &&
    keyName(event) === spec.key
  )
}

/**
 * 这次页面加载处于第几步。每次整页加载只推进一次（Pinia 热更新重跑 state() 时不能再加），
 * 没登录时的刷新不算：登录页上按 F5 不该把演示进度偷偷往前推。
 */
function bootStage() {
  if (window.__aiotPresentationStage != null) return window.__aiotPresentationStage
  const stored = Number(sessionStorage.getItem(STAGE_KEY))
  const hasStored = sessionStorage.getItem(STAGE_KEY) !== null && Number.isFinite(stored)
  let stage
  if (!hasStored) stage = 0
  else if (!localStorage.getItem(TOKEN_KEY)) stage = Math.max(0, Math.min(MAX_STAGE, stored))
  else stage = Math.max(0, Math.min(MAX_STAGE, stored + 1))
  sessionStorage.setItem(STAGE_KEY, String(stage))
  window.__aiotPresentationStage = stage
  return stage
}

export const usePresentationStore = defineStore('presentation', {
  state: () => ({
    stage: bootStage(),
    resetHotkey: localStorage.getItem(RESET_HOTKEY_KEY) || DEFAULT_RESET_HOTKEY,
    // 数据接入那一步是否顺带把模拟器进程拉起来（重置时也顺带停掉）
    autostartSimulator: localStorage.getItem(AUTOSTART_KEY) !== '0',
    busy: false,
    runtime: null,
    simulatorSynced: false,
    // 仅在 Ctrl 独立按下、期间没有出现第二个键时保持为 true。
    _ctrlAdvanceCandidate: false,
  }),
  getters: {
    dataLive: (state) => state.stage >= 1,
    scoreLive: (state) => state.stage >= 2,
    agentLive: (state) => state.stage >= 3,
    // 旧名字，页面里还在用
    live: (state) => state.stage >= 1,
    dormant: (state) => state.stage < 1,
    scoreDormant: (state) => state.stage < 2,
    agentDormant: (state) => state.stage < 3,
    stageInfo: (state) => STAGES[state.stage] || STAGES[0],
    nextStageInfo: (state) => STAGES[state.stage + 1] || null,
  },
  actions: {
    install() {
      if (this._installed) return
      this._installed = true
      window.addEventListener('keydown', this.onKeydown)
      window.addEventListener('keyup', this.onKeyup)
      // 数据没接入时不弹告警，否则界面上什么都没有却先冒出一个弹窗
      useRealtimeStore().notifyAlarms = this.dataLive
    },
    onKeydown(event) {
      if (matchesHotkey(event, this.resetHotkey)) {
        this._ctrlAdvanceCandidate = false
        event.preventDefault()
        this.reset()
        return
      }

      const isCtrl = ['ControlLeft', 'ControlRight'].includes(event.code) || event.key === 'Control'
      if (isCtrl) {
        if (!event.repeat) {
          // 等到 keyup 才推进，这样随后按下 C、Alt、S 等键时可判定为组合键并取消。
          this._ctrlAdvanceCandidate = !event.altKey && !event.shiftKey && !event.metaKey
        }
        return
      }
      if (this._ctrlAdvanceCandidate) this._ctrlAdvanceCandidate = false
    },
    onKeyup(event) {
      if (!['ControlLeft', 'ControlRight'].includes(event.code) && event.key !== 'Control') return
      if (!this._ctrlAdvanceCandidate) return
      this._ctrlAdvanceCandidate = false
      event.preventDefault()
      this.advance()
    },
    /** 等同于按一次 F5 或单独按一次 Ctrl：整页重新加载，进度加一。 */
    advance() {
      if (this.stage >= MAX_STAGE) {
        ElMessage.info('已经是最后一步了')
        return
      }
      window.location.reload()
    },
    /** 回到第 0 步：先停掉演示拉起的模拟器，再让下一次加载落在 0（存 -1，加载时 +1）。 */
    async reset() {
      if (this.busy) return
      this.busy = true
      try {
        if (this.autostartSimulator && this.stage >= 1) {
          await Promise.race([
            simulatorApi.runtimeStop(),
            new Promise((resolve) => setTimeout(resolve, 8000)),
          ])
        }
      } catch {
        // 停不掉也照样重置界面
      } finally {
        sessionStorage.setItem(STAGE_KEY, '-1')
        window.location.reload()
      }
    },
    /** 数据接入后确保模拟器在跑；一次加载只做一次，重复调用是幂等的。 */
    async syncSimulator() {
      if (!this.dataLive || !this.autostartSimulator || this.simulatorSynced) return
      this.simulatorSynced = true
      try {
        this.runtime = await simulatorApi.runtimeStart({})
      } catch {
        // 模拟器起不来不影响界面，真机数据照常显示
      }
    },
    async refreshRuntime(logLines = 0) {
      this.runtime = await simulatorApi.runtime(logLines)
      return this.runtime
    },
    setResetHotkey(text) {
      const spec = parseHotkey(text)
      if (!spec.key) return false
      this.resetHotkey = text
      localStorage.setItem(RESET_HOTKEY_KEY, text)
      return true
    },
    restoreResetHotkey() {
      this.setResetHotkey(DEFAULT_RESET_HOTKEY)
    },
    setAutostart(value) {
      this.autostartSimulator = Boolean(value)
      localStorage.setItem(AUTOSTART_KEY, value ? '1' : '0')
    },
  },
})
