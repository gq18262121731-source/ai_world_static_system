import { defineStore } from 'pinia'
import { authApi } from '@/api'
import { TOKEN_KEY } from '@/api/http'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: null,
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.token),
    isAdmin: (state) => ['admin', 'staff'].includes(state.user?.role),
    roleLabel: (state) =>
      ({ admin: '平台管理员', staff: '社区服务人员', family: '家属' })[state.user?.role] || '',
  },
  actions: {
    async login(username, password) {
      const data = await authApi.login({ username, password })
      this.token = data.access_token
      this.user = data.user
      localStorage.setItem(TOKEN_KEY, data.access_token)
      return data.user
    },
    async fetchProfile() {
      if (!this.token) return null
      this.user = await authApi.me()
      return this.user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
    },
  },
})
