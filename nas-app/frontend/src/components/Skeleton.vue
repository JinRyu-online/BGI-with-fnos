<script setup lang="ts">
/** 骨架屏块（shimmer 微光），height/width 由父组件给。 */
defineProps<{ height: string; width?: string }>()
</script>

<template>
  <div class="sk-block" :style="{ height, width: width || '100%' }"></div>
</template>
