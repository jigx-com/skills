#!/usr/bin/env node
// Ground truth for which expression shapes throw, run against the jsonata the SDK
// pins (1.8.9). evaluate() returns a Promise — awaiting it is what makes a throw visible.
import { execSync } from 'node:child_process'
import { join } from 'node:path'
// The jsonata the Jigx SDK pins; JIGX_NODE_MODULES overrides the global @jigx/core-sdk install.
const NM = process.env.JIGX_NODE_MODULES || join(execSync('npm root -g').toString().trim(), '@jigx/core-sdk/node_modules')
const { default: jsonata } = await import(join(NM, 'jsonata/jsonata.js'))

const VALUES = {
  undefined: undefined, null: null, 'empty string': '', 'string "abc"': 'abc',
  'string "7"': '7', 'number 7': 7, 'number 0': 0, 'boolean true': true,
  'boolean false': false, 'empty object {}': {}, 'object {value:1}': { value: 1 },
  'array [3,4]': [3, 4], 'array ["a"]': ['a'],
}

const EXPRS = [
  '$uppercase(x)',
  '$uppercase($string(x))',
  '$uppercase($trim(x))',
  'x >= 7',
  '$number(x) >= 7',
]

async function run(expr, data) {
  try { return { ok: true, value: await jsonata(expr).evaluate(data) } }
  catch (e) { return { ok: false, error: (e.message || String(e)).slice(0, 60) } }
}

const pad = (s, n) => String(s).padEnd(n)
for (const expr of EXPRS) {
  console.log('\n' + expr)
  for (const [label, v] of Object.entries(VALUES)) {
    const r = await run(expr, v === undefined ? {} : { x: v })
    console.log('  ' + pad(label, 18) + (r.ok ? 'OK   ' + JSON.stringify(r.value) : 'THROW ' + r.error))
  }
}
