const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const snapshot = (id, active = true) => ({business_date:'2026-11-07', attendance:active ? {
  property_id:id, check_in_at:'2026-11-07T00:05:10Z',check_out_at:null
}:null,daily_status:active ? {property_id:id,on_duty:'ON_DUTY',current_load:1}:null});
function setup() {
  let definition; const reads=[], writes=[]; let deny=false;
  const api={me:()=>deny?Promise.reject(Error('权限已变更')):Promise.resolve({scopes:[{scope_type:'SYSTEM',permissions:['attendance:check_in']}]}),
    catalogProperties:()=>Promise.resolve({items:[{id:1,tenant_id:1,name:'甲'},{id:2,tenant_id:1,name:'乙'}]}),
    attendanceToday(id){const d=deferred();reads.push({id,...d});return d.promise;},
    checkIn(id){const d=deferred();writes.push({kind:'in',id,...d});return d.promise;},
    checkOut(id){const d=deferred();writes.push({kind:'out',id,...d});return d.promise;}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/attendance/attendance.js'),'utf8'),{
    Page(v){definition=v;},require(n){if(n==='../../services/api')return api;return require(n.replace('../../','../'));}});
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(v){Object.assign(this.data,v);}};
  page.onLoad({});return {page,reads,writes,deny(){deny=true;},async boot(){page.onShow();await flush();}};
}
const tests=[];const test=(n,f)=>tests.push([n,f]);
test('opening page loads today and shows Beijing minute times',async()=>{const c=setup();await c.boot();assert.equal(c.reads.length,1);c.reads[0].resolve(snapshot(1));await flush();assert.equal(c.page.data.checkInLabel,'2026-11-07 08:05');assert.equal(c.page.data.onDutyLabel,'在岗');assert.equal(c.page.data.canCheckIn,false);assert.equal(c.page.data.canCheckOut,true);});
test('switching properties rejects old success and old failure',async()=>{const c=setup();await c.boot();c.page.onPropertyChange({detail:{value:'1'}});await flush();c.reads[1].resolve(snapshot(2,false));await flush();c.reads[0].reject(Error('旧小区错误'));await flush();assert.equal(c.page.data.propertyId,'2');assert.equal(c.page.data.error,'');assert.equal(c.page.data.canCheckIn,true);});
test('duplicate checkout and property change during write are blocked',async()=>{const c=setup();await c.boot();c.reads[0].resolve(snapshot(1));await flush();c.page.checkOut();c.page.checkOut();c.page.onPropertyChange({detail:{value:'1'}});await flush();assert.equal(c.writes.length,1);assert.equal(c.writes[0].id,1);assert.equal(c.page.data.propertyId,'1');const s=snapshot(1);s.attendance.check_out_at='2026-11-07T10:30:55Z';s.daily_status.on_duty='OFF_DUTY';c.writes[0].resolve(s);await flush();assert.equal(c.page.data.checkOutLabel,'2026-11-07 18:30');assert.equal(c.page.data.canCheckIn,true);assert.equal(c.page.data.canCheckOut,false);});
test('hidden response cannot restore status and return refreshes',async()=>{const c=setup();await c.boot();c.page.onHide();c.reads[0].resolve(snapshot(1));await flush();assert.equal(c.page.data.snapshot,null);assert.equal(c.page.data.canCheckOut,false);await c.boot();assert.equal(c.reads.length,2);});
test('failed mutation leaves no stale enabled actions',async()=>{const c=setup();await c.boot();c.reads[0].resolve(snapshot(1,false));await flush();c.page.checkIn();await flush();c.writes[0].reject(Error('网络中断，请刷新核实'));await flush();assert.equal(c.page.data.snapshot,null);assert.equal(c.page.data.canCheckIn,false);assert.match(c.page.data.error,/网络中断/);});
test('revoked access clears previous snapshot and property scope',async()=>{const c=setup();await c.boot();c.reads[0].resolve(snapshot(1));await flush();c.deny();await c.boot();assert.equal(c.page.data.snapshot,null);assert.equal(c.page.data.properties.length,0);assert.equal(c.page.data.canCheckOut,false);});
test('mismatched server snapshot is not displayed',async()=>{const c=setup();await c.boot();c.reads[0].resolve(snapshot(2));await flush();assert.equal(c.page.data.snapshot,null);assert.equal(c.page.data.canCheckOut,false);assert(c.page.data.error);});
test('returning while mutation pending waits then reloads server truth',async()=>{const c=setup();await c.boot();c.reads[0].resolve(snapshot(1));await flush();c.page.checkOut();await flush();c.page.onHide();await c.boot();assert.equal(c.reads.length,1);c.writes[0].resolve(snapshot(1,false));await flush();assert.equal(c.reads.length,2);assert.equal(c.page.data.snapshot,null);c.reads[1].resolve(snapshot(1,false));await flush();assert.equal(c.page.data.canCheckIn,true);});
(async()=>{let failures=0;for(const[n,f]of tests){try{await f();console.log('PASS '+n);}catch(e){failures++;console.error('FAIL '+n+': '+e.message);}}console.log(`${tests.length-failures}/${tests.length} attendance checks passed`);if(failures)process.exitCode=1;})();
