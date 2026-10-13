#!/usr/bin/env node
// Dependency-free static performance scan of a generated Jigx solution (build/output.json).
// No network or writes. Findings are leads to measure, not verdicts: confirm each with
// row/byte counts and a query plan before changing anything (references/measuring.md).
import fs from 'node:fs';
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

const asArray = v => Array.isArray(v) ? v : v === undefined || v === null ? [] : [v];
const statementText = s => typeof s === 'string' ? s : (s?.statement ?? s?.sql ?? '');

export function checkPerformance(app) {
  const findings = [];
  const add = (code, path, message, severity = 'warning') => findings.push({severity, code, path, message});

  const datasources = [], ddlActions = [], indexes = [];
  walk(app, (node, path) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'datasource.sqlite' && typeof node.options?.query === 'string') datasources.push({path, node});
    if (node.type === 'action.execute-sql') ddlActions.push({path, node});
    if (node.type === 'component.list-item') {
      const reads = new Set();
      walk(node, value => {
        if (typeof value === 'string' && value.startsWith('=')) {
          for (const r of withoutJsonataStrings(value).matchAll(/@ctx\.datasources\.([\w-]+)/g)) reads.add(r[1]);
        }
      });
      if (reads.size) add('per-row-datasource-read', path,
        `List item reads ${[...reads].join(', ')} per rendered row (one live subscription each). Resolve once in expressions:, feed data:, and read @ctx.current.item.*.`);
      return;
    }
    if (typeof node === 'object') {
      for (const [key, value] of Object.entries(node)) {
        if (typeof value !== 'string' || !value.startsWith('=')) continue;
        const expr = withoutJsonataStrings(value);
        if (/@ctx\.datasources\.[\w-]+(?:\.[\w-]+)+\s*!=/.test(expr))
          add('datasource-not-equals', `${path}.${key}`, "Comparison against an undefined field is false, so `ds.field != 'X'` blocks the no-row case. Use $count(ds[field='X']) = 0.");
        const map = expr.indexOf('$map(');
        if (map >= 0 && /@ctx\.datasources\.[\w-]+\s*\[/.test(expr.slice(map)))
          add('filter-inside-map', `${path}.${key}`, 'A datasource filter inside $map walks the view once per element (O(n²)). Key the view into a map once with $merge/$map and look up by id.');
      }
    }
  });

  const boundEntities = new Set();
  for (const {node} of datasources) for (const e of asArray(node.options?.entities)) boundEntities.add(typeof e === 'string' ? e : e?.entity);

  for (const {path, node} of ddlActions) {
    const opts = node.options ?? node;
    const entities = asArray(opts.entities ?? node.entities);
    const statements = asArray(opts.statements ?? node.statements).map(statementText);
    if (!entities.length) add('execute-sql-entities', path, 'action.execute-sql needs a non-empty entities: array; without it the action fails on shape before any statement runs.', 'error');
    const created = statements.map(parseCreateIndex).filter(Boolean);
    if (!created.length) continue;
    indexes.push(...created.map(c => ({...c, path})));
    const real = entities.map(e => typeof e === 'string' ? e : e?.entity).filter(e => boundEntities.has(e));
    if (real.length) add('ddl-invalidates-datasources', path,
      `Index DDL lists ${real.join(', ')} in entities:, which re-runs every datasource bound to them (~6.6 s measured on device). Use a table nothing binds to, e.g. default/debug.`);
    if (created.some(c => !c.ifNotExists)) add('index-not-idempotent', path, 'Use CREATE INDEX IF NOT EXISTS (or DROP INDEX IF EXISTS + CREATE in the same step) so re-runs do not fail the step.');
    if (node.when === undefined && opts.when === undefined) add('ddl-ungated', path,
      'Gate CREATE INDEX on the table existing (read sqlite_master into a datasource). One "no such table" on a fresh install rolls back every statement in the step.');
  }

  const indexed = new Set(indexes.flatMap(i => i.expressions));
  for (const {path, node} of datasources) {
    const query = node.options.query;
    const masked = maskSql(query);
    if (/\bLIKE\b/i.test(masked) && /'%'\s*\|\|/.test(stripComments(query)) && !/\bLIMIT\b/i.test(masked))
      add('uncapped-search-list', `${path}.options.query`, "Contains-search (LIKE '%' || …) with no LIMIT scans, sorts and renders every row. Cap it and union a pinned arm for the current selection.");
    for (const p of filterPredicates(query)) {
      if (p.like) continue; // a leading-wildcard LIKE cannot use an index anyway
      if (p.multiArm && p.wrapped) {
        add('wrapped-predicate', `${path}.options.query`, `${p.expr} wraps several json_extract arms in functions, so no index on either arm can be used. Filter on one indexable expression.`);
        continue;
      }
      if (!indexed.has(p.norm)) add('unindexed-predicate', `${path}.options.query`,
        `No shipped CREATE INDEX matches ${p.expr}. If this table can grow, add an expression index with exactly this text.`);
    }
  }
  return {findings, indexes: indexes.map(({name, table, path}) => ({name, table, path}))};
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const file = process.argv[2] || 'build/output.json';
    const {findings, indexes} = checkPerformance(JSON.parse(fs.readFileSync(file, 'utf8')));
    const count = s => findings.filter(x => x.severity === s).length;
    const byCode = {};
    for (const f of findings) byCode[f.code] = (byCode[f.code] ?? 0) + 1;
    console.log(JSON.stringify({file, errors: count('error'), warnings: count('warning'), byCode, indexesFound: indexes, findings,
      limits: 'Static leads only. Confirm each with row/byte counts and a query plan (scripts/profile-query.py); this does not measure device timing.'}, null, 2));
    process.exitCode = findings.some(x => x.severity === 'error') ? 1 : 0;
  } catch (error) { console.error(`Performance check failed: ${error.message}`); process.exitCode = 2; }
}
