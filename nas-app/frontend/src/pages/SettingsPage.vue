<script setup lang="ts">
/**
 * 设置页 /settings（照原型 ④）：配对状态卡 / 扫描区（进度条 + 子网列表 + 发现设备 →
 * discover-key → 配对）/ WOL 区（MAC 校验 + 唤醒）/ 危险区（强制清理、取消配对）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import Skeleton from '../components/Skeleton.vue'
import { api } from '../composables/useApi'
import { useConfig, loadConfig } from '../composables/useConfig'
import { forceStop } from '../composables/useJob'
import { showConfirm } from '../composables/useConfirm'
import { toast } from '../composables/useToast'
import { MAC_RE, normalizeMac } from '../utils'
import type { DeviceInfo, SubnetProgress } from '../types'

const { configState, paired } = useConfig()

/* ---- 配对状态卡 ---- */
const target = computed(() => configState.config?.default_target ?? null)
const hostText = computed(() => target.value ? (target.value.hostname || target.value.ip) : '尚未配对设备')
const subText = computed(() => {
  const c = configState.config
  if (target.value) {
    const ver = scanVersion.value
    return `${target.value.ip}:${target.value.port}${ver ? ' · BetterGI v' + ver : ''}`
  }
  return c ? '在下方"扫描设备"中发现监听器后即可配对' : ''
})

/* ---- 扫描区 ---- */
interface SubnetRow { subnet: string; status: string; found: number }

const scanning = ref(false)
const pairingIp = ref('') // 正在配对的设备 ip（按钮 spinner）
const scan = reactive({
  show: false,
  percent: 0,
  stat: '',
  subnets: [] as SubnetRow[],
  devices: [] as DeviceInfo[],
  errorMsg: '',
})
const scanVersion = ref('')

function subnetStatusText(s: SubnetRow): { text: string; cls: string } {
  switch (s.status) {
    case 'scanning': case 'skipping': return { text: '扫描中…', cls: 'doing' }
    case 'done': return s.found > 0 ? { text: `发现 ${s.found} 台`, cls: 'ok' } : { text: '无设备', cls: 'none' }
    case 'skipped': return { text: '跳过', cls: 'none' }
    default: return { text: '等待', cls: '' }
  }
}

async function startScan(): Promise<void> {
  if (scanning.value) return
  scanning.value = true
  scan.show = true
  scan.percent = 0
  scan.stat = ''
  scan.devices = []
  scan.errorMsg = ''
  scan.subnets = []
  try {
    const ack = await api.startScan()
    if (!ack.started) {
      scan.errorMsg = `扫描未启动：${ack.reason || '未知原因'}`
      return
    }
    // 轮询进度（~800ms，照旧版 GUI）
    for (;;) {
      await new Promise(r => setTimeout(r, 800))
      const snap = await api.scanProgress()
      scan.subnets = snap.subnets.map((s: SubnetProgress) => ({ subnet: s.subnet, status: String(s.status), found: s.found }))
      const totalIps = snap.total_ips || 0
      const scannedIps = snap.scanned_ips || 0
      scan.percent = totalIps > 0 ? Math.min(100, Math.round((scannedIps / totalIps) * 100)) : 0
      scan.stat = `已扫描 ${snap.scanned}/${snap.total} 个子网 · 约 ${scannedIps}/${totalIps} IP · 用时 ${snap.elapsed.toFixed(1)}s`
      scan.devices = snap.devices
      if (!snap.active) {
        if (snap.error) scan.errorMsg = `扫描出错：${snap.error}`
        else if (!snap.devices.length) toast('扫描完成，未发现监听器', 'info')
        else toast(`扫描完成，发现 ${snap.devices.length} 台设备`, 'success')
        break
      }
    }
  } catch (e) {
    scan.errorMsg = `扫描失败：${(e as Error).message}`
  } finally {
    scanning.value = false
  }
}

/** 选择设备 → 自动 discover-key → 配对。 */
async function pairDevice(dev: DeviceInfo): Promise<void> {
  if (pairingIp.value) return
  pairingIp.value = dev.ip
  try {
    // 关键：走同源后端 /api/discover-key 代理，不直连 http://Windows:port
    const key = await api.discoverKey(dev.ip, dev.port)
    await api.pair({
      ip: dev.ip,
      port: dev.port,
      hostname: key.hostname || dev.hostname || '',
      api_key: key.api_key,
    })
    scanVersion.value = dev.version || ''
    await loadConfig()
    toast(`已配对 ${key.hostname || dev.hostname || dev.ip}`, 'success')
  } catch (e) {
    toast(`配对失败：${(e as Error).message}`, 'error')
  } finally {
    pairingIp.value = ''
  }
}

