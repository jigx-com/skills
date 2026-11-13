import test from 'node:test';
import assert from 'node:assert/strict';
import {atScale, collect, compareTimings, dataKeys, growth, guessParam, paramNames, sampleQueries, scaleDatabase, sqlLiterals, stateKey, timeSolution, timingsReport} from './time-queries.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';

const sqlite = await import('node:sqlite').catch(() => null);
const ds = (query, extra = {}) => ({type: 'datasource.sqlite', options: {entities: ['orders'], query, rewriteQuery: false, ...extra}});

test('parameter names outside strings and comments', () => {
  assert.deepEqual(paramNames("SELECT '@not' FROM t WHERE a = @a AND b = :b -- @c\n AND json_extract(data, '$.x') = $d"), ['a', 'b', 'd']);
});

test('sampling probes the expression a parameter is compared to, aliases resolved', () => {
  const s = sampleQueries("SELECT o.id, trim(json_extract(o.data, '$.Nbr')) AS Nbr FROM [orders] AS o JOIN lines l ON l.id = o.id WHERE Nbr = @nbr AND @line = l.id");
  assert.equal(s.nbr, "SELECT trim(json_extract(o.data, '$.Nbr')) AS v FROM [orders] AS o WHERE trim(json_extract(o.data, '$.Nbr')) IS NOT NULL LIMIT 1");
  assert.equal(s.line, 'SELECT l.id AS v FROM lines AS l WHERE l.id IS NOT NULL LIMIT 1');
  assert.deepEqual(sampleQueries("SELECT 1 FROM t WHERE instr(x, @s) > 0"), {});
});

test('collect merges SDK copies of one query and skips SQL built by an expression', () => {
  const q = "SELECT id FROM [orders]";
  const app = {datasources: {a: ds(q)}, jigs: {j: {datasources: {a: ds(q)}}},
    actions: {x: {action: {type: 'action.execute-sql', options: {entities: ['default/debug'], statements: [
      "CREATE INDEX IF NOT EXISTS idx_o ON [orders] (json_extract(data, '$.Nbr'))", '="DELETE FROM orders WHERE id IN " & $join(@ctx.x)']}}}}};
  const c = collect(app);
  assert.equal(c.queries.length, 1);
  assert.equal(c.queries[0].copies, 2);
  assert.equal(c.indexes.length, 1);
  assert.deepEqual(c.steps, []);
  assert.deepEqual(c.built, ['$.actions.x.action']);
});

test('times queries on real rows, builds indexes, rolls back writes', {skip: !sqlite && 'node:sqlite needs Node 22.5+'}, () => {
  const db = new sqlite.DatabaseSync(':memory:');
  db.exec('CREATE TABLE orders (id TEXT PRIMARY KEY, data TEXT, timestamp INTEGER)');
  const insert = db.prepare('INSERT INTO orders VALUES (?, ?, 0)');
  for (let i = 0; i < 50; i++) insert.run(`o${i}`, JSON.stringify({Nbr: `N${i}`}));
  const app = {
    datasources: {
      byNbr: ds("SELECT id FROM [orders] WHERE json_extract(data, '$.Nbr') = @nbr"),
      missing: ds('SELECT id FROM [nope]'),
    },
    actions: {
      idx: {action: {type: 'action.execute-sql', options: {statements: ["CREATE INDEX IF NOT EXISTS idx_nbr ON [orders] (json_extract(data, '$.Nbr'))"]}}},
      wipe: {action: {type: 'action.execute-sql', options: {statements: ['DELETE FROM [orders]']}}},
    },
  };
  const r = timeSolution(app, db, {runs: 3});
  const byNbr = r.queries.find(q => q.where === 'datasources/byNbr');
  assert.equal(byNbr.rows, 1, 'the sampled value finds a real row');
  assert.deepEqual(byNbr.sampled, ['nbr']);
  assert.match(r.queries.find(q => q.where === 'datasources/missing').error, /no such table/);
  assert.equal(r.indexes[0].index, 'idx_nbr');
  assert.equal(typeof r.indexes[0].ms, 'number');
  assert.equal(r.actions[0].where, 'actions/wipe › action');
  assert.equal(db.prepare('SELECT count(*) AS n FROM orders').get().n, 50, 'writes are rolled back');
  assert.equal(timeSolution(app, db, {params: {nbr: 'none'}, runs: 2}).queries.find(q => q.where === 'datasources/byNbr').rows, 0);
});

