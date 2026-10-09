/**
 * v-ripple：点击时从指针位置扩散一圈水波。
 *
 * 用法 <button v-ripple>、<div v-ripple="'light'">（light 用于深色底）。
 */

const REDUCED = () =>
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches

function spawn(event, el, variant) {
  if (REDUCED()) return

  const rect = el.getBoundingClientRect()
  const size = Math.max(rect.width, rect.height)
  const ink = document.createElement('span')
  ink.className = 'ripple-ink'
  ink.style.width = ink.style.height = `${size}px`
  ink.style.left = `${event.clientX - rect.left - size / 2}px`
  ink.style.top = `${event.clientY - rect.top - size / 2}px`
  if (variant === 'light') {
    ink.style.background = '#fff'
    ink.style.opacity = '0.3'
  }
  el.appendChild(ink)
  ink.addEventListener('animationend', () => ink.remove())
}

export const ripple = {
  mounted(el, binding) {
    el.classList.add('ripple-host')
    el._rippleHandler = (event) => spawn(event, el, binding.value)
    el.addEventListener('pointerdown', el._rippleHandler)
  },
  unmounted(el) {
    if (el._rippleHandler) {
      el.removeEventListener('pointerdown', el._rippleHandler)
      delete el._rippleHandler
    }
  },
}
