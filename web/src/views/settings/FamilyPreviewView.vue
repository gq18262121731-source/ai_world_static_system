<script setup>
import { ref } from 'vue'
import FamilyView from '@/views/FamilyView.vue'

// 换 key 可以强制重挂载，相当于刷新预览
const previewKey = ref(0)

function openStandalone() {
  window.open(`${window.location.origin}${window.location.pathname}#/family`, '_blank')
}
</script>

<template>
  <div class="page">
    <div class="preview-head card">
      <div>
        <h3 class="card-title" style="margin-bottom: 6px">家属端视图</h3>
        <p class="muted" style="margin: 0">
          家属账号登录后默认进入这个页面。它会实时展示老人当前状态、外出位置、健康数据与告警，
          并支持一键确认告警。这里是等比例预览，操作与真实页面完全一致。
        </p>
      </div>
      <div class="actions">
        <el-button @click="previewKey += 1">
          <el-icon><Refresh /></el-icon>刷新预览
        </el-button>
        <el-button type="primary" @click="openStandalone">
          <el-icon><TopRight /></el-icon>新窗口打开
        </el-button>
      </div>
    </div>

    <div class="stage">
      <div class="phone">
        <div class="notch"></div>
        <div class="screen">
          <FamilyView :key="previewKey" preview />
        </div>
        <div class="home-bar"></div>
      </div>

      <div class="notes card">
        <h3 class="card-title">设计要点</h3>
        <ul class="note-list">
          <li>顶部状态卡用一句话概括老人当前状态，颜色随风险等级变化，家属扫一眼就知道要不要紧。</li>
          <li>体征数值放大展示并按状态着色，异常项单独用暖色块列出，不需要理解具体数字含义。</li>
          <li>外出时显示相对位置与电子围栏，标注出门时间与约定返回时长。</li>
          <li>告警卡片带「我已知晓」按钮，家属确认后后台能看到处置记录。</li>
          <li>页面每 30 秒自动刷新，同时通过 WebSocket 接收即时推送。</li>
        </ul>

        <h3 class="card-title" style="margin-top: 22px">家属账号</h3>
        <p class="muted" style="margin: 0">
          演示账号 family / family123，登录后自动进入家属端，只能看到与其绑定的老人。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}

.actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.stage {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 24px;
  align-items: start;
}

.phone {
  width: 390px;
  background: #2f3833;
  border-radius: 40px;
  padding: 12px;
  box-shadow: var(--shadow-lg);
  position: relative;
}

.notch {
  width: 118px;
  height: 22px;
  background: #2f3833;
  border-radius: 0 0 14px 14px;
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
}

.screen {
  height: 720px;
  overflow-y: auto;
  border-radius: 30px;
  background: var(--canvas);
}

.screen::-webkit-scrollbar {
  width: 0;
}

.home-bar {
  width: 120px;
  height: 4px;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 2px;
  margin: 8px auto 2px;
}

/* 预览里去掉家属端自身的居中限制，让它填满机身 */
.screen :deep(.family) {
  max-width: none;
  box-shadow: none;
  min-height: 100%;
}

/* 给刘海让出空间，避免遮住顶部问候语 */
.screen :deep(.top) {
  padding-top: 34px;
}

.note-list {
  margin: 0;
  padding-left: 19px;
  line-height: 2.1;
  font-size: 15.5px;
  color: var(--ink-mid);
}

@media (max-width: 1150px) {
  .stage {
    grid-template-columns: 1fr;
    justify-items: center;
  }
  .phone {
    width: 100%;
    max-width: 390px;
  }
}
</style>
