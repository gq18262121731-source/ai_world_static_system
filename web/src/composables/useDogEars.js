import { onUnmounted, ref } from 'vue'
import { TOKEN_KEY } from '@/api/http'

/**
 * 机器狗的"耳朵"：麦克风持续拾音 -> 16kHz PCM -> WebSocket 送到平台 -> 讯飞边说边转文字。
 *
 * 客户端只做一件判断：现在有没有人在说话（音量门限）。有人说话就开始送音频，
 * 说完后再多送 3 秒环境音让服务端的静音检测收尾。回答播放期间闭麦，免得听见自己。
 * 演示时浏览器的麦克风就是机器狗的耳朵；真机器狗接入时走同一条协议。
 */
export function useDogEars() {
  // idle 未启动 / connecting 连接中 / listening 倾听中 / hearing 听到有人说话 / thinking 分析中 / speaking 播报中 / error
  const state = ref('idle')
  const level = ref(0)
  const partial = ref('')
  const streaming = ref(false)
  const errorText = ref('')

  let socket = null
  let audioContext = null
  let mediaStream = null
  let processorNode = null
  let workletNode = null
  let muted = false
  let speaking = false
  let lastVoiceAt = 0
  let hangoverTimer = null
  let handlers = {}

  // 语音门限：RMS 超过这个值算有人说话；说完后 3 秒内继续送音频（要大于服务端 2.5 秒的静音判定）
  const VOICE_THRESHOLD = 0.012
  const HANGOVER_MS = 3000

  function emit(name, payload) {
    handlers[name]?.(payload)
  }

  function wsUrl(deviceId) {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const token = localStorage.getItem(TOKEN_KEY) || ''
    return `${protocol}://${window.location.host}/ws/dog-audio/${encodeURIComponent(deviceId)}?token=${encodeURIComponent(token)}`
  }

  async function start(deviceId, callbacks = {}) {
    if (state.value !== 'idle' && state.value !== 'error') return
    handlers = callbacks
    errorText.value = ''
    state.value = 'connecting'

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
    } catch (error) {
      state.value = 'error'
      errorText.value = error.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器地址栏允许后重试' : `打不开麦克风：${error.message}`
      emit('error', errorText.value)
      return
    }

    await new Promise((resolve, reject) => {
      socket = new WebSocket(wsUrl(deviceId))
      socket.binaryType = 'arraybuffer'
      socket.onopen = resolve
      socket.onerror = () => reject(new Error('连不上平台的音频通道'))
      socket.onclose = (event) => {
        if (state.value !== 'idle') {
          state.value = 'error'
          errorText.value = event.code === 4403 ? '机器狗未绑定老人或无权限' : '音频通道已断开'
          emit('error', errorText.value)
          stopAudio()
        }
      }
      socket.onmessage = (message) => {
        let event
        try {
          event = JSON.parse(message.data)
        } catch {
          return
        }
        onServerEvent(event)
      }
    }).catch((error) => {
      state.value = 'error'
      errorText.value = error.message
      emit('error', errorText.value)
      stopAudio()
    })
    if (state.value === 'error') return

    await startAudio()
    state.value = 'listening'
  }

  function onServerEvent(event) {
    if (event.event === 'ready') {
      streaming.value = Boolean(event.streaming)
    } else if (event.event === 'partial') {
      partial.value = event.text
      state.value = 'hearing'
    } else if (event.event === 'transcript') {
      partial.value = ''
      state.value = 'thinking'
    } else if (event.event === 'busy') {
      // 机器狗还在答上一句，这段声音被忽略了
    } else if (event.event === 'ignored') {
      // 只听到"嗯""啊"或飘过来的半个词，不值得回答，接着听
      partial.value = ''
      if (!muted) state.value = 'listening'
    } else if (event.event === 'idle') {
      if (!muted) state.value = 'listening'
    } else if (event.event === 'error') {
      emit('error', event.detail)
      if (state.value === 'hearing') state.value = 'listening'
    }
    emit(event.event, event)
  }

  async function startAudio() {
    audioContext = new AudioContext({ sampleRate: 16000 })
    const source = audioContext.createMediaStreamSource(mediaStream)
    // AudioWorklet 在独立线程里跑，不会因为页面卡顿丢音；不支持时退回 ScriptProcessor
    try {
      const workletCode = `
        class PcmForwarder extends AudioWorkletProcessor {
          process(inputs) {
            const channel = inputs[0] && inputs[0][0]
            if (channel) this.port.postMessage(channel.slice(0))
            return true
          }
        }
        registerProcessor('pcm-forwarder', PcmForwarder)
      `
      const blobUrl = URL.createObjectURL(new Blob([workletCode], { type: 'application/javascript' }))
      await audioContext.audioWorklet.addModule(blobUrl)
      workletNode = new AudioWorkletNode(audioContext, 'pcm-forwarder')
      workletNode.port.onmessage = (event) => handleSamples(event.data)
      source.connect(workletNode)
    } catch {
      processorNode = audioContext.createScriptProcessor(2048, 1, 1)
      processorNode.onaudioprocess = (event) => handleSamples(event.inputBuffer.getChannelData(0))
      source.connect(processorNode)
      processorNode.connect(audioContext.destination)
    }
  }

  let batch = []
  let batchSamples = 0
  const BATCH_SAMPLES = 1280 // 80ms

  function handleSamples(samples) {
    let sum = 0
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i]
    const rms = Math.sqrt(sum / samples.length)
    level.value = Math.min(1, rms * 12)
    if (muted) return

    const now = performance.now()
    if (rms > VOICE_THRESHOLD) {
      lastVoiceAt = now
      if (!speaking) {
        speaking = true
        if (state.value === 'listening') state.value = 'hearing'
      }
      clearTimeout(hangoverTimer)
      hangoverTimer = setTimeout(endUtterance, HANGOVER_MS)
    }
    if (!speaking) return

    batch.push(samples)
    batchSamples += samples.length
    if (batchSamples >= BATCH_SAMPLES) flushBatch()
  }

  function flushBatch() {
    if (!batch.length || !socket || socket.readyState !== WebSocket.OPEN) {
      batch = []
      batchSamples = 0
      return
    }
    const pcm = new Int16Array(batchSamples)
    let offset = 0
    for (const chunk of batch) {
      for (let i = 0; i < chunk.length; i += 1) {
        const s = Math.max(-1, Math.min(1, chunk[i]))
        pcm[offset + i] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      offset += chunk.length
    }
    socket.send(pcm.buffer)
    batch = []
    batchSamples = 0
  }

  function endUtterance() {
    // 说完了：把剩下的送出去，并明确告诉服务端这句结束（静音检测通常已经先收尾了）
    flushBatch()
    speaking = false
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'end' }))
    if (state.value === 'hearing') state.value = 'listening'
  }

  function sendText(text) {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'text', text }))
      state.value = 'thinking'
    }
  }

  /** 机器狗开口时闭麦，说完再打开。 */
  function mute(on) {
    muted = on
    if (on) {
      clearTimeout(hangoverTimer)
      speaking = false
      batch = []
      batchSamples = 0
      state.value = 'speaking'
    } else if (state.value === 'speaking') {
      state.value = 'listening'
    }
  }

  function stopAudio() {
    clearTimeout(hangoverTimer)
    processorNode?.disconnect()
    workletNode?.disconnect()
    mediaStream?.getTracks().forEach((track) => track.stop())
    audioContext?.close().catch(() => {})
    processorNode = workletNode = mediaStream = audioContext = null
    level.value = 0
  }

  function stop() {
    const wasIdle = state.value === 'idle'
    state.value = 'idle'
    partial.value = ''
    stopAudio()
    if (socket) {
      const sock = socket
      socket = null
      sock.onclose = null
      sock.close()
    }
    if (!wasIdle) emit('stopped')
  }

  onUnmounted(stop)

  return { state, level, partial, streaming, errorText, start, stop, mute, sendText }
}
