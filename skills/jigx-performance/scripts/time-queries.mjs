#!/usr/bin/env node
// Time every query and execute-sql step of a Jigx solution against a copy of a device database.
// Needs Node 22.5+ (node:sqlite). The device database is opened read-only and copied with
// VACUUM INTO; everything runs on the copy, which is deleted afterwards. Order: the shipped
// CREATE INDEX statements first (timed, kept, as on a device that ran them), then every
// datasource.sqlite query, then every other execute-sql step inside a transaction that is
// rolled back. Host timings show relative cost on this machine, not device timings.
//
//   node time-queries.mjs <solution folder | build/output.json> --db device.db
//     [--params params.json] [--runs 5] [--report timings.md] [--baseline before.json] [--scale 10]
//
// --scale N also times everything on a second copy with every table's rows multiplied by N,
// and lists what grows faster than the data (a scan per row, a join no index serves). Keys
// that link tables (a value found in two or more tables, such as an appointment number) get
// a per-copy suffix, so each copy joins to its own rows instead of multiplying the joins;
// values the solution's SQL compares with literally ('In Process', 1) are kept.
//
// params.json binds query parameters by name ({"appointmentId": "AP001"}), or every parameter that
// reads one solution-state value by the state's name ({"active-project": "BUDGETBYM "}). Without
// it, a state value gets one value for every query that reads it: the one with the most rows
// across the columns they compare it with, as stored (padding kept). Any other parameter it does
// not give is sampled from the column the query compares it with; failing that, it is guessed
// from a column with the same name (projectId -> ProjectID), taking the value with the most rows
// (the busiest project), and the report lists every guess. The rest are NULL, which makes most
// filtered queries return nothing quickly; those are reported as not measured.
//
// The database must hold the synced data: copy it with `track-load.py --copy-db`, or pass the
// live simulator database (it is opened read-only). A plain cp leaves the rows in the -wal file.
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
  // `= @p`, `@p =`, and the same with the parameter wrapped: `= trim(@p)`, `trim(COALESCE(@p, '')) =`
  // (a barcode or search box compared to a trimmed key): the other side's real value still matches.
  const WRAP = String.raw`(?:(?:trim|ltrim|rtrim|lower|upper|coalesce|ifnull|nullif)\s*\(\s*)`;
  const pattern = new RegExp(String.raw`=\s*${WRAP}*[@:$](\w+)|${WRAP}*[@:$](\w+)(?:\s*,[^()=]*)?(?:\s*\))*\s*=(?!=)`, 'gi');
  for (const m of masked.matchAll(pattern)) {
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
      // One index can be created by several steps (start-up and after the initial sync): time it once.
      ddl.forEach(sql => { const name = parseCreateIndex(sql).name; if (!indexes.some(i => i.name === name)) indexes.push({path: p, sql, name}); });
      const rest = statements.filter(s => !parseCreateIndex(s));
      if (rest.length) steps.push({path: p, statements: rest});
    }
  });
  return {queries: [...queries.values()], indexes, steps, built};
}

// Values the SQL names literally: kept as they are when scaling, so filters still match.
export function sqlLiterals(sqls) {
  const out = new Set();
  for (const sql of sqls) {
    for (const m of sql.matchAll(/'((?:[^']|'')*)'/g)) out.add(m[1].replace(/''/g, "'").trim());
    for (const m of maskSql(sql).matchAll(/(?<![\w.$@])-?\d+(?:\.\d+)?(?![\w.])/g)) out.add(String(Number(m[0])));
  }
  return out;
}

const DATE_LIKE = /^\d{4}-\d{2}-\d{2}/;

