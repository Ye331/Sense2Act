import { copyFileSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
export const DEFAULT_RUNS_DIR = resolve(SCRIPT_DIR, '../public/runs')

function readRun(filePath) {
  let payload
  try {
    payload = JSON.parse(readFileSync(filePath, 'utf8'))
  } catch (err) {
    throw new Error(`${filePath}:无法解析 JSON:${String(err)}`)
  }
  const eventId = payload?.event?.event_id
  const kind = payload?.event?.kind
  if (typeof eventId !== 'string' || !eventId || typeof kind !== 'string' ||
      !Array.isArray(payload?.nodes) || !Array.isArray(payload?.edges)) {
    throw new Error(`${filePath}:缺少 event.event_id/event.kind/nodes/edges，不能写入出件清单`)
  }
  return { eventId, kind, nNodes: payload.nodes.length, nEdges: payload.edges.length }
}

export function syncChainRuns(inputPaths, runsDir = DEFAULT_RUNS_DIR) {
  mkdirSync(runsDir, { recursive: true })
  for (const input of inputPaths) {
    const source = resolve(input)
    const file = basename(source)
    if (!file.endsWith('.web.json')) throw new Error(`${source}:文件名必须以 .web.json 结尾`)
    copyFileSync(source, resolve(runsDir, file))
  }

  const runs = readdirSync(runsDir)
    .filter(file => file.endsWith('.web.json'))
    .map(file => {
      const filePath = resolve(runsDir, file)
      return { file, ...readRun(filePath), generatedAt: statSync(filePath).mtime.toISOString() }
    })
    .sort((a, b) => b.generatedAt.localeCompare(a.generatedAt) || a.eventId.localeCompare(b.eventId) || a.file.localeCompare(b.file))

  writeFileSync(resolve(runsDir, 'index.json'), `${JSON.stringify({ runs }, null, 2)}\n`, 'utf8')
  return { runs }
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : ''
if (import.meta.url === invokedPath) {
  const inputs = process.argv.slice(2)
  if (inputs.length === 0) {
    console.error('用法:node scripts/sync-chain-runs.mjs <a.web.json> [b.web.json …]')
    process.exitCode = 2
  } else {
    try {
      const index = syncChainRuns(inputs)
      console.log(`已同步 ${inputs.length} 个文件；清单共 ${index.runs.length} 个出件 → ${DEFAULT_RUNS_DIR}`)
    } catch (err) {
      console.error(err instanceof Error ? err.message : String(err))
      process.exitCode = 1
    }
  }
}
