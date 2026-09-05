<script setup lang="ts">
/**
 * 触发按钮三态（照原型 §4.4）：
 * - 默认：btn-primary "执行"
 * - loading：本卡触发请求进行中，spinner + "触发中"
 * - 运行中禁用：btn-secondary "运行中，不可触发"（全局单活互斥）
 */
import { computed, ref } from 'vue'
import { hasActiveJob, jobState, triggerTask } from '../composables/useJob'
import { toast } from '../composables/useToast'
import GIcon from './GIcon.vue'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ triggered: [] }>()

const submitting = ref(false)

/** 本卡是否正在触发（loading 态只出现在被点的那张卡）。 */
const isSelfTriggering = computed(() =>
  submitting.value || (jobState.triggering && jobState.triggeringTaskId === props.taskId),
)

const mode = computed<'idle' | 'loading' | 'busy'>(() => {
  if (isSelfTriggering.value) return 'loading'
  if (hasActiveJob.value) return 'busy'
  return 'idle'
})

async function onClick(): Promise<void> {
  if (mode.value !== 'idle') return
  submitting.value = true
  try {
    await triggerTask(props.taskId)
    emit('triggered')
  } catch (e) {
    const err = e as Error & { status?: number }
    const msg = err.status === 409
      ? '已有任务在运行，请先等待完成或中止'
      : err.status === 404
        ? '尚未配对设备，或任务不存在'
        : `触发失败：${err.message}`
    toast(msg, 'error')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <button
    v-if="mode === 'idle'"
    class="btn btn-primary tc-run"
    @click="onClick"
  ><GIcon name="play" :size="14" /> 执行</button>
  <button v-else-if="mode === 'loading'" class="btn btn-primary tc-run" disabled>
    <span class="spinner"></span>触发中
  </button>
  <button v-else class="btn btn-secondary tc-run" disabled>运行中，不可触发</button>
</template>

<style scoped>
.tc-run { min-width: 112px; flex-shrink: 0; }
.tc-run.btn-secondary { font-size: var(--font-sm); }
</style>
