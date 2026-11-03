#!/usr/bin/env node
// Time every query and execute-sql step of a Jigx solution against a copy of a device database.
// Needs Node 22.5+ (node:sqlite). The device database is opened read-only and copied with
// VACUUM INTO; everything runs on the copy, which is deleted afterwards. Order: the shipped
// CREATE INDEX statements first (timed, kept, as on a device that ran them), then every
// datasource.sqlite query, then every other execute-sql step inside a transaction that is
// rolled back. Host timings show relative cost on this machine, not device timings.
//
//   node time-queries.mjs <solution folder | build/output.json> --db device.db
//     [--params params.json] [--runs 5] [--report timings.md] [--baseline before.json]
//
// params.json binds query parameters by name ({"appointmentId": "AP001"}); unbound
// parameters are NULL, which makes most filtered queries return nothing quickly.
// Read references/run-and-track.md.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {loadSolution, maskSql, parseCreateIndex, sdkSources, where} from './check-performance.mjs';

const SLOW_MS = 100;
const KIND = {queries: 'query', actions: 'execute-sql', indexes: 'index'};
const statementText = s => typeof s === 'string' ? s : (s?.statement ?? s?.sql ?? '');
const asArray = v => Array.isArray(v) ? v : v == null ? [] : [v];

function walk(value, visit, p = '$') {
  visit(value, p);
  if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) walk(v, visit, `${p}.${k}`);
}

// Named parameters (@x, :x, $x) outside strings and comments.
export function paramNames(sql) {
  return [...new Set([...maskSql(sql).matchAll(/[@:$]([A-Za-z_]\w*)/g)].map(m => m[1]))];
}

// For each `<expr> = @p` (either side), a query that reads one real value of <expr>, so a
// detail-screen query is timed on a row that exists rather than on NULL. <expr> is any column
// or function call; a select-list alias (WHERE AppointmentNbr = @nbr) resolves to its expression.
// Works on the masked text, where literals are blank but offsets match the original.
export function sampleQueries(sql) {
  const masked = maskSql(sql);
  const tables = {};
  for (const m of masked.matchAll(/\b(?:FROM|JOIN)\s+(\[[^\]]+\]|[A-Za-z_][\w/-]*)(?!\s*\()(?:\s+(?:AS\s+)?(?!ON\b|WHERE\b|JOIN\b|LEFT\b|INNER\b|CROSS\b|GROUP\b|ORDER\b|LIMIT\b|UNION\b)([A-Za-z_]\w*))?/gi)) {
    const table = m[1];
    if (m[2]) tables[m[2].toLowerCase()] ??= table;
    tables[table.replace(/^\[|\]$/g, '').toLowerCase()] ??= table;
  }
  const distinct = [...new Set(Object.values(tables))];
  const IDENT = /[\w.\[\]/-]/;
  // The operand ending just before `end` (an identifier chain, or a call with its arguments).
  const left = end => {
    let i = end;
    while (i > 0 && /\s/.test(masked[i - 1])) i--;
    const stop = i;
    if (masked[i - 1] === ')') {
      for (let depth = 0; i > 0; i--) {
        if (masked[i - 1] === ')') depth++;
        else if (masked[i - 1] === '(' && --depth === 0) { i--; break; }
      }
    }
    while (i > 0 && IDENT.test(masked[i - 1])) i--;
    return [i, stop];
  };
  const right = from => {
    let i = from;
    while (i < masked.length && /\s/.test(masked[i])) i++;
    const begin = i;
    while (i < masked.length && IDENT.test(masked[i])) i++;
    if (masked[i] === '(') for (let depth = 0; i < masked.length; i++) {
      if (masked[i] === '(') depth++;
      else if (masked[i] === ')' && --depth === 0) { i++; break; }
    }
    return [begin, i];
  };
  // A select-list alias: the expression between the previous top-level comma (or SELECT) and `AS name`.
  const aliasExpr = name => {
    const m = new RegExp(`\\bAS\\s+${name}\\b`, 'i').exec(masked);
    if (!m) return null;
    let i = m.index, depth = 0;
    while (i > 0) {
      const ch = masked[i - 1];
      if (ch === ')') depth++;
      else if (ch === '(') { if (depth === 0) break; depth--; }
      else if (depth === 0 && ch === ',') break;
      else if (depth === 0 && /\bSELECT\s*$/i.test(masked.slice(Math.max(0, i - 7), i))) break;
      i--;
    }
    return sql.slice(i, m.index).trim().replace(/^DISTINCT\s+/i, '');
  };
  const out = {};
  for (const m of masked.matchAll(/=\s*[@:$](\w+)|[@:$](\w+)\s*=(?!=)/g)) {
    const name = m[1] ?? m[2];
    if (out[name]) continue;
    const [a, b] = m[1] ? left(m.index) : right(m.index + m[0].length);
    let expr = sql.slice(a, b).trim();
    if (!expr || /[@:$]\w/.test(maskSql(expr)) || /^\d/.test(expr)) continue;
    if (/^[A-Za-z_]\w*$/.test(expr) && !/^id$/i.test(expr)) expr = aliasExpr(expr) ?? expr;
    const alias = /\b(\w+)\.(?:data|id)\b/i.exec(maskSql(expr))?.[1]?.toLowerCase();
    const table = alias ? tables[alias] : distinct.length === 1 ? distinct[0] : undefined;
    if (!table) continue;
    out[name] = `SELECT ${expr} AS v FROM ${table}${alias && alias !== table.replace(/^\[|\]$/g, '').toLowerCase() ? ` AS ${alias}` : ''} WHERE ${expr} IS NOT NULL LIMIT 1`;
  }
  return out;
}