test('comparison flags only real moves, keyed by location', () => {
  const before = {queries: [{where: 'a', ms: 100}, {where: 'b', ms: 2}, {where: 'c', ms: 40}]};
  const after = {queries: [{where: 'a', ms: 20}, {where: 'b', ms: 2.8}, {where: 'c', ms: 60}]};
  assert.deepEqual(compareTimings(before, after).map(r => [r.where, r.verdict]), [['a', 'faster'], ['c', 'SLOWER'], ['b', 'same']]);
});

test('scaling keeps linking keys apart per copy and leaves literal values alone', {skip: !sqlite && 'node:sqlite not available'}, () => {
  const db = new sqlite.DatabaseSync(':memory:');
  db.exec(`CREATE TABLE orders (id TEXT PRIMARY KEY, data TEXT); CREATE TABLE lines (id TEXT PRIMARY KEY, data TEXT);
    INSERT INTO orders VALUES ('O1  ', '{"Nbr": "N1", "Status": "Open", "Account": 7}'), ('O2', '{"Nbr": "N2", "Status": "Open", "Account": 8}');
    INSERT INTO lines VALUES ('L1', '{"Nbr": "N1", "Status": "Open", "Account": 7, "Order": "O1"}'), ('L2', '{"Nbr": "N2", "Status": "Open", "Order": "O2"}')`);
  const literals = sqlLiterals(["SELECT * FROM orders WHERE json_extract(data, '$.Status') = 'Open' AND x = 1"]);
  assert.ok(literals.has('Open') && literals.has('1'));
  const info = scaleDatabase(db, 3, literals);
  assert.equal(info.rowsAdded, 8);
  const join = "SELECT count(*) AS n FROM orders o JOIN lines l ON json_extract(l.data, '$.Nbr') = json_extract(o.data, '$.Nbr')";
  assert.equal(db.prepare(join).get().n, 6); // 2 per copy, not 2 x 9
  assert.equal(db.prepare("SELECT count(*) AS n FROM orders WHERE json_extract(data, '$.Status') = 'Open'").get().n, 6);
  assert.equal(db.prepare("SELECT count(*) AS n FROM orders o JOIN lines l ON json_extract(l.data, '$.Order') = trim(o.id)").get().n, 6);
  // 7 links two tables and moves per copy; 8 is in one table only and stays.
  assert.equal(db.prepare("SELECT json_extract(data, '$.Account') AS a FROM orders WHERE trim(id) = 'O1_x2'").get().a, 2e9 + 7);
  assert.equal(db.prepare("SELECT json_extract(data, '$.Account') AS a FROM orders WHERE id = 'O2_x2'").get().a, 8);
});

test('growth flags only items that grow faster than the data and real time', () => {
  const base = {queries: [{where: 'a', ms: 1, rows: 10}, {where: 'b', ms: 5, rows: 10}, {where: 'c', ms: 2, rows: 10}, {where: 'd', ms: 0.3, rows: 5}], actions: []};
  const big = {queries: [{where: 'a', ms: 10, rows: 100}, {where: 'b', ms: 300, rows: 100}, {where: 'c', ms: 50, rows: 1000}, {where: 'd', ms: 6, rows: 50}], actions: []};
  const v = Object.fromEntries(growth(base, big, 10).map(r => [r.where, r.verdict]));
  assert.deepEqual(v, {a: 'in line', b: 'GROWS FASTER', c: 'rows inflated', d: 'in line'});
});

test('a small query that gets much faster counts; sub-millisecond jitter does not; scaled runs compare too', () => {
  const run = ms => ({queries: [{where: 'a', ms: ms[0]}, {where: 'b', ms: ms[1]}, {where: 'c', ms: ms[2]}]});
  const v = compareTimings(run([2.5, 0.3, 40]), run([0.2, 0.8, 41])).reduce((o, r) => ({...o, [r.where]: r.verdict}), {});
  assert.deepEqual(v, {a: 'faster', b: 'same', c: 'same'});
  const g = ms => [{kind: 'queries', where: 'a', ms_1x: 1, ms_nx: ms}];
  assert.equal(compareTimings(atScale(g(213)), atScale(g(2.1)))[0].verdict, 'faster');
});

