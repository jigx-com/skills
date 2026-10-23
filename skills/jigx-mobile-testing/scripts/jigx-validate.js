#!/usr/bin/env node
// Validate .jigx files: YAML parse + every '='-prefixed expression through the real JSONata parser.
// Catches what a plain YAML parse misses: colon-space in an inline `=` expression, `#` "comments" or
// an unbalanced apostrophe inside a JSONata block. @ctx is rewritten to $ctx so stock jsonata accepts it.
// Usage: node jigx-validate.js <file.jigx> [...]      (exit 1 on any failure; usable as a pre-commit hook)
//        git diff --name-only origin/<base>...HEAD -- '*.jigx' | xargs node jigx-validate.js
// Modules: the yaml + jsonata shipped with @jigx/core-sdk; override with JIGX_NODE_MODULES.
const path = require('path'), fs = require('fs');
const NM = process.env.JIGX_NODE_MODULES ||
  path.join(require('child_process').execSync('npm root -g').toString().trim(), '@jigx/core-sdk/node_modules');
const YAML = require(path.join(NM, 'yaml')), jsonata = require(path.join(NM, 'jsonata'));
let bad = 0;
for (const p of process.argv.slice(2)) {
  const doc = YAML.parseDocument(fs.readFileSync(p, 'utf8'));
  if (doc.errors.length) { bad++; console.log('YAML ERR', p, doc.errors.map(e => e.message).join('\n')); continue; }
  let n = 0;
  const walk = (o, at) => {
    if (typeof o === 'string') {
      const s = o.trim();
      if (s.startsWith('=')) {
        n++;
        try { jsonata(s.slice(1).replace(/@ctx\./g, '$ctx.')); }
        catch (e) { bad++; console.log('JSONATA ERR', p, at, e.message); }
      }
    } else if (o && typeof o === 'object') for (const k in o) walk(o[k], at + '.' + k);
  };
  walk(doc.toJS(), '');
  console.log('OK', p, 'expressions:', n);
}
process.exit(bad ? 1 : 0);
