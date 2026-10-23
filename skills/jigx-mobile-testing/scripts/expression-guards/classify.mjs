#!/usr/bin/env node
// Classify each enumerated comparison site by the TYPE its operands can take on device.
// Feeds on enumerate.mjs output: node enumerate.mjs > inv.json && node classify.mjs inv.json
import { readFileSync } from 'node:fs'

const inv = JSON.parse(readFileSync(process.argv[2] || 'inv.json', 'utf8'))

// Walk back/forward from the operator to grab a balanced operand token.
function operands(text, opIdx, opLen) {
  const back = (i) => {
    let depth = 0, end = i
    while (end >= 0 && /\s/.test(text[end])) end--
    let j = end
    for (; j >= 0; j--) {
      const ch = text[j]
      if (ch === ')' || ch === ']') depth++
      else if (ch === '(' || ch === '[') { if (depth === 0) break; depth-- }
      else if (depth === 0 && /[\s,?:]/.test(ch)) {
        if (ch === '(' ) break
        // allow spaces inside a path only if the next char back is a path char
        if (/[\s,?]/.test(ch)) break
        if (ch === ':') break
      }
    }
    return text.slice(j + 1, end + 1).trim()
  }
  const fwd = (i) => {
    let depth = 0, start = i
    while (start < text.length && /\s/.test(text[start])) start++
    let j = start
    for (; j < text.length; j++) {
      const ch = text[j]
      if (ch === '(' || ch === '[') depth++
      else if (ch === ')' || ch === ']') { if (depth === 0) break; depth-- }
      else if (depth === 0 && /[\s,?]/.test(ch)) break
    }
    return text.slice(start, j).trim()
  }
  return { lhs: back(opIdx - 1), rhs: fwd(opIdx + opLen) }
}

const NUMERIC_LITERAL = /^-?\d+(\.\d+)?$/
// Functions whose return type is always a number -> operand can never throw.
const ALWAYS_NUMBER = /^\$(count|length|sum|abs|round|floor|ceil|power|sqrt|millis|toMillis)\(/
const STRING_LITERAL = /^(['"]).*\1$/

function classifyOperand(op) {
  // Strip the Jigx expression marker and any wrapping quote before typing the operand.
  op = op.replace(/^["']?=\s*/, '').replace(/^\(+/, '').trim()
  if (op === '') return 'unparsed'
  if (NUMERIC_LITERAL.test(op)) return 'number-literal'
  if (STRING_LITERAL.test(op)) return 'string-literal'
  if (ALWAYS_NUMBER.test(op)) return 'always-number'
  if (/^\$number\(/.test(op)) return 'number-cast'      // safe only if arg is never null/{}/""
  if (/state\.value/.test(op)) return 'component-state' // "" or null until typed -> THROWS
  if (/\.state\./.test(op)) return 'component-state-other'
  if (/@ctx\./.test(op)) return 'ctx-path'
  if (/^\$[\w.]+\(/.test(op)) return 'other-function'
  return 'unknown'
}

// Resolve `$var := <expr>` bindings so a comparison against a local variable is
// classified by the variable's SOURCE, not treated as unknown.
import { readFileSync as rf } from 'node:fs'
const bindingCache = new Map()
function bindingsFor(file) {
  if (!bindingCache.has(file)) {
    const map = new Map()
    for (const line of rf(file, 'utf8').split('\n')) {
      const m = line.match(/(\$[A-Za-z]\w*)\s*:=\s*(.+?);?\s*$/)
      if (m && !map.has(m[1])) map.set(m[1], m[2].trim())
    }
    bindingCache.set(file, map)
  }
  return bindingCache.get(file)
}

const rows = []
for (const s of inv.comparisons.sites) {
  const re = /(>=|<=|(?<![-=<>!])>(?!=)|(?<![-=<>!])<(?!=))/g
  let m
  while ((m = re.exec(s.text))) {
    const { lhs, rhs } = operands(s.text, m.index, m[1].length)
    const binds = bindingsFor(s.file)
    const resolve = (op) => {
      const bare = op.replace(/^["']?=\s*/, '').trim()
      return /^\$[A-Za-z]\w*$/.test(bare) && binds.has(bare) ? binds.get(bare) : op
    }
    const lhsR = resolve(lhs), rhsR = resolve(rhs)
    const lt = classifyOperand(lhsR), rt = classifyOperand(rhsR)
    const safe = ['number-literal', 'string-literal', 'always-number']
    const bare = s.text.replace(/^\s*/, '')
    // An operand already guarded upstream on the same line by the house idiom.
    const guardedOperand = (op) => {
      const path = op.replace(/^["']?=\s*/, '').trim()
      if (!path) return false
      const esc = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return new RegExp("\\$type\\(\\s*" + esc + "\\s*\\)\\s*=\\s*['\"]number['\"]").test(s.text) ||
             /\$type\([^)]*\)\s*=\s*['"]number['"]/.test(s.text) && /!=\s*null\s*and/.test(s.text) === false && new RegExp(esc.slice(0, 40)).test(s.text) && /\$type\(/.test(s.text)
    }
    let verdict
    if (/^#/.test(bare)) verdict = 'LEAVE-commented-out'
    else if (/\b(SELECT|FROM|WHERE|CASE WHEN|COALESCE|json_extract|GROUP BY|ORDER BY)\b/.test(s.text) && /CAST|json_extract|COALESCE/.test(s.text)) verdict = 'LEAVE-sql-not-jsonata'
    else if (guardedOperand(lhsR) || guardedOperand(rhsR)) verdict = 'LEAVE-already-type-guarded'
    else if (safe.includes(lt) && safe.includes(rt)) verdict = 'LEAVE-safe-by-construction'
    else if (lt === 'component-state' || rt === 'component-state') verdict = 'FIX-component-state'
    else if (lt === 'number-cast' || rt === 'number-cast') verdict = 'REVIEW-number-cast'
    else verdict = 'REVIEW'
    rows.push({ ...s, op: m[1], lhs, rhs, lt, rt, verdict })
  }
}

const counts = {}
for (const r of rows) counts[r.verdict] = (counts[r.verdict] || 0) + 1
if (process.argv.includes('--json')) console.log(JSON.stringify(rows, null, 2))
else {
  console.log('TOTAL comparison operators:', rows.length)
  for (const [k, v] of Object.entries(counts).sort((a,b)=>b[1]-a[1])) console.log('  ' + k.padEnd(28), v)
  for (const want of ['FIX-component-state', 'REVIEW-number-cast', 'REVIEW']) {
    console.log('\n### ' + want)
    for (const r of rows.filter(r => r.verdict === want))
      console.log(`${r.file.replace('AcumaticaBase/','')}:${r.line}  [${r.lhs}] ${r.op} [${r.rhs}]   (${r.lt}/${r.rt})`)
  }
}
