import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {RULES, checkPerformance, cteJoins, filterPredicates, parseCreateIndex, normaliseExpr, loadSolution, markdownReport, newFindings, orAcrossKeys, orderByTerms, outerColumns, riskyCalls, scriptFunctions, sdkSources, shorthandPaths, where} from './check-performance.mjs';

// Fixtures are minimal (no id, rewriteQuery on, loose entities); those checks have their own tests.
const NOISE = new Set(['unused-datasource', 'rewrite-query-on', 'datasource-no-id', 'watched-table-unread', 'order-by-unindexed']);
const codes = (app, opts) => checkPerformance(app, opts).findings.map(x => x.code).filter(c => !NOISE.has(c)).sort();
const ds = (query, entities = ['default/fileUploadQueue']) => ({type: 'datasource.sqlite', options: {entities, query}});
const PRED = "LOWER(COALESCE(json_extract(q.data, '$.status'), ''))";
const INDEX = "CREATE INDEX IF NOT EXISTS idx_fileUploadQueue_status ON [fileUploadQueue] (LOWER(COALESCE(json_extract(data, '$.status'), '')))";

test('parses expression indexes and normalises the table alias away', () => {
  const idx = parseCreateIndex(INDEX);
  assert.equal(idx.name, 'idx_fileUploadQueue_status');
  assert.equal(idx.table, 'fileUploadQueue');
  assert.equal(idx.ifNotExists, true);
  assert.deepEqual(idx.expressions, [normaliseExpr(PRED)]);
});

test('finds the outermost function around json_extract in WHERE only', () => {
  const q = `SELECT json_extract(data,'$.name') AS name FROM [fileUploadQueue] AS q WHERE ${PRED} = 'pending' AND (json_extract(q.data,'$.x') = @x) ORDER BY name`;
  const preds = filterPredicates(q);
  assert.deepEqual(preds.map(p => p.expr), [PRED, "json_extract(q.data,'$.x')"]);
  assert.equal(preds[0].wrapped, true);
  assert.equal(preds[1].wrapped, false);
});

test('a shipped matching index silences unindexed-predicate; a mismatch does not', () => {
  const action = (statement, extra = {}) => ({type: 'action.execute-sql', when: '=true', options: {entities: ['default/debug'], statements: [{statement}]}, ...extra});
  const query = `SELECT * FROM [fileUploadQueue] q WHERE ${PRED} = 'pending'`;
  assert.deepEqual(codes({datasources: {a: ds(query)}}), ['unindexed-predicate']);
  assert.deepEqual(codes({datasources: {a: ds(query)}, actions: {i: action(INDEX)}}), []);
  const typo = INDEX.replace("'$.status'", "'$.Status'");
  assert.deepEqual(codes({datasources: {a: ds(query)}, actions: {i: action(typo)}}), ['unindexed-predicate']);
});

test('flags wrapped multi-arm predicates, uncapped search and leading-wildcard LIKE is not an index lead', () => {
  const wrapped = "SELECT * FROM t WHERE TRIM(COALESCE(json_extract(data,'$.A'), json_extract(data,'$.B'))) = @v";
  assert.deepEqual(codes({datasources: {a: ds(wrapped)}}), ['wrapped-predicate']);
  const search = "SELECT * FROM t WHERE (@search IS NULL OR json_extract(data,'$.name') LIKE '%' || @search || '%') ORDER BY 1";
  assert.deepEqual(codes({datasources: {a: ds(search)}}), ['uncapped-search-list']);
  assert.deepEqual(codes({datasources: {a: ds(search + ' LIMIT 100')}}), []);
  const picked = checkPerformance({datasources: {a: ds(search)},
    jigs: {j: {children: [{type: 'component.dropdown', instanceId: 'd', options: {data: '=@ctx.datasources.a'}}]}}}).findings;
  assert.match(picked.find(f => f.code === 'uncapped-search-list').message, /feeds a dropdown/);
  assert.match(picked.find(f => f.code === 'uncapped-search-list').fix, /@selectedId IS NOT NULL/);
});

test('execute-sql shape, invalidation, idempotence and gating', () => {
  const bad = {type: 'action.execute-sql', options: {statements: [{statement: 'CREATE INDEX idx_a ON [fileUploadQueue] (id)'}]}};
  assert.deepEqual(codes({actions: {i: bad}}), ['ddl-ungated', 'execute-sql-entities', 'index-not-idempotent']);
  const realTable = {type: 'action.execute-sql', when: '=true', options: {entities: ['default/fileUploadQueue'], statements: [INDEX]}};
  assert.deepEqual(codes({datasources: {a: ds('SELECT 1')}, actions: {i: realTable}}), ['ddl-invalidates-datasources']);
  const errors = checkPerformance({actions: {i: bad}}).findings.filter(f => f.severity === 'error');
  assert.equal(errors.length, 1);
});

