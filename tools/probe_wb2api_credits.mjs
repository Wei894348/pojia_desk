#!/usr/bin/env node
/**
 * 直接打上游查真实剩余积分 —— 面板 /credits 的 CLI 版，绕开「改了 host 半侧要重启」。
 *
 * 用法：
 *   node probe_wb2api_credits.mjs          # 每账号汇总
 *   node probe_wb2api_credits.mjs --raw    # 附带包明细（segments，与面板进度条同源）
 *
 * 复用插件 lib 的 collectCredits（含 60 秒缓存语义，--force 绕过），**只读查询，
 * 不消耗额度**；会真实请求上游计费面（与面板刷新一次等价），禁用账号也查。
 */
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const pluginLib = join(homedir(), '.dsh', 'plugins', 'dsh-plugin-wb2api-ui', 'lib', 'index.js')
const { collectCredits } = await import(pathToFileURL(pluginLib).href)

const raw = process.argv.slice(2).includes('--raw')
const credits = await collectCredits({ force: true })

console.log(`合计 ${credits.total} ｜ 今日已用 ${credits.todayUsed} ｜ 不限量 ${credits.unlimited} 个`)
console.log(`账号 ${credits.counts.total}：ok ${credits.counts.ok} / error ${credits.counts.error} / unreadable ${credits.counts.unreadable} / 禁用 ${credits.counts.disabled}`)
for (const row of credits.accounts) {
  const head = `${row.nickname || row.uid} [${row.state}${row.disabled ? ' · 未参与轮换' : ''}]`
  if (row.state !== 'ok') {
    console.log(`  ${head}${row.error ? ` —— ${row.error}` : ''}`)
    continue
  }
  console.log(`  ${head}：剩余 ${row.total}${row.unlimited ? '（不限量）' : ''}${row.todayUsage ? ` ｜ 今日已用 ${row.todayUsage.used}（${row.todayUsage.count} 笔）` : ''}`)
  if (raw) {
    for (const segment of row.segments ?? []) {
      const expire = segment.expiresAt ? `到期 ${new Date(segment.expiresAt).toLocaleString('zh-CN')}` : '无到期'
      console.log(`    - ${segment.source}：${segment.remaining} / ${segment.total}（${expire}）`)
    }
    const gap = Number(row.sumCapacityRemain || 0) - (Number(row.total) || 0)
    if (gap > 0) console.log(`    （另有 ${gap} 本周期已用尽，总量口径会算进去，可用口径不计）`)
  }
}