test('a wrapped parameter (trim(@barcode)) is sampled from the other side', () => {
  const s = sampleQueries("SELECT * FROM stock s WHERE trim(json_extract(s.data,'$.Code')) = trim(@barcode)");
  assert.match(s.barcode, /SELECT trim\(json_extract\(s\.data,'\$\.Code'\)\) AS v FROM stock AS s/);
  assert.ok(sampleQueries("SELECT * FROM stock s WHERE trim(COALESCE(@code, '')) = trim(s.id)").code);
});

test('NULL-parameter timings are not measurements; index names lose their quoting and count once', () => {
  const before = {queries: [{where: 'a', ms: 2.5, rows: 0, unbound: ['barcode'], nullParams: true}, {where: 'b', ms: 9, rows: 3}]};
  const after = {queries: [{where: 'a', ms: 0.1, rows: 0, unbound: ['barcode'], nullParams: true}, {where: 'b', ms: 1, rows: 3}]};
  const rows = Object.fromEntries(compareTimings(before, after).map(r => [r.where, r.verdict]));
  assert.equal(rows.a, 'not measured (NULL parameters)');
  assert.equal(rows.b, 'faster');
  assert.equal(growth(before, after, 10).find(r => r.where === 'a').verdict, 'not measured (NULL parameters)');
  const step = sql => ({type: 'action.execute-sql', options: {statements: [{statement: sql}]}});
  const app = {actions: {a: step('CREATE INDEX IF NOT EXISTS `idx_tasks_trim` ON [default/tasks] (trim(id))'),
                         b: step('CREATE INDEX IF NOT EXISTS idx_tasks_trim ON [default/tasks] (trim(id))')}};
  assert.deepEqual(collect(app).indexes.map(i => i.name), ['idx_tasks_trim']);
});

test('a parameter nothing samples is guessed from a same-named column: the busiest record', {skip: !sqlite && 'node:sqlite needs Node 22.5+'}, () => {
  const db = new sqlite.DatabaseSync(':memory:');
  db.exec('CREATE TABLE [default/photologs] (id TEXT PRIMARY KEY, data TEXT); CREATE TABLE _syncScope (id TEXT, state TEXT)');
  const add = db.prepare('INSERT INTO [default/photologs] VALUES (?, ?)');
  ['A', 'B', 'B', 'B', 'C'].forEach((p, i) => add.run(`l${i}`, JSON.stringify({ProjectID: p, LogNbr: i})));
  assert.ok(!dataKeys(db).some(k => k.table === '_syncScope'));
  assert.deepEqual(guessParam(db, 'selectedProject'), {value: 'B', from: 'default/photologs.ProjectID', rows: 3});
  assert.equal(guessParam(db, 'searchText'), null);
  // the query reads the project through solution state, so sampling cannot see it; the guess binds it
  const app = {datasources: {logs: ds("SELECT id FROM [default/photologs] WHERE instr(json_extract(data, '$.ProjectID'), @projectId) > 0", {entities: ['default/photologs']})}};
  const r = timeSolution(app, db, {runs: 1});
  assert.equal(r.queries[0].rows, 3);
  assert.deepEqual(r.queries[0].guessed, ['projectId']);
  assert.equal(r.queries[0].nullParams, undefined);
  assert.deepEqual(Object.values(r.guesses), [{name: 'projectId', value: 'B', from: 'default/photologs.ProjectID', rows: 3}]);
  // a key in the query's own table wins over a busier one elsewhere
  db.exec('CREATE TABLE [default/other] (id TEXT, data TEXT)');
  for (let i = 0; i < 9; i++) db.prepare('INSERT INTO [default/other] VALUES (?, ?)').run(`o${i}`, JSON.stringify({ProjectID: 'Z'}));
  assert.equal(guessParam(db, 'projectId', undefined, ['default/photologs']).value, 'B');
  assert.equal(guessParam(db, 'projectId').value, 'Z');
  // a guess that returns no rows timed nothing: not a measurement
  const none = timeSolution({datasources: {x: ds("SELECT id FROM [default/photologs] WHERE json_extract(data, '$.LogNbr') = 99 AND instr(json_extract(data, '$.ProjectID'), @projectId) > 0", {entities: ['default/photologs']})}}, db, {runs: 1});
  assert.equal(none.queries[0].rows, 0);
  assert.equal(none.queries[0].nullParams, true);
  // a per-record count returns one row of 0 with its record NULL: that is no data, so it is guessed too
  const count = timeSolution({datasources: {c: ds("SELECT count(*) AS n FROM [default/photologs] WHERE instr(json_extract(data, '$.ProjectID'), @projectId) > 0", {entities: ['default/photologs']})}}, db, {runs: 1});
  assert.deepEqual(count.queries[0].guessed, ['projectId']);
});

