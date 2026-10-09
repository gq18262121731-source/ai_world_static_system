import { ref } from 'vue'
import { elderApi } from '@/api'

const elders = ref([])
const currentElderId = ref(null)
const loaded = ref(false)

/** 老人列表在多个页面共用, 这里做一次轻量缓存, 避免每次切页都重新拉取。 */
export function useElders() {
  async function load(force = false) {
    if (loaded.value && !force) return elders.value
    elders.value = await elderApi.list()
    loaded.value = true
    if (!currentElderId.value && elders.value.length) {
      currentElderId.value = elders.value[0].id
    }
    return elders.value
  }

  function elderName(id) {
    return elders.value.find((item) => item.id === id)?.name || `老人#${id}`
  }

  return { elders, currentElderId, load, elderName }
}
