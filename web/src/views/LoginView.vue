<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const form = reactive({ username: 'admin', password: 'admin123' })
const now = ref(dayjs())
let clock = null

const accounts = [
  { username: 'admin', password: 'admin123', label: '平台管理员', icon: 'Setting' },
  { username: 'staff', password: 'staff123', label: '社区服务人员', icon: 'Avatar' },
  { username: 'family', password: 'family123', label: '家属', icon: 'HomeFilled' },
]

// 三张错落的说明卡，构成非对称的主视觉
const cards = [
  {
    icon: 'ChatDotRound',
    title: '出门前问一句',
    text: '老人说想出去走走，系统结合体征、外出频次与天气判断能不能出门。',
    tone: 'brand',
  },
  {
    icon: 'Guide',
    title: '路上有狗陪着',
    text: '机器狗全程跟随，越界或超时会温和提醒，家属能看到实时位置。',
    tone: 'accent',
  },
  {
    icon: 'FirstAidKit',
    title: '摔倒立刻有人管',
    text: '手表与机器狗双源检测，先语音确认，无应答自动升级通知。',
    tone: 'ok',
  },
]

function fill(account) {
  form.username = account.username
  form.password = account.password
}

async function submit() {
  loading.value = true
  try {
    const user = await auth.login(form.username, form.password)
    ElMessage.success(`欢迎回来，${user.real_name || user.username}`)
    const redirect = route.query.redirect
    if (redirect) router.push(redirect)
    else router.push(user.role === 'family' ? { name: 'family' } : { name: 'dashboard' })
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  clock = setInterval(() => (now.value = dayjs()), 1000)
})
onUnmounted(() => clearInterval(clock))
</script>

<template>
  <div class="login">
    <span class="orb orb-a"></span>
    <span class="orb orb-b"></span>
    <span class="orb orb-c"></span>

    <div class="wrap">
      <!-- 左：品牌与场景说明，卡片错落排布 -->
      <div class="left rise-stagger">
        <div class="brand">
          <span class="brand-mark"><el-icon :size="24"><Sunny /></el-icon></span>
          <div>
            <div class="brand-title">基于AIoT的智慧健康监测平台</div>
            <div class="brand-clock">{{ now.format('YYYY年M月D日  HH:mm:ss') }}</div>
          </div>
        </div>

        <h1 class="slogan">
          让独居老人<br />
          <em>安心出门</em>，放心回家
        </h1>

        <div class="cards">
          <article
            v-for="(card, index) in cards"
            :key="card.title"
            class="scene liftable"
            :class="[`tone-${card.tone}`, `offset-${index}`]"
            v-ripple
          >
            <span class="scene-icon">
              <el-icon :size="18"><component :is="card.icon" /></el-icon>
            </span>
            <div>
              <div class="scene-title">{{ card.title }}</div>
              <div class="scene-text">{{ card.text }}</div>
            </div>
          </article>
        </div>
      </div>

      <!-- 右：登录面板 -->
      <div class="panel rise">
        <div class="panel-head">
          <h2>欢迎回来</h2>
          <p>登录后即可查看老人今天的状态</p>
        </div>

        <el-form :model="form" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" size="large" placeholder="请输入用户名">
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              size="large"
              show-password
              placeholder="请输入密码"
              @keyup.enter="submit"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <button class="submit" :disabled="loading" v-ripple="'light'" @click.prevent="submit">
            <el-icon v-if="loading" class="is-loading"><Loading /></el-icon>
            <span>{{ loading ? '正在进入' : '进入平台' }}</span>
            <el-icon class="submit-arrow"><Right /></el-icon>
          </button>
        </el-form>

        <div class="split"><span>演示账号 · 点击填入</span></div>

        <div class="accounts">
          <button
            v-for="account in accounts"
            :key="account.username"
            class="account"
            :class="{ active: form.username === account.username }"
            v-ripple
            @click="fill(account)"
          >
            <el-icon :size="17"><component :is="account.icon" /></el-icon>
            <span>{{ account.label }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 12% 18%, #e8f3ef 0%, transparent 45%),
    radial-gradient(circle at 88% 78%, #faeee1 0%, transparent 48%),
    var(--canvas);
}

/* 缓慢漂浮的色块，让静态页面有呼吸感 */
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(72px);
  pointer-events: none;
}

.orb-a {
  width: 420px;
  height: 420px;
  background: rgba(76, 190, 172, 0.42);
  top: -120px;
  left: 6%;
  animation: drift 17s ease-in-out infinite;
}

.orb-b {
  width: 360px;
  height: 360px;
  background: rgba(240, 178, 122, 0.4);
  bottom: -130px;
  right: 12%;
  animation: drift 21s ease-in-out infinite reverse;
}

.orb-c {
  width: 260px;
  height: 260px;
  background: rgba(120, 190, 230, 0.26);
  top: 44%;
  left: 46%;
  animation: drift 26s ease-in-out infinite;
}

