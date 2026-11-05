#!/usr/bin/env node
// Pull-request performance check for a Jigx solution repo. For every solution the branch
// changes (a YAML folder with index.jigx, or an SDK project with src/app.ts), it audits the
// branch and the branch it merges into, and lists what the branch adds and what it fixes.
// It fails (exit 1) when the branch adds a finding at --fail-on priority or above, so a PR
// cannot bring a regression into its target branch unnoticed.
//
// The target is compared at the merge base, so only the branch's own changes count. In
// GitHub Actions, a pull_request checkout is the merge of the PR into its target, whose
// merge base with the target is the target's tip: what merging would do to it.
//
// Usage (from the repo root):
//   node pr-check.mjs --base origin/main [--fail-on high] [--report perf-check.md] [--all] [solution ...]
//   --base     the branch the PR merges into (default: origin/$GITHUB_BASE_REF, else origin/main)
//   --fail-on  high (default), medium or low
//   --all      audit every solution, not only the ones the branch changes
//   --build    command that builds an SDK project to build/output.json (default: npm ci && npx jigx build)
// Exit: 0 nothing new at --fail-on, 1 the branch adds one, 2 the check could not run.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {checkPerformance, loadSolution, newFindings, PRIORITIES, sdkSources} from './check-performance.mjs';

const SKIP = new Set(['node_modules', 'build', 'dist']);

// Solution folders under root: YAML (index.jigx) or SDK (src/app.ts). Hidden folders, such as a
// checkout of this skill in .jigx-skills, are skipped, and nothing inside a solution is searched.
export function findSolutions(root, rel = '') {
  const dir = path.join(root, rel);
  if (rel && fs.existsSync(path.join(dir, 'index.jigx'))) return [{dir: rel, kind: 'yaml'}];
  if (rel && fs.existsSync(path.join(dir, 'src', 'app.ts'))) return [{dir: rel, kind: 'sdk'}];
  if (!rel && fs.existsSync(path.join(dir, 'index.jigx'))) return [{dir: '.', kind: 'yaml'}];
  if (!rel && fs.existsSync(path.join(dir, 'src', 'app.ts'))) return [{dir: '.', kind: 'sdk'}];
  return fs.readdirSync(dir, {withFileTypes: true})
    .filter(e => e.isDirectory() && !e.name.startsWith('.') && !SKIP.has(e.name))
    .flatMap(e => findSolutions(root, rel ? `${rel}/${e.name}` : e.name));
}

export function changedSolutions(solutions, changedFiles) {
  return solutions.filter(s => s.dir === '.' ? changedFiles.length : changedFiles.some(f => f.startsWith(`${s.dir}/`)));
}

// What the branch adds and fixes against the target, and the added findings that fail the check.
export function compareAudits(baseFindings, headFindings, failOn = 'high') {
  const added = baseFindings ? newFindings(headFindings, baseFindings) : headFindings;
  const fixed = baseFindings ? newFindings(baseFindings, headFindings) : [];
  const limit = PRIORITIES.indexOf(failOn);
  const gated = added.filter(f => PRIORITIES.indexOf(f.priority) <= limit || f.severity === 'error');
  return {added, fixed, gated};
}

const counts = findings => Object.fromEntries(PRIORITIES.map(p => [p, findings ? findings.filter(f => f.priority === p).length : null]));

export function prReport(results, {base, failOn}) {
  const failed = results.some(r => r.gated?.length || r.error);
  const lines = ['# Jigx performance check', '',
    failed ? `**Fails:** this branch adds findings at ${failOn} priority or above, compared with \`${base}\`.`
      : `**Passes:** nothing new at ${failOn} priority or above, compared with \`${base}\`.`, ''];
  if (!results.length) return [...lines, 'No Jigx solution changed on this branch.', ''].join('\n');
  lines.push('| Solution | Target | This branch | New | Fixed |', '| --- | --- | --- | ---: | ---: |');
  const fmt = c => c.high === null ? 'new solution' : `${c.high} high · ${c.medium} medium · ${c.low} low`;
  for (const r of results) {
    lines.push(r.error ? `| ${r.dir} | | could not audit | | |`
      : `| ${r.dir} | ${fmt(counts(r.base))} | ${fmt(counts(r.head))} | ${r.added.length} | ${r.fixed.length} |`);
  }
  lines.push('');
  for (const r of results) {
    if (r.error) { lines.push(`## ${r.dir}`, '', `Could not audit: ${r.error}`, ''); continue; }
    if (!r.added.length && !r.fixed.length) continue;
    lines.push(`## ${r.dir}`, '');
    for (const p of PRIORITIES) {
      const items = r.added.filter(f => f.priority === p);
      if (!items.length) continue;
      lines.push(`### New, ${p} priority${PRIORITIES.indexOf(p) <= PRIORITIES.indexOf(failOn) ? ' (fails the check)' : ''}`, '');
      for (const code of [...new Set(items.map(f => f.code))]) {
        const group = items.filter(f => f.code === code);
        lines.push(`- **${code}** · fix: ${group[0].fix}`);
        for (const f of group.slice(0, 10)) lines.push(`  - \`${f.where}\`: ${f.message}`);
        if (group.length > 10) lines.push(`  - … and ${group.length - 10} more`);
      }
      lines.push('');
    }
    if (r.fixed.length) {
      const byCode = {};
      for (const f of r.fixed) byCode[f.code] = (byCode[f.code] ?? 0) + 1;
      lines.push(`### Fixed by this branch`, '', Object.entries(byCode).map(([c, n]) => `${c} (${n})`).join(', '), '');
    }
  }
  lines.push('A finding is new when its type, file and message are not on the target branch; moving code inside a file does not count. ' +
    'Accept a new finding only with a reason in the PR description.', '');
  return lines.join('\n');
}

