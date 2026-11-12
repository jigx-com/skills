#!/usr/bin/env node
// Runs probe.mjs against a local fake Acumatica and checks the safety rules:
// GETs only, one login check that stops on a wrong password, a stop on a later
// failed login, a logout for every login, and the report's main findings.
//
//   node selftest.mjs
import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'

const PASSWORD = 'pw-9f3kQz'
const log = []
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url)
  let body = ''
  req.on('data', (d) => { body += d })
  req.on('end', () => {
    log.push(`${req.method} ${u}`)
    const json = (status, b) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(b === undefined ? '' : JSON.stringify(b)) }
    if (u === '/Frames/Login.aspx') return res.end('<select id="cmbCompany"><option value="Co`s A"><option value="Other"><option value="Co`s B"><option value="Co`s Locked"></select>')
    if (u === '/entity/auth/login') {
      const p = JSON.parse(body)
      if (p.password !== PASSWORD) return json(500, { exceptionMessage: 'Invalid credentials' })
      if (p.company.includes('Locked')) return json(500, { exceptionMessage: 'You are not allowed to log in to this company' })
      res.writeHead(204, { 'Set-Cookie': 'ASP.NET_SessionId=s1; path=/' })
      return res.end()
    }
    if (u === '/entity/auth/logout') return json(204)
    if (!String(req.headers.cookie).includes('s1')) return json(401, { message: 'not signed in' })
    if (u === '/entity') return json(200, { version: { acumaticaBuildVersion: '25.200.0248' }, endpoints: [{ name: 'SALESEP', version: '1.0.0' }, { name: 'Custom', version: '2.0.0' }] })
    if (u === '/entity/Custom/2.0.0/Hidden/$adHocSchema') return json(500, { exceptionMessage: 'The type is not found: X' })
    if (u.includes('/Hidden/')) return json(404, { message: 'Entity Hidden not found' })
    if (u.includes('/Order/$adHocSchema')) return json(200, { OrderNbr: {}, Details: [{ UnitPrice: {} }], custom: { Document: { UsrDeliveryZone: {} } } })
    if (u.includes('/Locked/$adHocSchema')) return json(403, { message: 'You have insufficient rights to access the Locked (XX1000) form.' })
    if (u.startsWith('/entity/SalesEP/1.0.0/Order?')) return json(200, [{ OrderNbr: { value: '1' } }])
    if (u.includes('/Customer/$adHocSchema')) return json(200, { CustomerID: {}, CustomerClassID: {} })
    if (u.startsWith('/entity/SalesEP/1.0.0/Customer?')) return json(200, [{ CustomerID: { value: 'C1' }, CustomerClassID: { value: 'RETAIL' } }, { CustomerID: { value: 'C2' }, CustomerClassID: { value: 'RETAIL' } }, { CustomerID: { value: 'C3' }, CustomerClassID: { value: 'WHOLESALE' } }])
    if (/\/odata\/[^/]+\/\?/.test(u)) return json(200, { value: [{ name: 'GIOne' }, { name: 'ZoneGI' }] })
    if (u.includes('/GIOne?')) return json(200, { value: [{ __metadata: {}, A: 1, B: 2 }] })
    if (u.includes('/GITally?')) return json(200, { value: [{ Class: 'RESELL', Name: 'Secret Store' }, { Class: null, Name: 'Other Store' }] })
    if (u.includes('/GIMissing?')) return json(404, { error: { message: { value: 'not found' } } })
    return json(404, {})
  })
})
await new Promise((r) => server.listen(0, r))
const base = `http://localhost:${server.address().port}/`

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'probe-selftest-'))
const manifest = path.join(dir, 'm.json')
const writeManifest = (extra = {}) => fs.writeFileSync(manifest, JSON.stringify({
  name: 'selftest', base, environment: 'test', companies: { match: 'co`s' }, interesting: 'zone',
  endpoints: [
    { name: 'SalesEP', version: '1.0.0', entities: [
      { name: 'Order', why: 'orders', read: '$top=1', expect: ['OrderNbr', 'Details.UnitPrice', 'Details.ManualPrice'] },
      { name: 'Locked', why: 'locked', read: null },
      { name: 'Customer', why: 'classes', read: '$top=50&$select=CustomerID,CustomerClassID', tally: 'CustomerClassID' },
    ] },
    { name: 'Absent', version: '9.9.9', entities: [{ name: 'X' }] },
  ],
  locate: ['Hidden'],
  inquiries: [{ name: 'GIOne', why: 'one', expect: ['A', 'C'], sample: 1 }, { name: 'GIMissing', why: 'missing' }, { name: 'GITally', why: 'per class', top: 2, tally: 'class' }],
  ...extra,
}))