test('per-row datasource reads, != against a datasource field, and filters inside $map', () => {
  const item = {type: 'component.list-item', options: {title: '=@ctx.current.item.name', color: '=@ctx.datasources.status[id=@ctx.current.item.id].color', label: '="@ctx.datasources.literal"'}};
  const found = checkPerformance({jigs: {list: {item}}}).findings;
  assert.deepEqual(found.map(f => f.code), ['per-row-datasource-read']);
  assert.match(found[0].message, /status/);
  assert.doesNotMatch(found[0].message, /literal/);
  assert.deepEqual(codes({jigs: {a: {when: "=@ctx.datasources.queue.state != 'done'"}}}), ['datasource-not-equals']);
  assert.deepEqual(codes({jigs: {a: {expressions: {rows: '=$map(@ctx.datasources.forms, function($f) { @ctx.datasources.subs[formId = $f.id] })'}}}}), ['filter-inside-map']);
  assert.deepEqual(codes({jigs: {list: {item: {type: 'component.list-item', options: {title: '=@ctx.current.item.name'}}}}}), []);
  assert.deepEqual(codes({jigs: {home: {children: [{type: 'component.list-item', options: {title: '=@ctx.datasources.sync.state'}}]}}}), []);
  assert.deepEqual(codes({jigs: {a: {data: '=$map(@ctx.datasources.files[id != null], function($v) { $v.id })'}}}), []);
});

test('every finding carries a priority, a fix and a readable location, high first', () => {
  const bad = {type: 'action.execute-sql', options: {statements: [{statement: 'CREATE INDEX idx_a ON [t] (id)'}]}};
  const {findings} = checkPerformance({actions: {i: bad}, datasources: {a: ds("SELECT * FROM t WHERE json_extract(data,'$.x') = 1")}});
  for (const f of findings) {
    assert.ok(['high', 'medium', 'low'].includes(f.priority), f.code);
    assert.ok(f.fix && f.where && f.message, f.code);
  }
  assert.equal(findings[0].priority, 'high');
  assert.equal(where('$.jigs.list-orders.datasources.a.options.query', {'$.jigs.list-orders': 'jigs/Orders/list-orders.jigx'}),
    'jigs/Orders/list-orders.jigx › datasources.a.options.query');
  assert.equal(where('$.onLoad.options'), 'index › onLoad.options');
});

test('unused global datasources; a reference from anywhere but itself counts', () => {
  const app = {datasources: {used: ds('SELECT 1'), unused: ds('SELECT 1'), self: {...ds('SELECT 1'), note: '=@ctx.datasources.self'}},
    jigs: {home: {title: '=@ctx.datasources.used.name'}}};
  const unused = checkPerformance(app).findings.filter(f => f.code === 'unused-datasource').map(f => f.path).sort();
  assert.deepEqual(unused, ['$.datasources.self', '$.datasources.unused']);
  const sdk = {datasources: {orders: {...ds('SELECT 1'), datasourceId: 'orders'}},
    jigs: {home: {datasources: {orders: {...ds('SELECT 1'), datasourceId: 'orders'}}}}};
  assert.deepEqual(checkPerformance(sdk).findings.filter(f => f.code === 'unused-datasource'), []);
});

test('sync: unread tables, full pulls, missing $select, sized by device stats', () => {
  const sync = entities => ({type: 'action.sync-entities', options: {provider: 'DATA_PROVIDER_REST', entities}});
  const app = {
    functions: {
      'get-orders': {method: 'GET', url: 'https://{u}Order', parameters: {$filter: {location: 'query'}, $expand: {location: 'query'}}},
      'get-types': {method: 'GET', url: 'https://{u}Type', parameters: {$select: {value: 'ID'}}},
    },
    actions: {s: {action: sync([
      {entity: 'default/orders', function: 'get-orders', parameters: {$filter: "=LastModifiedDateTime gt @ctx.datasources.lastmod"}},
      {entity: 'default/types', function: 'get-types'},
      {entity: 'default/notes', function: 'get-types'},
    ])}},
    datasources: {orders: ds('SELECT * FROM [default/orders]', ['default/orders']), types: ds('SELECT 1 FROM [types] t', [])},
  };
  assert.deepEqual(codes(app), ['sync-full-pull', 'sync-full-pull', 'sync-no-select', 'synced-table-unread']);
  const unread = checkPerformance(app).findings.find(f => f.code === 'synced-table-unread');
  assert.match(unread.message, /^notes /);
  // A $top 1 sync only tests access; nothing reads the table on purpose.
  app.functions['get-permission'] = {method: 'GET', url: 'https://{u}Permission', parameters: {$top: {location: 'query', value: '1'}}};
  app.actions.s.action.options.entities.push({entity: 'default/permissionOutput', function: 'get-permission'});
  assert.ok(!checkPerformance(app).findings.some(f => /permissionOutput/.test(f.message) && f.code === 'synced-table-unread'));
  app.actions.s.action.options.entities.pop();
  const stats = {orders: {rows: 30000, bytes: 90 * 1024 * 1024}, types: {rows: 1500, bytes: 1000}, notes: {rows: 5, bytes: 10}};
  const sized = checkPerformance(app, {stats}).findings;
  assert.equal(sized.find(f => f.code === 'large-synced-table').priority, 'high');
  assert.equal(sized.find(f => f.code === 'sync-full-pull' && /types/.test(f.message)).priority, 'high');
  assert.equal(sized.find(f => f.code === 'sync-no-select').priority, 'medium');
});

