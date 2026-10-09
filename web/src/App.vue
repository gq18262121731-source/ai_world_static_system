<script setup>
import { onMounted } from 'vue'
import { usePresentationStore } from '@/stores/presentation'

// 演示模式的快捷键挂在根组件上，管理端和独立的家属端页面都能响应
const presentation = usePresentationStore()
onMounted(() => presentation.install())
</script>

<template>
  <router-view />
</template>