test('an empty database copy stops with how to copy it', {skip: !sqlite && 'node:sqlite needs Node 22.5+'}, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-empty-'));
  const empty = path.join(dir, 'copy.db');
  const db = new sqlite.DatabaseSync(empty);
  db.exec("CREATE TABLE orders (id TEXT, data TEXT); CREATE TABLE __migrations (id TEXT); INSERT INTO __migrations VALUES ('1')");
  db.close();  // the runtime's own table has a row, the data table none: still empty
  fs.writeFileSync(path.join(dir, 'output.json'), JSON.stringify({datasources: {a: ds('SELECT id FROM orders')}}));
  const out = spawnSync(process.execPath, [new URL('./time-queries.mjs', import.meta.url).pathname, path.join(dir, 'output.json'), '--db', empty], {encoding: 'utf8'});
  assert.equal(out.status, 2);
  assert.match(out.stderr, /has no data rows .*--copy-db/s);
});

test('parameters that read one solution-state value get one value, the busiest, padding kept; empty tables are not measured', {skip: !sqlite && 'node:sqlite needs Node 22.5+'}, () => {
  // Project Central: 26 queries read @ctx.solution.state.active-project under four spellings of the name.
  const db = new sqlite.DatabaseSync(':memory:');
  db.exec('CREATE TABLE [default/drawinglogs] (id TEXT PRIMARY KEY, data TEXT); CREATE TABLE [default/submittals] (id TEXT PRIMARY KEY, data TEXT); CREATE TABLE [default/photologs] (id TEXT PRIMARY KEY, data TEXT)');
  const put = (t, rows) => rows.forEach((p, i) => db.prepare(`INSERT INTO [${t}] VALUES (?, ?)`).run(`${t}${i}`, JSON.stringify({ProjectId: p})));
  // The first row is a quiet project; the busiest across both tables is the padded "BUDGETBYM ".
  put('default/drawinglogs', ['QUIET', 'BUDGETBYM ', 'BUDGETBYM ']);
  put('default/submittals', ['QUIET', 'BUDGETBYM ', 'OTHER']);
  const active = '=@ctx.solution.state.active-project';
  const q = (t, name) => ds(`SELECT id FROM [${t}] WHERE json_extract(data, '$.ProjectId') = @${name}`, {entities: [t], queryParameters: {[name]: active}});
  const app = {datasources: {a: q('default/drawinglogs', 'ProjectId'), b: q('default/submittals', 'projectID'), c: q('default/photologs', 'Project')}};
  assert.equal(stateKey(active), 'active-project');
  assert.equal(stateKey('=@ctx.solution.state.a & "x"'), undefined);
  const r = timeSolution(app, db, {runs: 1});
  assert.deepEqual(r.state['active-project'], {value: 'BUDGETBYM ', rows: 3, from: ['default/drawinglogs', 'default/submittals'], queries: 3});
  const by = Object.fromEntries(r.queries.map(x => [x.where, x]));
  assert.equal(by['datasources/a'].rows, 2);
  assert.equal(by['datasources/b'].rows, 1);
  assert.deepEqual(by['datasources/b'].state, {projectID: 'active-project'});
  assert.deepEqual(by['datasources/b'].unbound, []);
  // photologs syncs only after a project is picked: empty in this copy, so not a measurement
  assert.deepEqual(by['datasources/c'].emptyTables, ['default/photologs']);
  const report = timingsReport({title: 't', result: r, meta: {host: 'h', sqlite: '3', db: 'd', rows: 6, tables: 3, runs: 1}});
  assert.match(report, /active-project = "BUDGETBYM " \(3 rows in default\/drawinglogs, default\/submittals\), for 3 queries/);
  assert.match(report, /## Empty tables: not measured \(1 queries\)[^]*default\/photologs/);
  assert.equal(compareTimings({queries: [{where: 'datasources/c', ms: 1, emptyTables: ['default/photologs']}]},
    {queries: [{where: 'datasources/c', ms: 0.1}]})[0].verdict, 'not measured (empty tables)');
  // params.json can pin the state by its name
  const pinned = timeSolution(app, db, {runs: 1, params: {'active-project': 'QUIET'}});
  assert.deepEqual(pinned.state['active-project'], {value: 'QUIET', given: true, queries: 3});
  assert.equal(pinned.queries.find(x => x.where === 'datasources/a').rows, 1);
});
