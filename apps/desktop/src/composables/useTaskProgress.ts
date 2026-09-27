import { onBeforeUnmount, ref, type Ref } from 'vue'

/** Keep progress interpolation out of App.vue while preserving its 0..1000 contract. */
export function useTaskProgress(reducedMotion: Ref<boolean>) {
  const progress = ref(0)
  let target = 0
  let frame = 0
  let startedAt = 0
  let from = 0

  function setProgress(value: number, immediate = false) {
    target = Math.max(0, Math.min(1000, value))
    if (immediate || reducedMotion.value) {
      progress.value = target
      cancelAnimationFrame(frame)
      frame = 0
      return
    }
    from = progress.value
    startedAt = performance.now()
    const duration = Math.min(260, Math.max(120, 120 + Math.abs(target - from) * 0.18))
    cancelAnimationFrame(frame)
    const tick = (now: number) => {
      const elapsed = Math.min(1, (now - startedAt) / duration)
      progress.value = from + (target - from) * (1 - Math.pow(1 - elapsed, 3))
      if (elapsed >= 1) {
        progress.value = target
        frame = 0
      } else {
        frame = requestAnimationFrame(tick)
      }
    }
    frame = requestAnimationFrame(tick)
  }

  onBeforeUnmount(() => cancelAnimationFrame(frame))
  return { progress, setProgress }
}
