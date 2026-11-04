import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url))
const patterns = [
  /\bv-html\b/i,
  /\.innerHTML\s*=/,
  /\.outerHTML\s*=/,
  /\binsertAdjacentHTML\b/,
  /\bdocument\.write\b/,
]

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? collectFiles(path) : [path]
  }))
  return files.flat()
}

const violations = []
for (const file of await collectFiles(sourceRoot)) {
  if (!/\.(vue|ts|js)$/.test(file)) continue
  const content = await readFile(file, 'utf8')
  if (patterns.some((pattern) => pattern.test(content))) violations.push(file)
}

if (violations.length) {
  console.error(`Unsafe HTML rendering API found:\n${violations.join('\n')}`)
  process.exit(1)
}

console.log('No unsafe HTML rendering APIs found in frontend source.')