test('markdown report groups findings by priority and code with the fix once', () => {
  const findings = checkPerformance({jigs: {a: {item: {type: 'component.list-item', options: {t: '=@ctx.datasources.x.y'}}},
    b: {item: {type: 'component.list-item', options: {t: '=@ctx.datasources.x.y'}}}}}).findings;
  const md = markdownReport({title: 'sol', findings});
  assert.match(md, /\*\*0 high · 2 medium · 0 low\*\*/);
  assert.match(md, /### per-row-datasource-read \(2\) · C Lists and rendering/);
  assert.equal(md.split('**Fix:**').length, 2);
  assert.match(md, /\| C \| Lists and rendering \| How lists are built and drawn\. .* \| 0 \| 2 \| 0 \|/);
  assert.doesNotMatch(md, /\| B \| Queries and indexes/);
});

test('heavy-focus names every table it syncs, so the count matches the list', () => {
  const sync = entities => ({type: 'action.sync-entities', options: {entities}});
  const app = {
    actions: {a: {action: sync(['default/x', {entity: 'default/y'}])}, b: {action: sync([{entity: 'default/x'}])}},
    jigs: {s: {type: 'jig.default', onFocus: {type: 'action.action-list', options: {actions: [
      {type: 'action.execute-action', options: {action: 'a'}}, {type: 'action.execute-action', options: {action: 'b'}}]}}}},
  };
  const focus = checkPerformance(app).findings.find(f => f.code === 'heavy-focus');
  assert.match(focus.message, /syncs 3 entities \(x, y; x more than once\) through a, b every time/);
});

test('priority counts leave out expression safety, as audit.md does', async () => {
  const {spawnSync: run} = await import('node:child_process');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-counts-'));
  const app = {jigs: {l: {type: 'jig.list', data: '=@ctx.datasources.rows', item: {type: 'component.list-item',
    options: {title: '=$trim(@ctx.current.item.Description)', subtitle: '=@ctx.datasources.x.y'}}}}};
  fs.writeFileSync(path.join(dir, 'output.json'), JSON.stringify(app));
  const out = run(process.execPath, [new URL('./check-performance.mjs', import.meta.url).pathname, path.join(dir, 'output.json'),
    '--report', path.join(dir, 'audit.md')], {encoding: 'utf8'});
  const json = JSON.parse(out.stdout);
  assert.equal(json.expressionSafety.medium, 1);
  assert.equal(json.byPriority.medium, json.findings.filter(f => f.priority === 'medium' && f.code !== 'expression-may-throw').length);
  assert.match(out.stderr, new RegExp(`${json.byPriority.medium} medium, ${json.byPriority.low} low, plus 1 expression-safety`));
  assert.match(fs.readFileSync(path.join(dir, 'audit.md'), 'utf8'), new RegExp(`· ${json.byPriority.medium} medium ·`));
});

test('markdown report counts low findings by type unless listLow is set', () => {
  const low = (code, where) => ({priority: 'low', section: 'B', code, where, message: `m ${where}`, fix: 'Index a | b'});
  const findings = [low('unindexed-predicate', 'a.jigx'), low('unindexed-predicate', 'b.jigx'), low('order-by-unindexed', 'c.jigx'),
    {priority: 'low', section: 'D', code: 'expression-may-throw', where: 'k.jigx › $.x', message: 'key', fix: 'Guard'}];
  const md = markdownReport({title: 'sol', findings});
  assert.match(md, /\| unindexed-predicate \| 2 \| B \| Index a \\\| b \|/);
  assert.ok(md.indexOf('| unindexed-predicate |') < md.indexOf('| order-by-unindexed |'));
  assert.doesNotMatch(md, /a\.jigx|k\.jigx/);
  assert.match(md, /1 low \(keys and other columns\) not listed/);
  const full = markdownReport({title: 'sol', findings, listLow: true});
  assert.match(full, /### unindexed-predicate \(2\)/);
  assert.match(full, /`a\.jigx`/);
  assert.match(full, /`k\.jigx`/);
});

test('loads a YAML solution folder into the output.json shape', {skip: spawnSync('ruby', ['-v']).status !== 0 && 'ruby not installed'}, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-sol-'));
  const write = (rel, text) => { fs.mkdirSync(path.dirname(path.join(dir, rel)), {recursive: true}); fs.writeFileSync(path.join(dir, rel), text); };
  write('index.jigx', 'name: demo\ntitle: Demo\n');
  write('datasources/Lists/orders.jigx', 'type: datasource.sqlite\noptions:\n  provider: DATA_PROVIDER_LOCAL\n  entities: [default/orders]\n  query: SELECT * FROM [default/orders]\n');
  write('jigs/Orders/list-orders.jigx', 'type: jig.list\ntitle: =@ctx.datasources.orders.name\n');
  write('_backup/jigs/old.jigx', 'broken: [\n');
  write('jigs/broken.jigx', 'a: [\n');
  const {app, sources, parseErrors} = loadSolution(dir);
  assert.equal(app.name, 'demo');
  assert.equal(app.datasources.orders.options.query, 'SELECT * FROM [default/orders]');
  assert.equal(sources['$.jigs.list-orders'], 'jigs/Orders/list-orders.jigx');
  assert.deepEqual(parseErrors.map(e => e.file), ['jigs/broken.jigx']);
  assert.deepEqual(checkPerformance(app, {sources}).findings.filter(f => !NOISE.has(f.code)), []);
});

test('targeted pulls, tables pulled by delta elsewhere and expression entity lists are not flagged', () => {
  const sync = entities => ({type: 'action.sync-entities', options: {entities}});
  const app = {
    functions: {'get-appts': {method: 'GET', url: 'https://{u}Appointment', parameters: {$filter: {required: false}}}},
    actions: {
      one: {action: sync([{entity: 'appts', function: 'get-appts', parameters: {$filter: "=\"AppointmentNbr eq '\" & @ctx.action.parameters.nbr & \"'\""}}])},
      delta: {action: sync([{entity: 'appts', function: 'get-appts', parameters: {$filter: '=@ctx.datasources.lastmod-appts.LastModifiedDateTime'}}])},
      full: {action: sync([{entity: 'appts', function: 'get-appts'}])},
      dynamic: {action: sync('=$map(@ctx.action.parameters.files, function($v) { {"entity": "files", "function": "get-file"} })')},
    },
    datasources: {appts: ds('SELECT * FROM [default/appts]', ['default/appts'])},
  };
  assert.deepEqual(codes(app).filter(c => c !== 'sync-no-select'), []);
});

test('index steps: listed tables cannot be missing, one bound table is medium, several are high', () => {
  const step = (entities, tables, extra = {}) => ({type: 'action.execute-sql', ...extra, options: {entities,
    statements: tables.map(t => `CREATE INDEX IF NOT EXISTS idx_${t} ON ${t} (json_extract(data, '$.LastModifiedDateTime'))`)}});
  const datasources = {a: ds('SELECT 1', ['employees']), b: ds('SELECT 1', ['vendor'])};
  const run = action => checkPerformance({datasources, actions: {i: action}}).findings.filter(f => f.code.startsWith('ddl-'));
  assert.deepEqual(run(step(['employees'], ['employees'])).map(f => [f.code, f.priority]), [['ddl-invalidates-datasources', 'medium']]);
  assert.deepEqual(run(step(['employees', 'vendor'], ['employees', 'vendor'])).map(f => [f.code, f.priority]), [['ddl-invalidates-datasources', 'high']]);
  const ungated = run(step(['default/debug'], ['employees']));
  assert.deepEqual(ungated.map(f => f.code), ['ddl-ungated']);
  assert.match(ungated[0].message, /employees/);
});

test('maps SDK build entries to the TypeScript file that declares them', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-sdk-'));
  const write = (rel, text) => { fs.mkdirSync(path.dirname(path.join(dir, rel)), {recursive: true}); fs.writeFileSync(path.join(dir, rel), text); };
  write('build/output.json', '{}');
  write('src/jigs/Orders/list-orders.ts', "app.addScreen({ screenId: 'list-orders' })\nscreen.addDatasource.static({ datasourceId: 'local' })");
  write('src/datasources/orders.ts', "app.addDatasource.sqlite({ datasourceId: 'orders' })");
  write('src/index.ts', "app.addTab({ tabId: 'x', screenId: 'list-orders' })");
  assert.deepEqual(sdkSources(path.join(dir, 'build/output.json')),
    {'$.jigs.list-orders': 'src/jigs/Orders/list-orders.ts', '$.datasources.orders': 'src/datasources/orders.ts'});
});

