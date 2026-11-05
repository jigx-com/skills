#!/usr/bin/env node
// Dependency-free static performance scan of a generated Jigx solution (build/output.json).
// No network or writes. Findings are leads to measure, not verdicts: confirm each with
// row/byte counts and a query plan before changing anything (references/measuring.md).
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';

function walk(value, visit, path = '$') {
  if (visit(value, path) === false) return;
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) walk(child, visit, `${path}.${key}`);
  }
}

// Blank out SQL string literals, quoted identifiers and comments, keeping offsets stable.
const blank = s => s.replace(/[^\n]/g, ' ');
export function maskSql(sql) {
  return sql.replace(/'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*|\/\*[\s\S]*?\*\//g, blank);
}
const stripComments = sql => sql.replace(/--[^\n]*|\/\*[\s\S]*?\*\//g, blank);
const withoutJsonataStrings = v => v.replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, ' ');

// Normalise an SQL expression so a datasource predicate and an index expression compare
// as SQLite would: case-insensitive, whitespace-free, table alias on `data` ignored.
export function normaliseExpr(expr) {
  // String literals (JSON paths, compared values) stay exact: SQLite matches them byte for byte.
  return stripComments(expr).split(/('(?:''|[^'])*')/).map((part, i) => i % 2 ? part : part
    .replace(/\b[A-Za-z_]\w*\.(data)\b/gi, '$1')
    .replace(/\s+/g, '')
    .replace(/\[([^\]]*)\]/g, '$1')
    .toLowerCase()).join('');
}

function matchParen(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === '(') depth++;
    else if (text[i] === ')' && --depth === 0) return i;
  }
  return -1;
}

// Split on top-level commas.
function splitTop(text) {
  const out = []; let depth = 0, start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '(') depth++;
    else if (text[i] === ')') depth--;
    else if (text[i] === ',' && depth === 0) { out.push(text.slice(start, i)); start = i + 1; }
  }
  out.push(text.slice(start));
  return out.map(x => x.trim()).filter(Boolean);
}

export function parseCreateIndex(statement) {
  const masked = maskSql(statement);
  const m = /\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(IF\s+NOT\s+EXISTS\s+)?([^\s(]+)\s+ON\s+(\[[^\]]+\]|"[^"]+"|[^\s(]+)\s*\(/i.exec(masked);
  if (!m) return null;
  const open = m.index + m[0].length - 1;
  const close = matchParen(masked, open);
  if (close < 0) return null;
  return {
    name: statement.slice(m.index, m.index + m[0].length).match(/INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)/i)[1],
    ifNotExists: Boolean(m[1]),
    table: statement.slice(m.index, m.index + m[0].length).match(/\bON\s+(\[[^\]]+\]|"[^"]+"|[^\s(]+)/i)[1].replace(/^[\["]|[\]"]$/g, ''),
    expressions: splitTop(statement.slice(open + 1, close)).map(normaliseExpr),
  };
}

