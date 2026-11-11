const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const api=require('../../build/typescript/index.js');
function fixture(files,fn){const root=fs.mkdtempSync(path.join(os.tmpdir(),'laya-contract-'));try{for(const [name,text] of Object.entries(files)){const f=path.join(root,name);fs.mkdirSync(path.dirname(f),{recursive:true});fs.writeFileSync(f,text);}return fn(root);}finally{fs.rmSync(root,{recursive:true,force:true});}}
test('public invoice outline stops at both callback bodies',()=>{
 const file=path.resolve(__dirname,'../../tests/fixtures/first_flow_project/src/billing/invoice-service.ts');
 const r=api.outline(file);assert.deepEqual(r.callables.map(c=>c.name),['calculateInvoiceTotal','calculateInvoiceTotalWithAdjustment']);
 assert.match(api.extractCallable(file,r.callables[0].id).code,/reduce/);
 assert.ok(r.callables.every(c=>!c.signature.includes('return ')));
});
test('outline stops before nested functions/classes/default callbacks; extraction keeps them',()=>fixture({'main.ts':`function outer(value = (() => 1)()) { function nested() {} class Local { run() {} } const obj = { run() {} }; return value; }
const bound = () => { const inside = () => 2; return inside(); };
[1].map(x => x);`},root=>{
 const f=path.join(root,'main.ts'),r=api.outline(f);assert.deepEqual(r.callables.map(c=>c.name),['outer','bound']);assert.match(api.extractCallable(f,r.callables[0].id).code,/class Local/);
}));
test('list scans complete selected block, omits inside targets, resolves aliases/reexports/static calls',()=>fixture({
 'tsconfig.json':JSON.stringify({compilerOptions:{module:'commonjs',moduleResolution:'node',baseUrl:'.',paths:{'@helpers/*':['lib/*']}},include:['**/*.ts']}),
 'lib/helper.ts':'export function external(n: number): number { return n+1; } export default function defaultFn(){return 3;} export class Utility { static work(){ return 1; } }',
 'barrel.ts':'export {external as renamed} from "./lib/helper";',
 'main.ts':`import {renamed as imported} from './barrel'; import defaultFn, * as ns from '@helpers/helper';
function local(n: number) { return n*2; }
function selected(items: number[]) {
 function inside(n: number) { return imported(n); }
 class Inner { run() { return ns.Utility.work(); } }
 const shadow = () => defaultFn();
 items.map(n => local(inside(n)));
 function neverInvoked() { imported(999); }
 shadow(); selected([]); return new Inner();
}`},root=>{
 const file=path.join(root,'main.ts'),selected=api.outline(file).callables.find(c=>c.name==='selected');
 const result=api.listCalledCallables(file,selected.id,{repositoryRoot:root});
 assert.equal(result.status,'partial');assert.deepEqual(result.data.calls.map(c=>c.name).sort(),['defaultFn','external','local','work']);
 for(const c of result.data.calls){assert.deepEqual(Object.keys(c).sort(),['id','name','path','signature']);assert.equal(fs.realpathSync(c.path),c.path);assert.ok(api.extractCallable(c.path,c.id).code);assert.ok(!c.signature.includes('999'));}
 assert.ok(result.diagnostics.some(d=>d.code==='unsupported_constructor'));
 assert.ok(result.diagnostics.some(d=>d.code==='dynamic_receiver'));
 assert.deepEqual(result.data.calls,api.listCalledCallables(file,selected.id,{repositoryRoot:root}).data.calls);
}));
test('call-list distinguishes shadowing, declaration-only, dynamic/interface receivers and stale selections',()=>fixture({
 'tsconfig.json':'{"compilerOptions":{"module":"commonjs","moduleResolution":"node"},"include":["*.ts"]}',
 'main.ts':`declare function library(): void; interface Service { run(): void; }
function helper(){return 1;} function selected(service:Service, target:any){
 function helper(){ library(); } helper(); service.run(); target['run']();
}`},root=>{
 const f=path.join(root,'main.ts'),c=api.outline(f).callables.find(c=>c.name==='selected');
 const r=api.listCalledCallables(f,c.id,{repositoryRoot:root});assert.equal(r.status,'partial');assert.deepEqual(r.data.calls,[]);
 assert.ok(r.diagnostics.some(d=>d.code==='declaration_only'));assert.ok(r.diagnostics.some(d=>d.code==='dynamic_target'));
 fs.appendFileSync(f,'\n');const stale=api.listCalledCallables(f,c.id,{repositoryRoot:root});assert.equal(stale.status,'error');assert.deepEqual(stale.data,null);
}));
test('injected concrete class receiver is diagnostic, while a static class target resolves',()=>fixture({
 'tsconfig.json':'{"compilerOptions":{"module":"commonjs"}}',
 'main.ts':'class Service { run(){return 1;} static work(){return 2;} } function selected(service: Service){ service.run(); return Service.work(); }'
},root=>{
 const f=path.join(root,'main.ts'),c=api.outline(f).callables.find(c=>c.name==='selected');
 const r=api.listCalledCallables(f,c.id,{repositoryRoot:root});
 assert.equal(r.status,'partial');assert.deepEqual(r.data.calls.map(c=>c.name),['work']);
 assert.ok(r.diagnostics.some(d=>d.code==='dynamic_receiver'));
}));
test('injected parameter-properties and uninitialized fields remain uncertain receivers',()=>fixture({
 'tsconfig.json':'{"compilerOptions":{"module":"commonjs"}}',
 'main.ts':'class Service { run(){return 1;} } class Controller { constructor(private service: Service){} other!: Service; selected(){ this.service.run(); this.other.run(); } }'
},root=>{
 const f=path.join(root,'main.ts'),c=api.outline(f).callables.find(c=>c.name==='selected');
 const r=api.listCalledCallables(f,c.id,{repositoryRoot:root});assert.equal(r.status,'partial');assert.deepEqual(r.data.calls,[]);
 assert.equal(r.diagnostics.filter(d=>d.code==='dynamic_receiver').length,2);
}));
test('outline signatures do not leak bodies inside parameter defaults or computed names',()=>fixture({
 'main.ts':'function selected(callback = () => { return 987654; }) { callback(); } const obj = { [(() => { return "computed-body"; })()]() {} };'
},root=>{
 const r=api.outline(path.join(root,'main.ts'));assert.equal(r.callables.length,2);
 assert.ok(!JSON.stringify(r.callables).includes('987654'));
 assert.ok(!JSON.stringify(r.callables).includes('computed-body'));
 assert.match(api.extractCallable(r.path,r.callables[0].id).code,/987654/);
}));
