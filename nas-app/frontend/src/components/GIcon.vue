<script setup lang="ts">
/**
 * 内联 SVG 线条图标（原神风格细线 + 圆角端点），替代 emoji。
 * name 取自下方 paths 表；尺寸默认 1em，随字号缩放。
 */
const paths: Record<string, string> = {
  // 扫帚（强制清理）
  broom: 'M4 27c0-5 3-9 8-9M12 18 22 4l4 3-9 13M6 27c1-4 3-6 6-7l3 2c-1 3-3 5-6 6z',
  // 放大镜（扫描）
  search: 'M12.5 21a8.5 8.5 0 1 1 0-17 8.5 8.5 0 0 1 0 17zM26 26l-7.8-7.8',
  // 广播/信号（发现设备）
  signal: 'M16 24v-3M10 15a8.5 8.5 0 0 1 12 0M6 10.5a14 14 0 0 1 20 0M16 27.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
  // 停止（中止）
  stop: 'M10 10h12v12H10z',
  // 沙漏（时长）
  hourglass: 'M9 5h14v4c0 3-3 5-5 7 2 2 5 4 5 7v4H9v-4c0-3 3-5 5-7-2-2-5-4-5-7zM11 26h10',
  // 月亮（休眠）
  moon: 'M24 18.5A9.5 9.5 0 0 1 13.5 8 9.5 9.5 0 1 0 24 18.5z',
  // 电源（关机）
  power: 'M16 6v10M9.5 9a10 10 0 1 0 13 0',
  // 锁（锁屏）
  lock: 'M11 14v-3a5 5 0 0 1 10 0v3M9 14h14v13H9zM16 19v4',
  // 暂停（不操作）
  pause: 'M12 9v14M20 9v14',
  // 对勾（已配对）
  check: 'M8 17l6 6L26 10',
  // 圆点（未配对）
  dot: 'M16 22a6 6 0 1 0 0-12 6 6 0 0 0 0 12z',
  // 播放（执行）
  play: 'M11 7l16 9-16 9z',
  // 刷新
  refresh: 'M25 12a10 10 0 1 0 1 8M26 6v7h-7',
  // 插头（取消配对）
  plug: 'M12 5v6M20 5v6M9 11h14v4a7 7 0 0 1-14 0zM16 22v6',
}

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 16 })
</script>

<template>
  <svg
    class="g-icon"
    :width="props.size"
    :height="props.size"
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
  >
    <path
      :d="paths[props.name] ?? ''"
      stroke="currentColor"
      stroke-width="2.4"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

<style scoped>
.g-icon { display: inline-block; vertical-align: -0.15em; flex-shrink: 0; }
</style>