// Queries, index DDL and other execute-sql steps; SDK copies of one global query are merged.
export function collect(app) {
  const queries = new Map(), indexes = [], steps = [], built = [];
  // SQL built by an expression at runtime (="… in " & $join(…)) has no fixed text to time.
  const isExpr = sql => /^\s*=/.test(sql);
  walk(app, (node, p) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'datasource.sqlite' && typeof node.options?.query === 'string') {
      const sql = node.options.query;
      if (isExpr(sql)) { built.push(p); return; }
      const q = queries.get(sql) ?? {path: p, sql, copies: 0, parameters: node.options.queryParameters ?? node.options.parameters ?? {}};
      q.copies++;
      queries.set(sql, q);
    }
    if (node.type === 'action.execute-sql') {
      const all = asArray(node.options?.statements ?? node.statements).map(statementText).filter(Boolean);
      if (all.some(isExpr)) built.push(p);
      const statements = all.filter(sql => !isExpr(sql));
      const ddl = statements.filter(s => parseCreateIndex(s));
      ddl.forEach(sql => indexes.push({path: p, sql, name: parseCreateIndex(sql).name}));
      const rest = statements.filter(s => !parseCreateIndex(s));
      if (rest.length) steps.push({path: p, statements: rest});
    }
  });
  return {queries: [...queries.values()], indexes, steps, built};
}

const median = xs => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor((s.length - 1) / 2)] : null; };
const round = ms => Math.round(ms * 10) / 10;

function time(fn, runs) {
  fn(); // warm-up: the first run pays for statement preparation and a cold page cache
  const first = performance.now(); const result = fn(); const once = performance.now() - first;
  const times = [once];
  for (let i = 1; i < (once > 2000 ? 3 : runs); i++) { const t = performance.now(); fn(); times.push(performance.now() - t); }
  return {ms: round(median(times)), max: round(Math.max(...times)), result};
}

export function timeSolution(app, db, {params = {}, runs = 5, sources = {}} = {}) {
  const {queries, indexes, steps, built} = collect(app);
  // Given values first, then a real value sampled from the data, else NULL.
  const sampled = new Map();
  const sample = sql => {
    if (!sampled.has(sql)) {
      const values = {};
      for (const [name, probe] of Object.entries(sampleQueries(sql))) {
        if (name in params) continue;
        try { const v = db.prepare(probe).get()?.v; if (v != null) values[name] = v; } catch { /* table not on this device */ }
      }
      sampled.set(sql, values);
    }
    return sampled.get(sql);
  };
  const bind = sql => { const s = sample(sql); return Object.fromEntries(paramNames(sql).map(n => [n, params[n] ?? s[n] ?? null])); };
  const unbound = sql => paramNames(sql).filter(n => !(n in params) && !(n in sample(sql)));
  const loc = p => where(p, sources);
  const existing = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type = 'index'").all().map(r => r.name));

  const indexResults = indexes.map(i => {
    if (existing.has(i.name)) return {where: loc(i.path), index: i.name, ms: null, note: 'already on the device'};
    try {
      const t = performance.now(); db.exec(i.sql); existing.add(i.name);
      return {where: loc(i.path), index: i.name, ms: round(performance.now() - t)};
    } catch (e) { return {where: loc(i.path), index: i.name, error: e.message}; }
  });

  const queryResults = queries.map(q => {
    const base = {where: loc(q.path), copies: q.copies, sampled: Object.keys(sample(q.sql)).filter(n => !(n in params)), unbound: unbound(q.sql)};
    try {
      const stmt = db.prepare(q.sql);
      const args = bind(q.sql);
      const {ms, max, result} = time(() => stmt.all(args), runs);
      return {...base, ms, max, rows: result.length};
    } catch (e) { return {...base, error: e.message}; }
  }).sort((a, b) => (b.ms ?? -1) - (a.ms ?? -1));

  const stepResults = steps.map(s => {
    const base = {where: loc(s.path), statements: s.statements.length, unbound: [...new Set(s.statements.flatMap(unbound))]};
    try {
      const prepared = s.statements.map(sql => [db.prepare(sql), bind(sql)]);
      const {ms, max} = time(() => {
        db.exec('BEGIN');
        try { for (const [stmt, args] of prepared) stmt.run(args); } finally { db.exec('ROLLBACK'); }
      }, runs);
      return {...base, ms, max};
    } catch (e) { return {...base, error: e.message}; }
  }).sort((a, b) => (b.ms ?? -1) - (a.ms ?? -1));

  return {indexes: indexResults, queries: queryResults, actions: stepResults, notTimed: [...new Set(built)].map(loc)};
}

