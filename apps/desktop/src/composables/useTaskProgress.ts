import { onBeforeUnmount, ref, type Ref } from 'vue'

/** Keep progress interpolation out of App.vue while preserving its 0..1000 contract. */
export function useTaskProgress(reducedMotion: Ref<boolean>) {
  const interpolationTimeConstantMs = 180
  const maxFrameElapsedMs = 32
  const maxProgressPerMs = 1.2
  const progress = ref(0)
  let target = 0
  let frame = 0
  let previousTime = 0

  function setProgress(value: number, immediate = false) {
    target = Math.max(0, Math.min(1000, value))
    if (immediate || reducedMotion.value) {
      progress.value = target
      cancelAnimationFrame(frame)
      frame = 0
      previousTime = 0
      return
    }
    if (frame) return
    previousTime = performance.now()
    const tick = (now: number) => {
      // A delayed RAF must not turn into a visible leap when the WebView was
      // busy. Keep the step bounded by both elapsed time and progress rate.
      const elapsed = Math.max(0, Math.min(maxFrameElapsedMs, now - previousTime))
      previousTime = now
      // Frame-rate independent easing keeps high-frequency worker updates from
      // restarting a short tween and appearing to jump forward.
      const blend = 1 - Math.exp(-elapsed / interpolationTimeConstantMs)
      const delta = (target - progress.value) * blend
      const maxStep = elapsed * maxProgressPerMs
      progress.value += Math.sign(delta) * Math.min(Math.abs(delta), maxStep)
      if (Math.abs(target - progress.value) < 0.1) {
        progress.value = target
        frame = 0
        previousTime = 0
      } else {
        frame = requestAnimationFrame(tick)
      }
    }
    frame = requestAnimationFrame(tick)
  }

  onBeforeUnmount(() => cancelAnimationFrame(frame))
  return { progress, setProgress }
}
