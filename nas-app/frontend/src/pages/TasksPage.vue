<script setup lang="ts">
/**
 * 任务页 /tasks（照原型 ②）：TaskCard 列表；触发成功跳状态页（轮询+WS 已在 useJob 启动）。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import TaskCard from '../components/TaskCard.vue'
import Skeleton from '../components/Skeleton.vue'
import { api } from '../composables/useApi'
import type { BgiTask } from '../types'

const router = useRouter()
const tasks = ref<BgiTask[]>([])
const loading = ref(true)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    tasks.value = await api.getTasks()
  } catch (e) {
    error.value = `加载任务失败：${(e as Error).message}`
  } finally {
    // 骨架屏至少停留 300ms（照原型节奏）
    setTimeout(() => { loading.value = false }, 300)
  }
}

function onTriggered(): void {
  void router.push('/')
}

onMounted(load)
</script>

<template>
  <div>
    <template v-if="loading">
      <Skeleton height="120px" />
      <Skeleton height="120px" />
      <Skeleton height="120px" />
    </template>
    <template v-else>
      <div v-if="error" class="card"><div class="empty-hint">{{ error }}<br>请确认已配对设备后重试</div></div>
      <div v-else-if="!tasks.length" class="card">
        <div class="empty-hint">暂无任务<br>请先在「设置」页扫描并配对 Windows 主机</div>
      </div>
      <template v-else>
        <TaskCard v-for="t in tasks" :key="t.id" :task="t" @triggered="onTriggered" />
      </template>
    </template>
  </div>
</template>
