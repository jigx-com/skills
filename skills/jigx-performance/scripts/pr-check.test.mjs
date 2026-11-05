import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {changedSolutions, compareAudits, findSolutions, prReport} from './pr-check.mjs';

const SCRIPT = new URL('./pr-check.mjs', import.meta.url).pathname;
const f = (code, priority, file = 'datasources/a.jigx', message = 'm') => ({code, priority, severity: 'warning', where: `${file} › options`, message, fix: 'fix it'});

test('finds YAML and SDK solutions, skipping hidden folders and nested copies', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-repo-'));
  const write = rel => { fs.mkdirSync(path.dirname(path.join(root, rel)), {recursive: true}); fs.writeFileSync(path.join(root, rel), ''); };
  write('Base/index.jigx'); write('Base/old/index.jigx'); write('apps/expenses/src/app.ts');
  write('.jigx-skills/demo/index.jigx'); write('node_modules/x/index.jigx');
  const found = findSolutions(root);
  assert.deepEqual(found, [{dir: 'Base', kind: 'yaml'}, {dir: 'apps/expenses', kind: 'sdk'}]);
  assert.deepEqual(changedSolutions(found, ['Base/jigs/a.jigx', 'README.md']).map(s => s.dir), ['Base']);
  assert.deepEqual(changedSolutions(found, ['Basement/x']), []);
});

test('only findings the branch adds count; fixes and moves are not regressions', () => {
  const base = [f('per-row-datasource-read', 'medium'), f('rewrite-query-on', 'low')];
  const head = [f('rewrite-query-on', 'low'), f('rewrite-query-shorthand', 'high'), f('list-no-id', 'medium')];
  const {added, fixed, gated} = compareAudits(base, head, 'high');
  assert.deepEqual(added.map(x => x.code), ['rewrite-query-shorthand', 'list-no-id']);
  assert.deepEqual(fixed.map(x => x.code), ['per-row-datasource-read']);
  assert.deepEqual(gated.map(x => x.code), ['rewrite-query-shorthand']);
  assert.deepEqual(compareAudits(base, head, 'medium').gated.map(x => x.code), ['rewrite-query-shorthand', 'list-no-id']);
  // A new solution has no target to compare with: everything in it is new.
  assert.equal(compareAudits(null, head).added.length, 3);
  const report = prReport([{dir: 'Base', base, head, added, fixed, gated}], {base: 'origin/main', failOn: 'high'});
  assert.match(report, /\*\*Fails:\*\*/);
  assert.match(report, /New, high priority \(fails the check\)/);
  assert.match(report, /Fixed by this branch\n\nper-row-datasource-read \(1\)/);
});

test('fails a branch that adds a high finding and passes one that does not', {skip: spawnSync('ruby', ['-v']).status !== 0 && 'ruby not installed'}, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-pr-'));
  const write = (rel, text) => { fs.mkdirSync(path.dirname(path.join(root, rel)), {recursive: true}); fs.writeFileSync(path.join(root, rel), text); };
  const git = (...args) => assert.equal(spawnSync('git', args, {cwd: root}).status, 0, `git ${args.join(' ')}`);
  const ds = (select, extra = '') => `type: datasource.sqlite\noptions:\n  provider: DATA_PROVIDER_LOCAL\n  entities: [default/orders]\n${extra}  query: SELECT id, ${select} AS name FROM [default/orders]\n`;
  const jig = 'type: jig.list\ntitle: Orders\ndata: =@ctx.datasources.orders\nitem:\n  type: component.list-item\n  options:\n    title: =@ctx.current.item.name\n';
  write('Base/index.jigx', 'name: base\ntitle: Base\n');
  write('Base/datasources/orders.jigx', ds("json_extract(data, '$.name')", '  rewriteQuery: false\n'));
  write('Base/jigs/orders.jigx', jig);
  write('README.md', 'repo\n');
  git('init', '-q', '-b', 'main'); git('add', '.'); git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base');
  const check = () => spawnSync('node', [SCRIPT, '--base', 'main', '--report', 'report.md'], {cwd: root, encoding: 'utf8'});

  git('checkout', '-qb', 'docs'); write('README.md', 'docs only\n');
  git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'docs');
  let run = check();
  assert.equal(run.status, 0, run.stderr);
  assert.match(fs.readFileSync(path.join(root, 'report.md'), 'utf8'), /No Jigx solution changed/);

  git('checkout', '-qb', 'shorthand', 'main');
  write('Base/datasources/orders.jigx', ds("'$.name'", '  rewriteQuery: false\n'));
  git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'shorthand');
  run = check();
  assert.equal(run.status, 1, run.stderr);
  assert.match(run.stderr, /\[high\] Base\/datasources\/orders\.jigx/);
  assert.equal(JSON.parse(run.stdout).solutions[0].gated[0].code, 'rewrite-query-shorthand');
});
