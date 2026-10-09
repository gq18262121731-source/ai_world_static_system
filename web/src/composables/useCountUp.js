import { ref, watch } from 'vue'

/**
 * 数字变化时滚动到新值，让"数据在动"这件事被看见。
 * 变化幅度很大时直接跳过动画，避免长时间滚数字。
 */
export function useCountUp(source, { duration = 650, decimals = 0 } = {}) {
  const display = ref(Number(source.value) || 0)
  let frame = null

  watch(source, (next, prev) => {
    const to = Number(next)
    const from = Number(prev ?? display.value)
    if (Number.isNaN(to)) return
    if (Number.isNaN(from) || Math.abs(to - from) > 1000) {
      display.value = to
      return
    }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) {
      display.value = to
      return
    }

    cancelAnimationFrame(frame)
    const start = performance.now()
    const step = (now) => {
      const progress = Math.min(1, (now - start) / duration)
      // easeOutCubic：开头快、结尾稳
      const eased = 1 - (1 - progress) ** 3
      const value = from + (to - from) * eased
      display.value = Number(value.toFixed(decimals))
      if (progress < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
  })

  return display
}