// Find the outermost function-call expression around each json_extract inside WHERE/ON
// clauses. Grouping parentheses stop the expansion; function calls (LOWER(, COALESCE() extend it.
export function filterPredicates(query) {
  const masked = maskSql(query);
  const regions = [];
  const kw = /\b(WHERE|ON|HAVING)\b/gi;
  for (let m; (m = kw.exec(masked));) {
    let depth = 0, i = m.index + m[0].length;
    for (; i < masked.length; i++) {
      const c = masked[i];
      if (c === '(') depth++;
      else if (c === ')') { if (depth === 0) break; depth--; }
      else if (depth === 0 && /^\s*\b(GROUP\s+BY|ORDER\s+BY|LIMIT|UNION|EXCEPT|INTERSECT|WHERE|JOIN|LEFT|INNER|CROSS|WINDOW)\b/i.test(masked.slice(i, i + 12)) && /\s/.test(masked[i - 1] ?? ' ')) break;
    }
    regions.push([m.index + m[0].length, i]);
  }
  const preds = new Map();
  for (const m of masked.matchAll(/\bjson_extract\s*\(/gi)) {
    const at = m.index;
    if (!regions.some(([a, b]) => at >= a && at < b)) continue;
    let start = at, end = matchParen(masked, at + m[0].length - 1);
    if (end < 0) continue;
    let wrapped = false;
    for (;;) {
      // find the nearest unmatched '(' before start
      let depth = 0, j = start - 1;
      for (; j >= 0; j--) {
        if (masked[j] === ')') depth++;
        else if (masked[j] === '(') { if (depth === 0) break; depth--; }
      }
      if (j < 0) break;
      const fn = /([A-Za-z_]\w*)\s*$/.exec(masked.slice(0, j));
      if (!fn || /^(AND|OR|NOT|IN|WHERE|ON|EXISTS|SELECT|WHEN|THEN|ELSE|HAVING)$/i.test(fn[1])) break;
      const close = matchParen(masked, j);
      if (close < 0) break;
      start = fn.index; end = close; wrapped = true;
    }
    const after = masked.slice(end + 1, end + 12);
    const expr = query.slice(start, end + 1);
    preds.set(normaliseExpr(expr), {
      expr: expr.replace(/\s+/g, ' ').trim(),
      wrapped,
      like: /^\s*(NOT\s+)?LIKE\b/i.test(after),
      multiArm: (expr.match(/json_extract\s*\(/gi) || []).length > 1,
    });
  }
  return [...preds.entries()].map(([norm, p]) => ({norm, ...p}));
}

// The function argument of each $map(array, function) call; the array argument runs once.
// JSONata built-ins that throw on null or on a value of the wrong type, while a missing value is
// safe (they return undefined). Verified with jsonata 1.8.9: $trim(null) T0410, $number("") D3030,
// $toMillis("2026-11-04 10:00:00") D3110 (SQLite's datetime() is not ISO 8601).
const STRICT = /\$(trim|length|uppercase|lowercase|substring|substringBefore|substringAfter|contains|split|replace|pad|number|toMillis|fromMillis|formatNumber|formatInteger|round|abs|floor|ceil|sum|join)\s*\(\s*(@ctx\.(?:components\.[\w-]+\.state\.value|datasources\.[\w-]+\.[\w-]+|current\.item\.[\w-]+))\s*(?=[,)])/g;
export function riskyCalls(expr) {
  const e = withoutJsonataStrings(expr);
  const out = [];
  for (const m of e.matchAll(STRICT)) {
    const [, fn, ref] = m;
    const esc = escapeRe(ref);
    // A component value can be a number or object, so only a type check guards it; a NULL
    // column is guarded by any condition on it (null is falsy).
    const typed = new RegExp(`\\$type\\(\\s*${esc}\\s*\\)`).test(e);
    // $exists(null) is true, so $exists is not a guard.
    const tested = new RegExp(`${esc}\\s*(\\?|\\band\\b|\\bor\\b|!=|=|>|<)`).test(e.replace(m[0], ''));
    if (typed || (!ref.includes('.components.') && tested)) continue;
    out.push({fn, ref});
  }
  return out;
}

function mapBodies(expr) {
  const out = [];
  for (const m of expr.matchAll(/\$map\s*\(/g)) {
    let depth = 0;
    for (let i = m.index + m[0].length; i < expr.length; i++) {
      const c = expr[i];
      if ('([{'.includes(c)) depth++;
      else if (')]}'.includes(c)) { if (depth-- === 0) break; }
      else if (c === ',' && depth === 0) { out.push(expr.slice(i + 1)); break; }
    }
  }
  return out;
}

const asArray = v => Array.isArray(v) ? v : v === undefined || v === null ? [] : [v];
const statementText = s => typeof s === 'string' ? s : (s?.statement ?? s?.sql ?? '');
const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const tableName = entity => String(entity).replace(/^default\//, '').toLowerCase();
// The outer SELECT's column names (null when it selects *), for checking a list query returns id.
export function outerColumns(sql) {
  const masked = maskSql(sql);
  let depth = 0, start = -1, end = -1;
  for (let i = 0; i < masked.length && end < 0; i++) {
    const ch = masked[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    else if (depth === 0 && /\w/.test(ch) && !/\w/.test(masked[i - 1] ?? '')) {
      const word = /^\w+/.exec(masked.slice(i))[0].toUpperCase();
      if (start < 0 && word === 'SELECT') start = i + 6;
      else if (start >= 0 && ['FROM', 'UNION', 'ORDER', 'LIMIT'].includes(word)) end = i;
      i += word.length - 1;
    }
  }
  if (start < 0) return [];
  const cols = [];
  let from = start; depth = 0;
  const stop = end < 0 ? masked.length : end;
  for (let i = start; i <= stop; i++) {
    if (masked[i] === '(') depth++;
    else if (masked[i] === ')') depth--;
    if (i === stop || (depth === 0 && masked[i] === ',')) { cols.push(sql.slice(from, i).trim()); from = i + 1; }
  }
  if (cols.some(c => /(^|\.)\*$/.test(c))) return null;
  return cols.map(c => (/\bAS\s+["\[`]?([\w-]+)["\]`]?$/i.exec(c) ?? /^(?:[\w-]+\.)?["\[`]?(\w+)["\]`]?$/.exec(c))?.[1]).filter(Boolean);
}

// '$.field' literals the rewriter would turn into json_extract: those not passed to a json_* function.
export function shorthandPaths(sql) {
  const found = [];
  for (const m of sql.matchAll(/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'/g)) {
    if (!m[0].startsWith("'$")) continue;
    const before = maskSql(sql.slice(0, m.index));
    let depth = 0, open = -1;
    for (let i = before.length - 1; i >= 0; i--) {
      if (before[i] === ')') depth++;
      else if (before[i] === '(' && depth-- === 0) { open = i; break; }
    }
    if (open < 0 || !/\bjson_\w+\s*$/i.test(before.slice(0, open))) found.push(m[0]);
  }
  return found;
}

// The expression behind a select-list alias (`… AS name`), or the column itself.
export function columnExpr(sql, name) {
  const masked = maskSql(sql);
  const m = new RegExp(`\\bAS\\s+["\\[\`]?${escapeRe(name)}["\\]\`]?(?=\\s*(,|FROM\\b|$))`, 'i').exec(masked);
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
}

// Equality arms of one OR group that test different keys of the same table, e.g.
// `h.id = q.k OR json_extract(h.data, '$.ApptID') = q.k`. SQLite cannot use one index per arm
// for such an OR, so it scans the table for every row it is evaluated for. Returns
// [{keys, perRow}] where perRow means the OR sits in a JOIN ON or a correlated subquery.
// CTEs that compute columns from one table (SELECT json_extract(data, ...) AS x FROM t) and are
// then joined on those columns. SQLite flattens such a CTE, but it does not look rows up through
// an expression index on a column reached this way: the join scans the table for every outer
// row (SCAN t USING INDEX ..., not SEARCH). Joining the table itself lets the index work.
export function cteJoins(sql) {
  const masked = maskSql(sql);
  const ctes = new Map();
  const head = /(?:\bWITH|,)\s*(\w+)\s+AS\s+((?:NOT\s+)?MATERIALIZED\s+)?\(/gi;
  for (const m of masked.matchAll(head)) {
    let depth = 1, i = m.index + m[0].length;
    for (; i < masked.length && depth; i++) depth += masked[i] === '(' ? 1 : masked[i] === ')' ? -1 : 0;
    const body = masked.slice(m.index + m[0].length, i - 1);
    const from = /^\s*SELECT\b([\s\S]*?)\bFROM\s+(\[?[\w/-]+\]?)(?:\s+(?:AS\s+)?(\w+))?\s*([\s\S]*)$/i.exec(body);
    if (!from || /\b(JOIN|GROUP\s+BY|UNION|DISTINCT|LIMIT)\b|json_each|json_tree/i.test(body) || /\bSELECT\b/i.test(from[1]) || /^\s*,/.test(from[4])) continue;
    // Columns computed by an expression: the ones an expression index would have to match.
    const computed = new Set();
    // Over another CTE, a plain column the inner CTE computes is computed too.
    const inner = ctes.get(from[2].toLowerCase());
    let depth2 = 0, part = '';
    for (const ch of from[1] + ',') {
      if (ch === '(') depth2++; else if (ch === ')') depth2--;
      if (ch === ',' && depth2 === 0) {
        const a = /^\s*([\s\S]*?)\s+AS\s+(\w+)\s*$/i.exec(part);
        const bare = /^\s*(?:\w+\.)?(\w+)\s*$/.exec(a ? a[1] : part);
        if (a && /\(/.test(a[1])) computed.add(a[2].toLowerCase());
        else if (bare && inner?.computed.has(bare[1].toLowerCase())) computed.add((a ? a[2] : bare[1]).toLowerCase());
        part = '';
      } else part += ch;
    }
    // AS MATERIALIZED builds it once, and SQLite gives a built CTE an automatic index: not affected.
    const built = m[2] && !/NOT/i.test(m[2]);
    ctes.set(m[1].toLowerCase(), {table: inner?.table ?? from[2].replace(/^\[|\]$/g, ''), computed, built});
  }
  const out = [];
  for (const j of masked.matchAll(/\bJOIN\s+(\w+)(?:\s+AS)?\s+(\w+)\s+ON\b([\s\S]*?)(?=\b(?:LEFT|INNER|CROSS|JOIN|WHERE|GROUP|ORDER|LIMIT|UNION)\b|\)\s*(?:,|SELECT)|$)/gi)) {
    const cte = ctes.get(j[1].toLowerCase());
    if (!cte || cte.built) continue;
    const cols = [...j[3].matchAll(new RegExp(`\\b${j[2]}\\.(\\w+)`, 'gi'))].map(c => c[1].toLowerCase()).filter(c => cte.computed.has(c));
    if (cols.length) out.push({cte: j[1], alias: j[2], table: cte.table, columns: [...new Set(cols)]});
  }
  return out;
}

export function orAcrossKeys(sql) {
  const masked = maskSql(sql);
  const out = [], seen = new Set();
  const KEY = /(?:\b(\w+)\.id\b|(?<![\w.])id\b|json_extract\s*\(\s*(?:(\w+)\.)?data\s*,)/i;
  for (const m of masked.matchAll(/\bOR\b/gi)) {
    // The enclosing parenthesised group, or the whole statement.
    let start = 0, end = masked.length;
    for (let i = m.index, depth = 0; i >= 0; i--) {
      if (masked[i] === ')') depth++;
      else if (masked[i] === '(' && depth-- === 0) { start = i + 1; break; }
    }
    for (let i = m.index, depth = 0; i < masked.length; i++) {
      if (masked[i] === '(') depth++;
      else if (masked[i] === ')' && depth-- === 0) { end = i; break; }
    }
    if (seen.has(start)) continue;
    seen.add(start);
    // Only the condition: after the group's last top-level WHERE/ON/HAVING, before ORDER/GROUP/LIMIT.
    for (let i = start, depth = 0; i < end; i++) {
      if (masked[i] === '(') depth++;
      else if (masked[i] === ')') depth--;
      else if (depth === 0 && /\W/.test(masked[i - 1] ?? ' ')) {
        const kw = /^(WHERE|ON|HAVING)\b/i.exec(masked.slice(i, i + 7));
        if (kw && i < m.index) start = i + kw[0].length;
        else if (i > m.index && /^(ORDER\s+BY|GROUP\s+BY|LIMIT|UNION)\b/i.test(masked.slice(i, i + 9))) { end = i; break; }
      }
    }
    const arms = [];
    let from = start;
    for (let i = start, depth = 0; i <= end; i++) {
      if (masked[i] === '(') depth++;
      else if (masked[i] === ')') depth--;
      const atOr = depth === 0 && /^OR\b/i.test(masked.slice(i, i + 3)) && /\W/.test(masked[i - 1] ?? ' ');
      if (i === end || atOr) { arms.push([from, i]); from = i + 2; }
    }
    if (arms.length < 2) continue;
    const keys = new Map();
    for (const [a, b] of arms) {
      const arm = masked.slice(a, b);
      if (/\b(AND|SELECT)\b/i.test(arm) || (arm.match(/=/g) ?? []).length !== 1) continue;
      const sides = arm.split('=');
      for (const [n, side] of sides.entries()) {
        const k = KEY.exec(side);
        if (!k) continue;
        // A lookup compares the key with a parameter or another row's column, not a constant.
        if (!/[@:$]\w|[A-Za-z_]\w*\s*\.\s*\w|json_extract|\bid\b/i.test(sides[1 - n] ?? '')) break;
        const alias = (k[1] ?? k[2] ?? '').toLowerCase();
        const off = a + arm.indexOf(side);
        const text = normaliseExpr(sql.slice(off, off + side.length).trim().replace(/^\(+/, ''));
        keys.set(alias, new Set([...(keys.get(alias) ?? []), text]));
        break;
      }
    }
    const best = [...keys.values()].find(k => k.size >= 2);
    if (!best) continue;
    // Per row: the nearest clause keyword is ON, or the OR is inside a (SELECT …) that is not a CTE body.
    const before = masked.slice(0, m.index);
    const clause = [...before.matchAll(/\b(WHERE|ON)\b/gi)].pop()?.[1]?.toUpperCase();
    const stack = [];
    for (let i = 0; i < m.index; i++) {
      if (masked[i] === '(') stack.push(/^\s*SELECT\b/i.test(masked.slice(i + 1, i + 40)) && !/\bAS\s*$/i.test(masked.slice(Math.max(0, i - 6), i)));
      else if (masked[i] === ')') stack.pop();
    }
    out.push({keys: [...best], perRow: clause === 'ON' || stack.some(Boolean)});
  }
  return out;
}

// ORDER BY terms that read a JSON field, as normalised expressions.
export function orderByTerms(sql) {
  const masked = maskSql(sql);
  const terms = [];
  for (const m of masked.matchAll(/\bORDER\s+BY\b/gi)) {
    let i = m.index + m[0].length, depth = 0, from = i;
    for (; i <= masked.length; i++) {
      const ch = masked[i];
      if (ch === '(') depth++;
      else if (ch === ')') { if (depth === 0) break; depth--; }
      const stop = i === masked.length || (depth === 0 && /^(LIMIT|OFFSET|UNION)\b/i.test(masked.slice(i, i + 6)));
      if (stop || (depth === 0 && ch === ',')) {
        const term = sql.slice(from, i).replace(/\s+(ASC|DESC)\b.*$/is, '').replace(/\s+COLLATE\s+\w+/i, '').trim();
        if (/json_extract/i.test(term)) terms.push(normaliseExpr(term));
        from = i + 1;
        if (stop) break;
      }
    }
  }
  return terms;
}

const MB = 1024 * 1024;
const size = st => `${st.rows} rows, ${(st.bytes / MB).toFixed(1)} MB`;

// Priority, section (A-H in references/recipe-index.md) and recommended fix for each finding.
export const RULES = {
  'execute-sql-entities': {priority: 'high', section: 'B', fix: 'Add entities: with a table nothing binds to, e.g. [default/debug].'},
  'ddl-ungated': {priority: 'high', section: 'B', fix: 'Gate each CREATE INDEX on its table existing (read sqlite_master into a datasource), or keep a real entity for tables that may not exist yet (references/indexes-and-execute-sql.md).'},
  'ddl-invalidates-datasources': {priority: 'high', section: 'B', fix: 'List a table nothing binds to (default/debug) in entities:, after gating every statement on its table existing.'},
  'filter-inside-map': {priority: 'high', section: 'D', fix: 'Key the view into a map once ($merge of $map) and look up by id inside the loop.'},
  'per-row-datasource-read': {priority: 'medium', section: 'C', fix: 'Resolve the datasource once in expressions: or the list data:, and read @ctx.current.item.* in the item.'},
  'uncapped-search-list': {priority: 'medium', section: 'C', fix: 'Add a LIMIT (e.g. 200) and union a pinned arm for the current selection.'},
  'unindexed-predicate': {priority: 'low', section: 'B', fix: 'If the table can grow, ship an expression index with exactly this text, then confirm with check-plans.py.'},
  'wrapped-predicate': {priority: 'low', section: 'B', fix: 'Filter on one indexable json_extract expression (store a normalised key if needed) and index it.'},
  'rewrite-query-shorthand': {priority: 'high', section: 'B', fix: "Write every '$.field' as json_extract(data, '$.field') (select list and WHERE), or remove rewriteQuery: false."},
  'list-no-id': {priority: 'medium', section: 'C', fix: 'Select a unique id column (e.g. SELECT id, … or … AS id) so the list keys rows by it (DS-02).'},
  'list-item-instance-id': {priority: 'medium', section: 'C', fix: 'Remove instanceId from the list item; the list sets it from each row id (CO-04).'},
  'list-custom-component-item': {priority: 'medium', section: 'C', fix: 'Use a built-in list item; each custom component adds renderer wrappers per row (CO-01).'},
  'component-list-for-data': {priority: 'low', section: 'C', fix: 'Use jig.list, which virtualises, for a datasource list that can exceed about ten rows (CO-02).'},
  'list-dynamic-height': {priority: 'low', section: 'C', fix: 'Use fixed-height items; hasDynamicHeight measures every item (CO-03).'},
  'rewrite-query-on': {priority: 'low', section: 'B', fix: "Write json_extract in full and set rewriteQuery: false (DS-03); small gain, do it after index and list fixes."},
  'cte-join-no-index': {priority: 'medium', section: 'B', fix: 'Join the table itself (LEFT JOIN customers AS c ON trim(c.id) = …) and write the expressions in the ON clause, so the expression index is used; check the plan says SEARCH, and that the result is unchanged (profile-query.py --compare).'},
  'or-across-keys': {priority: 'high', section: 'B', fix: 'Split the OR into one UNION branch per key, each with a matching index; adding indexes alone does not help while the OR stays.'},
  'order-by-unindexed': {priority: 'low', section: 'B', fix: 'If the list can grow, ship an expression index on exactly this sort expression.'},
  'watched-table-unread': {priority: 'medium', section: 'E', fix: 'Remove the table from entities:; the datasource re-runs on every change to it.'},
  'datasource-no-id': {priority: 'low', section: 'C', fix: "Select a unique id column; without one the runtime hashes every row ('Missing ID … using hash function')."},
  'heavy-focus': {priority: 'medium', section: 'E', fix: 'Sync on sign-in, on a schedule or on pull-to-refresh; if it must run on focus, gate it with when: (e.g. only when data is older than N minutes).'},
  'table-change-unguarded': {priority: 'medium', section: 'F', fix: 'Add a cheap when: guard (a state flag, not a datasource count) so the handler does nothing outside the moment it is needed.'},
  'table-change-hot-guard': {priority: 'medium', section: 'F', fix: 'Gate on a cheap flag first (e.g. sync in progress), and move datasource counts after it; this guard runs on every status write during sync.'},
  'double-scheduled-action': {priority: 'medium', section: 'F', fix: 'Start the action from one place only (the trigger or the direct call), and make its guard reject a run already in progress.'},
  'sequential-sync-steps': {priority: 'medium', section: 'F', fix: 'Group the calls by which local tables they read and run each group in parallel waves (jigx sync-orchestration.md); keep sequential only steps that need an earlier step or log out.'},
  'file-transfer-sequential': {priority: 'medium', section: 'F', fix: "Set processingType: parallel (or async) so one file does not block every command queued behind it."},
  'heavy-expression': {priority: 'medium', section: 'D', fix: 'Filter once: bind the datasource rows to a variable ($rows := …) or move the counts into the SQL, then reuse the result.'},
  'solution-state-in-global-datasource': {priority: 'medium', section: 'D', fix: 'Pass the value from the screen instead; a global datasource re-runs for everyone whenever solution state changes (ST-02).'},
  'solution-state-in-handler': {priority: 'low', section: 'D', fix: 'Each write re-evaluates every expression that reads solution state; write only when the value changes, or keep it in jig state (ST-01).'},
  'expression-may-throw': {priority: 'medium', section: 'D', fix: "Guard the value (tested guards in jigx jsonata-validation-formatting.md): `x ? $trim($string(x)) : ''` for a component value, COALESCE the column in the SQL, ISO 8601 dates from SQL (or a matching picture) before $toMillis, and a $type check before $match and $number. $exists does not guard null."},
  'script-console-log': {priority: 'medium', section: 'H', fix: 'Remove console.log from expression functions; each call writes to the device log.'},
  'simple-script-function': {priority: 'low', section: 'D', fix: 'Write it as a JSONata expression (built-ins like $formatNumber, $substringBefore, $boolean, object lookups); keep JavaScript for logic JSONata cannot express clearly.'},
  'unused-script-function': {priority: 'low', section: 'E', fix: 'Delete it; the script ships and loads with the solution.'},
  'index-not-idempotent': {priority: 'medium', section: 'B', fix: 'Use CREATE INDEX IF NOT EXISTS.'},
  'datasource-not-equals': {priority: 'low', section: 'D', fix: "Use $count(ds[field='X']) = 0 so the no-row case passes."},
  'unused-datasource': {priority: 'medium', section: 'E', fix: 'Delete it, or move it into the one screen that needs it. A global datasource re-runs whenever its tables change.'},
  'synced-table-unread': {priority: 'medium', section: 'F', fix: 'Stop syncing it, or sync it on demand where it is used. First check scripts and functions that may read it.'},
  'sync-full-pull': {priority: 'low', section: 'F', fix: 'Pull by modified date (filter on the newest local value) and merge; keep full pulls for small reference tables (jigx references/sync-orchestration.md).'},
  'sync-no-select': {priority: 'low', section: 'F', fix: 'Add $select with only the fields the app reads, so each sync downloads and stores less.'},
  'large-synced-table': {priority: 'medium', section: 'F', fix: 'Pull less: filter to the user (branch, assignee, date window), select fewer fields, or sync the table on demand.'},
};
export const PRIORITIES = ['high', 'medium', 'low'];
const DELTA = /modif|lastmod|last_mod|since|delta|changed|updated/i;
const FILTERED = /filter|where|query|\$top/i;

const throwMessage = ({fn, ref}) =>
  fn === 'number' ? `$number(${ref}) throws on null, "" and text like "12,5" (D3030).`
  : /^(toMillis|fromMillis)$/.test(fn) ? `$${fn}(${ref}) throws on null, "" and SQLite's "2026-11-04 10:00:00" (D3110).`
  : `$${fn}(${ref}) throws when the value is null or not the expected type (T0410).`;

// Readable location: the source file (YAML folder) or the solution section, then the path inside it.
const KINDS = ['jigs', 'datasources', 'actions', 'functions', 'components', 'databases'];
export function where(path, sources = {}) {
  if (sources[path]) return sources[path];
  const m = /^\$\.([\w-]+)\.([^.]+)(.*)$/.exec(path);
  if (!m || !KINDS.includes(m[1])) return `index › ${path.replace(/^\$\.?/, '')}`;
  const file = sources[`$.${m[1]}.${m[2]}`] ?? `${m[1]}/${m[2]}`;
  const rest = m[3].replace(/^\./, '');
  return rest ? `${file} › ${rest}` : file;
}

// stats: optional {table: {rows, bytes}} from a device database (tableStats), to size sync findings.
export function checkPerformance(app, {stats = null, sources = {}} = {}) {
  sources = {...sources}; // script functions add their own locations
  const findings = [];
  const add = (code, path, message, {severity = 'warning', priority} = {}) => findings.push({
    severity, priority: priority ?? RULES[code].priority, section: RULES[code].section, code, path,
    where: where(path, sources), message, fix: RULES[code].fix});

  const datasources = [], ddlActions = [], indexes = [], syncs = [], calls = {}, listSources = new Set(), listItems = new Map();
  const refs = new Map(), readTables = new Set();
  let sqlText = '';
  walk(app, (node, path) => {
    if (typeof node === 'string') {
      for (const r of node.matchAll(/@ctx\.datasources\.([\w-]+)/g)) refs.set(r[1], [...(refs.get(r[1]) ?? []), path]);
      return;
    }
    if (!node || typeof node !== 'object') return;
    // SDK builds copy a global datasource into each jig that uses it, tagged with its datasourceId.
    if (typeof node.datasourceId === 'string') refs.set(node.datasourceId, [...(refs.get(node.datasourceId) ?? []), path]);
    if (typeof node.type === 'string' && node.type.startsWith('datasource.')) {
      for (const e of asArray(node.options?.entities)) readTables.add(tableName(typeof e === 'string' ? e : e?.entity));
      if (typeof node.options?.query === 'string') sqlText += '\n' + node.options.query;
    }
    if (node.type === 'datasource.sqlite' && typeof node.options?.query === 'string') datasources.push({path, node});
    if (node.type === 'jig.list' || node.type === 'component.list') checkList(node, path);
    if (node.type === 'action.execute-action' && typeof node.options?.action === 'string') {
      const from = path.startsWith('$.onTableChanged.') ? 'handler' : 'direct';
      (calls[node.options.action] ??= {handler: [], direct: []})[from].push(path);
    }
    if (/^action\.execute-entit(y|ies)$/.test(node.type) && /download|upload/i.test(node.options?.method ?? '') && !node.options?.processingType)
      add('file-transfer-sequential', path, `A file ${node.options.method} runs with the default sync processing, so it blocks the commands queued behind it.`);
    if (node.type === 'action.action-list' && node.options?.isSequential) {
      const n = asArray(node.options.actions).filter(a => a?.type === 'action.sync-entities').length;
      if (n >= 4) add('sequential-sync-steps', path, `${n} sync calls run one after another; network waits add up.`);
    }
    if (node.type === 'action.set-state' && /^=?\s*@ctx\.solution\.state\./.test(String(node.options?.state ?? '')) && path.startsWith('$.onTableChanged.'))
      add('solution-state-in-handler', path, `A table-change handler writes ${String(node.options.state).replace(/^=\s*/, '')}.`);
    if (/^\$\.datasources\.[^.]+$/.test(path) && /@ctx\.solution\.state\./.test(JSON.stringify(node.options?.queryParameters ?? node.options?.parameters ?? {})))
      add('solution-state-in-global-datasource', path, 'A global datasource takes solution state as a query parameter.');
    if (node.type === 'action.execute-sql') {
      ddlActions.push({path, node});
      sqlText += '\n' + asArray(node.options?.statements ?? node.statements).map(statementText).join('\n');
    }
    if (node.type === 'action.sync-entities') asArray(node.options?.entities).forEach((e, i) => {
      const entity = typeof e === 'string' ? e : e?.entity;
      if (entity && !String(entity).startsWith('=')) syncs.push({path: `${path}.options.entities.${i}`, table: tableName(entity), entry: typeof e === 'object' ? e : {}});
    });
    // Only a list's item: template renders per row; a list-item placed on its own renders once.
    if (node.type === 'component.list-item' && /\.item$/.test(path)) {
      const reads = new Set();
      walk(node, value => {
        if (typeof value === 'string' && value.startsWith('=')) {
          for (const r of withoutJsonataStrings(value).matchAll(/@ctx\.datasources\.([\w-]+)/g)) reads.add(r[1]);
        }
      });
      if (reads.size) add('per-row-datasource-read', path,
        `List item reads ${[...reads].join(', ')} per rendered row (one live subscription each).`);
      return;
    }
    for (const [key, value] of Object.entries(node)) {
      if (typeof value !== 'string' || !value.startsWith('=')) continue;
      const expr = withoutJsonataStrings(value);
      if (/@ctx\.datasources\.[\w-]+(?:\.[\w-]+)+\s*!=/.test(expr))
        add('datasource-not-equals', `${path}.${key}`, "Comparison against an undefined field is false, so `ds.field != 'X'` blocks the no-row case.");
      if (mapBodies(expr).some(body => /@ctx\.datasources\.[\w-]+\s*\[/.test(body)))
        add('filter-inside-map', `${path}.${key}`, 'A datasource filter inside $map walks the view once per element (O(n²)).');
      for (const r of new Map(riskyCalls(value).map(r => [r.fn + r.ref, r])).values()) {
        const grade = throwRisk(r, path);
        if (grade) add('expression-may-throw', `${path}.${key}`, throwMessage(r), {priority: grade});
      }
      const filters = (expr.match(/@ctx\.datasources\.[\w-]+\s*\[|\$filter\(\s*@ctx\.datasources/g) ?? []).length;
      if (filters >= 10) add('heavy-expression', `${path}.${key}`, `One expression filters datasources ${filters} times each time it is evaluated.`);
    }
  });

  // How likely a strict call is to throw: a component value can be any type (the errors seen in
  // device logs), a list row's column can be NULL for some rows. A column the query COALESCEs is
  // never NULL.
  function throwRisk({fn, ref}, at) {
    // A component holds what the user typed or picked; a SQLite datetime is not ISO 8601.
    if (ref.includes('.components.') || /^(toMillis|fromMillis)$/.test(fn)) return 'medium';
    const col = ref.split('.').pop();
    if (/^id$/i.test(col)) return null;
    let ds;
    if (ref.startsWith('@ctx.current.item.')) ds = [...listItems].filter(([p]) => at.startsWith(p)).pop()?.[1];
    else {
      const name = ref.split('.')[2];
      const owner = /^\$\.(jigs|components)\.([^.]+)/.exec(at);
      ds = (owner && app[owner[1]]?.[owner[2]]?.datasources?.[name]) ?? app.datasources?.[name];
    }
    if (ds?.type === 'datasource.sqlite' && typeof ds.options?.query === 'string' && /^\s*(\w+\s*\(\s*)*(COALESCE|IFNULL)\s*\(/i.test(columnExpr(ds.options.query, col) ?? '')) return null;
    return ref.startsWith('@ctx.current.item.') ? 'medium' : 'low';
  }

  function checkList(node, path) {
    const inJig = node.type === 'jig.list';
    const data = inJig ? node.data : node.options?.data;
    const item = inJig ? node.item : node.options?.item;
    const itemPath = `${path}${inJig ? '' : '.options'}.item`;
    if (item && (item.instanceId !== undefined || item.options?.instanceId !== undefined))
      add('list-item-instance-id', itemPath, 'The list item sets instanceId, which overrides the id the list keys each row by.');
    if (item?.type === 'component.custom-component')
      add('list-custom-component-item', itemPath, 'Each row renders a custom component.');
    if (item?.options?.hasDynamicHeight ?? node.options?.hasDynamicHeight)
      add('list-dynamic-height', path, 'hasDynamicHeight makes the list measure every item.');
    const name = /^=\s*@ctx\.datasources\.([\w-]+)\s*(\[[^\]]*\])?\s*$/.exec(typeof data === 'string' ? data : '')?.[1];
    if (!name) return;
    const owner = /^\$\.(jigs|components)\.([^.]+)/.exec(path);
    const ds = (owner && app[owner[1]]?.[owner[2]]?.datasources?.[name]) ?? app.datasources?.[name];
    if (ds?.type !== 'datasource.sqlite' || typeof ds.options?.query !== 'string') return;
    listSources.add(ds);
    listItems.set(`${path}${inJig ? '' : '.options'}.item`, ds);
    if (!inJig) add('component-list-for-data', path, `component.list renders every row of ${name} at once.`);
    const cols = outerColumns(ds.options.query);
    if (cols && !cols.some(c => c.toLowerCase() === 'id'))
      add('list-no-id', path, `The list's datasource ${name} does not select an id column, so rows have no stable key.`);
  }

  const boundEntities = new Set();
  for (const {node} of datasources) for (const e of asArray(node.options?.entities)) boundEntities.add(typeof e === 'string' ? e : e?.entity);

  for (const {path, node} of ddlActions) {
    const opts = node.options ?? node;
    const entities = asArray(opts.entities ?? node.entities);
    const statements = asArray(opts.statements ?? node.statements).map(statementText);
    if (!entities.length) add('execute-sql-entities', path, 'action.execute-sql has no entities: array, so the action fails on shape before any statement runs.', {severity: 'error'});
    // Writes that rebuild rows (INSERT … SELECT) join CTEs the same way datasources do.
    for (const [i, sql] of statements.entries()) for (const c of cteJoins(sql))
      add('cte-join-no-index', `${path}.options.statements.${i}`,
        `Joins ${c.cte} on ${c.columns.map(x => `${c.alias}.${x}`).join(', ')}, columns the CTE computes from ${c.table}: no index is used, so ${c.table} is scanned for every row the statement writes.`);
    const created = statements.map(parseCreateIndex).filter(Boolean);
    if (!created.length) continue;
    indexes.push(...created.map(c => ({...c, path})));
    const names = entities.map(e => typeof e === 'string' ? e : e?.entity);
    const real = names.filter(e => boundEntities.has(e));
    // One table per step is the documented trade-off (the entity also creates a missing table): medium.
    if (real.length) add('ddl-invalidates-datasources', path,
      `Index DDL lists ${real.join(', ')} in entities:, which re-runs every datasource bound to them (~6.6 s measured on device).`,
      {priority: real.length === 1 ? 'medium' : 'high'});
    if (created.some(c => !c.ifNotExists)) add('index-not-idempotent', path, 'CREATE INDEX without IF NOT EXISTS fails the step on a re-run.');
    // A table listed in entities: is created if missing, so only unlisted tables can fail.
    const listed = new Set(names.filter(Boolean).map(tableName));
    const unlisted = [...new Set(created.map(c => tableName(c.table)))].filter(t => !listed.has(t));
    if (node.when === undefined && opts.when === undefined && unlisted.length) add('ddl-ungated', path,
      `CREATE INDEX on ${unlisted.join(', ')} is not gated on the table existing, nor listed in entities:. One "no such table" on a fresh install rolls back every statement in the step.`);
  }

  const indexed = new Set(indexes.flatMap(i => i.expressions));
  for (const {path, node} of datasources) {
    const query = node.options.query;
    // An index matters once a table can grow: low unless the device data shows 1000+ rows.
    const rows = Math.max(0, ...asArray(node.options.entities).map(e => stats?.[tableName(typeof e === 'string' ? e : e?.entity)]?.rows ?? 0));
    const sized = rows >= 1000 ? {priority: 'medium'} : {};
    const masked = maskSql(query);
    const shorthand = node.options.rewriteQuery === false ? shorthandPaths(query) : [];
    if (shorthand.length) add('rewrite-query-shorthand', `${path}.options.query`,
      `rewriteQuery: false, but ${[...new Set(shorthand)].join(', ')} is shorthand: the query returns the literal text, not the field.`, {severity: 'error'});
    if (node.options.rewriteQuery !== false) add('rewrite-query-on', `${path}.options`, 'rewriteQuery is on, so the query is rewritten before its first run.');
    if (/\bLIKE\b/i.test(masked) && /'%'\s*\|\|/.test(stripComments(query)) && !/\bLIMIT\b/i.test(masked))
      add('uncapped-search-list', `${path}.options.query`, "Contains-search (LIKE '%' || …) with no LIMIT scans, sorts and renders every row.");
    for (const p of filterPredicates(query)) {
      if (p.like) continue; // a leading-wildcard LIKE cannot use an index anyway
      if (p.multiArm && p.wrapped) {
        add('wrapped-predicate', `${path}.options.query`, `${p.expr} wraps several json_extract arms in functions, so no index on either arm can be used.`, sized);
        continue;
      }
      if (!indexed.has(p.norm)) add('unindexed-predicate', `${path}.options.query`, `No shipped CREATE INDEX matches ${p.expr}${rows ? ` (${rows} rows)` : ''}.`, sized);
    }
  }

  for (const {path, node} of datasources) {
    const query = node.options.query;
    const rows = Math.max(0, ...asArray(node.options.entities).map(e => stats?.[tableName(typeof e === 'string' ? e : e?.entity)]?.rows ?? 0));
    for (const o of orAcrossKeys(query)) {
      // With an index on every arm SQLite can use one index per arm; any unindexed arm makes it scan.
      const missing = o.keys.filter(k => !/^(\w+\.)?id$/i.test(k) && !indexed.has(k));
      if (missing.length) add('or-across-keys', `${path}.options.query`,
        `OR across ${o.keys.join(', ')}${o.perRow ? ', evaluated per row (JOIN ON or a correlated subquery)' : ''}; no index on ${missing.join(', ')}, so SQLite scans.`,
        {priority: o.perRow || rows >= 1000 ? 'high' : 'medium'});
    }
    for (const c of cteJoins(query))
      add('cte-join-no-index', `${path}.options.query`,
        `Joins ${c.cte} on ${c.columns.map(x => `${c.alias}.${x}`).join(', ')}, columns the CTE computes from ${c.table}: no index is used, so ${c.table} is scanned for every row${listSources.has(node) ? ' of this list' : ''}.`,
        listSources.has(node) || rows >= 1000 ? {priority: 'high'} : {});
    for (const t of new Set(orderByTerms(query)))
      if (!indexed.has(t)) add('order-by-unindexed', `${path}.options.query`, `ORDER BY ${t} has no matching index${rows ? ` (${rows} rows)` : ''}.`, rows >= 1000 ? {priority: 'medium'} : {});
    const sqlLower = maskSql(query).toLowerCase().replace(/\s+/g, ' ');
    const text = query.toLowerCase();
    const watched = asArray(node.options.entities).map(e => typeof e === 'string' ? e : e?.entity).filter(e => e && !String(e).startsWith('='));
    const unread = watched.filter(e => !new RegExp(`(^|[^\\w-])(default/)?${escapeRe(tableName(e))}([^\\w-]|$)`).test(text));
    // A query that reads none of its entities uses a placeholder (e.g. dummy) that never changes.
    if (unread.length < watched.length)
      for (const entity of unread) add('watched-table-unread', `${path}.options.entities`, `Watches ${entity}, which the query does not read.`);
    // Lists have their own rule; skip single-row and aggregate reads, which need no key.
    const cols = outerColumns(query);
    const single = /\bLIMIT\s+1\b\s*;?\s*$/i.test(sqlLower.trim()) || (cols && cols.length && /^\s*select\s+(count|sum|max|min|avg|total|group_concat|json_group_array|json_group_object)\s*\(/i.test(sqlLower.replace(/^.*?\)\s*select/s, 'select')));
    if (cols && !cols.some(c => c.toLowerCase() === 'id') && !single && !listSources.has(node))
      add('datasource-no-id', path, 'The datasource does not select an id column.');
  }

  // Table-change handlers run on every write to their table; system tables change all through a sync.
  const HOT = new Set(['_syncstatus', '_syncscope', '_commandqueue', '_filestatus']);
  asArray(app.onTableChanged).forEach((h, i) => {
    const table = String(h?.table ?? asArray(h?.tables)[0] ?? '');
    const guard = h?.when ?? h?.action?.when;
    const p = `$.onTableChanged.${i}`;
    if (guard === undefined) add('table-change-unguarded', p, `Every change to ${table} runs this handler's ${h?.action?.options?.actions?.length ?? 1} step(s) with no when: guard.`,
      {priority: HOT.has(table.toLowerCase()) ? 'high' : 'medium'});
    else if (HOT.has(table.toLowerCase()) && /@ctx\.datasources\./.test(String(guard)))
      add('table-change-hot-guard', p, `The guard on ${table} reads datasources, and runs on every status write during sync.`);
  });
  // An action started by a handler on table T, and also directly by an action that writes T: one write
  // fires both (acumatica#444 scheduled every PDF download twice this way).
  const writes = (node, t) => {
    let hit = false;
    walk(node, v => {
      if (hit || !v || typeof v !== 'object') return;
      if (/^action\.(sync-entities|execute-sql|execute-entit(y|ies))$/.test(v.type ?? ''))
        hit = [...asArray(v.options?.entities), v.options?.entity].some(e => e && tableName(typeof e === 'string' ? e : e.entity ?? '') === t);
    });
    return hit;
  };
  asArray(app.onTableChanged).forEach((h, i) => {
    const t = tableName(String(h?.table ?? asArray(h?.tables)[0] ?? ''));
    for (const [id, c] of Object.entries(calls)) {
      if (!c.handler.some(p => p.startsWith(`$.onTableChanged.${i}.`)) || !app.actions?.[id]) continue;
      const callers = [...new Set(c.direct.map(p => /^\$\.actions\.([^.]+)/.exec(p)?.[1]).filter(a => a && a !== id && app.actions[a] && writes(app.actions[a], t)))];
      if (callers.length) add('double-scheduled-action', `$.actions.${id}`,
        `${id} is started by the ${h.table} table-change handler and directly by ${callers.join(', ')}, which writes ${h.table}; one write runs it twice.`);
    }
  });

  // onFocus runs every time the screen is shown.
  const syncs_ = (node, depth = 0, seen = new Set()) => {
    let found = false;
    walk(node, v => {
      if (found || !v || typeof v !== 'object') return false;
      if (v.when !== undefined && v !== node) return false; // a guarded branch is fine
      if (v.type === 'action.sync-entities') { found = true; return false; }
      const target = v.type === 'action.execute-action' && v.options?.action;
      if (typeof target === 'string' && depth < 3 && !seen.has(target) && app.actions?.[target]) {
        seen.add(target);
        if (syncs_(app.actions[target].action ?? app.actions[target], depth + 1, seen)) found = true;
      }
    });
    return found;
  };
  for (const [name, jig] of Object.entries(app.jigs ?? {}))
    if (jig?.onFocus && jig.onFocus.when === undefined && syncs_(jig.onFocus))
      add('heavy-focus', `$.jigs.${name}.onFocus`, 'Opening the screen runs a sync every time, with no when: guard.');

  // Expression scripts: JSONata for simple logic, JavaScript for what JSONata cannot express clearly.
  const allText = JSON.stringify({...app, scripts: undefined});
  const scriptRoot = Object.values(sources).some(v => String(v).startsWith('src/')) ? 'src/scripts' : 'scripts';
  const scriptText = Object.values(app.scripts ?? {}).flatMap(g => Object.values(g ?? {})).filter(t => typeof t === 'string').join('\n');
  for (const [group, files] of Object.entries(app.scripts ?? {})) for (const [file, text] of Object.entries(files ?? {})) {
    if (typeof text !== 'string') continue;
    const mod = file.replace(/\.js$/, '');
    for (const f of scriptFunctions(text)) {
      const call = new RegExp(`\\$${escapeRe(mod)}\\.${f.name}\\s*\\(`);
      const calledFromApp = call.test(allText);
      const p = `$.scripts.${group}.${mod}.${f.name}`;
      sources[p] ??= `${scriptRoot}/${group}/${file} › ${f.name}`;
      if (!calledFromApp && !new RegExp(`\\b${f.name}\\s*\\(`).test(scriptText.replace(new RegExp(`function\\s+${f.name}\\b`, 'g'), ''))) {
        add('unused-script-function', p, `${f.name} is not called by any expression or script.`);
        continue;
      }
      if (/\bconsole\.(log|info|debug)\s*\(/.test(f.body))
        add('script-console-log', p, `${f.name} logs on every call${calledFromApp ? ' from an expression' : ''}.`, calledFromApp ? {} : {priority: 'low'});
      // Simple: short, no loops, no error handling, no regular expressions or dates.
      const complex = /\b(for|while|try|switch|new\s+(RegExp|Date)|Intl)\b|\.(forEach|map|reduce|filter|find|some|every|sort)\s*\(|\/[^/\n]+\/[gimsuy]*\.(test|exec)|\.match\s*\(/.test(f.body);
      if (calledFromApp && f.lines <= 15 && !complex)
        add('simple-script-function', p, `${f.name} (${f.lines} lines) is simple enough to write in JSONata.`);
    }
  }

  // Unnecessary loads: global datasources nothing reads.
  for (const name of Object.keys(app.datasources ?? {})) {
    const own = `$.datasources.${name}`;
    if (!(refs.get(name) ?? []).some(p => p !== own && !p.startsWith(own + '.')))
      add('unused-datasource', own, `Global datasource ${name} is not referenced by any screen, expression or action.`);
  }

  // Sync: tables nothing reads, full pulls, unselected fields and payload size.
  const sql = sqlText.toLowerCase();
  const isRead = t => readTables.has(t) || new RegExp(`(^|[^\\w-])${escapeRe(t)}([^\\w-]|$)`).test(sql);
  const seen = new Set();
  const entryText = s => {
    const fn = app.functions?.[s.entry.function] ?? {};
    return {fn, own: JSON.stringify(s.entry.parameters ?? s.entry.functionParameters ?? {}),
      all: JSON.stringify([s.entry.parameters ?? s.entry.functionParameters ?? {}, fn.parameters ?? {}, fn.url ?? '', fn.inputTransform ?? ''])};
  };
  const deltaTables = new Set(syncs.filter(s => s.entry.function && DELTA.test(entryText(s).all)).map(s => s.table));
  for (const s of syncs) {
    const st = stats?.[s.table];
    const once = code => !seen.has(`${code}:${s.table}`) && seen.add(`${code}:${s.table}`);
    if (!isRead(s.table) && once('unread'))
      add('synced-table-unread', s.path, `${s.table} is synced${st ? ` (${size(st)})` : ''} but no datasource or SQL reads it.`);
    if (st && (st.bytes >= 5 * MB || st.rows >= 5000) && once('large'))
      add('large-synced-table', s.path, `${s.table} holds ${size(st)} on the device.`,
        {priority: st.bytes >= 20 * MB || st.rows >= 20000 ? 'high' : 'medium'});
    const fnName = s.entry.function;
    if (!fnName) continue;
    const {fn, own} = entryText(s);
    const params = fn.parameters ?? {};
    // A targeted or windowed pull (a filter in the call) is not a full pull; nor is a table another step pulls by delta.
    const required = Object.entries(params).some(([k, v]) => FILTERED.test(k) && v?.required);
    if (!deltaTables.has(s.table) && !FILTERED.test(Object.keys(JSON.parse(own)).join(' ')) && !required && once('full'))
      add('sync-full-pull', s.path, `${s.table} is pulled in full by ${fnName} on every run; no modified-date filter found${st ? ` (${size(st)})` : ''}.`,
        {priority: !st ? 'low' : st.rows >= 1000 ? 'high' : st.rows >= 200 ? 'medium' : 'low'});
    const odata = Object.keys(params).some(k => k.startsWith('$')) || /\$(filter|expand|top)=/i.test(fn.url ?? '');
    if ((fn.method ?? 'GET').toUpperCase() === 'GET' && odata && !('$select' in params) && !/\$select=/i.test(fn.url ?? '') && once('select'))
      add('sync-no-select', s.path, `${fnName} fetches every field of ${s.table}${st?.rows ? ` (${Math.round(st.bytes / st.rows)} bytes a row)` : ''}.`,
        {priority: st?.rows && st.bytes / st.rows >= 2048 ? 'medium' : 'low'});
  }

  findings.sort((a, b) => PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority));
  return {findings, indexes: indexes.map(({name, table, path}) => ({name, table, path}))};
}

// A YAML solution folder as one object shaped like build/output.json, plus each entry's source file.
// Ruby's built-in YAML parser keeps this script dependency-free (macOS ships Ruby).
const RUBY = 'require "yaml"; require "json"; require "date"; d = ARGV[0]; out = {}; ' +
  'STDIN.read.split("\\n").each { |f| begin; out[f] = YAML.safe_load(File.read(File.join(d, f)), permitted_classes: [Date, Time], aliases: true); ' +
  'rescue => e; out[f] = {"__parseError" => e.message}; end }; puts JSON.generate(out)';
export function loadSolution(dir) {
  const files = [];
  const visit = rel => {
    for (const e of fs.readdirSync(path.join(dir, rel), {withFileTypes: true})) {
      if (/^[._]/.test(e.name) || e.name === 'node_modules' || e.name === 'build') continue;
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) visit(r); else if (e.name.endsWith('.jigx')) files.push(r);
    }
  };
  visit('');
  if (!files.includes('index.jigx')) throw new Error(`${dir} has no index.jigx; give a YAML solution folder or build/output.json`);
  const run = spawnSync('ruby', ['-e', RUBY, dir], {input: files.join('\n'), encoding: 'utf8', maxBuffer: 1 << 30});
  if (run.error || run.status !== 0) throw new Error(`could not parse the YAML with Ruby: ${run.error?.message ?? run.stderr.trim()}`);
  const docs = JSON.parse(run.stdout);
  const app = {}, sources = {}, parseErrors = [];
  for (const rel of files) {
    const doc = docs[rel];
    if (doc?.__parseError) { parseErrors.push({file: rel, error: doc.__parseError}); continue; }
    const kind = rel.split('/')[0];
    if (rel === 'index.jigx') Object.assign(app, doc);
    else if (KINDS.includes(kind) && rel.includes('/')) {
      const name = path.basename(rel, '.jigx');
      (app[kind] ??= {})[name] = doc;
      sources[`$.${kind}.${name}`] = rel;
    }
  }
  // Expression scripts (scripts/<group>/<file>.js), shaped like an SDK build's `scripts`.
  const scriptsDir = path.join(dir, 'scripts');
  if (fs.existsSync(scriptsDir)) for (const group of fs.readdirSync(scriptsDir, {withFileTypes: true})) {
    if (!group.isDirectory()) continue;
    for (const f of fs.readdirSync(path.join(scriptsDir, group.name)).filter(f => f.endsWith('.js')))
      ((app.scripts ??= {})[group.name] ??= {})[f] = fs.readFileSync(path.join(scriptsDir, group.name, f), 'utf8');
  }
  return {app, sources, parseErrors};
}

// Exported functions of an expression script: name, body text and line count.
export function scriptFunctions(text) {
  const out = [];
  for (const m of text.matchAll(/^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)\s*\{|^\s*exports\.(\w+)\s*=\s*(?:async\s+)?function\s*\w*\s*\([^)]*\)\s*\{/gm)) {
    const name = m[1] ?? m[2];
    if (!/^\s*(export|exports)/.test(m[0])) continue; // helpers are not callable from expressions
    let depth = 0, i = m.index + m[0].length - 1;
    for (; i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) break;
    }
    const body = text.slice(m.index + m[0].length, i);
    out.push({name, body, lines: body.split('\n').filter(l => l.trim() && !/^\s*\/\//.test(l)).length});
  }
  return out;
}

// For an SDK build, map each jig, datasource, action and function to the TypeScript file that
// declares it (the first screenId/datasourceId/actionId/functionId in the file), so findings
// point at src/... instead of the build.
const SDK_KIND = {screenId: 'jigs', jigId: 'jigs', datasourceId: 'datasources', actionId: 'actions', functionId: 'functions'};
export function sdkSources(outputFile) {
  const project = path.dirname(path.dirname(path.resolve(outputFile)));
  const src = path.join(project, 'src');
  const sources = {}, declared = {};
  if (!fs.existsSync(src)) return sources;
  const id = /\b(screenId|jigId|datasourceId|actionId|functionId)\s*:\s*['"]([^'"]+)['"]/;
  // A declaration (app.addScreen.default({ screenId })) wins over a reference such as a tab's screenId.
  const declaration = new RegExp(`\\.add(?:Screen|Jig|Datasource|Action|Function)\\w*(?:\\.\\w+)?\\(\\s*\\{[^}]*?${id.source}`);
  const visit = dir => {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { if (e.name !== 'node_modules') visit(full); continue; }
      if (!e.name.endsWith('.ts')) continue;
      const text = fs.readFileSync(full, 'utf8');
      const d = declaration.exec(text);
      const m = d ?? id.exec(text);
      const key = m && `$.${SDK_KIND[m[1]]}.${m[2]}`;
      if (key && (!sources[key] || (d && !declared[key]))) { sources[key] = path.relative(project, full); declared[key] = !!d; }
    }
  };
  visit(src);
  return sources;
}

// Rows and bytes per table in an exported or simulator database (read-only).
export async function tableStats(dbPath) {
  process.removeAllListeners('warning'); // node:sqlite prints an experimental warning on Node 22
  const {DatabaseSync} = await import('node:sqlite');
  const db = new DatabaseSync(dbPath, {readOnly: true});
  const out = {};
  for (const {name} of db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all()) {
    const q = `"${name.replace(/"/g, '""')}"`;
    try { out[tableName(name)] = db.prepare(`SELECT count(*) AS rows, coalesce(sum(length(data)), 0) AS bytes FROM ${q}`).get(); }
    catch { out[tableName(name)] = {rows: db.prepare(`SELECT count(*) AS rows FROM ${q}`).get().rows, bytes: 0}; }
  }
  db.close();
  return out;
}

const SECTIONS = {A: 'Measure and track', B: 'Queries and indexes', C: 'Lists and rendering', D: 'State and expressions',
  E: 'Screens and start-up', F: 'Sync', G: 'Writes and duplicates', H: 'Security and logs'};

export function markdownReport({title, findings, parseErrors = [], stats}) {
  const count = p => findings.filter(f => f.priority === p).length;
  const lines = [`# Performance audit: ${title}`, '',
    `**${count('high')} high · ${count('medium')} medium · ${count('low')} low**`, '',
    'Each finding is a lead from a static scan: confirm it on the device before changing anything',
    '(row and byte counts, a query plan, a timed load). Fix high first, one section at a time.', ''];
  if (parseErrors.length) lines.push(`Not scanned (YAML errors): ${parseErrors.map(e => e.file).join(', ')}`, '');
  for (const p of PRIORITIES) {
    const items = findings.filter(f => f.priority === p);
    if (!items.length) continue;
    lines.push(`## ${p[0].toUpperCase()}${p.slice(1)} priority`, '');
    for (const code of [...new Set(items.map(f => f.code))]) {
      const group = items.filter(f => f.code === code);
      lines.push(`### ${code} (${group.length}) · ${group[0].section} ${SECTIONS[group[0].section]}`, '',
        `**Fix:** ${group[0].fix}`, '');
      for (const message of [...new Set(group.map(f => f.message))]) {
        const at = group.filter(f => f.message === message).map(f => `\`${f.where}\``);
        lines.push(at.length === 1 ? `- ${at[0]}: ${message}` : `- ${message} ${at.length} places: ${at.slice(0, 3).join(', ')}${at.length > 3 ? ', …' : ''}`);
      }
      lines.push('');
    }
  }
  if (stats) {
    const top = Object.entries(stats).filter(([, s]) => s.rows).sort((a, b) => b[1].bytes - a[1].bytes).slice(0, 10);
    lines.push('## Largest tables on the device', '', '| Table | Rows | MB |', '| --- | ---: | ---: |',
      ...top.map(([t, s]) => `| ${t} | ${s.rows} | ${(s.bytes / MB).toFixed(2)} |`), '');
  }
  if (!findings.length) lines.push('No findings.', '');
  return lines.join('\n');
}

// Findings on this branch that the baseline (an earlier audit.json, e.g. from main) does not
// have. Matched by code, file and message, not the path inside the file, so moving a block
// does not count as new. Repeats are counted, so a second copy of a known finding is new.
export function newFindings(findings, baseline) {
  const key = f => `${f.code}|${(f.where ?? f.path).split(' › ')[0]}|${f.message}`;
  const seen = new Map();
  for (const f of baseline) seen.set(key(f), (seen.get(key(f)) ?? 0) + 1);
  return findings.filter(f => { const n = seen.get(key(f)) ?? 0; seen.set(key(f), n - 1); return n <= 0; });
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const opt = name => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
    const dump = args.includes('--dump-app') && args.splice(args.indexOf('--dump-app'), 1);
    const dbPath = opt('--db'), reportPath = opt('--report'), baselinePath = opt('--baseline'), failOn = opt('--fail-on');
    if (failOn && !PRIORITIES.includes(failOn)) throw new Error(`--fail-on takes ${PRIORITIES.join(', ')}`);
    const file = args[0] || 'build/output.json';
    const loaded = fs.statSync(file).isDirectory()
      ? loadSolution(file) : {app: JSON.parse(fs.readFileSync(file, 'utf8')), sources: sdkSources(file), parseErrors: []};
    if (dump) { process.stdout.write(JSON.stringify(loaded.app)); return; }
    const stats = dbPath ? await tableStats(dbPath) : null;
    const {findings, indexes} = checkPerformance(loaded.app, {stats, sources: loaded.sources});
    const count = s => findings.filter(x => x.severity === s).length;
    const byCode = {}, byPriority = {high: 0, medium: 0, low: 0};
    for (const f of findings) { byCode[f.code] = (byCode[f.code] ?? 0) + 1; byPriority[f.priority]++; }
    const added = baselinePath ? newFindings(findings, JSON.parse(fs.readFileSync(baselinePath, 'utf8')).findings ?? []) : null;
    const gated = failOn ? (added ?? findings).filter(x => PRIORITIES.indexOf(x.priority) <= PRIORITIES.indexOf(failOn)) : [];
    console.log(JSON.stringify({file, errors: count('error'), warnings: count('warning'), byPriority, byCode,
      parseErrors: loaded.parseErrors, indexesFound: indexes, findings, ...(added && {newFindings: added}),
      limits: 'Static leads only. Confirm each with row/byte counts and a query plan (scripts/profile-query.py); this does not measure device timing.'}, null, 2));
    if (reportPath) {
      const title = loaded.app.title ?? loaded.app.name ?? path.basename(path.resolve(file));
      fs.writeFileSync(reportPath, markdownReport({title, findings, parseErrors: loaded.parseErrors, stats}));
      console.error(`Report written to ${reportPath}: ${byPriority.high} high, ${byPriority.medium} medium, ${byPriority.low} low.`);
    }
    if (added) console.error(`${added.length} finding(s) not in the baseline.`);
    for (const f of gated) console.error(`[${f.priority}] ${f.code} ${f.where}: ${f.message}`);
    if (gated.length) console.error(`${gated.length} ${added ? 'new ' : ''}finding(s) at ${failOn} priority or above.`);
    // With a baseline, only errors this branch adds fail the run, like the gated findings.
    process.exitCode = gated.length || (added ?? findings).some(x => x.severity === 'error') ? 1 : 0;
  } catch (error) { console.error(`Performance check failed: ${error.message}`); process.exitCode = 2; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
