// 跨平台部署脚本：把 dist/* 复制到 NAS 后端静态目录 static/spa/
// 用法：npm run build:deploy（等价于 npm run build && node scripts/deploy.mjs）
//
// 实现说明：
// - Windows：优先用系统自带 robocopy（对非 ASCII 路径，如中文用户目录，比 cpSync 更稳；
//   robocopy 退出码 0-7 均视为成功）。
// - 非 Windows（或 robocopy 不可用）：回退 node:fs.cpSync，保证跨平台。
import { execFileSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'

const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const dist = resolve(frontendRoot, 'dist')
const dest = resolve(frontendRoot, '../app/docker/app/static/spa')

function isWindows() {
  return process.platform === 'win32'
}

/**
 * Windows 下用 robocopy 复制。
 * 返回 true 表示复制已成功执行；false 表示应回退 cpSync。
 */
function tryRobocopy(src, dst) {
  if (!isWindows()) return false
  const args = [src, dst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP']
  try {
    execFileSync('robocopy', args, { stdio: 'pipe' })
    return true // 退出码 0（无文件需复制）
  } catch (e) {
    // robocopy 以退出码表达结果：1-7 为成功（1=有文件被复制）
    const status = e && typeof e.status === 'number' ? e.status : -1
    return status >= 1 && status <= 7
  }
}

function report() {
  const files = readdirSync(dest, { recursive: true })
  console.log(`[deploy] 已复制 ${files.length} 个条目: ${dist} -> ${dest}`)
  for (const f of files) console.log('  -', f)
  console.log('[deploy] 产物目录:', dest)
}

function main() {
  if (!existsSync(dist)) {
    console.error('[deploy] dist/ 不存在，请先执行 npm run build')
    process.exit(1)
  }

  rmSync(dest, { recursive: true, force: true })
  mkdirSync(dest, { recursive: true })

  if (tryRobocopy(dist, dest)) {
    console.log('[deploy] robocopy 复制完成（Windows 非 ASCII 路径兼容模式）')
  } else {
    cpSync(dist, dest, { recursive: true })
    console.log('[deploy] cpSync 复制完成')
  }
  report()
}

main()
