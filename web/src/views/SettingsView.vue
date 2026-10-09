<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const items = computed(() =>
  router.options.routes
    .find((item) => item.path === '/')
    .children.find((item) => item.path === 'settings')
    .children.filter((item) => !item.meta?.adminOnly || auth.isAdmin),
)

const current = computed(() => route.path.split('/')[2] || '')
</script>

<template>
  <div class="page settings">
    <div class="page-head">
      <div>
        <h2 class="page-title">设置</h2>
        <p class="page-subtitle">
          告警处置、设备与阈值管理、模拟演示，以及账号与 AI 能力等系统配置
        </p>
      </div>
    </div>

    <div class="settings-body">
      <nav class="side-nav">
        <router-link
          v-for="item in items"
          :key="item.path"
          :to="`/settings/${item.path}`"
          class="nav-item pressable"
          :class="{ active: current === item.path }"
          v-ripple
        >
          <span class="nav-icon">
            <el-icon :size="17"><component :is="item.meta.icon" /></el-icon>
          </span>
          <span class="nav-text">
            <span class="nav-title">{{ item.meta.title }}</span>
            <span class="nav-desc">{{ item.meta.desc }}</span>
          </span>
        </router-link>
      </nav>

      <section class="settings-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </section>
    </div>
  </div>
</template>

<style scoped>
.settings-body {
  display: grid;
  grid-template-columns: 244px 1fr;
  gap: 20px;
  align-items: start;
}

.side-nav {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 10px;
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: 22px;
}

.nav-item {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 11px 12px;
  border-radius: var(--radius);
  text-decoration: none;
  color: var(--ink-mid);
  transition: 0.18s;
}

.nav-item + .nav-item {
  margin-top: 3px;
}

.nav-item:hover {
  background: var(--brand-tint);
}

.nav-item.active {
  background: var(--brand-soft);
  color: var(--brand-deep);
}

.nav-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: var(--surface-tint);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--ink-soft);
}

.nav-item.active .nav-icon {
  background: #fff;
  color: var(--brand);
}

.nav-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.nav-title {
  font-size: 16px;
  font-weight: 500;
  line-height: 1.5;
}

.nav-item.active .nav-title {
  font-weight: 600;
}

.nav-desc {
  font-size: 13.5px;
  color: var(--ink-soft);
  line-height: 1.5;
}

.settings-content {
  min-width: 0;
}

/* 子页面自带 .page 内边距与大标题，这里收敛掉避免和「设置」标题重复 */
.settings-content :deep(.page) {
  padding: 0;
  max-width: none;
}

.settings-content :deep(.page-title) {
  font-size: 18px;
}

.settings-content :deep(.page-head) {
  margin-bottom: 16px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.16s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 1000px) {
  .settings-body {
    grid-template-columns: 1fr;
  }
  .side-nav {
    position: static;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 4px;
  }
  .nav-item + .nav-item {
    margin-top: 0;
  }
  .nav-desc {
    display: none;
  }
}
</style>