// Multiply every table's rows by `factor` in place. Linking keys get "_x<k>" (before any
// trailing spaces, so trim() still matches) or, for integers, + k * 1e9; ids always change.
export function scaleDatabase(db, factor, literals = new Set()) {
  const q = name => `"${name.replace(/"/g, '""')}"`;
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\'").all().map(r => r.name);
  const parse = v => { if (typeof v !== 'string' || !/^\s*[[{]/.test(v)) return undefined; try { return JSON.parse(v); } catch { return undefined; } };
  const norm = v => typeof v === 'string' ? v.trim() : String(v);
  // Pass 1: which tables each scalar value appears in.
  const seen = new Map();
  const note = (v, t) => {
    if (typeof v === 'boolean' || v == null) return;
    if (typeof v === 'number' && !Number.isInteger(v)) return;
    const k = norm(v);
    if (!k || literals.has(k) || DATE_LIKE.test(k)) return;
    (seen.get(k) ?? seen.set(k, new Set()).get(k)).add(t);
  };
  const leaves = (v, fn) => { if (v && typeof v === 'object') { for (const x of Object.values(v)) leaves(x, fn); } else fn(v); };
  const data = {};
  for (const t of tables) {
    const rows = db.prepare(`SELECT * FROM ${q(t)}`).all();
    data[t] = rows;
    for (const r of rows) for (const [c, v] of Object.entries(r)) {
      if (c === 'id') { note(v, t); continue; }
      const j = parse(v);
      if (j !== undefined) leaves(j, x => note(x, t)); else note(v, t);
    }
  }
  const linking = new Set([...seen].filter(([, ts]) => ts.size > 1).map(([k]) => k));
  const shift = (v, k, force = false) => {
    if ((force || linking.has(norm(v))) && v != null && typeof v !== 'boolean') {
      if (typeof v === 'number') return Number.isInteger(v) ? v + k * 1e9 : v;
      return String(v).replace(/(\s*)$/, `_x${k}$1`);
    }
    return v;
  };
  const remap = (v, k) => Array.isArray(v) ? v.map(x => remap(x, k))
    : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([a, b]) => [a, remap(b, k)])) : shift(v, k);
  let added = 0;
  db.exec('BEGIN');
  try {
    for (const t of tables) {
      const rows = data[t];
      if (!rows.length) continue;
      const cols = Object.keys(rows[0]);
      const insert = db.prepare(`INSERT INTO ${q(t)} (${cols.map(q).join(', ')}) VALUES (${cols.map(() => '?').join(', ')})`);
      for (let k = 1; k < factor; k++) for (const r of rows) {
        insert.run(...cols.map(c => {
          const v = r[c];
          if (c === 'id') return shift(v, k, true);
          const j = parse(v);
          return j !== undefined ? JSON.stringify(remap(j, k)) : shift(v, k);
        }));
        added++;
      }
    }
    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }
  return {tables: tables.length, linkingKeys: linking.size, rowsAdded: added};
}

