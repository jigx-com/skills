import test from 'node:test';
import assert from 'node:assert/strict';
import {collect, compareTimings, growth, paramNames, sampleQueries, scaleDatabase, sqlLiterals, timeSolution} from './time-queries.mjs';

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
  const after = {queries: [{where: 'a', ms: 20}, {where: 'b', ms: 4}, {where: 'c', ms: 60}]};
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
