const fs = require('fs');
const f = process.argv[2];
const raw = fs.readFileSync(f, 'utf8');
const d = JSON.parse(raw);

const types = {};
(d.errors || []).forEach(e => { types[e.type] = (types[e.type] || 0) + 1; });
console.log('totals:', JSON.stringify(d.totals));
console.log('error types:', JSON.stringify(types));

const keys = ['Expected', 'before end of expression', 'Error Boundary', 'S0203', 'S0201', 'syntax error'];
for (const k of keys) {
  console.log('  match "' + k + '": ' + (raw.split(k).length - 1));
}

const all = (d.recent || []).concat(d.errors || []);
const bad = all.filter(e => e.type !== 'MCP_INTERNAL_ERROR' &&
  (e.severity === 'error' || /error/i.test(e.summary || '')));
console.log('non-MCP error/error-ish events:', bad.length);
bad.slice(0, 8).forEach((e, i) => console.log('  BAD' + i, JSON.stringify(e).slice(0, 500)));
