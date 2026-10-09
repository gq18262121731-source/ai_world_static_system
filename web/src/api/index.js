import { ElMessage } from 'element-plus'
import http from './http'
import { usePresentationStore } from '@/stores/presentation'

// 演示第 0 步"未接入"：遥测类接口（体征、告警、外出、事件、对话）不发请求，直接给空结果，
// 页面照常渲染，只是数字都是 --、列表是空的。老人档案、设备、账号这类静态档案不受影响。
const PAGE_EMPTY = () => ({ items: [], total: 0 })

function whenLive(fallback, request) {
  return (...args) => {
    if (!usePresentationStore().dataLive) {
      return Promise.resolve(typeof fallback === 'function' ? fallback() : fallback)
    }
    return request(...args)
  }
}

// 会改动数据的操作在未接入时直接拦下，免得后台悄悄产生了记录
function whenLiveAction(request) {
  return (...args) => {
    if (!usePresentationStore().dataLive) {
      ElMessage.info('数据尚未接入，刷新一次页面后再操作')
      return Promise.reject(new Error('数据尚未接入'))
    }
    return request(...args)
  }
}

// 智能体是演示的第 3 步：对话记录、对话接口在此之前一律按"未接入"处理
function whenAgentLive(fallback, request) {
  return (...args) => {
    if (!usePresentationStore().agentLive) {
      return Promise.resolve(typeof fallback === 'function' ? fallback() : fallback)
    }
    return request(...args)
  }
}

function whenAgentLiveAction(request) {
  return (...args) => {
    if (!usePresentationStore().agentLive) {
      ElMessage.info('智能体尚未接入')
      return Promise.reject(new Error('智能体尚未接入'))
    }
    return request(...args)
  }
}

export const authApi = {
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/auth/me'),
  users: () => http.get('/auth/users'),
  createUser: (data) => http.post('/auth/users', data),
}

export const elderApi = {
  list: () => http.get('/elders'),
  detail: (id) => http.get(`/elders/${id}`),
  create: (data) => http.post('/elders', data),
  update: (id, data) => http.put(`/elders/${id}`, data),
  remove: (id) => http.delete(`/elders/${id}`),
  addContact: (id, data) => http.post(`/elders/${id}/contacts`, data),
  removeContact: (id, contactId) => http.delete(`/elders/${id}/contacts/${contactId}`),
}

export const deviceApi = {
  list: (params) => http.get('/devices', { params }),
  create: (data) => http.post('/devices', data),
  update: (deviceId, data) => http.put(`/devices/${deviceId}`, data),
  remove: (deviceId) => http.delete(`/devices/${deviceId}`),
  command: (deviceId, data) => http.post(`/devices/${deviceId}/command`, data),
  snapshot: (elderId) => http.get('/devices/status/snapshot', { params: { elder_id: elderId } }),
  mqttStatus: () => http.get('/devices/mqtt/status'),
  selfCheck: (pingTimeout = 3) =>
    http.post('/devices/self-check', null, { params: { ping_timeout: pingTimeout } }),
  gateways: () => http.get('/devices/gateways'),
}

export const healthApi = {
  live: whenLive([], (points = 30) => http.get('/health/live', { params: { points } })),
  records: whenLive([], (params) => http.get('/health/records', { params })),
  latest: whenLive(null, (elderId) => http.get('/health/latest', { params: { elder_id: elderId } })),
  assessment: whenLive(null, (elderId, windowHours = 24) =>
    http.get('/health/assessment', { params: { elder_id: elderId, window_hours: windowHours } }),
  ),
  summaries: whenLive([], (elderId, days = 7) =>
    http.get('/health/summaries', { params: { elder_id: elderId, days } }),
  ),
  trend: whenLive(null, (elderId, metric, hours = 24, points = 240) =>
    http.get('/health/trend', { params: { elder_id: elderId, metric, hours, points } }),
  ),
}

export const outingApi = {
  stats: whenLive(null, (elderId) => http.get('/outings/stats', { params: { elder_id: elderId } })),
  ruleReport: whenLive(null, (elderId) =>
    http.get('/outings/rule-report', { params: { elder_id: elderId } }),
  ),
  evaluate: whenLiveAction((data) => http.post('/outings/evaluate', data)),
  request: whenLiveAction((data) => http.post('/outings/request', data)),
  requests: whenLive(PAGE_EMPTY, (params) => http.get('/outings/requests', { params })),
  decision: whenLive(null, (id) => http.get(`/outings/decisions/${id}`)),
  tasks: whenLive(PAGE_EMPTY, (params) => http.get('/outings/tasks', { params })),
  activeTask: whenLive(null, (elderId) =>
    http.get('/outings/tasks/active', { params: { elder_id: elderId } }),
  ),
  track: whenLive([], (taskId) => http.get(`/outings/tasks/${taskId}/track`)),
  endTask: whenLiveAction((taskId, reason = 'manual') =>
    http.post(`/outings/tasks/${taskId}/end`, null, { params: { reason } }),
  ),
}

export const alarmApi = {
  list: whenLive(PAGE_EMPTY, (params) => http.get('/alarms', { params })),
  detail: whenLive(null, (id) => http.get(`/alarms/${id}`)),
  handle: whenLiveAction((id, data) => http.post(`/alarms/${id}/handle`, data)),
}

export const agentApi = {
  talk: whenAgentLiveAction((data) => http.post('/agent/talk', data)),
  conversations: whenAgentLive([], (elderId, limit = 20) =>
    http.get('/agent/conversations', { params: { elder_id: elderId, limit } }),
  ),
  status: () => http.get('/agent/status'),
  turns: whenAgentLive(PAGE_EMPTY, (params) => http.get('/agent/turns', { params })),
  metrics: () => http.get('/agent/metrics'),
  intents: () => http.get('/agent/intents'),
  testProviders: () => http.post('/agent/providers/test'),
  templates: () => http.get('/agent/templates'),
  updateTemplate: (key, data) => http.put(`/agent/templates/${key}`, data),
  resetTemplate: (key) => http.delete(`/agent/templates/${key}`),
}

export const ruleApi = {
  defaults: () => http.get('/rules/defaults'),
  effective: (elderId) => http.get('/rules/effective', { params: { elder_id: elderId } }),
  configs: (elderId) => http.get('/rules/configs', { params: { elder_id: elderId } }),
  upsert: (data, elderId) => http.put('/rules/configs', data, { params: { elder_id: elderId } }),
  remove: (id) => http.delete(`/rules/configs/${id}`),
}

export const simulatorApi = {
  state: (elderId) => http.get('/simulator/state', { params: { elder_id: elderId } }),
  healthProfile: (data) => http.post('/simulator/health-profile', data),
  deviceState: (data) => http.post('/simulator/device-state', data),
  fall: (data) => http.post('/simulator/fall', data),
  speak: (data) => http.post('/simulator/speak', data),
  walk: (data) => http.post('/simulator/walk', data),
  reset: (data) => http.post('/simulator/reset', data),
  restoreBaseline: () => http.post('/simulator/demo-baseline'),
  runtime: (logLines = 0) => http.get('/simulator/runtime', { params: { log_lines: logLines } }),
  runtimeStart: (data = {}) => http.post('/simulator/runtime/start', data),
  runtimeStop: () => http.post('/simulator/runtime/stop'),
}

export const dashboardApi = {
  overview: whenLive(null, () => http.get('/dashboard/overview')),
  outingTrend: whenLive([], (days = 7) => http.get('/dashboard/outing-trend', { params: { days } })),
  auditLogs: (limit = 100) => http.get('/dashboard/audit-logs', { params: { limit } }),
}
