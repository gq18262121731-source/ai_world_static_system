import { TOKEN_KEY } from '@/api/http'

/**
 * 以 POST 发起 SSE 请求并逐条回调事件。
 * EventSource 不支持 POST 和自定义头，所以用 fetch + ReadableStream 自己解析。
 * 返回一个 abort 函数，组件卸载或用户打断时调用。
 */
export function streamPost(path, body, onEvent, onDone, onError) {
  const controller = new AbortController()
  const token = localStorage.getItem(TOKEN_KEY)

  fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        let detail = `${response.status}`
        try {
          detail = (await response.json()).detail || detail
        } catch {
          // 非 JSON 错误体
        }
        throw new Error(detail)
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 事件以空行分隔，一个 chunk 里可能有多条、也可能只有半条
        let index
        while ((index = buffer.indexOf('\n\n')) >= 0) {
          const raw = buffer.slice(0, index)
          buffer = buffer.slice(index + 2)
          for (const line of raw.split('\n')) {
            if (!line.startsWith('data:')) continue
            try {
              onEvent(JSON.parse(line.slice(5).trim()))
            } catch {
              // 跳过无法解析的行
            }
          }
        }
      }
      onDone?.()
    })
    .catch((error) => {
      if (error.name === 'AbortError') return
      onError?.(error)
    })

  return () => controller.abort()
}