/* ---- WOL 区 ---- */
const macInput = ref('')
const wolBusy = ref(false)

async function sendWol(): Promise<void> {
  const mac = macInput.value.trim()
  if (mac && !MAC_RE.test(mac)) {
    toast('MAC 地址格式不正确，示例：AA-BB-CC-DD-EE-FF', 'error')
    return
  }
  wolBusy.value = true
  try {
    const r = await api.wol(mac || undefined) // 空则用服务端配置
    toast(`已发送 Magic Packet → ${normalizeMac(r.mac)}`, 'success')
  } catch (e) {
    toast(`唤醒失败：${(e as Error).message}`, 'error')
  } finally {
    wolBusy.value = false
  }
}

/* ---- 危险区 ---- */
async function onDangerCleanup(): Promise<void> {
  const ok = await showConfirm(
    '强制清理',
    '将终止当前任务并重置监听器运行状态，可能造成任务中断。仅建议任务卡死时使用。',
    '强制清理',
  )
  if (!ok) return
  try {
    const { killed } = await forceStop()
    toast(killed.length ? `已强制清理：${killed.join('、')}` : '已发送强制清理指令', 'success')
  } catch (e) {
    toast(`强制清理失败：${(e as Error).message}`, 'error')
  }
}

const unpairing = ref(false)
async function onDangerUnpair(): Promise<void> {
  const host = hostText.value
  const ok = await showConfirm(
    '取消配对',
    `将断开与 ${host}（${target.value?.ip ?? '?'}）的配对，触发任务前需重新扫描配对。`,
    '取消配对',
  )
  if (!ok) return
  unpairing.value = true
  try {
    await api.unpair()
    await loadConfig()
    toast('已取消配对', 'info')
  } catch (e) {
    toast(`取消配对失败：${(e as Error).message}`, 'error')
  } finally {
    unpairing.value = false
  }
}

onMounted(async () => {
  if (!configState.config) await loadConfig()
  // 默认 MAC：服务端配置优先
  if (configState.config?.target_mac) macInput.value = configState.config.target_mac
})
</script>

<template>
  <div>
    <template v-if="configState.loading && !configState.config">
      <Skeleton height="84px" />
      <Skeleton height="180px" />
      <Skeleton height="110px" />
    </template>
    <template v-else>
      <!-- 配对状态卡 -->
      <div class="card">
        <div class="pair-row">
          <span class="badge" :class="paired ? 'b-done' : 'b-idle'">{{ paired ? '✅ 已配对' : '⚪ 未配对' }}</span>
          <div class="pair-info">
            <div class="pair-host">{{ hostText }}</div>
            <div class="pair-sub">{{ subText }}</div>
          </div>
        </div>
      </div>

      <!-- 扫描区 -->
      <div class="sec-title">扫描设备</div>
      <div class="card">
        <button class="btn btn-primary btn-block" :disabled="scanning" @click="startScan">
          <span v-if="scanning" class="spinner"></span>{{ scanning ? '扫描中…' : '🔍 开始扫描' }}
        </button>
        <template v-if="scan.show">
          <div class="scan-progress">
            <div class="scan-progress-bar" :style="{ width: scan.percent + '%' }"></div>
          </div>
          <div class="scan-stat">{{ scan.stat }}</div>
          <div
            v-for="s in scan.subnets"
            :key="s.subnet"
            class="subnet-row"
          >
            <code>{{ s.subnet }}</code>
            <span class="st" :class="subnetStatusText(s).cls">{{ subnetStatusText(s).text }}</span>
          </div>
          <div v-if="scan.errorMsg" class="scan-error">{{ scan.errorMsg }}</div>
          <div v-for="d in scan.devices" :key="d.ip" class="card dev-card">
            <div class="pair-row">
              <span class="badge b-running">📡 发现设备</span>
              <div class="pair-info">
                <div class="pair-host">{{ d.hostname || '(未知主机)' }}</div>
                <div class="pair-sub">{{ d.ip }}:{{ d.port }}{{ d.version ? ' · v' + d.version : '' }}</div>
              </div>
              <button class="btn btn-primary btn-pair" :disabled="!!pairingIp" @click="pairDevice(d)">
                <span v-if="pairingIp === d.ip" class="spinner"></span>配对
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- WOL 区 -->
      <div class="sec-title">WOL 网络唤醒</div>
      <div class="card">
        <div class="field-row">
          <input
            v-model="macInput"
            type="text"
            placeholder="AA-BB-CC-DD-EE-FF"
            autocomplete="off"
            spellcheck="false"
          >
          <button class="btn btn-primary" :disabled="wolBusy" @click="sendWol">
            <span v-if="wolBusy" class="spinner"></span>唤醒
          </button>
        </div>
        <div class="hint">向 Windows 主机网卡发送 Magic Packet，用于任务执行前远程开机。留空使用服务端配置的 MAC。</div>
      </div>

      <!-- 危险区 -->
      <div class="sec-title">危险区</div>
      <div class="card danger-card">
        <button class="danger-item" @click="onDangerCleanup">
          <span>🧹 强制清理<small>终止当前任务并重置监听器运行状态</small></span>
          <span>›</span>
        </button>
        <button class="danger-item" :disabled="unpairing" @click="onDangerUnpair">
          <span>🔌 取消配对<small>断开与当前 Windows 主机的配对</small></span>
          <span>›</span>
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pair-row { display: flex; align-items: center; gap: var(--space-3); }
.pair-info { flex: 1; min-width: 0; }
.pair-host { font-size: var(--font-md); font-weight: 700; }
.pair-sub { font-size: var(--font-xs); color: var(--text-2); margin-top: 2px; font-family: var(--font-mono); }