// Change per item against an earlier run, matched by kind and location.
export function compareTimings(before, after) {
  const rows = [];
  for (const kind of ['queries', 'actions', 'indexes']) {
    const old = new Map((before[kind] ?? []).map(r => [r.where, r]));
    for (const r of after[kind] ?? []) {
      const b = old.get(r.where);
      if (!b || b.ms == null || r.ms == null) continue;
      const change = b.ms ? round((r.ms - b.ms) / b.ms * 100) : null;
      // Below 5 ms both sides are noise on a host; flag only real moves.
      const verdict = Math.abs(r.ms - b.ms) < 5 ? 'same' : r.ms < b.ms ? 'faster' : change > 10 ? 'SLOWER' : 'same';
      rows.push({kind, where: r.where, before_ms: b.ms, after_ms: r.ms, change_pct: change, verdict});
    }
  }
  return rows.sort((a, b) => Math.abs(b.after_ms - b.before_ms) - Math.abs(a.after_ms - a.before_ms));
}

export function timingsReport({title, result, comparison, meta}) {
  const lines = [`# Query and action timings: ${title}`, '',
    `Host: ${meta.host}, SQLite ${meta.sqlite}. Database: ${meta.db} (${meta.rows} rows in ${meta.tables} tables).`,
    `Median of ${meta.runs} runs after a warm-up. Host timings show relative cost; a phone is several times slower.`, ''];
  const total = xs => round(xs.reduce((s, r) => s + (r.ms ?? 0), 0));
  const slow = result.queries.filter(q => q.ms >= SLOW_MS);
  lines.push(`**${result.queries.length} queries, ${total(result.queries)} ms in total; ${slow.length} at ${SLOW_MS} ms or more. ` +
    `${result.actions.length} execute-sql steps, ${total(result.actions)} ms. ${result.indexes.filter(i => i.ms != null).length} indexes built, ${total(result.indexes)} ms.**`, '');
  if (comparison?.length) {
    const moved = comparison.filter(r => r.verdict !== 'same');
    lines.push('## Before and after', '', `${moved.filter(r => r.verdict === 'faster').length} faster, ${moved.filter(r => r.verdict === 'SLOWER').length} slower, ${comparison.length - moved.length} the same.`, '');
    if (moved.length) {
      lines.push('| What | Where | Before | After | Change |', '| --- | --- | ---: | ---: | ---: |');
      for (const r of moved.slice(0, 25)) lines.push(`| ${KIND[r.kind]} | \`${r.where}\` | ${r.before_ms} ms | ${r.after_ms} ms | ${r.change_pct ?? '-'}% ${r.verdict === 'SLOWER' ? '⚠' : ''} |`);
      lines.push('');
    }
  }
  const table = (heading, rows, cols) => {
    if (!rows.length) return;
    lines.push(`## ${heading}`, '', `| ${cols.map(c => c[0]).join(' | ')} |`, `| ${cols.map(c => c[2] ? '---:' : '---').join(' | ')} |`);
    for (const r of rows) lines.push(`| ${cols.map(c => c[1](r)).join(' | ')} |`);
    lines.push('');
  };
  const note = r => r.error ? `error: ${r.error}` : [r.sampled?.length && `sampled: ${r.sampled.join(', ')}`,
    r.unbound?.length && `NULL: ${r.unbound.join(', ')}`, r.note].filter(Boolean).join('; ');
  table('Slowest queries', result.queries.filter(q => q.ms != null).slice(0, 25), [
    ['Where', r => `\`${r.where}\`${r.copies > 1 ? ` (${r.copies} copies)` : ''}`], ['Median', r => `${r.ms} ms`, 1], ['Max', r => `${r.max} ms`, 1], ['Rows', r => r.rows, 1], ['Note', note]]);
  table('execute-sql steps', result.actions.filter(a => a.ms != null).slice(0, 15), [
    ['Where', r => `\`${r.where}\``], ['Statements', r => r.statements, 1], ['Median', r => `${r.ms} ms`, 1], ['Note', note]]);
  table('Index builds', result.indexes.filter(i => i.ms != null).sort((a, b) => b.ms - a.ms).slice(0, 15), [
    ['Index', r => r.index], ['Where', r => `\`${r.where}\``], ['Build', r => `${r.ms} ms`, 1]]);
  const failed = [...result.queries, ...result.actions, ...result.indexes].filter(r => r.error);
  if (failed.length) {
    lines.push(`## Could not run (${failed.length})`, '', 'Usually a table this database does not have yet (not synced for this user), or a parameter that must be bound.', '');
    for (const r of failed.slice(0, 20)) lines.push(`- \`${r.where}\`${r.index ? ` (${r.index})` : ''}: ${r.error}`);
    lines.push('');
  }
  if (result.notTimed?.length) lines.push(`## Built at runtime, not timed (${result.notTimed.length})`, '',
    'SQL these steps build with an expression; time them through the screen or sync that runs them.', '',
    ...result.notTimed.slice(0, 20).map(w => `- \`${w}\``), '');
  lines.push('## Not measured here', '',
    '- Sync and network time: `track-load.py`. Screen open time, which includes expression evaluation and rendering: `time-screens.py`.',
    '- Parameters: given in --params, else "sampled" (one real value of the column the query compares them to), else NULL. A NULL parameter usually returns no rows, so that query\'s time is a lower bound.');
  return lines.join('\n') + '\n';
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const opt = name => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
    const dbPath = opt('--db'), reportPath = opt('--report'), paramsPath = opt('--params'), baselinePath = opt('--baseline');
    const runs = Number(opt('--runs') ?? 5);
    const file = args[0] || 'build/output.json';
    if (!dbPath) throw new Error('--db <device database> is required (track-load.py --list finds simulator databases)');
    const loaded = fs.statSync(file).isDirectory()
      ? loadSolution(file) : {app: JSON.parse(fs.readFileSync(file, 'utf8')), sources: sdkSources(file)};
    process.removeAllListeners('warning'); // node:sqlite prints an experimental warning on Node 22
    const {DatabaseSync} = await import('node:sqlite');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-time-'));
    const copy = path.join(dir, 'copy.db');
    try {
      const src = new DatabaseSync(dbPath, {readOnly: true});
      src.exec(`VACUUM INTO '${copy.replace(/'/g, "''")}'`);
      src.close();
      // The device's SQLite accepts "text" as a string literal; match it.
      const db = new DatabaseSync(copy, {enableDoubleQuotedStringLiterals: true});
      const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map(r => r.name);
      const rows = tables.reduce((n, t) => n + db.prepare(`SELECT count(*) AS n FROM "${t.replace(/"/g, '""')}"`).get().n, 0);
      const params = paramsPath ? JSON.parse(fs.readFileSync(paramsPath, 'utf8')) : {};
      const result = timeSolution(loaded.app, db, {params, runs, sources: loaded.sources});
      const meta = {host: `${os.type()} ${os.arch()}, ${os.cpus()[0]?.model ?? 'unknown CPU'}`, sqlite: db.prepare('SELECT sqlite_version() AS v').get().v,
        db: path.basename(dbPath), tables: tables.length, rows, runs, params: Object.keys(params)};
      db.close();
      const comparison = baselinePath ? compareTimings(JSON.parse(fs.readFileSync(baselinePath, 'utf8')), result) : null;
      console.log(JSON.stringify({file, meta, ...result, ...(comparison && {comparison})}, null, 2));
      if (reportPath) {
        const title = loaded.app.title ?? loaded.app.name ?? path.basename(path.resolve(file));
        fs.writeFileSync(reportPath, timingsReport({title, result, comparison, meta}));
        console.error(`Report written to ${reportPath}.`);
      }
      process.exitCode = comparison?.some(r => r.verdict === 'SLOWER') ? 1 : 0;
    } finally { fs.rmSync(dir, {recursive: true, force: true}); }
  } catch (error) { console.error(`Timing failed: ${error.message}`); process.exitCode = 2; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