@keyframes drift {
  0%, 100% {
    transform: translate(0, 0) scale(1);
  }
  33% {
    transform: translate(38px, -28px) scale(1.08);
  }
  66% {
    transform: translate(-26px, 24px) scale(0.95);
  }
}

.wrap {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) 384px;
  gap: 40px;
  align-items: center;
  max-width: 1120px;
  width: 100%;
}

/* ---------- 左侧 ---------- */

.brand {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-bottom: 26px;
}

.brand-mark {
  width: 46px;
  height: 46px;
  border-radius: 16px;
  background: linear-gradient(140deg, var(--brand), #4ec2ac);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(47, 158, 147, 0.3);
}

.brand-title {
  font-size: 16px;
  font-weight: 600;
}

.brand-clock {
  font-size: 14.5px;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}

.slogan {
  font-size: 42px;
  line-height: 1.42;
  font-weight: 600;
  margin: 0 0 32px;
  letter-spacing: 1px;
  color: var(--ink);
}

/* 荧光笔式高亮：用渐变背景实现，不依赖 z-index 层叠 */
.slogan em {
  font-style: normal;
  color: var(--brand-deep);
  background: linear-gradient(transparent 76%, rgba(224, 130, 64, 0.34) 76%, rgba(224, 130, 64, 0.34) 94%, transparent 94%);
  padding: 0 5px;
  border-radius: 3px;
}

.cards {
  display: grid;
  gap: 12px;
  max-width: 470px;
}

.scene {
  display: flex;
  gap: 13px;
  align-items: flex-start;
  padding: 15px 17px;
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: var(--shadow-sm);
  cursor: default;
}

/* 逐张递增的缩进，形成阶梯而不是整齐的列表 */
.offset-1 {
  margin-left: 30px;
}

.offset-2 {
  margin-left: 60px;
}

.scene-icon {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}

.tone-brand .scene-icon {
  background: linear-gradient(140deg, var(--brand), #4ec2ac);
}

.tone-accent .scene-icon {
  background: linear-gradient(140deg, var(--accent), #eda76a);
}

.tone-ok .scene-icon {
  background: linear-gradient(140deg, var(--ok), #62c396);
}

.scene-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 3px;
}

.scene-text {
  font-size: 14.5px;
  color: var(--ink-soft);
  line-height: 1.75;
}

/* ---------- 右侧登录面板 ---------- */

.panel {
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: 26px;
  padding: 36px 32px;
  box-shadow: 0 24px 60px rgba(75, 68, 57, 0.16);
}

.panel-head h2 {
  margin: 0;
  font-size: 23px;
}

.panel-head p {
  color: var(--ink-soft);
  font-size: 15.5px;
  margin: 6px 0 24px;
}

.submit {
  width: 100%;
  height: 50px;
  margin-top: 8px;
  border: none;
  border-radius: var(--radius-pill);
  background: linear-gradient(135deg, var(--brand-deep), var(--brand) 55%, #4ec2ac);
  color: #fff;
  font-size: 17px;
  font-family: inherit;
  letter-spacing: 2px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  box-shadow: 0 10px 24px rgba(47, 158, 147, 0.34);
  transition:
    transform var(--dur-fast) var(--spring-snap),
    box-shadow var(--dur) var(--ease-out);
}

.submit:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 14px 30px rgba(47, 158, 147, 0.42);
}

.submit:active:not(:disabled) {
  transform: translateY(1px) scale(0.985);
}

.submit:disabled {
  opacity: 0.75;
  cursor: not-allowed;
}

.submit-arrow {
  transition: transform var(--dur) var(--spring);
}

.submit:hover .submit-arrow {
  transform: translateX(5px);
}

.split {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 24px 0 14px;
  color: var(--ink-soft);
  font-size: 14px;
}

.split::before,
.split::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}

.accounts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 9px;
}

.account {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 13px 6px;
  border: 1px solid var(--line);
  background: var(--surface-tint);
  border-radius: var(--radius);
  cursor: pointer;
  font-family: inherit;
  font-size: 14px;
  color: var(--ink-mid);
  position: relative;
  overflow: hidden;
  transition:
    transform var(--dur-fast) var(--spring-snap),
    border-color var(--dur) var(--ease-out),
    background-color var(--dur) var(--ease-out),
    color var(--dur) var(--ease-out);
}

.account:hover {
  transform: translateY(-3px);
  border-color: var(--brand);
  color: var(--brand);
}

.account:active {
  transform: scale(0.95);
}

.account.active {
  border-color: var(--brand);
  background: var(--brand-soft);
  color: var(--brand-deep);
  font-weight: 600;
}

@media (max-width: 1120px) {
  .slogan {
    font-size: 34px;
  }
  .offset-1 {
    margin-left: 18px;
  }
  .offset-2 {
    margin-left: 36px;
  }
}

@media (max-width: 820px) {
  .wrap {
    grid-template-columns: 1fr;
    gap: 26px;
    max-width: 440px;
  }
  .slogan {
    font-size: 28px;
    margin-bottom: 22px;
  }
  .cards {
    display: none;
  }
  .panel {
    padding: 28px 24px;
  }
}
</style>
