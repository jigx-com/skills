import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {checkPerformance, filterPredicates, parseCreateIndex, normaliseExpr, loadSolution, markdownReport, newFindings, outerColumns, sdkSources, shorthandPaths, where} from './check-performance.mjs';

// Fixtures hold unreferenced global datasources and leave rewriteQuery on; those checks have their own tests.
const NOISE = new Set(['unused-datasource', 'rewrite-query-on']);
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
  const all = app => checkPerformance(app).findings.map(f => f.code).filter(c => c !== 'unused-datasource');
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
