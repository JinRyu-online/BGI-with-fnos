<script setup lang="ts">
/** 徽章：7 态 + idle/triggering 兜底（unknown 灰）。 */
import { computed } from 'vue'
import { JOB_STATE_MAP } from '../constants'

const props = defineProps<{ state: string }>()

const meta = computed(() => JOB_STATE_MAP[props.state as keyof typeof JOB_STATE_MAP] ?? JOB_STATE_MAP.idle)
</script>

<template>
  <span class="badge" :class="meta.badgeClass">{{ meta.zh }}</span>
</template>