test('baseline: only findings missing from the earlier audit are new, moves are not', () => {
  const f = (code, where, message, priority = 'high') => ({code, where, message, priority});
  const base = [f('a', 'jigs/x.jigx › children.1', 'm'), f('b', 'jigs/y.jigx › item', 'n')];
  const now = [f('a', 'jigs/x.jigx › children.4', 'm'), f('a', 'jigs/x.jigx › children.5', 'm'), f('c', 'jigs/z.jigx › item', 'o')];
  assert.deepEqual(newFindings(now, base).map(x => x.where), ['jigs/x.jigx › children.5', 'jigs/z.jigx › item']);
});

test('--fail-on gates the exit code, against the baseline when one is given', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-gate-'));
  const app = {jigs: {a: {children: [{type: 'component.list', options: {item: {type: 'component.list-item', options: {t: '=@ctx.datasources.x.y'}}}}]}}};
  fs.writeFileSync(path.join(dir, 'output.json'), JSON.stringify(app));
  const script = path.join(import.meta.dirname, 'check-performance.mjs');
  const run = (...args) => spawnSync(process.execPath, [script, path.join(dir, 'output.json'), ...args], {encoding: 'utf8'});
  assert.equal(run('--fail-on', 'high').status, 0);
  const failed = run('--fail-on', 'medium');
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /\[medium\] per-row-datasource-read/);
  fs.writeFileSync(path.join(dir, 'base.json'), failed.stdout);
  assert.equal(run('--fail-on', 'medium', '--baseline', path.join(dir, 'base.json')).status, 0);
  assert.equal(run('--fail-on', 'nope').status, 2);
});

test('outer SELECT columns: aliases, qualified names, star, CTEs and subqueries', () => {
  assert.deepEqual(outerColumns("SELECT q.id, json_extract(q.data, '$.a') AS a, (SELECT 1 FROM t) AS n FROM q"), ['id', 'a', 'n']);
  assert.equal(outerColumns('WITH c AS (SELECT id FROM t) SELECT c.* FROM c'), null);
  assert.deepEqual(outerColumns("WITH c AS (SELECT id FROM t) SELECT json_extract(data, '$.x') FROM c UNION SELECT 1"), []);
  assert.deepEqual(outerColumns("SELECT 'x' AS id"), ['id']);
});

