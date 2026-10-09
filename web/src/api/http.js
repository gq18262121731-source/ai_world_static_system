import axios from 'axios'
import { ElMessage } from 'element-plus'

export const TOKEN_KEY = 'aiot_token'

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail

    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (!window.location.hash.includes('/login')) {
        window.location.hash = '#/login'
      }
    }
    ElMessage.error(detail || error.message || '请求失败')
    return Promise.reject(error)
  },
)

export default http
