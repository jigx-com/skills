import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {checkSyntax, findJsonata, forParser} from './check-syntax.mjs';

const solution = files => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-syntax-'));
  for (const [f, text] of Object.entries({'index.jigx': 'name: demo\ntitle: Demo\n', ...files})) {
    fs.mkdirSync(path.dirname(path.join(dir, f)), {recursive: true});
    fs.writeFileSync(path.join(dir, f), text);
  }
  return dir;
};
const found = findJsonata(process.cwd());

test('Jigx @ctx and @. read as a variable for the parser', () => {
  assert.equal(forParser('$count(@ctx.datasources.x) > 0 and @.user.email'), '$count($ctx.datasources.x) > 0 and $ctx.user.email');
  assert.equal(forParser('"a@b.c"'), '"a@b.c"');
  // a hyphenated name is one name in Jigx (chkYes-3, big-list-10k), a subtraction in JSONata
  assert.equal(forParser('@ctx.components.chkYes-3.state.value'), '$ctx.components.`chkYes-3`.state.value');
  assert.equal(forParser('$count(@ctx.datasources.big-list-10k) - 1'), '$count($ctx.datasources.`big-list-10k`) - 1');
  assert.equal(forParser('@ctx.inputs.1position-team-avatar'), '$ctx.inputs.`1position-team-avatar`');
  assert.equal(forParser('@ctx.inputs.list[0].name'), '$ctx.inputs.list[0].name');
});

test('a " : " in a plain YAML value is a YAML error in that file (Project Central, a ternary in when:)', () => {
  const dir = solution({'jigs/a.jigx': 'title: A\ntype: jig.default\nwhen: =@ctx.jig.state.x ? true : false\n',
    'jigs/b.jigx': 'title: B\ntype: jig.default\nwhen: |\n  =@ctx.jig.state.x ? true : false\n'});
  const r = checkSyntax(dir);
  assert.deepEqual(r.yaml.map(e => e.file), ['jigs/a.jigx']);
  assert.deepEqual(checkSyntax(dir, {files: [path.join(dir, 'jigs/b.jigx')]}).yaml, []);
});

test('an expression that does not parse is named with its place (KOI: a ternary with no then-branch)', {skip: !found && 'no jsonata package on this machine'}, () => {
  const dir = solution({'jigs/photos.jigx': 'title: P\ntype: jig.default\nplaceholders:\n  - title: Loading\n    when: =$count(@ctx.datasources.file) > 0 = false :true\n' +
    'children:\n  - type: component.text\n    options:\n      value: =$uppercase(@ctx.inputs.title)\n'});
  const r = checkSyntax(dir, {jsonata: found.jsonata});
  assert.equal(r.expressions, 2);
  assert.deepEqual(r.errors.map(e => [e.where, e.code]), [['jigs/photos.jigx › placeholders.0.when', 'S0201']]);
});