// Items whose time grows faster than the data between a 1x and an Nx run. An item whose rows
// grew more than N times joins on a value the scaling could not separate: shown, not judged.
export function growth(base, scaled, factor, {minMs = 10} = {}) {
  const rows = [];
  for (const kind of ['queries', 'actions']) {
    const old = new Map((base[kind] ?? []).map(r => [r.where, r]));
    for (const r of scaled[kind] ?? []) {
      const b = old.get(r.where);
      if (!b || b.ms == null || r.ms == null) continue;
      const timeX = b.ms > 0 ? r.ms / b.ms : null;
      const rowsX = b.rows ? r.rows / b.rows : null;
      const inflated = rowsX != null && rowsX > factor * 1.5;
      const verdict = (b.nullParams || r.nullParams) ? 'not measured (NULL parameters)'
        : inflated ? 'rows inflated' : r.ms >= minMs && timeX != null && timeX > factor * 2 ? 'GROWS FASTER' : 'in line';
      rows.push({kind, where: r.where, ms_1x: b.ms, ms_nx: r.ms, time_x: timeX && round(timeX), rows_1x: b.rows, rows_nx: r.rows, verdict});
    }
  }
  return rows.sort((a, b) => (b.verdict === 'GROWS FASTER') - (a.verdict === 'GROWS FASTER') || b.ms_nx - a.ms_nx);
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

// Normalised parameter or column name, and its base without a key suffix or a selection prefix:
// projectId, ProjectID, selectedProject and project_cd all have the base "project".
const norm = n => String(n).toLowerCase().replace(/[^a-z0-9]/g, '');
const base = n => norm(n).replace(/^(?:selected|current|active|the)(?=.)/, '').replace(/(?:id|nbr|number|cd|code|key|no)$/, '') || norm(n);

// Columns and JSON data keys of every solution table, for guessing parameter values by name.
export function dataKeys(db) {
  const out = [];
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\'").all().map(r => r.name);
  for (const t of tables) {
    const q = `"${t.replace(/"/g, '""')}"`;
    try {
      const cols = db.prepare(`SELECT name FROM pragma_table_info('${t.replace(/'/g, "''")}')`).all().map(r => r.name);
      for (const c of cols) if (c !== 'data') out.push({table: t, expr: `"${c.replace(/"/g, '""')}"`, key: c});
      if (cols.includes('data')) {
        for (const {key} of db.prepare(`SELECT DISTINCT j.key AS key FROM (SELECT data FROM ${q} LIMIT 20) r, json_each(r.data) j`).all())
          out.push({table: t, expr: `json_extract(data, '$.${String(key).replace(/'/g, "''")}')`, key});
      }
    } catch { /* not JSON, or not readable */ }
  }
  return out;
}

// No data: no rows, or one row of zeros and NULLs (count(*) or sum() over nothing, as a per-record
// photo count with its record NULL returns).
export const noData = rows => !rows.length || (rows.length === 1 && Object.values(rows[0]).every(v => v == null || v === 0 || v === ''));

// The tables a query reads (FROM and JOIN), without brackets.
export function queryTables(sql) {
  return [...new Set([...maskSql(sql).matchAll(/\b(?:FROM|JOIN)\s+(\[[^\]]+\]|[A-Za-z_][\w/-]*)(?!\s*\()/gi)]
    .map(m => m[1].replace(/^\[|\]$/g, '')))];
}

// A value for a parameter nothing sampled: the most frequent value of a same-named column or data
// key (the record with the most rows, e.g. the busiest project), with where it came from. Keys in
// the tables the query reads come first; other tables only when none of those has the name.
export function guessParam(db, name, keys = dataKeys(db), tables = []) {
  const match = ks => {
    const exact = ks.filter(k => norm(k.key) === norm(name));
    return exact.length ? exact : ks.filter(k => base(k.key) === base(name) && base(name).length >= 3);
  };
  const own = match(keys.filter(k => tables.includes(k.table)));
  const near = own.length ? own : match(keys);
  let best = null;
  for (const k of near) {
    try {
      const r = db.prepare(`SELECT ${k.expr} AS v, count(*) AS n FROM "${k.table.replace(/"/g, '""')}" WHERE ${k.expr} IS NOT NULL AND ${k.expr} <> '' GROUP BY v ORDER BY n DESC LIMIT 1`).get();
      if (r && (!best || r.n > best.rows)) best = {value: r.v, from: `${k.table}.${k.key}`, rows: r.n};
    } catch { /* column not comparable */ }
  }
  return best;
}

// The solution state a parameter reads on its own: `=@ctx.solution.state.active-project` -> 'active-project'.
export const stateKey = v => /^=?\s*@ctx\.solution\.state\.([\w-]+)\s*$/.exec(String(v ?? '').trim())?.[1];

// One value per solution-state key, for every parameter that reads it: the value given in
// params.json (by the state's name, "active-project", or "@ctx.solution.state.active-project"),
// else the value with the most rows across the columns those queries compare it with (the busiest
// project). Values are used as stored: Acumatica pads some keys ("BUDGETBYM ").
export function stateValues(db, queries, params = {}) {
  const uses = new Map();
  for (const q of queries) for (const [name, v] of Object.entries(q.parameters ?? {})) {
    const key = stateKey(v);
    if (key && !(name in params)) (uses.get(key) ?? uses.set(key, []).get(key)).push({sql: q.sql, name});
  }
  const out = {};
  for (const [key, list] of uses) {
    const given = [`@ctx.solution.state.${key}`, `state.${key}`, key].find(k => k in params);
    if (given) { out[key] = {value: params[given], given: true, queries: list.length}; continue; }
    const tally = new Map(), from = new Set();
    for (const probe of new Set(list.map(u => sampleQueries(u.sql)[u.name]).filter(Boolean))) {
      const count = probe.replace(/ AS v FROM /, ' AS v, count(*) AS n FROM ').replace(/ LIMIT 1$/, ' GROUP BY v');
      try {
        for (const r of db.prepare(count).all()) {
          const k = JSON.stringify(r.v);
          tally.set(k, (tally.get(k) ?? 0) + r.n);
          from.add(/\bFROM\s+(\[[^\]]+\]|\S+)/.exec(probe)?.[1]?.replace(/^\[|\]$/g, ''));
        }
      } catch { /* table not on this device */ }
    }
    const best = [...tally].sort((a, b) => b[1] - a[1])[0];
    out[key] = best ? {value: JSON.parse(best[0]), rows: best[1], from: [...from].filter(Boolean).sort(), queries: list.length}
      : {value: null, none: true, queries: list.length};
  }
  return out;
}

