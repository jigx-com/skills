#!/usr/bin/env node
// Read-only probe of an Acumatica test instance, driven by a manifest that
// lists what a Jigx solution needs: endpoints, entities, fields and generic
// inquiries. One company (tenant) at a time. The owner runs it from their own
// terminal; the password is prompted with echo off, never printed or written.
//
//   node probe.mjs <manifest.json> --user <acumatica-user> [--companies 'A,B'] [--out <report.md>]
//
// Requests: the public login page (GET), auth/login and auth/logout (POST, no
// data change) and GETs. Nothing else is ever sent. The first login is a check
// on its own, so a wrong password costs one failed attempt; any later failed
// login stops the run. Every session is logged out, on failure too.
import fs from 'node:fs'
import path from 'node:path'
import readline from 'node:readline'

const [manifestPath, ...rest] = process.argv.slice(2)
const args = Object.fromEntries(rest.reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []))
if (!manifestPath || !args.user) {
  console.error("usage: node probe.mjs <manifest.json> --user <acumatica-user> [--companies 'A,B'] [--out <report.md>]")
  process.exit(1)
}
const M = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
const BASE = (args.base ?? M.base).replace(/\/?$/, '/')
const USER = args.user
const today = new Date().toISOString().slice(0, 10)
const OUT = args.out ?? path.join(path.dirname(manifestPath), `probe-${M.name ?? 'acumatica'}-${today}.md`)
const TIMEOUT = 60_000
const short = (s, n = 200) => String(s ?? '').replace(/\s+/g, ' ').trim().slice(0, n)
const sameName = (a, b) => a.toLowerCase() === b.toLowerCase()

function ask(prompt, hidden) {
  if (!process.stdin.isTTY) {
    // Piped input: one line per question, in order.
    ask.lines ??= fs.readFileSync(0, 'utf8').split('\n')
    return Promise.resolve(ask.lines.shift() ?? '')
  }
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true })
    if (hidden) rl._writeToOutput = (s) => { if (s.includes(prompt)) process.stdout.write(prompt) }
    rl.question(prompt, (answer) => { rl.close(); if (hidden) process.stdout.write('\n'); resolve(answer) })
  })
}

class Session {
  constructor(company, password) {
    this.company = company
    this.password = password
    this.cookies = new Map()
    this.basic = false
  }

  async send(method, url, body) {
    if (method !== 'GET' && !/entity\/auth\/(login|logout)$/.test(url)) throw new Error(`refused: ${method} ${url}`)
    const headers = { Accept: 'application/json' }
    if (body) headers['Content-Type'] = 'application/json'
    if (this.cookies.size) headers.Cookie = [...this.cookies].map(([k, v]) => `${k}=${v}`).join('; ')
    if (this.basic) headers.Authorization = 'Basic ' + Buffer.from(`${USER}@${this.company}:${this.password}`).toString('base64')
    try {
      const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(TIMEOUT) })
      for (const c of res.headers.getSetCookie?.() ?? []) {
        const [pair] = c.split(';')
        const i = pair.indexOf('=')
        this.cookies.set(pair.slice(0, i), pair.slice(i + 1))
      }
      const text = await res.text()
      let json = null
      try { json = text ? JSON.parse(text) : null } catch { /* not JSON */ }
      return { status: res.status, json, text }
    } catch (e) {
      return { status: 0, json: null, text: String(e.cause?.code ?? e.message ?? e) }
    }
  }

  get(url) { return this.send('GET', url) }
  login() { return this.send('POST', BASE + 'entity/auth/login', { name: USER, password: this.password, company: this.company }) }
  logout() { return this.send('POST', BASE + 'entity/auth/logout') }
}

const ok = (r) => r.status >= 200 && r.status < 300
function verdict(r) {
  if (ok(r)) return 'OK'
  if (r.status === 0) return `no response (${r.text})`
  const j = r.json
  const inner = j?.['odata.error']?.innererror?.message ?? j?.error?.innererror?.message
  const msg = inner ?? j?.exceptionMessage ?? j?.message ?? j?.['odata.error']?.message?.value ?? j?.error?.message?.value ?? j?.error?.message ?? r.text
  return `${r.status}: ${short(typeof msg === 'string' ? msg : JSON.stringify(msg))}`
}

