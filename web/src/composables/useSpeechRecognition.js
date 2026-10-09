import { onUnmounted, ref } from 'vue'

/**
 * 浏览器语音识别（Web Speech API）。
 *
 * 对应真机上机器狗的"拾音 + 语音转文字"环节：Chrome / Edge 免密钥可用，
 * 中文识别效果足够演示。浏览器不支持时降级为打字输入。
 */
export function useSpeechRecognition({ lang = 'zh-CN' } = {}) {
  const Engine = window.SpeechRecognition || window.webkitSpeechRecognition
  const supported = Boolean(Engine)

  const listening = ref(false)
  const interim = ref('')
  const finalText = ref('')
  const error = ref('')

  let recognition = null
  let onFinal = null

  function start(callback) {
    if (!supported || listening.value) return
    onFinal = callback
    interim.value = ''
    finalText.value = ''
    error.value = ''

    recognition = new Engine()
    recognition.lang = lang
    recognition.interimResults = true
    recognition.continuous = false
    recognition.maxAlternatives = 1

    recognition.onstart = () => {
      listening.value = true
    }

    recognition.onresult = (event) => {
      let partial = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (result.isFinal) {
          finalText.value = result[0].transcript.trim()
        } else {
          partial += result[0].transcript
        }
      }
      interim.value = partial
    }

    recognition.onerror = (event) => {
      const messages = {
        'no-speech': '没有听到声音，请再说一次',
        'audio-capture': '找不到麦克风，请检查设备',
        'not-allowed': '麦克风权限被拒绝，请在浏览器地址栏允许后重试',
        network: '语音识别服务连接失败，请检查网络',
        aborted: '',
      }
      error.value = messages[event.error] ?? `识别出错：${event.error}`
    }

    recognition.onend = () => {
      listening.value = false
      const text = finalText.value.trim()
      if (text && onFinal) onFinal(text)
      recognition = null
    }

    try {
      recognition.start()
    } catch (exc) {
      error.value = `无法启动麦克风：${exc.message}`
      listening.value = false
    }
  }

  function stop() {
    recognition?.stop()
  }

  function abort() {
    onFinal = null
    recognition?.abort()
    listening.value = false
  }

  onUnmounted(abort)

  return { supported, listening, interim, finalText, error, start, stop, abort }
}