test("rewriteQuery: false with '$.' shorthand is an error; json_* path arguments are not shorthand", () => {
  assert.deepEqual(shorthandPaths("SELECT '$.name' AS name, json_extract(data, '$.a'), json_set(x, '$.b', 1) FROM t WHERE COALESCE('$.c', 0) = 1 -- '$.d'"), ["'$.name'", "'$.c'"]);
  const ds = (query, rewriteQuery) => ({type: 'datasource.sqlite', options: {entities: ['t'], query, ...(rewriteQuery !== undefined && {rewriteQuery})}});
  const all = app => checkPerformance(app).findings.map(f => f.code).filter(c => !['unused-datasource', 'datasource-no-id'].includes(c));
  assert.deepEqual(all({datasources: {a: ds("SELECT '$.name' AS name FROM t", false)}}), ['rewrite-query-shorthand']);
  assert.deepEqual(all({datasources: {a: ds("SELECT json_extract(data, '$.name') AS name FROM t", false)}}), []);
  assert.deepEqual(all({datasources: {a: ds("SELECT '$.name' AS name FROM t")}}), ['rewrite-query-on']);
});

test('lists: datasource without id, instanceId on items, custom items, component.list, dynamic height', () => {
  const ds = query => ({type: 'datasource.sqlite', options: {entities: ['t'], query, rewriteQuery: false}});
  const item = (extra = {}) => ({type: 'component.list-item', options: {title: '=@ctx.current.item.name'}, ...extra});
  const app = {
    datasources: {noid: ds('SELECT name FROM t'), withid: ds('SELECT t.id, name FROM t')},
    jigs: {
      a: {type: 'jig.list', data: '=@ctx.datasources.noid', item: item({instanceId: 'x'})},
      b: {type: 'jig.list', data: "=@ctx.datasources.local[status='open']", item: item(), datasources: {local: ds('SELECT * FROM t')}},
      c: {type: 'jig.default', children: [{type: 'component.list', options: {data: '=@ctx.datasources.withid', item: {type: 'component.custom-component', options: {}}}}]},
      d: {type: 'jig.list', data: '=@ctx.datasources.withid', item: item(), options: {hasDynamicHeight: true}},
      e: {type: 'jig.list', data: '=$filter(@ctx.datasources.noid, function($v) {$v.ok})', item: item()},
    },
  };
  const got = checkPerformance(app).findings.filter(f => f.code !== 'unused-datasource').map(f => `${f.code} ${f.where}`).sort();
  assert.deepEqual(got, [
    'component-list-for-data jigs/c › children.0',
    'list-custom-component-item jigs/c › children.0.options.item',
    'list-dynamic-height jigs/d',
    'list-item-instance-id jigs/a › item',
    'list-no-id jigs/a',
  ]);
});

test('OR across keys of one table: per row is high, a top-level filter medium, one key or AND is not', () => {
  const perRow = "SELECT q.id FROM queue q JOIN header h ON (h.id = q.k OR json_extract(h.data, '$.ApptID') = q.k OR trim(json_extract(h.data, '$.Nbr')) = q.k)";
  assert.deepEqual(orAcrossKeys(perRow).map(o => [o.keys.length, o.perRow]), [[3, true]]);
  assert.deepEqual(orAcrossKeys("SELECT id FROM h WHERE id = @a OR json_extract(data, '$.Nbr') = @a").map(o => o.perRow), [false]);
  assert.deepEqual(orAcrossKeys("SELECT id FROM h WHERE json_extract(data, '$.S') = 'a' OR json_extract(data, '$.S') = 'b'"), []);
  assert.deepEqual(orAcrossKeys("SELECT id FROM h WHERE json_extract(data, '$.status') = 'x' OR json_extract(data, '$.retry') = 1"), [], 'constants are filters, not key lookups');
  assert.deepEqual(orAcrossKeys("SELECT 1 FROM q WHERE EXISTS (\n      SELECT 1 FROM c WHERE trim(c.id) = q.k OR trim(json_extract(c.data, '$.K')) = q.k)").map(o => o.perRow), [true]);
  assert.deepEqual(orAcrossKeys("SELECT id FROM h WHERE (id = @a AND x = 1) OR json_extract(data, '$.Nbr') = @a"), []);
  const cte = "WITH k AS (SELECT id FROM h WHERE id = @a OR json_extract(data, '$.Nbr') = @a) SELECT * FROM k";
  assert.deepEqual(orAcrossKeys(cte).map(o => o.perRow), [false], 'a CTE body is not a correlated subquery');
  const app = {datasources: {a: ds(perRow, ['queue', 'header']), b: ds("SELECT id FROM h WHERE id = @a OR json_extract(data, '$.Nbr') = @a", ['h'])}};
  const got = checkPerformance(app).findings.filter(f => f.code === 'or-across-keys').map(f => `${f.where} ${f.priority}`);
  assert.deepEqual(got, ['datasources/a › options.query high', 'datasources/b › options.query medium']);
});

test('ORDER BY on a JSON field without an index, watched tables the query does not read, datasources without id', () => {
  assert.deepEqual(orderByTerms("SELECT id FROM t ORDER BY json_extract(o.data, '$.Date') DESC, id LIMIT 5"), [normaliseExpr("json_extract(o.data, '$.Date')")]);
  const app = {datasources: {
    a: ds("SELECT id FROM [orders] ORDER BY json_extract(data, '$.Date') DESC", ['default/orders', 'customers']),
    b: ds("SELECT json_extract(data, '$.n') AS n FROM [orders]", ['orders']),
    c: ds('SELECT count(*) AS n FROM [orders]', ['orders']),
    d: ds("SELECT json_extract(data, '$.n') AS n FROM [orders] LIMIT 1", ['orders']),
  }, actions: {i: {type: 'action.execute-sql', options: {entities: ['default/debug'], statements: [
    "CREATE INDEX IF NOT EXISTS idx_b ON [orders] (json_extract(data, '$.Other'))"]}}}};
  const got = checkPerformance(app).findings.filter(f => ['order-by-unindexed', 'watched-table-unread', 'datasource-no-id'].includes(f.code)).map(f => `${f.code} ${f.where}`).sort();
  assert.deepEqual(got, ['datasource-no-id datasources/b', 'order-by-unindexed datasources/a › options.query', 'watched-table-unread datasources/a › options.entities']);
});