// "Details.ManualPrice" against an $adHocSchema body; arrays look at their first row.
function hasPath(json, dotted) {
  let v = json
  for (const key of dotted.split('.')) {
    if (Array.isArray(v)) v = v[0]
    if (!v || typeof v !== 'object') return false
    const k = Object.keys(v).find((x) => sameName(x, key))
    if (k === undefined) return false
    v = v[k]
  }
  return true
}

function customFields(json) {
  const out = []
  for (const [view, cols] of Object.entries(json?.custom ?? {})) for (const col of Object.keys(cols ?? {})) out.push(`${view}.${col}`)
  return out
}

const stripMeta = (row) => Object.fromEntries(Object.entries(row ?? {}).filter(([k]) => !k.startsWith('__') && !k.startsWith('@') && !k.startsWith('odata.')))

// Counts per value of one column, so a "which values occur" question needs no
// customer rows in the report. Entity fields arrive wrapped as { value }.
function tally(rows, field) {
  const counts = {}
  for (const row of rows) {
    const key = Object.keys(row ?? {}).find((k) => sameName(k, field))
    let v = key ? row[key] : undefined
    if (v && typeof v === 'object' && 'value' in v) v = v.value
    const label = v === undefined || v === null || v === '' ? '(empty)' : String(v)
    counts[label] = (counts[label] ?? 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
}

async function listCompanies() {
  try {
    const html = await (await fetch(BASE + 'Frames/Login.aspx', { signal: AbortSignal.timeout(30_000) })).text()
    const select = html.match(/<select[^>]*cmbCompany[^>]*>([\s\S]*?)<\/select>/i)
    return select ? [...select[1].matchAll(/<option[^>]*value="([^"]*)"/gi)].map((m) => m[1]).filter(Boolean) : []
  } catch {
    return []
  }
}

async function probeCompany(company, password) {
  const s = new Session(company, password)
  const out = { company, login: null, build: null, release: null, endpoints: [], entities: [], located: [], inquiries: [], odataList: [], notes: [] }
  const login = await s.login()
  out.login = verdict(login)
  if (!ok(login)) return out
  try {
    const eps = await s.get(BASE + 'entity')
    out.endpoints = (eps.json?.endpoints ?? []).map((e) => ({ name: e.name, version: e.version }))
    out.build = eps.json?.version?.acumaticaBuildVersion ?? null
    const m = out.build?.match(/^(\d+)\.(\d)/)
    if (m) out.release = `${m[1]}R${m[2]}`
    if (!eps.json) out.notes.push(`Endpoint list: ${verdict(eps)}`)
    const published = (ep) => out.endpoints.some((e) => sameName(e.name, ep.name) && e.version === ep.version)

    for (const ep of M.endpoints ?? []) {
      if (!published(ep)) { out.notes.push(`Endpoint ${ep.name} ${ep.version} is not published.`); continue }
      for (const ent of ep.entities ?? []) {
        const root = `${BASE}entity/${ep.name}/${ep.version}/${ent.name}`
        const schema = await s.get(`${root}/$adHocSchema`)
        const row = { endpoint: `${ep.name}/${ep.version}`, entity: ent.name, why: ent.why ?? '', schema: verdict(schema), read: '', rows: '', missing: [], custom: [], sample: undefined, tally: undefined, tallyField: ent.tally, top: Number(String(ent.read ?? '').match(/\$top=(\d+)/)?.[1]) || undefined }
        if (ok(schema)) {
          row.missing = (ent.expect ?? []).filter((p) => !hasPath(schema.json, p))
          row.custom = customFields(schema.json)
          if (ent.read !== undefined && ent.read !== null) {
            const r = await s.get(`${root}?${ent.read}`)
            row.read = verdict(r)
            if (Array.isArray(r.json)) {
              row.rows = String(r.json.length)
              if (ent.sample) row.sample = r.json.slice(0, ent.sample)
              if (ent.tally) row.tally = tally(r.json, ent.tally)
            }
          }
        }
        out.entities.push(row)
      }
    }

    // Entities whose endpoint is unknown: try each published endpoint's schema.
    for (const name of M.locate ?? []) {
      const hits = []
      for (const ep of out.endpoints) {
        const r = await s.get(`${BASE}entity/${ep.name}/${ep.version}/${name}/$adHocSchema`)
        if (r.status !== 404) hits.push(`${ep.name}/${ep.version}: ${verdict(r)}`)
      }
      out.located.push({ entity: name, hits })
    }

    // Generic inquiries over OData: the session cookie first, Basic if refused.
    const odata = `${BASE}odata/${encodeURIComponent(company)}/`
    let svc = await s.get(odata + '?$format=json')
    if (svc.status === 401) { s.basic = true; svc = await s.get(odata + '?$format=json') }
    out.odataList = (svc.json?.value ?? []).map((v) => v.name ?? v.url).filter(Boolean)
    if (!svc.json) out.notes.push(`OData service list: ${verdict(svc)}`)
    for (const q of M.inquiries ?? []) {
      const params = new URLSearchParams({ $format: 'json', $top: String(q.top ?? 1) })
      if (q.filter) params.set('$filter', q.filter)
      if (q.select) params.set('$select', q.select)
      const r = await s.get(`${odata}${encodeURIComponent(q.name)}?${params}`)
      const rows = (r.json?.value ?? []).map(stripMeta)
      out.inquiries.push({
        name: q.name,
        why: q.why ?? '',
        listed: out.odataList.some((n) => sameName(n, q.name)),
        status: verdict(r),
        columns: rows[0] ? Object.keys(rows[0]) : [],
        missing: rows[0] ? (q.expect ?? []).filter((c) => !Object.keys(rows[0]).some((k) => sameName(k, c))) : [],
        sample: q.sample ? rows.slice(0, q.sample) : undefined,
        rows: String(rows.length),
        top: q.top ?? 1,
        tallyField: q.tally,
        tally: q.tally ? tally(rows, q.tally) : undefined,
      })
    }
  } finally {
    const bye = await s.logout()
    if (!ok(bye)) out.notes.push(`Logout returned ${verdict(bye)}. Close the session in SM201530 if it lingers.`)
  }
  return out
}

function report(results) {
  const L = [`# Acumatica probe: ${M.name ?? ''}, ${today}`, '', `Instance \`${BASE}\` (${M.environment}). User \`${USER}\`. Read only: login, GETs, logout.`, '']
  for (const r of results) {
    L.push(`## ${r.company}`, '', `Login: ${r.login}`)
    if (r.login !== 'OK') { L.push(''); continue }
    L.push(`Build: ${r.build ?? 'not reported'}${r.release ? ` (${r.release})` : ''}`, '')
    L.push(`Endpoints: ${r.endpoints.map((e) => `${e.name} ${e.version}`).join(', ') || 'none listed'}`, '')
    if (r.entities.length) {
      L.push('| Endpoint | Entity | Used for | Schema | Read | Rows | Missing fields |', '|---|---|---|---|---|---|---|')
      for (const e of r.entities) L.push(`| ${e.endpoint} | ${e.entity} | ${e.why} | ${e.schema} | ${e.read || '-'} | ${e.rows || '-'} | ${e.missing.join(', ') || '-'} |`)
      L.push('')
      const pattern = M.interesting ? new RegExp(M.interesting, 'i') : null
      for (const e of r.entities.filter((e) => e.custom.length)) {
        const usr = e.custom.filter((c) => /\.Usr/.test(c))
        const hits = pattern ? e.custom.filter((c) => pattern.test(c)) : []
        L.push(`- ${e.entity}: ${usr.length} custom Usr fields${usr.length ? ` (${usr.map((c) => c.split('.').pop()).join(', ')})` : ''}${hits.length ? `. Matching "${M.interesting}": ${hits.join(', ')}` : ''}`)
      }
    }
    for (const l of r.located) L.push('', `Located ${l.entity}: ${l.hits.length ? l.hits.join('; ') : 'on no published endpoint'}`)
    if (r.inquiries.length) {
      L.push('', '| Inquiry | Used for | In OData list | Result | Missing columns | Columns |', '|---|---|---|---|---|---|')
      for (const q of r.inquiries) L.push(`| ${q.name} | ${q.why} | ${q.listed ? 'yes' : '**no**'} | ${q.status} | ${q.missing.join(', ') || '-'} | ${q.columns.join(', ') || '-'} |`)
    }
    if (M.interesting) {
      const extra = r.odataList.filter((n) => new RegExp(M.interesting, 'i').test(n))
      if (extra.length) L.push('', `Other inquiries matching "${M.interesting}": ${extra.join(', ')}`)
    }
    for (const x of [...r.entities, ...r.inquiries].filter((x) => x.sample?.length)) {
      L.push('', `Sample from ${x.entity ?? x.name}${x.why ? ` (${x.why})` : ''}:`, '', '```json', JSON.stringify(x.sample, null, 2), '```')
    }
    for (const x of [...r.entities, ...r.inquiries].filter((x) => x.tally)) {
      const capped = x.top !== undefined && Number(x.rows) >= x.top ? ` **Hit $top=${x.top}: counts may be incomplete.**` : ''
      L.push('', `${x.tallyField} in ${x.entity ?? x.name}${x.why ? ` (${x.why})` : ''}, ${x.rows} rows read.${capped}`, '', `| ${x.tallyField} | Rows |`, '|---|---|')
      for (const [v, n] of x.tally) L.push(`| ${v} | ${n} |`)
    }
    if (r.notes.length) L.push('', ...r.notes.map((n) => `- ${n}`))
    L.push('')
  }
  return L.join('\n')
}

// Test environments only, unless the owner types the host name to go on.
if (M.environment !== 'test') {
  const host = new URL(BASE).host
  const typed = await ask(`Manifest environment is "${M.environment ?? 'unset'}", not "test". Type ${host} to probe it anyway: `, false)
  if (typed.trim() !== host) { console.error('Stopped.'); process.exit(1) }
}

const listed = await listCompanies()
const companies = args.companies
  ? args.companies.split(',').map((c) => c.trim()).filter(Boolean)
  : M.companies?.list ?? listed.filter((c) => new RegExp(M.companies?.match ?? '.', 'i').test(c))
if (!companies.length) {
  console.error(`No companies to probe. On the login page: ${listed.join(', ') || 'none found'}.`)
  process.exit(1)
}
console.log(`Instance ${BASE}\nCompanies: ${companies.join(', ')}`)
const password = await ask(`Acumatica password for ${USER}: `, true)

process.stdout.write(`Checking the login on ${companies[0]}… `)
const check = new Session(companies[0], password)
const first = await check.login()
if (!ok(first)) {
  console.log(`failed: ${verdict(first)}\nStopped after one failed attempt. Nothing else was tried.`)
  process.exit(1)
}
await check.logout()
console.log('OK')

const results = []
for (const [i, company] of companies.entries()) {
  process.stdout.write(`Probing ${company}… `)
  const r = await probeCompany(company, password)
  console.log(r.login === 'OK' ? 'done' : `login ${r.login}`)
  results.push(r)
  if (r.login !== 'OK') {
    const skipped = companies.slice(i + 1)
    if (skipped.length) console.log(`Stopped after a failed login. Not probed: ${skipped.join(', ')}`)
    break
  }
}
fs.mkdirSync(path.dirname(OUT), { recursive: true })
fs.writeFileSync(OUT, report(results))
fs.writeFileSync(OUT.replace(/\.md$/, '.json'), JSON.stringify({ base: BASE, user: USER, date: today, results }, null, 2))
console.log(`Report: ${OUT}`)