function git(args, cwd, input) {
  const run = spawnSync('git', args, {cwd, encoding: input === undefined ? 'utf8' : 'buffer', maxBuffer: 1 << 30});
  if (run.status !== 0) throw new Error(`git ${args.join(' ')}: ${String(run.stderr).trim()}`);
  return run.stdout;
}

// The solution folder as it is at `ref`, extracted to a temporary folder; null when it does not exist there.
function extract(root, ref, dir) {
  const spec = dir === '.' ? ref : `${ref}:${dir}`;
  if (spawnSync('git', ['cat-file', '-e', spec], {cwd: root}).status !== 0) return null;
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), 'jigx-pr-check-'));
  const tar = spawnSync('git', ['archive', '--format=tar', ref, ...(dir === '.' ? [] : [dir])], {cwd: root, maxBuffer: 1 << 30});
  if (tar.status !== 0) throw new Error(`git archive ${spec}: ${String(tar.stderr).trim()}`);
  const untar = spawnSync('tar', ['-x', '-C', dest], {input: tar.stdout, maxBuffer: 1 << 30});
  if (untar.status !== 0) throw new Error(`tar: ${String(untar.stderr).trim()}`);
  return path.join(dest, dir);
}

function audit(dir, kind, build) {
  if (kind === 'yaml') {
    const {app, sources} = loadSolution(dir);
    return checkPerformance(app, {sources}).findings;
  }
  const run = spawnSync(build, {cwd: dir, shell: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']});
  const output = path.join(dir, 'build', 'output.json');
  if (run.status !== 0 || !fs.existsSync(output)) throw new Error(`build failed in ${dir}: ${(run.stderr || run.stdout).trim().slice(-500)}`);
  return checkPerformance(JSON.parse(fs.readFileSync(output, 'utf8')), {sources: sdkSources(output)}).findings;
}

async function main() {
  try {
    const args = process.argv.slice(2);
    const opt = name => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
    const flag = name => args.includes(name) && !!args.splice(args.indexOf(name), 1);
    const base = opt('--base') ?? (process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : 'origin/main');
    const failOn = opt('--fail-on') ?? 'high', reportPath = opt('--report');
    const build = opt('--build') ?? 'npm ci --no-audit --no-fund && npx jigx build';
    const all = flag('--all');
    if (!PRIORITIES.includes(failOn)) throw new Error(`--fail-on takes ${PRIORITIES.join(', ')}`);
    const root = git(['rev-parse', '--show-toplevel'], process.cwd()).trim();
    const mergeBase = git(['merge-base', base, 'HEAD'], root).trim();
    const changed = git(['diff', '--name-only', mergeBase, 'HEAD'], root).split('\n').filter(Boolean);
    const solutions = args.length
      ? args.map(a => { const dir = path.relative(root, path.resolve(a)) || '.'; return {dir, kind: fs.existsSync(path.join(root, dir, 'index.jigx')) ? 'yaml' : 'sdk'}; })
      : findSolutions(root);
    const targets = all || args.length ? solutions : changedSolutions(solutions, changed);
    const results = [];
    for (const s of targets) {
      try {
        const head = audit(path.join(root, s.dir), s.kind, build);
        const baseDir = extract(root, mergeBase, s.dir);
        const baseFindings = baseDir ? audit(baseDir, s.kind, build) : null;
        results.push({dir: s.dir, kind: s.kind, base: baseFindings, head, ...compareAudits(baseFindings, head, failOn)});
      } catch (error) { results.push({dir: s.dir, kind: s.kind, error: error.message}); }
      const r = results.at(-1);
      console.error(r.error ? `${s.dir}: could not audit (${r.error})` : `${s.dir}: ${r.added.length} new, ${r.fixed.length} fixed, ${r.gated.length} at ${failOn} or above`);
    }
    const report = prReport(results, {base, failOn});
    if (reportPath) fs.writeFileSync(reportPath, report);
    console.log(JSON.stringify({base, mergeBase, failOn, solutions: results.map(({base: b, head, ...r}) => ({...r, baseCounts: counts(b), headCounts: counts(head)}))}, null, 2));
    for (const r of results) for (const f of r.gated ?? []) console.error(`[${f.priority}] ${r.dir}/${f.where}: ${f.message}`);
    process.exitCode = results.some(r => r.error) ? 2 : results.some(r => r.gated.length) ? 1 : 0;
  } catch (error) { console.error(`PR check failed: ${error.message}`); process.exitCode = 2; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
