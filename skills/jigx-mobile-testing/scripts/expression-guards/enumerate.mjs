#!/usr/bin/env node
// Enumerate the two expression families that throw on wrong-typed data:
//   1. $uppercase(x) — throws when x is a boolean or an object
//   2. JSONata >= <= > < — throws when a side is "", null, boolean, object or array
// SQL inside a datasource.sqlite `query:` block is not JSONata and is excluded.
// Usage: node enumerate.mjs [rootDir] > inventory.json
import { readdirSync, statSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = process.argv[2] || 'AcumaticaBase'
const EXT = /\.(jigx|yaml|yml)$/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walk(p, out)
    else if (EXT.test(name)) out.push(p)
  }
  return out
}

// Mark every line that sits inside a `query:` (or `sql:`) scalar block, plus
// single-line query values. Those are SQLite, not JSONata.
function sqlLineMask(lines) {
  const mask = new Array(lines.length).fill(false)
  let blockIndent = null
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (blockIndent !== null) {
      const indent = line.search(/\S/)
      if (line.trim() === '' || indent > blockIndent) { mask[i] = true; continue }
      blockIndent = null
    }
    const m = line.match(/^(\s*)(query|sql)\s*:\s*(.*)$/)
    if (m) {
      mask[i] = true
      if (/^[|>][-+0-9]*\s*$/.test(m[3].trim()) || m[3].trim() === '') blockIndent = m[1].length
    }
  }
  return mask
}

// Extract the argument of the call starting at `open` (index of the '(').
function argOf(text, open) {
  let depth = 0
  for (let i = open; i < text.length; i++) {
    if (text[i] === '(') depth++
    else if (text[i] === ')') { depth--; if (depth === 0) return text.slice(open + 1, i) }
  }
  return text.slice(open + 1)
}

const CMP = /(>=|<=|(?<![-=<>!])>(?!=)|(?<![-=<>!])<(?!=))/

// A line is JSONata if it is (or is inside) an expression: Jigx expressions are
// `key: =…` / `- =…`, and continuation lines of such a block.
function jsonataMask(lines) {
  const mask = new Array(lines.length).fill(false)
  let blockIndent = null
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (blockIndent !== null) {
      const indent = line.search(/\S/)
      if (line.trim() === '' || indent > blockIndent) { mask[i] = true; continue }
      blockIndent = null
    }
    const m = line.match(/^(\s*)(?:-\s+)?(?:[\w.$-]+\s*:\s*)?(.*)$/)
    const val = (m ? m[2] : line).trim()
    if (/^["']?=/.test(val)) {
      mask[i] = true
      if (/^["']?=?\s*[|>][-+0-9]*\s*$/.test(val)) blockIndent = m[1].length
    } else if (/^[|>][-+0-9]*\s*$/.test(val)) {
      // scalar block whose first content line may start with '='
      const next = lines[i + 1] || ''
      if (/^\s*["']?=/.test(next)) { blockIndent = m[1].length }
    } else if (/@ctx\.|\$\w+\(/.test(line)) {
      mask[i] = true // inline expression fragment (flow mapping, quoted string)
    }
  }
  return mask
}

const uppercase = []
const comparisons = []

for (const file of walk(ROOT)) {
  const rel = relative('.', file)
  const lines = readFileSync(file, 'utf8').split('\n')
  const sql = sqlLineMask(lines)
  const jsonata = jsonataMask(lines)

  lines.forEach((line, idx) => {
    const no = idx + 1

    let from = 0
    for (;;) {
      const at = line.indexOf('$uppercase(', from)
      if (at === -1) break
      uppercase.push({
        file: rel, line: no,
        operand: argOf(line, at + '$uppercase'.length).trim(),
        text: line.trim(),
        inSql: sql[idx],
      })
      from = at + 1
    }

    if (sql[idx] || !jsonata[idx]) return
    if (!CMP.test(line)) return
    // Drop YAML/HTML noise that survives the mask: tags, arrows, comment prose.
    const stripped = line.replace(/<[/!a-zA-Z][^>]*>/g, '')
    if (!CMP.test(stripped)) return
    const op = stripped.match(CMP)[1]
    comparisons.push({ file: rel, line: no, op, text: line.trim() })
  })
}

console.log(JSON.stringify({
  generatedAt: new Date().toISOString(),
  root: ROOT,
  uppercase: { total: uppercase.length, sites: uppercase },
  comparisons: { total: comparisons.length, sites: comparisons },
}, null, 2))