test('sync behaviour: handlers, double scheduling, sequential calls, file transfers, focus syncs', () => {
  const sync = {type: 'action.sync-entities', options: {provider: 'DATA_PROVIDER_REST', entities: ['orders']}};
  const run = id => ({type: 'action.execute-action', options: {action: id}});
  const app = {
    onTableChanged: [
      {table: '_syncStatus', action: {type: 'action.action-list', options: {actions: [run('load')]}}},
      {table: '_syncScope', when: '=$count(@ctx.datasources.queue) > 0', action: {type: 'action.action-list', options: {actions: [{type: 'action.set-state', options: {state: '=@ctx.solution.state.busy', value: true}}]}}},
      {table: 'orders', when: '=@ctx.solution.state.ready', action: {type: 'action.action-list', options: {actions: []}}},
    ],
    actions: {
      load: {action: {type: 'action.action-list', options: {isSequential: true, actions: [sync, sync, sync, sync]}}},
      after: {action: {type: 'action.action-list', options: {actions: [run('load'), {type: 'action.execute-entity', options: {method: 'download', provider: 'DATA_PROVIDER_DYNAMIC'}}]}}},
      writer: {action: {type: 'action.action-list', options: {actions: [{type: 'action.execute-sql', options: {entities: ['_syncStatus'], statements: ['UPDATE _syncStatus SET x = 1']}}, run('load')]}}},
    },
    jigs: {
      a: {type: 'jig.default', onFocus: run('load')},
      b: {type: 'jig.default', onFocus: {type: 'action.action-list', options: {actions: [{...sync, when: '=$stale'}]}}},
    },
  };
  const got = checkPerformance(app).findings.map(f => `${f.code} ${f.where} ${f.priority}`).filter(x => !/unused-datasource|synced-table-unread/.test(x)).sort();
  assert.deepEqual(got, [
    'double-scheduled-action actions/load medium',
    'file-transfer-sequential actions/after › action.options.actions.1 medium',
    'heavy-focus jigs/a › onFocus medium',
    'sequential-sync-steps actions/load › action medium',
    'solution-state-in-handler index › onTableChanged.1.action.options.actions.0 low',
    'table-change-hot-guard index › onTableChanged.1 medium',
    'table-change-unguarded index › onTableChanged.0 high',
  ]);
  // The screen re-runs the sync the app runs on start: a full sync on every tab switch.
  const focus = checkPerformance({...app, onLoad: run('load')}).findings.find(f => f.code === 'heavy-focus');
  assert.equal(focus.priority, 'high');
  assert.match(focus.message, /load is the same sync the app runs on start/);
});

test('expressions: many datasource filters in one expression; solution state in a global datasource', () => {
  const filters = Array.from({length: 10}, (_, i) => `$count(@ctx.datasources.sync[step='${i}'])`).join(' + ');
  const app = {
    datasources: {g: {type: 'datasource.sqlite', options: {entities: ['t'], query: 'SELECT id FROM t WHERE x = @x', rewriteQuery: false,
      queryParameters: {x: '=@ctx.solution.state.branch'}}}},
    jigs: {a: {type: 'jig.default', title: '=' + filters}},
  };
  const got = checkPerformance(app).findings.map(f => f.code).filter(c => !['unused-datasource', 'unindexed-predicate'].includes(c)).sort();
  assert.deepEqual(got, ['heavy-expression', 'solution-state-in-global-datasource']);
});

test('an OR with an index on every arm and a placeholder entity are not findings', () => {
  const idx = {type: 'action.execute-sql', options: {entities: ['default/debug'], statements: [
    "CREATE INDEX IF NOT EXISTS idx_nbr ON [h] (json_extract(data, '$.Nbr'))"]}};
  const app = {datasources: {
    a: ds("SELECT id FROM [h] WHERE id = @a OR json_extract(data, '$.Nbr') = @a", ['h']),
    b: ds("SELECT date('now') AS id", ['dummy']),
  }, actions: {idx}};
  assert.deepEqual(checkPerformance(app).findings.filter(f => ['or-across-keys', 'watched-table-unread'].includes(f.code)), []);
});