.scan-progress {
  height: 8px; border-radius: var(--radius-full);
  background: var(--state-idle-weak); overflow: hidden;
  margin: var(--space-3) 0 6px;
}
.scan-progress-bar {
  height: 100%; width: 0%;
  background: var(--brand); border-radius: var(--radius-full);
  transition: width .3s ease;
}
.scan-stat { font-size: var(--font-xs); color: var(--text-3); margin-bottom: var(--space-2); }
.subnet-row {
  display: flex; align-items: center; gap: var(--space-2);
  padding: 8px 10px; margin-bottom: 6px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface-2); font-size: var(--font-sm);
}
.subnet-row code { font-family: var(--font-mono); font-size: var(--font-xs); flex: 1; }
.subnet-row .st { font-size: var(--font-xs); color: var(--text-3); }
.subnet-row .st.ok { color: var(--success); font-weight: 600; }
.subnet-row .st.none { color: var(--text-3); }
.subnet-row .st.doing { color: var(--brand); }
.scan-error { font-size: var(--font-sm); color: var(--danger); margin-top: var(--space-2); }
.dev-card { margin-top: var(--space-3); margin-bottom: 0; box-shadow: none; border: 1px solid var(--border); }
.btn-pair { padding: 0 14px; font-size: var(--font-sm); flex-shrink: 0; }

.field-row { display: flex; gap: var(--space-2); }
.field-row input {
  flex: 1; min-width: 0; min-height: 44px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  padding: 0 12px; font-size: var(--font-base);
  font-family: var(--font-mono);
  background: var(--surface); color: var(--text-1);
  outline: none;
}
.field-row input:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-weak); }
.hint { font-size: var(--font-xs); color: var(--text-3); margin-top: 6px; line-height: 1.5; }

/* 危险区（红描边按钮列表） */
.danger-card { border: 1px solid var(--danger-weak); padding: 0; }
.danger-item {
  width: 100%; min-height: 48px;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--surface); border: none;
  border-top: 1px solid var(--danger-weak);
  padding: var(--space-2) var(--space-3); font-size: var(--font-base); font-weight: 500;
  color: var(--danger); text-align: left;
}
.danger-item:first-of-type { border-top: none; border-radius: var(--radius-lg) var(--radius-lg) 0 0; }
.danger-item:last-of-type { border-radius: 0 0 var(--radius-lg) var(--radius-lg); }
.danger-item small { display: block; font-size: var(--font-xs); color: var(--text-3); font-weight: 400; margin-top: 2px; }
</style>
