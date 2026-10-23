#!/usr/bin/env node
// Regression proof for the expression type-guard pass.
// For every shape a device can produce, compare the OLD expression against the NEW one.
// The gate: NEW must never throw, and must return the same effective truthiness as OLD
// wherever OLD did not throw (a throw blanks the expression, i.e. reads as falsy).
import { execSync } from 'node:child_process'
import { join } from 'node:path'
// The jsonata the Jigx SDK pins; JIGX_NODE_MODULES overrides the global @jigx/core-sdk install.
const NM = process.env.JIGX_NODE_MODULES || join(execSync('npm root -g').toString().trim(), '@jigx/core-sdk/node_modules')
const { default: jsonata } = await import(join(NM, 'jsonata/jsonata.js'))

const SHAPES = {
  'undefined (never touched)': undefined,
  'null': null,
  '"" (typed then cleared)': '',
  'number 3 (below gate)': 3,
  'number 7 (on gate)': 7,
  'number 10 (above gate)': 10,
  '"7" (numeric string)': '7',
  'true (boolean)': true,
  '{} (Acumatica empty)': {},
  '[3,4] (two rows)': [3, 4],
}

const CASES = [
  { name: 'age gate  when: flockAge >= 7',
    old: 'x >= 7',
    new: "$type(x) = 'number' and x >= 7" },
  { name: 'isFormValid  flockAge < 7 clause',
    old: 'x < 7',
    new: "$type(x) = 'number' and x < 7" },
  { name: 'errorText  Quantity < 1',
    old: "x < 1 ? 'err' : ''",
    new: "$type(x) = 'number' and x < 1 ? 'err' : ''" },
  { name: 'errorText  PaymentAmount > due(100)',
    old: 'x > 100',
    new: "$type(x) = 'number' and x > 100" },
  { name: 'checkbox  BillingAddressOverride',
    old: 'juthy(x) ? $uppercase(x) : false',
    new: 'juthy(x)' },
]

const juthy = (i) => !(typeof i === 'undefined' || i === false || i === '' ||
  (typeof i === 'object' && i !== null && Object.keys(i).length === 0) || i === null ||
  (Array.isArray(i) && i.length === 0) || i === 0)

async function run(expr, v) {
  const e = jsonata(expr.replace(/\bjuthy\(/g, '$juthy('))
  e.registerFunction('juthy', juthy)
  try { return { ok: true, v: await e.evaluate(v === undefined ? {} : { x: v }) } }
  catch { return { ok: false } }
}
const truthy = (r) => r.ok && r.v !== undefined && r.v !== false && r.v !== '' && r.v !== null

let fail = 0
for (const c of CASES) {
  console.log('\n' + c.name)
  console.log('  old: ' + c.old + '\n  new: ' + c.new)
  for (const [label, v] of Object.entries(SHAPES)) {
    const o = await run(c.old, v), n = await run(c.new, v)
    const fmt = (x) => x === undefined ? 'undef' : JSON.stringify(x)
    const oS = o.ok ? fmt(o.v) : 'THROW'
    const nS = n.ok ? fmt(n.v) : 'THROW'
    let verdict
    if (!n.ok) { verdict = 'FAIL new throws'; fail++ }
    else if (!o.ok) verdict = truthy(n) ? 'CHANGED (was throw->falsy, now shows)' : 'fixed (throw -> same falsy result)'
    else verdict = truthy(o) === truthy(n) ? 'same' : 'CHANGED'
    if (verdict.startsWith('CHANGED')) fail++
    console.log('    ' + label.padEnd(26) + 'old=' + oS.padEnd(8) + ' new=' + nS.padEnd(8) + ' ' + verdict)
  }
}
console.log('\n' + (fail ? fail + ' shape(s) need a human decision — see CHANGED/FAIL above' : 'ALL SHAPES: no throw, no behaviour change'))
