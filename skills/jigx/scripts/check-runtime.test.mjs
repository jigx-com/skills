import test from 'node:test';import assert from 'node:assert/strict';
import {checkRuntime} from './check-runtime.mjs';
const fn=()=>({provider:'DATA_PROVIDER_REST',useLocalCall:true,method:'GET',parameters:{base:{location:'path',required:true},auth:{location:'header',required:true},'$top':{location:'query',value:500}}});
const codes=app=>checkRuntime(app).map(x=>x.code);
test('detects undeclared state and unreachable bottom actions',()=>{
 const app={jigs:{form:{state:{id:{initialValue:null}},actions:[{children:[]},{children:[]}],onLoad:{type:'action.set-jig-state',options:{changes:{id:'ok',newId:'bad'}}}}}};
 assert.deepEqual(codes(app),['undeclared-state','unreachable-action-group']);
 app.jigs.form.actions[0].when='=condition';delete app.jigs.form.onLoad.options.changes.newId;assert.deepEqual(codes(app),[]);
});
test('checks actual SQL bind tokens without mistaking JSON paths or literals',()=>{
 const ds=query=>({type:'datasource.sqlite',options:{query}});
 assert.deepEqual(codes({datasources:{x:ds("SELECT json_extract(data,'$.id') FROM t WHERE id=@id -- $comment\n")}}),[]);
 assert.deepEqual(codes({datasources:{x:ds('SELECT * FROM t WHERE id=$id')}}),['sql-binding']);
});
test('catches GET metadata bodies, invalid scope, and shallow continuation omissions',()=>{
 const f=fn();f.parameters.id={location:'body'};f.parameters.base.value='=@ctx.datasources.config.url';f.continuation={parameters:{$skip:{location:'query',value:500}}};
 const found=codes({functions:{read:f}});for(const c of ['get-body','function-datasource-scope','continuation-parameter'])assert.ok(found.includes(c));
 f.parameters.id.location='output';delete f.parameters.base.value;f.continuation.parameters=structuredClone(f.parameters);assert.deepEqual(codes({functions:{read:f}}),[]);
});
test('custom guard calls fail while function output and guard operations stay valid',()=>{
 const f=fn();f.output='=$custom.allow(@ctx.response.body)';f.guard={result:'=$custom.allow(@ctx.guard.output)',operations:[{records:'=$custom.recover(@ctx.guard.output)'}]};
 const app={functions:{read:f},scripts:{expressions:{'custom.js':'exports.allow=()=>true'}}};
 assert.deepEqual(codes(app),['guard-script-scope']);f.guard.result='=@ctx.guard.output.allowed=true';assert.deepEqual(codes(app),[]);
});

test('explicit GET transforms and changed continuation endpoints are not false-positive errors',()=>{
 const f=fn();f.url='{base}/rows';f.parameters.id={location:'body'};f.inputTransform='={"id":@ctx.parameters.id}';
 let findings=checkRuntime({functions:{read:f}});
 assert.equal(findings.some(x=>x.severity==='error'),false);assert.equal(findings[0].severity,'warning');
 for(const key of ['body','file']) {
  const special=fn();special.parameters[key]={location:'body'};
  assert.equal(checkRuntime({functions:{read:special}}).some(x=>x.severity==='error'),false);
 }
 delete f.parameters.id;f.continuation={url:'https://example.test/next',parameters:{auth:f.parameters.auth,'$top':f.parameters.$top}};
 assert.deepEqual(checkRuntime({functions:{read:f}}),[]);
 f.continuation={url:'=@ctx.parameters.next',parameters:{}};
 assert.equal(checkRuntime({functions:{read:f}}).some(x=>x.severity==='error'),false);
});
test('literal script names and datasource paths do not count as executed expressions',()=>{
 const f=fn();f.parameters.base.value='="@ctx.datasources.literal"';
 f.guard={result:'=$contains("$custom.allow(", "allow")'};
 const app={functions:{read:f},scripts:{expressions:{'custom.js':'exports.allow=()=>true'}}};
 assert.deepEqual(checkRuntime(app),[]);
 f.guard.result='=$custom.allow("quoted")';assert.deepEqual(codes(app),['guard-script-scope']);
});