export function timeSolution(app, db, {params = {}, runs = 5, sources = {}} = {}) {
  const {queries, indexes, steps, built} = collect(app);
  const state = stateValues(db, queries, params);
  const stateOf = new Map(queries.map(q => [q.sql, Object.fromEntries(Object.entries(q.parameters ?? {})
    .map(([n, v]) => [n, stateKey(v)]).filter(([n, k]) => k && !(n in params) && !state[k]?.none))]));
  const fromState = (sql, n) => { const k = stateOf.get(sql)?.[n]; return k ? state[k].value : undefined; };
  // Tables with no rows on this copy: a query that reads only those timed nothing.
  const tableRows = new Map(db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all()
    .map(({name}) => { try { return [name.toLowerCase(), db.prepare(`SELECT count(*) AS n FROM "${name.replace(/"/g, '""')}"`).get().n]; } catch { return [name.toLowerCase(), null]; } }));
  const emptyTables = sql => { const ts = queryTables(sql).map(t => t.toLowerCase()).filter(t => tableRows.has(t));
    return ts.length && ts.every(t => tableRows.get(t) === 0) ? ts : null; };
  let keys = null;
  const guessed = new Map();
  const used = new Set();
  const guessKey = (name, sql) => `${name}|${queryTables(sql).sort().join(',')}`;
  const guess = (name, sql) => {
    const key = guessKey(name, sql);
    if (!guessed.has(key)) guessed.set(key, {name, ...(guessParam(db, name, keys ??= dataKeys(db), queryTables(sql)) ?? {none: true})});
    const g = guessed.get(key);
    return g.none ? null : g;
  };
  // Given values first, then a real value sampled from the data, else NULL.
  const sampled = new Map();
  const sample = sql => {
    if (!sampled.has(sql)) {
      const values = {};
      for (const [name, probe] of Object.entries(sampleQueries(sql))) {
        if (name in params || fromState(sql, name) !== undefined) continue;
        try { const v = db.prepare(probe).get()?.v; if (v != null) values[name] = v; } catch { /* table not on this device */ }
      }
      sampled.set(sql, values);
    }
    return sampled.get(sql);
  };
  // Unbound parameters are NULL first: an optional filter (@p IS NULL OR ...) then shows every row,
  // as the screen does. Only a query that returns nothing with NULL is tried with guessed values.
  const bind = (sql, guessing = false) => { const s = sample(sql); return Object.fromEntries(paramNames(sql).map(n =>
    [n, params[n] ?? fromState(sql, n) ?? s[n] ?? (guessing ? guess(n, sql)?.value : null) ?? null])); };
  const open = sql => paramNames(sql).filter(n => !(n in params) && fromState(sql, n) === undefined && !(n in sample(sql)));
  const unbound = sql => open(sql);
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
    const base = {where: loc(q.path), copies: q.copies, sampled: Object.keys(sample(q.sql)).filter(n => !(n in params)),
      unbound: unbound(q.sql), ...(Object.keys(stateOf.get(q.sql) ?? {}).length && {state: stateOf.get(q.sql)})};
    const empty = emptyTables(q.sql);
    if (empty) base.emptyTables = empty;
    try {
      const stmt = db.prepare(q.sql);
      let timed = time(() => stmt.all(bind(q.sql)), runs);
      const guessable = base.unbound.filter(n => guess(n, q.sql));
      if (noData(timed.result) && guessable.length) {
        const tried = time(() => stmt.all(bind(q.sql, true)), runs);
        if (!noData(tried.result)) {
          timed = tried;
          base.guessed = guessable;
          for (const n of guessable) used.add(guessKey(n, q.sql));
          base.unbound = base.unbound.filter(n => !guessable.includes(n));
        }
      }
      const {ms, max, result} = timed;
      // A parameter the screen fills (a barcode, a search box) is NULL here; with no rows the
      // query timed nothing, so its time is not a measurement: pass the value with --params.
      return {...base, ms, max, rows: result.length, ...(base.unbound.length && noData(result) ? {nullParams: true} : {})};
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

  // One line per parameter and value, for the guesses a query was timed with.
  const guesses = {};
  for (const [key, g] of guessed) {
    if (g.none || !used.has(key)) continue;
    const k = `${g.name}=${JSON.stringify(g.value)}`;
    guesses[k] ??= {name: g.name, value: g.value, from: g.from, rows: g.rows};
  }
  return {indexes: indexResults, queries: queryResults, actions: stepResults, notTimed: [...new Set(built)].map(loc), guesses, state};
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
      if (b.nullParams || r.nullParams) {
        rows.push({kind, where: r.where, before_ms: b.ms, after_ms: r.ms, change_pct: change, verdict: 'not measured (NULL parameters)'});
        continue;
      }
      if (b.emptyTables || r.emptyTables) {
        rows.push({kind, where: r.where, before_ms: b.ms, after_ms: r.ms, change_pct: change, verdict: 'not measured (empty tables)'});
        continue;
      }
      // A host repeats a small query within about a millisecond, so a move counts when it is
      // 5 ms or more, or 1 ms or more and at least twice (2.5 -> 0.2 ms is faster, 0.3 -> 0.8 is noise).
      const diff = Math.abs(r.ms - b.ms), ratio = Math.max(r.ms, b.ms) / Math.max(Math.min(r.ms, b.ms), 0.05);
      const moved = diff >= 5 || (diff >= 1 && ratio >= 2);
      const verdict = !moved ? 'same' : r.ms < b.ms ? 'faster' : change > 10 ? 'SLOWER' : 'same';
      rows.push({kind, where: r.where, before_ms: b.ms, after_ms: r.ms, change_pct: change, verdict});
    }
  }
  return rows.sort((a, b) => Math.abs(b.after_ms - b.before_ms) - Math.abs(a.after_ms - a.before_ms));
}

