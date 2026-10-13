import test from 'node:test';
import assert from 'node:assert/strict';
import {checkPerformance, filterPredicates, parseCreateIndex, normaliseExpr} from './check-performance.mjs';

const codes = app => checkPerformance(app).findings.map(x => x.code).sort();
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
});
