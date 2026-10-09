import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus'
import { TOKEN_KEY } from '@/api/http'
import { usePresentationStore } from '@/stores/presentation'

const MAX_EVENTS = 200
const RECONNECT_DELAY = 3000

const ALARM_LEVEL_TYPE = {
  info: 'info',
  warning: 'warning',
  critical: 'error',
}

export const useRealtimeStore = defineStore('realtime', {
  state: () => ({
    connected: false,
    events: [],
    socket: null,
    timer: null,
    notifyAlarms: true,
  }),
  getters: {
    latestByEvent: (state) => (name) => state.events.find((item) => item.event === name),
    eventsOf: (state) => (names) => state.events.filter((item) => names.includes(item.event)),
  },
  actions: {
    connect() {
      const token = localStorage.getItem(TOKEN_KEY)
      if (!token || this.socket) return
      // 演示第 0 步"未接入"：不连推送，事件流保持空白
      if (!usePresentationStore().dataLive) return

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(
        `${protocol}://${window.location.host}/ws?token=${encodeURIComponent(token)}`,
      )
      this.socket = socket

      socket.onopen = () => {
        this.connected = true
      }
      socket.onmessage = (raw) => {
        if (raw.data === 'pong') return
        let message
        try {
          message = JSON.parse(raw.data)
        } catch {
          return
        }
        if (message.event === 'connected') {
          this.events = (message.data?.recent || []).reverse()
          return
        }
        if (message.event === 'heartbeat') return

        this.events.unshift(message)
        if (this.events.length > MAX_EVENTS) {
          this.events.length = MAX_EVENTS
        }
        this.handleNotification(message)
      }
      socket.onclose = () => {
        this.connected = false
        this.socket = null
        this.scheduleReconnect()
      }
      socket.onerror = () => socket.close()
    },
    scheduleReconnect() {
      if (this.timer || !localStorage.getItem(TOKEN_KEY)) return
      this.timer = setTimeout(() => {
        this.timer = null
        this.connect()
      }, RECONNECT_DELAY)
    },
    handleNotification(message) {
      if (!this.notifyAlarms) return
      if (message.event === 'alarm') {
        ElNotification({
          title: message.data.title || '新告警',
          message: message.data.description || '',
          type: ALARM_LEVEL_TYPE[message.data.level] || 'warning',
          duration: message.data.level === 'critical' ? 0 : 6000,
        })
      }
      if (message.event === 'outing_decision' && message.data.decision === 'deny') {
        ElNotification({
          title: '外出申请被拒绝',
          message: (message.data.reasons || [])[0] || '',
          type: 'warning',
          duration: 6000,
        })
      }
    },
    disconnect() {
      if (this.timer) {
        clearTimeout(this.timer)
        this.timer = null
      }
      this.socket?.close()
      this.socket = null
      this.connected = false
      this.events = []
    },
  },
})
