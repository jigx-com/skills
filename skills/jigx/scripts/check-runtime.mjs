#!/usr/bin/env node
// Dependency-free checks for known Jigx mobile runtime contracts. No network or writes.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';

function walk(value, visit, path='$') {
  visit(value,path);
  if(value && typeof value==='object') for(const [key,child] of Object.entries(value)) walk(child,visit,`${path}.${key}`);
}
const withoutStrings=value=>value.replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g,' ');
export function checkRuntime(app) {
  const findings=[];
  const add=(code,path,message,severity='error')=>findings.push({severity,code,path,message});
  const namespaces=new Set(Object.keys(app.scripts?.expressions??{}).map(x=>x.replace(/\.js$/,'')));
  for(const [id,jig] of Object.entries(app.jigs??{})) {
    const base=`$.jigs.${id}`;
    walk(jig,(node,path)=>{
      if(node?.type==='action.set-jig-state') for(const key of Object.keys(node.options?.changes??{})) {
        if(!Object.hasOwn(jig.state??{},key)) add('undeclared-state',path,`Declare state key ${key} on the owning jig.`);
      }
    },base);
    let unconditional=false;
    for(const [i,group] of (jig.actions??[]).entries()) {
      if(unconditional) add('unreachable-action-group',`${base}.actions.${i}`,'An earlier unconditional group hides this group; append buttons to the same panel.');
      if(group.when===undefined || group.when===true) unconditional=true;
    }
  }
  walk(app,(node,path)=>{
    if(node?.type==='datasource.sqlite' && typeof node.options?.query==='string') {
      // Ignore quoted SQL strings/identifiers and comments, including JSON paths.
      const sql=node.options.query.replace(/'(?:''|[^'])*'|"(?:""|[^"])*"|\[[^\]]*\]|--[^\n]*|\/\*[\s\S]*?\*\//g,' ');
      if(/\$[A-Za-z_]\w*/.test(sql)) add('sql-binding',path,'Screen SQLite bindings use @name; $name does not follow the mobile binding contract.');
    }
  });
  for(const [id,fn] of Object.entries(app.functions??{})) {
    if(fn.provider!=='DATA_PROVIDER_REST' || !fn.useLocalCall) continue;
    const base=`$.functions.${id}`;
    const checkParameters=(params,path,definition=fn)=>{
      for(const [key,p] of Object.entries(params??{})) {
        if(String(definition.method).toUpperCase()==='GET' && p.location==='body') {
          if(definition.inputTransform || ['body','file'].includes(key)) add('get-body',`${path}.${key}`,'GET uses a supported body-construction path; verify the resulting request and server support. Prefer output parameters for internal context.','warning');
          else add('get-body',`${path}.${key}`,'GET body parameters without an input transform prevent request generation. Use output for internal context.');
        }
        if(typeof p.value==='string' && p.value.startsWith('=') && /@ctx\.datasources\b/.test(withoutStrings(p.value))) add('function-datasource-scope',`${path}.${key}`,'Resolve the datasource in the calling action and pass the value into the function.');
      }
    };
    checkParameters(fn.parameters,`${base}.parameters`);
    if(fn.continuation?.parameters) {
      const effective={...fn,...fn.continuation};
      const changedUrl=fn.continuation.url!==undefined && fn.continuation.url!==fn.url;
      checkParameters(fn.continuation.parameters,`${base}.continuation.parameters`,effective);
      for(const [key,p] of Object.entries(fn.parameters??{})) {
        if(!['header','path','query'].includes(p.location) || Object.hasOwn(fn.continuation.parameters,key)) continue;
        if(p.location==='path' && typeof effective.url==='string' && !effective.url.startsWith('=') && !effective.url.includes(`{${key}}`)) continue;
        add('continuation-parameter',`${base}.continuation.parameters`,`Continuation replaces the map and drops ${key}; retain it or verify the intentional endpoint change.`,p.required&&!changedUrl?'error':'warning');
      }
    }
    for(const key of ['when','result','parameters']) walk(fn.guard?.[key],(value,path)=>{
      if(typeof value!=='string' || !value.startsWith('=')) return;
      for(const match of withoutStrings(value).matchAll(/\$([\w-]+)\.[\w-]+\s*\(/g)) if(namespaces.has(match[1]))
        add('guard-script-scope',path,'Guard decision/parameter evaluation lacks solution script metadata; compute in called output and test native guard expressions.');
    },`${base}.guard.${key}`);
  }
  return findings;
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const file=process.argv[2] || 'build/output.json';
    const findings=checkRuntime(JSON.parse(fs.readFileSync(file,'utf8')));
    console.log(JSON.stringify({file,errors:findings.filter(x=>x.severity==='error').length,warnings:findings.filter(x=>x.severity==='warning').length,findings,limits:'Static runtime-contract checks only; does not establish device behavior, tenant lookup eligibility, or successful remote writes.'},null,2));
    process.exitCode=findings.some(x=>x.severity==='error')?1:0;
  } catch(error) { console.error(`Runtime check failed: ${error.message}`);process.exitCode=2; }
}
