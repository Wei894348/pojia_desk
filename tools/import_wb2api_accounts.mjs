#!/usr/bin/env node
/**
 * 批量导入 workbuddy 凭证 —— 与面板 /import 走同一条归一化/落盘路径。
 *
 * 用法：
 *   node import_wb2api_accounts.mjs <accounts.json>             # 写入
 *   node import_wb2api_accounts.mjs <accounts.json> --dry-run   # 只看结果，不落盘
 *
 * 直接 import 插件 lib 的 normalizeDocument + writeAuthDocument，落盘结果与
 * 面板逐字节一致；不依赖 dsh 重启 —— 网关对 auths/ 是 5 秒热加载，跑完即生效。
 * 支持 account+auth 两段式与扁平 snake_case / camelCase（时间戳毫秒秒都认）。
 */
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const pluginLib = join(homedir(), '.dsh', 'plugins', 'dsh-plugin-wb2api-ui', 'lib', 'index.js')
const { normalizeDocument, writeAuthDocument } = await import(pathToFileURL(pluginLib).href)

const [file, ...flags] = process.argv.slice(2)
const dryRun = flags.includes('--dry-run')
if (file === undefined) {
  console.error('用法：node import_wb2api_accounts.mjs <accounts.json> [--dry-run]')
  process.exit(1)
}

let parsed
try {
  parsed = JSON.parse(readFileSync(file, 'utf8'))
} catch (error) {
  console.error(`读取/解析 ${file} 失败：${error.message}`)
  process.exit(1)
}

const items = Array.isArray(parsed) ? parsed : [parsed]
let ok = 0
for (const item of items) {
  try {
    const document = normalizeDocument(item)
    if (dryRun) {
      console.log(`[dry-run] 将写入 workbuddy-${document.account.uid}.json（${document.auth.realm}，${document.account.nickname || '无昵称'}，到期 ${document.auth.expiresAt || '未知'}）`)
    } else {
      const written = writeAuthDocument(document)
      console.log(`已写入 ${written}`)
    }
    ok += 1
  } catch (error) {
    console.error(`跳过一个凭证：${error.message}`)
  }
}
console.log(`共 ${items.length} 项，可导入 ${ok}${dryRun ? '（dry-run，未落盘）' : ''}`)
process.exitCode = ok === items.length ? 0 : 1
