#!/usr/bin/env node
// Drafts a probe manifest from what a Jigx solution actually calls, so the
// probe checks the solution's own reads rather than a guessed list.
//
//   node manifest-from-solution.mjs <build/output.json | solution folder> [--base <url>] [--name <name>]
//
// Core SDK: pass build/output.json. Standard solution: pass the folder; every
// .jigx file with a `url:` under it is read. URLs are matched on the usual
// placeholders: {…acumatica…}<Entity> (contract REST) and {…odata…}<Inquiry>
// (OData), in any casing. Anything else is listed under
// "unmatched" for a person to place. Prints the manifest to stdout.
import fs from 'node:fs'
import path from 'node:path'

const [src, ...rest] = process.argv.slice(2)
const args = Object.fromEntries(rest.reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []))
if (!src) {
  console.error('usage: node manifest-from-solution.mjs <build/output.json | solution folder> [--base <url>] [--name <name>]')
  process.exit(1)
}

const calls = [] // { id, method, url }
let corpus = ''
if (fs.statSync(src).isFile()) {
  const out = JSON.parse(fs.readFileSync(src, 'utf8'))
  corpus = JSON.stringify(out)
  for (const [id, f] of Object.entries(out.functions ?? {})) if (f.url) calls.push({ id, method: f.method ?? 'GET', url: f.url })
} else {
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.name === 'node_modules' || d.name.startsWith('.') ? [] : d.isDirectory() ? walk(path.join(dir, d.name)) : d.name.endsWith('.jigx') ? [path.join(dir, d.name)] : [])
  for (const file of walk(src)) {
    const text = fs.readFileSync(file, 'utf8')
    corpus += text
    const url = text.match(/^\s*url:\s*['"]?([^'"\n]+)/m)?.[1]
    if (url) calls.push({ id: path.basename(file, '.jigx'), method: text.match(/^\s*method:\s*(\w+)/m)?.[1]?.toUpperCase() ?? 'GET', url })
  }
}

// The contract endpoint the solution builds its REST URLs on, e.g. entity/JigxMobile/23.200.001/.
const eps = [...new Set([...corpus.matchAll(/entity\/([A-Za-z0-9_]+)\/(\d+\.\d+\.\d+)\//g)].map((m) => `${m[1]} ${m[2]}`))]
const [epName, epVersion] = (eps[0] ?? '<Endpoint> <version>').split(' ')

const entities = new Map()
const inquiries = new Map()
const unmatched = []
for (const c of calls) {
  // Placeholder names vary between solutions ({acumaticaOdataV3URL}, {customerAcumaticaUrl}).
  const odata = c.url.match(/\{[A-Za-z0-9_]*odata[A-Za-z0-9_]*\}\/?([A-Za-z0-9_\-]+)/i)
  const rest = !odata && c.url.match(/\{[A-Za-z0-9_]*acumatica[A-Za-z0-9_]*\}\/?([A-Za-z0-9_]+)/i)
  // A placeholder followed by entity/... carries its own endpoint: left for a person.
  if (rest && rest[1] !== 'entity') {
    const e = entities.get(rest[1]) ?? { name: rest[1], why: [], read: null }
    e.why.push(`${c.id} (${c.method})`)
    // A GET is read back with one row; anything else is checked by schema only.
    if (c.method === 'GET') e.read = '$top=1'
    entities.set(rest[1], e)
  } else if (odata) {
    const q = inquiries.get(odata[1]) ?? { name: odata[1], why: [], top: 1 }
    q.why.push(c.id)
    inquiries.set(odata[1], q)
  } else if (/^https?:|\{/.test(c.url)) {
    unmatched.push(`${c.id}: ${c.method} ${c.url}`)
  }
}

const manifest = {
  name: args.name ?? path.basename(path.resolve(src, fs.statSync(src).isFile() ? '../..' : '.')),
  base: args.base ?? '<https://host/Instance/>',
  environment: 'test',
  companies: { match: '.' },
  interesting: '',
  endpoints: [{
    name: epName,
    version: epVersion,
    entities: [...entities.values()].map((e) => ({ name: e.name, why: e.why.join(', '), read: e.read, expect: [] })),
  }],
  locate: [],
  inquiries: [...inquiries.values()].map((q) => ({ name: q.name, why: q.why.join(', '), top: 1, expect: [] })),
  ...(eps.length > 1 ? { otherEndpointsSeen: eps.slice(1) } : {}),
  ...(unmatched.length ? { unmatched } : {}),
}
console.log(JSON.stringify(manifest, null, 2))
