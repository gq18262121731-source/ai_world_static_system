import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '总览看板', icon: 'DataBoard' },
      },
      {
        path: 'elders',
        name: 'elders',
        component: () => import('@/views/EldersView.vue'),
        meta: { title: '老人档案', icon: 'User' },
      },
      {
        path: 'elders/:id',
        name: 'elder-detail',
        component: () => import('@/views/ElderDetailView.vue'),
        meta: { title: '健康看板', hidden: true },
      },
      {
        path: 'outings',
        name: 'outings',
        component: () => import('@/views/OutingsView.vue'),
        meta: { title: '外出与决策', icon: 'Guide' },
      },
      {
        path: 'agent',
        name: 'agent',
        component: () => import('@/views/AgentView.vue'),
        meta: { title: '智能体', icon: 'ChatDotRound' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/SettingsView.vue'),
        redirect: '/settings/alarms',
        meta: { title: '设置', icon: 'Setting' },
        children: [
          {
            path: 'alarms',
            name: 'alarms',
            component: () => import('@/views/AlarmsView.vue'),
            meta: {
              title: '告警中心',
              icon: 'BellFilled',
              desc: '跌倒、越界与健康异常处置',
            },
          },
          {
            path: 'devices',
            name: 'devices',
            component: () => import('@/views/DevicesView.vue'),
            meta: { title: '设备管理', icon: 'Cpu', desc: '手表与机器狗的绑定和状态' },
          },
          {
            path: 'rules',
            name: 'rules',
            component: () => import('@/views/RulesView.vue'),
            meta: {
              title: '规则阈值',
              icon: 'SetUp',
              desc: '外出评估的红线与风险阈值',
              adminOnly: true,
            },
          },
          {
            path: 'simulator',
            name: 'simulator',
            component: () => import('@/views/SimulatorView.vue'),
            meta: { title: '设备模拟器', icon: 'Monitor', desc: '无真机时驱动完整闭环' },
          },
          {
            path: 'presentation',
            name: 'presentation',
            component: () => import('@/views/settings/PresentationView.vue'),
            meta: { title: '演示模式', icon: 'VideoPlay', desc: '快捷键揭示数据并启动模拟器' },
          },
          {
            path: 'family',
            name: 'family-preview',
            component: () => import('@/views/settings/FamilyPreviewView.vue'),
            meta: { title: '家属端视图', icon: 'Iphone', desc: '家属手机上看到的界面' },
          },
          {
            path: 'prompts',
            name: 'prompts',
            component: () => import('@/views/settings/PromptsView.vue'),
            meta: {
              title: '提示词与话术',
              icon: 'EditPen',
              desc: '机器狗说什么、模型怎么想',
              adminOnly: true,
            },
          },
          {
            path: 'ai',
            name: 'ai-capability',
            component: () => import('@/views/settings/AiCapabilityView.vue'),
            meta: {
              title: 'AI 能力',
              icon: 'MagicStick',
              desc: '模型服务与连通性检查',
              adminOnly: true,
            },
          },
          {
            path: 'accounts',
            name: 'accounts',
            component: () => import('@/views/settings/AccountsView.vue'),
            meta: {
              title: '账号管理',
              icon: 'UserFilled',
              desc: '管理员、服务人员与家属账号',
              adminOnly: true,
            },
          },
          {
            path: 'audit',
            name: 'audit',
            component: () => import('@/views/AuditView.vue'),
            meta: {
              title: '操作审计',
              icon: 'Document',
              desc: '关键操作的追溯记录',
              adminOnly: true,
            },
          },
        ],
      },
    ],
  },
  {
    path: '/family',
    name: 'family',
    component: () => import('@/views/FamilyView.vue'),
    meta: { title: '家属端' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  if (!auth.isLoggedIn) return { name: 'login', query: { redirect: to.fullPath } }
  if (!auth.user) {
    try {
      await auth.fetchProfile()
    } catch {
      auth.logout()
      return { name: 'login' }
    }
  }
  if (to.meta.adminOnly && !auth.isAdmin) return { name: 'dashboard' }
  // 家属账号默认进入家属端
  if (auth.user?.role === 'family' && to.name === 'dashboard') return { name: 'family' }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} - AIoT居家养老健康平台`
    : 'AIoT居家养老健康平台'
})

export default router