// The Nx timings of a --scale run, in the shape compareTimings reads.
export const atScale = growthRows => ({
  queries: (growthRows ?? []).filter(r => r.kind === 'queries').map(r => ({where: r.where, ms: r.ms_nx})),
  actions: (growthRows ?? []).filter(r => r.kind === 'actions').map(r => ({where: r.where, ms: r.ms_nx})),
});

export function timingsReport({title, result, comparison, comparisonScaled, meta, growthRows, scale}) {
  const lines = [`# Query and action timings: ${title}`, '',
    `Host: ${meta.host}, SQLite ${meta.sqlite}. Database: ${meta.db} (${meta.rows} rows in ${meta.tables} tables).`,
    `Median of ${meta.runs} runs after a warm-up. Host timings show relative cost; a phone is several times slower.`, ''];
  const total = xs => round(xs.reduce((s, r) => s + (r.ms ?? 0), 0));
  const slow = result.queries.filter(q => q.ms >= SLOW_MS);
  lines.push(`**${result.queries.length} queries, ${total(result.queries)} ms in total; ${slow.length} at ${SLOW_MS} ms or more. ` +
    `${result.actions.length} execute-sql steps, ${total(result.actions)} ms. ${result.indexes.length} indexes in the solution: ` +
    `${result.indexes.filter(i => i.ms != null).length} built on the copy (${total(result.indexes)} ms), ` +
    `${result.indexes.filter(i => i.note === 'already on the device').length} already on the device` +
    `${result.indexes.some(i => i.error) ? `, ${result.indexes.filter(i => i.error).length} failed` : ''}.**`, '');
  const guesses = Object.values(result.guesses ?? {}).sort((a, b) => b.rows - a.rows);
  if (guesses.length) lines.push(`## Guessed parameters (${guesses.length})`, '',
    'For queries that return nothing with the parameter NULL: bound from a column with the same name in the tables the ' +
    'query reads (else another table), taking the value with the most rows (the busiest record). Check the ones the key ' +
    'screens use; pin others with --params params.json ({"name": "value"}).', '',
    ...guesses.slice(0, 25).map(g => `- @${g.name} = ${JSON.stringify(g.value)} (from ${g.from}, ${g.rows} rows)`),
    ...(guesses.length > 25 ? [`- … ${guesses.length - 25} more in the JSON output (\`guesses\`)`] : []), '');
  const states = Object.entries(result.state ?? {});
  if (states.length) lines.push(`## Solution state (${states.length})`, '',
    'Parameters that read one solution-state value (the active project) get one value in every query: the value with the most ' +
    'rows across the columns those queries compare it with, used as stored (padding kept). Pin it with --params ' +
    'params.json ({"<state name>": "value"}).', '',
    ...states.map(([k, v]) => `- ${k} = ${v.none ? 'none found (the compared tables are empty)' : JSON.stringify(v.value)}` +
      (v.given ? ' (from --params)' : v.none ? '' : ` (${v.rows} rows in ${v.from.join(', ')})`) + `, for ${v.queries} quer${v.queries === 1 ? 'y' : 'ies'}`), '');
  const empties = result.queries.filter(q => q.emptyTables && !q.nullParams);
  const emptyRead = [...new Set(result.queries.filter(q => q.emptyTables).flatMap(q => q.emptyTables))].sort();
  if (emptyRead.length) lines.push(`## Empty tables: not measured (${result.queries.filter(q => q.emptyTables).length} queries)`, '',
    `These tables have no rows in this copy: ${emptyRead.join(', ')}. Queries that read only them timed nothing, and a fix to ` +
    'them cannot be timed or checked. Often they sync only after a screen opens (pick a project): open those screens ' +
    '(the screen spec\'s prepare steps), then take the copy again with track-load.py --copy-db.', '',
    ...empties.slice(0, 15).map(q => `- \`${q.where}\``), ...(empties.length > 15 ? [`- … ${empties.length - 15} more`] : []), '');
  const nulls = result.queries.filter(q => q.nullParams);
  if (nulls.length) lines.push(`## No rows on NULL or guessed parameters: not measured (${nulls.length})`, '',
    'These queries compare a value the screen supplies (a barcode, a search box, a selected record) and returned no rows ' +
    'with it NULL (and with a guessed value, where one was found), so their time says nothing. Pass real values with --params params.json ({"name": "value"}) ' +
    'and time them again.', '',
    ...nulls.slice(0, 20).map(q => `- \`${q.where}\`: ${q.unbound.map(n => '@' + n).join(', ')}`),
    ...(nulls.length > 20 ? [`- … ${nulls.length - 20} more`] : []), '');
  for (const [heading, rows] of [['Before and after', comparison], [`Before and after at ${scale}x data`, comparisonScaled]]) {
    if (!rows?.length) continue;
    const moved = rows.filter(r => r.verdict !== 'same');
    lines.push(`## ${heading}`, '', `${moved.filter(r => r.verdict === 'faster').length} faster, ${moved.filter(r => r.verdict === 'SLOWER').length} slower, ${rows.length - moved.length} the same ` +
      '(a move counts at 5 ms, or at 1 ms when the time at least halves or doubles).', '');
    if (moved.length) {
      lines.push('| What | Where | Before | After | Change |', '| --- | --- | ---: | ---: | ---: |');
      for (const r of moved.slice(0, 25)) lines.push(`| ${KIND[r.kind]} | \`${r.where}\` | ${r.before_ms} ms | ${r.after_ms} ms | ${r.change_pct ?? '-'}% ${r.verdict === 'SLOWER' ? '⚠' : ''} |`);
      lines.push('');
    }
  }
  if (growthRows) {
    const faster = growthRows.filter(r => r.verdict === 'GROWS FASTER');
    lines.push(`## Growth at ${scale}x data`, '', `Every table's rows multiplied by ${scale} (${scale > 1 ? `${meta.scaled.linkingKeys} linking keys kept apart per copy` : ''}). ` +
      `${faster.length} item(s) grow faster than the data (more than ${scale * 2}x the time, at ${scale}x data).`, '');
    const shown = growthRows.filter(r => r.verdict !== 'in line').slice(0, 20);
    if (shown.length) {
      lines.push('| What | Where | 1x | ' + scale + 'x | Time | Rows | |', '| --- | --- | ---: | ---: | ---: | ---: | --- |');
      for (const r of shown) lines.push(`| ${KIND[r.kind]} | \`${r.where}\` | ${r.ms_1x} ms | ${r.ms_nx} ms | ${r.time_x ?? '-'}x | ${r.rows_1x ?? '-'} → ${r.rows_nx ?? '-'} | ${r.verdict === 'GROWS FASTER' ? '⚠ grows faster' : r.verdict.startsWith('not measured') ? 'not measured: NULL parameters' : 'rows inflated: a join key the scaling could not separate'} |`);
      lines.push('');
    }
    lines.push('Read the query plan of each item that grows faster (`profile-query.py`) before changing it.', '');
  }
  const table = (heading, rows, cols) => {
    if (!rows.length) return;
    lines.push(`## ${heading}`, '', `| ${cols.map(c => c[0]).join(' | ')} |`, `| ${cols.map(c => c[2] ? '---:' : '---').join(' | ')} |`);
    for (const r of rows) lines.push(`| ${cols.map(c => c[1](r)).join(' | ')} |`);
    lines.push('');
  };
  const note = r => r.error ? `error: ${r.error}` : [r.state && `state: ${Object.entries(r.state).map(([n, k]) => n === k ? n : `${n}=${k}`).join(', ')}`,
    r.emptyTables && 'empty tables', r.sampled?.length && `sampled: ${r.sampled.join(', ')}`,
    r.guessed?.length && `guessed: ${r.guessed.join(', ')}`,
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
    '- Parameters: given in --params, else the solution state they read (one value for every query), else "sampled" (one real value of the column the query compares them to), else "guessed" (the most frequent value of a same-named column), else NULL. A NULL parameter usually returns no rows, so that query\'s time is a lower bound.');
  return lines.join('\n') + '\n';
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const opt = name => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
    const dbPath = opt('--db'), reportPath = opt('--report'), paramsPath = opt('--params'), baselinePath = opt('--baseline');
    const runs = Number(opt('--runs') ?? 5), scale = Number(opt('--scale') ?? 1);
    const file = args[0] || 'build/output.json';
    if (!dbPath) throw new Error('--db <device database> is required (track-load.py --list finds simulator databases)');
    const loaded = fs.statSync(file).isDirectory()
      ? loadSolution(file) : {app: JSON.parse(fs.readFileSync(file, 'utf8')), sources: sdkSources(file)};
    process.removeAllListeners('warning'); // node:sqlite prints an experimental warning on Node 22
    const {DatabaseSync} = await import('node:sqlite');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-time-'));
    const copy = path.join(dir, 'copy.db');
    try {
      try {
        const src = new DatabaseSync(dbPath, {readOnly: true});
        src.exec(`VACUUM INTO '${copy.replace(/'/g, "''")}'`);
        src.close();
      } catch (e) {
        throw new Error(`cannot read ${dbPath} (${e.message}). Copy it with: python3 track-load.py --solution <name> --copy-db <file>`);
      }
      // The device's SQLite accepts "text" as a string literal; match it.
      const db = new DatabaseSync(copy, {enableDoubleQuotedStringLiterals: true});
      const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map(r => r.name);
      const rows = tables.reduce((n, t) => n + db.prepare(`SELECT count(*) AS n FROM "${t.replace(/"/g, '""')}"`).get().n, 0);
      // The runtime's own tables (_syncScope, __migrations) exist before any data does: count data tables only.
      const dataRows = tables.filter(t => !t.startsWith('_') && !t.startsWith('sqlite_'))
        .reduce((n, t) => n + db.prepare(`SELECT count(*) AS n FROM "${t.replace(/"/g, '""')}"`).get().n, 0);
      if (!dataRows) throw new Error(`${dbPath} has no data rows (${tables.length} tables), so nothing can be timed. A copy made with cp ` +
        'leaves the rows in the -wal file: copy it with python3 track-load.py --solution <name> --copy-db <file>, or sign in and let the sync finish.');
      const params = paramsPath ? JSON.parse(fs.readFileSync(paramsPath, 'utf8')) : {};
      const result = timeSolution(loaded.app, db, {params, runs, sources: loaded.sources});
      const meta = {host: `${os.type()} ${os.arch()}, ${os.cpus()[0]?.model ?? 'unknown CPU'}`, sqlite: db.prepare('SELECT sqlite_version() AS v').get().v,
        db: path.basename(dbPath), tables: tables.length, rows, runs, params: Object.keys(params)};
      db.close();
      let growthRows = null;
      if (scale > 1) {
        // A second copy, scaled; the 1x copy above already holds the shipped indexes, so start from it.
        const big = path.join(dir, 'scaled.db');
        const one = new DatabaseSync(copy, {readOnly: true});
        one.exec(`VACUUM INTO '${big.replace(/'/g, "''")}'`);
        one.close();
        const sdb = new DatabaseSync(big, {enableDoubleQuotedStringLiterals: true});
        const {queries, steps} = collect(loaded.app);
        meta.scaled = scaleDatabase(sdb, scale, sqlLiterals([...queries.map(x => x.sql), ...steps.flatMap(x => x.statements)]));
        sdb.exec('ANALYZE');
        const scaledResult = timeSolution(loaded.app, sdb, {params, runs, sources: loaded.sources});
        sdb.close();
        growthRows = growth(result, scaledResult, scale);
        meta.scale = scale;
      }
      const baseline = baselinePath ? JSON.parse(fs.readFileSync(baselinePath, 'utf8')) : null;
      const comparison = baseline ? compareTimings(baseline, result) : null;
      // Compare the scaled runs too, when both sides were scaled by the same factor.
      if (baseline && growthRows && baseline.growth && baseline.meta?.scale !== scale)
        console.error(`Not comparing at ${scale}x: the baseline was scaled ${baseline.meta?.scale ?? 1}x.`);
      const comparisonScaled = baseline?.growth && growthRows && baseline.meta?.scale === scale
        ? compareTimings(atScale(baseline.growth), atScale(growthRows)) : null;
      console.log(JSON.stringify({file, meta, ...result, ...(comparison && {comparison}), ...(comparisonScaled && {comparisonScaled}),
        ...(growthRows && {growth: growthRows})}, null, 2));
      if (reportPath) {
        const title = loaded.app.title ?? loaded.app.name ?? path.basename(path.resolve(file));
        fs.writeFileSync(reportPath, timingsReport({title, result, comparison, comparisonScaled, meta, growthRows, scale}));
        console.error(`Report written to ${reportPath}.`);
      }
      process.exitCode = [...(comparison ?? []), ...(comparisonScaled ?? [])].some(r => r.verdict === 'SLOWER') ? 1 : 0;
    } finally { fs.rmSync(dir, {recursive: true, force: true}); }
  } catch (error) { console.error(`Timing failed: ${error.message}`); process.exitCode = 2; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