const probe = path.join(path.dirname(new URL(import.meta.url).pathname), 'probe.mjs')
function run(input, extra = []) {
  log.length = 0
  return new Promise((resolve) => {
    const p = spawn(process.execPath, [probe, manifest, '--user', 'u', ...extra])
    let out = ''
    p.stdout.on('data', (d) => { out += d })
    p.stderr.on('data', (d) => { out += d })
    p.on('close', (code) => resolve({ code, out, requests: [...log] }))
    p.stdin.end(input)
  })
}
const count = (reqs, re) => reqs.filter((r) => re.test(r)).length

writeManifest()

// 1. A wrong password costs exactly one login and stops.
let r = await run('wrong\n')
assert.equal(r.code, 1, r.out)
assert.equal(count(r.requests, /^POST \/entity\/auth\/login$/), 1)
assert.match(r.out, /Stopped after one failed attempt/)

// 2. Good password: the check, then A and B probed; Locked fails and stops.
r = await run(`${PASSWORD}\n`)
assert.equal(r.code, 0, r.out)
assert.deepEqual([...new Set(r.requests.map((x) => x.split(' ')[0]))].sort(), ['GET', 'POST'])
assert.equal(count(r.requests, /^POST (?!\/entity\/auth\/(login|logout)$)/), 0, 'only login and logout are posted')
assert.equal(count(r.requests, /auth\/login$/), 4) // check, A, B, Locked
assert.equal(count(r.requests, /auth\/logout$/), 3) // every login that worked
assert.match(r.out, /Not probed: |Locked/)
const report = fs.readFileSync(path.join(dir, `probe-selftest-${new Date().toISOString().slice(0, 10)}.md`), 'utf8')
assert.match(report, /Build: 25\.200\.0248 \(25R2\)/)
assert.match(report, /\| SalesEP\/1\.0\.0 \| Order \| orders \| OK \| OK \| 1 \| Details\.ManualPrice \|/) // case-insensitive endpoint
assert.match(report, /\| Locked \| locked \| 403: You have insufficient rights/)
assert.match(report, /Endpoint Absent 9\.9\.9 is not published/)
assert.match(report, /Located Hidden: Custom\/2\.0\.0: 500: The type is not found: X/)
assert.match(report, /\| GIOne \| one \| yes \| OK \| C \| A, B \|/)
assert.match(report, /\| GIMissing \| missing \| \*\*no\*\* \| 404: not found/)
assert.match(report, /Matching "zone": Document\.UsrDeliveryZone/)
assert.match(report, /Other inquiries matching "zone": ZoneGI/)
assert.doesNotMatch(report, /__metadata/)
assert.match(report, /CustomerClassID in Customer \(classes\), 3 rows read\.\n\n\| CustomerClassID \| Rows \|\n\|---\|---\|\n\| RETAIL \| 2 \|\n\| WHOLESALE \| 1 \|/)
assert.match(report, /class in GITally \(per class\), 2 rows read\. \*\*Hit \$top=2: counts may be incomplete\.\*\*/)
assert.match(report, /\| \(empty\) \| 1 \|/)
assert.doesNotMatch(report, /Secret Store|C1|C3/) // a tally copies no other columns
assert.doesNotMatch(report + fs.readFileSync(path.join(dir, `probe-selftest-${new Date().toISOString().slice(0, 10)}.json`), 'utf8'), new RegExp(PASSWORD))

// 3. A non-test manifest needs the host typed; a wrong answer stops before any login.
writeManifest({ environment: 'production' })
r = await run(`nope\n${PASSWORD}\n`)
assert.equal(r.code, 1)
assert.equal(count(r.requests, /auth\/login$/), 0)

server.close()
fs.rmSync(dir, { recursive: true })
console.log('probe selftest passed')