test('expressions that throw on null or the wrong type, graded by where the value comes from', () => {
  // The two errors found in device logs.
  assert.deepEqual(riskyCalls('=$trim(@ctx.components.task.state.value)'), [{fn: 'trim', ref: '@ctx.components.task.state.value'}]);
  assert.equal(riskyCalls('=$length(@ctx.components.nbr.state.value) > 0').length, 1);
  assert.deepEqual(riskyCalls('=$type(@ctx.components.a.state.value) = "string" ? $trim(@ctx.components.a.state.value) : ""'), []);
  assert.deepEqual(riskyCalls('=@ctx.current.item.Date ? $toMillis(@ctx.current.item.Date) : 0'), []);
  assert.deepEqual(riskyCalls('=$substringBefore(@ctx.current.item.data.Date.value, "T")'), [], 'a missing JSON field is undefined, which is safe');
  const app = {
    datasources: {
      rows: ds("SELECT id, COALESCE(json_extract(data, '$.N'), '') AS n, json_extract(data, '$.M') AS m FROM [t]", ['t']),
    },
    jigs: {
      l: {type: 'jig.list', data: '=@ctx.datasources.rows', item: {type: 'component.list-item', options: {
        title: '=$trim(@ctx.current.item.m)', subtitle: '=$trim(@ctx.current.item.n)'}}},
      f: {type: 'jig.default', title: '=$number(@ctx.components.amount.state.value) + $trim(@ctx.datasources.rows.m)'},
    },
  };
  const got = checkPerformance(app).findings.filter(f => f.code === 'expression-may-throw').map(f => `${f.where} ${f.priority} ${f.message.split(' ')[0]}`).sort();
  assert.deepEqual(got, [
    'jigs/f › title low $trim(@ctx.datasources.rows.m)',
    'jigs/f › title medium $number(@ctx.components.amount.state.value)',
    'jigs/l › item.options.title medium $trim(@ctx.current.item.m)',
  ]);
});

test('expression scripts: console.log, simple functions, unused functions', () => {
  const js = `export function label(s) {
  console.log(s)
  return s === 'A' ? 'Active' : 'Other'
}
export function parse(rows) {
  return rows.map(r => r.id).filter(Boolean)
}
export function unused(x) { return x }
function helper() { return 1 }
`;
  assert.deepEqual(scriptFunctions(js).map(f => f.name), ['label', 'parse', 'unused']);
  const app = {scripts: {expressions: {'util.js': js}}, jigs: {a: {type: 'jig.default', title: '=$util.label(@ctx.jig.inputs.s) & $util.parse([])'}}};
  const got = checkPerformance(app).findings.filter(f => /script/.test(f.code)).map(f => `${f.code} ${f.where} ${f.priority}`).sort();
  assert.deepEqual(got, [
    'script-console-log scripts/expressions/util.js › label medium',
    'simple-script-function scripts/expressions/util.js › label low',
    'unused-script-function scripts/expressions/util.js › unused low',
  ]);
});

test('a join on columns a CTE computes from one table is found; direct joins and aggregates are not', () => {
  const q = `WITH loc AS (SELECT json_extract(data, '$.AccountID') AS AccountID, trim(json_extract(data, '$.CD')) AS CD FROM locations),
    cust AS (SELECT trim(id) AS sid, data FROM customers),
    agg AS (SELECT json_extract(data, '$.Nbr') AS Nbr, count(*) AS n FROM lines GROUP BY 1)
    SELECT o.id FROM opportunities o
    LEFT JOIN cust AS c ON c.sid = @customer
    LEFT JOIN loc l ON l.AccountID = json_extract(o.data, '$.AccountID') AND l.CD = 'X'
    LEFT JOIN agg a ON a.Nbr = o.id
    LEFT JOIN locations AS direct ON json_extract(direct.data, '$.AccountID') = 1`;
  assert.deepEqual(cteJoins(q).map(c => `${c.cte}:${c.columns.join('+')}:${c.table}`), ['cust:sid:customers', 'loc:accountid+cd:locations']);
  // Through a chain of CTEs, and not when the joined CTE is MATERIALIZED (built once, auto-indexed).
  const chain = `WITH src AS (SELECT trim(json_extract(h.data, '$.Nbr')) AS nbr, h.data FROM headers h),
    offs AS (SELECT nbr, CASE WHEN 1 THEN 'a' END AS o FROM src),
    built AS MATERIALIZED (SELECT nbr FROM src)
    SELECT l.id FROM logs l LEFT JOIN offs o ON o.nbr = l.nbr LEFT JOIN built b ON b.nbr = l.nbr`;
  assert.deepEqual(cteJoins(chain).map(c => `${c.cte}:${c.columns.join('+')}:${c.table}`), ['offs:nbr:headers']);
  const app = {datasources: {opps: {type: 'datasource.sqlite', options: {entities: ['opportunities'], query: q}}}};
  assert.ok(checkPerformance(app).findings.some(f => f.code === 'cte-join-no-index'));
});

test('filter-inside-map sees only the $map function body', async () => {
  const {mapBodies} = await import('./check-performance.mjs');
  const after = "$merge([{'Products': $map(@ctx.datasources.deleted, function($v){ {'id': $v.id} })}, $count(@ctx.datasources.details[x = 1]) = 0 ? {} : {}])";
  assert.equal(mapBodies(after).some(b => /@ctx\.datasources\.[\w-]+\s*\[/.test(b)), false);
  const inside = "$map(@ctx.datasources.logs, function($l){ @ctx.datasources.employees[id = $l.emp][0].name })";
  assert.equal(mapBodies(inside).some(b => /@ctx\.datasources\.[\w-]+\s*\[/.test(b)), true);
  const nextLine = "($c := $map($parts, function($p){ $trim($p) }); $crew := @ctx.datasources.staff[nbr = 1].name)";
  assert.equal(mapBodies(nextLine).some(b => /@ctx\.datasources\.[\w-]+\s*\[/.test(b)), false);
});

test('per-row reads skip tap handlers and rise to high with 4+ filters per row', () => {
  const tap = {type: 'component.list-item', options: {title: '=@ctx.current.item.name',
    onPress: {type: 'action.go-to', options: {parameters: {x: '=@ctx.datasources.details[id=@ctx.current.item.id].x'}}},
    swipeable: {left: [{label: 'Delete', onPress: {type: 'action.execute-entity', options: {data: '=@ctx.datasources.files[id=1]'}}}]}}};
  assert.deepEqual(codes({jigs: {list: {item: tap}}}), []);
  const heavy = {type: 'component.list-item', options: {
    title: '=$count(@ctx.datasources.photos[log=@ctx.current.item.id]) & " " & $count(@ctx.datasources.photos[log=@ctx.current.item.id and done])',
    subtitle: '=@ctx.datasources.photos[log=@ctx.current.item.id][0].name & @ctx.datasources.photos[log=@ctx.current.item.id][-1].name'}};
  const f = checkPerformance({jigs: {list: {item: heavy}}}).findings.find(x => x.code === 'per-row-datasource-read');
  assert.equal(f.priority, 'high');
  assert.match(f.message, /4 datasource filters per row/);
});

test('a fixed non-date filter is a full pull; one-record calls need no $select; heavy-focus names the syncing action', () => {
  const sync = entities => ({type: 'action.sync-entities', options: {entities}});
  const app = {
    functions: {'get-projects': {method: 'GET', url: 'https://{u}Project', parameters: {$filter: {location: 'query'}, $expand: {location: 'query'}}},
                'whoami': {method: 'GET', url: 'https://{u}Employee', parameters: {$filter: {location: 'query'}, $expand: {location: 'query'}}}},
    actions: {
      s: {action: sync([{entity: 'default/projects', function: 'get-projects', parameters: {$filter: "Status eq 'Active'"}},
                        {entity: 'default/me', function: 'whoami', parameters: {$filter: '="LoginName eq \'" & @ctx.action.parameters.user & "\'"'}}])},
      'sync-appts': {action: sync([{entity: 'default/appts', function: 'get-projects'}])},
      wrap: {action: {type: 'action.action-list', options: {actions: [{type: 'action.execute-action', options: {action: 'sync-appts'}},
                                                                     {type: 'action.execute-action', options: {action: 'action-logout'}}]}}},
      'action-logout': {action: {type: 'action.set-state', options: {state: '=@ctx.solution.state.x', value: 1}}},
    },
    jigs: {list: {type: 'jig.default', onFocus: {type: 'action.execute-action', options: {action: 'wrap'}}}},
    datasources: {p: ds('SELECT * FROM [default/projects] JOIN [default/me] JOIN [default/appts]', ['default/projects', 'default/me', 'default/appts'])},
  };
  const f = checkPerformance(app).findings;
  const full = f.filter(x => x.code === 'sync-full-pull').map(x => x.message);
  assert.ok(full.some(m => /^projects .*does not compare a modified date/.test(m)));
  assert.ok(!full.some(m => /^me /.test(m)));
  assert.ok(!f.some(x => x.code === 'sync-no-select' && /whoami/.test(x.message)));
  const focus = f.find(x => x.code === 'heavy-focus');
  assert.match(focus.message, /syncs 1 entity \(appts\) through sync-appts every time/);
  assert.doesNotMatch(focus.message, /logout|full/);
});

test('sequential-sync-steps follows execute-action: onLoad calling sync actions in series', () => {
  const sync = e => ({action: {type: 'action.sync-entities', options: {entities: [e]}}});
  const call = name => ({type: 'action.execute-action', options: {action: name}});
  const app = {
    actions: {issues: sync('issues'), transfers: sync('transfers'), pos: sync('pos'), whs: {action: {type: 'action.action-list', options: {actions: [call('pos')]}}},
              logout: {action: {type: 'action.set-state', options: {state: '=@ctx.solution.state.x', value: 1}}}},
    onLoad: {type: 'action.action-list', options: {isSequential: true, actions: [call('issues'), call('transfers'), call('whs'), call('logout')]}},
  };
  const f = checkPerformance(app).findings.find(x => x.code === 'sequential-sync-steps');
  assert.ok(f, 'flagged');
  assert.match(f.message, /3 sync steps run one after another \(issues, transfers, whs\)/);
  assert.match(RULES['sequential-sync-steps'].fix, /write the same table/);
});

test('expression safety: bracketed guards count, keys are low, and the report gives them their own block', () => {
  assert.deepEqual(riskyCalls("=((@ctx.current.item.Date) ? $substring(@ctx.current.item.Date, 0, 16) :'')"), []);
  assert.equal(riskyCalls("=($exists(@ctx.current.item.Date) ? $substring(@ctx.current.item.Date, 0, 16) :'')").length, 1);
  const list = (title) => ({type: 'jig.list', data: '=@ctx.datasources.rows', item: {type: 'component.list-item', options: {title}}});
  const grade = title => checkPerformance({jigs: {l: list(title)}}).findings.find(f => f.code === 'expression-may-throw')?.priority;
  assert.equal(grade('=$trim(@ctx.current.item.AppointmentNbr)'), 'low');
  assert.equal(grade('=$trim(@ctx.current.item.Description)'), 'medium');
  const findings = checkPerformance({jigs: {l: list('=$trim(@ctx.current.item.Description)')}}).findings;
  const md = markdownReport({title: 'sol', findings});
  assert.match(md, /^\*\*0 high · 0 medium · \d+ low\*\* · 1 expression-safety findings/m);
  assert.match(md, /## Expression safety \(1: 1 medium, 0 low\)/);
  assert.doesNotMatch(md, /### expression-may-throw/);
});
