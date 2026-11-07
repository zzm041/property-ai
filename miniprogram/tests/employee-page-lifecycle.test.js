const assert=require('assert');
const fs=require('fs');const path=require('path');const vm=require('vm');
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function employee(id,tenant=7){return {employee:{id,tenant_id:tenant,employee_no:'SYN-'+id,status:'ACTIVE',max_capacity:1,user_id:null,department_id:null},assignments:[],duties:[],skills:[]};}
function setup(tenant=true){
  let definition,meFails=false,slow=false,slowUpdates=false,meCalls=0;
  const requests=[],invites=[],updates=[],copied=[];
  const properties=[{id:1,tenant_id:7,name:'SYN-A'},{id:2,tenant_id:7,name:'SYN-B'}];
  const api={
    me(){meCalls++;return meFails?Promise.reject(Error('identity denied')):Promise.resolve({scopes:tenant?
      [{scope_type:'TENANT',tenant_id:7,permissions:['employee:read','employee:manage']}]:
      properties.map(p=>({scope_type:'PROPERTY',tenant_id:7,property_id:p.id,permissions:['employee:read','employee:manage']}))});},
    catalogProperties(){return Promise.resolve({items:properties});},
    employeeCatalog(){return Promise.resolve({departments:[],duties:[],skills:[]});},
    staffDaily(){return Promise.resolve({items:[]});},
    employees(propertyId){if(!slow)return Promise.resolve({items:[employee(Number(propertyId)*10+1),employee(Number(propertyId)*10+2)]});
      const request=deferred();requests.push({...request,propertyId});return request.promise;},
    issueEmployeeInvite(id,phone){const request=deferred();invites.push({...request,id,phone});return request.promise;},
    updateEmployee(id,body){const request=deferred();updates.push({...request,id,body});return slowUpdates?request.promise:Promise.resolve();}
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/supervisor/staff.js'),'utf8'),{
    Page(value){definition=value;},wx:{setClipboardData(value){copied.push(value.data);}},
    require(name){if(name==='../../services/api')return api;return require(name.replace('../../','../'));}
  });
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(value){Object.assign(this.data,value);}};
  page.onLoad({propertyId:'1'});
  return {page,requests,invites,updates,copied,slow(){slow=true;},slowWrites(){slowUpdates=true;},meCalls(){return meCalls;},deny(){meFails=true;},async boot(){page.onShow();await flush();}};
}
const tests=[];
function test(name,work){tests.push([name,work]);}
test('identity failure clears sensitive state immediately',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({invitePhone:'synthetic',inviteToken:'SYN-TOKEN'});
  c.deny();c.page.onShow();assert.equal(c.page.data.inviteToken,'');assert.equal(c.page.data.canEdit,false);
  await flush();assert.equal(c.page.data.selected,null);assert.equal(c.page.data.items.length,0);assert.equal(c.page.data.canInvite,false);
});
test('cross-property late list cannot replace current list',async()=>{
  const c=setup();await c.boot();c.slow();c.page.load();await flush();c.page.onPropertyChange({detail:{value:'1'}});await flush();
  c.requests[1].resolve({items:[employee(21)]});await flush();c.requests[0].resolve({items:[employee(11)]});await flush();
  assert.equal(c.page.data.propertyId,'2');assert.equal(c.page.data.items[0].employee.id,21);
});
test('old failure cannot replace fresh state or loading',async()=>{
  const c=setup();await c.boot();c.slow();c.page.load();await flush();c.page.load();await flush();
  c.requests[0].reject(Error('old failure'));await flush();assert.equal(c.page.data.loading,true);assert.notEqual(c.page.data.error,'old failure');
  c.requests[1].resolve({items:[employee(11)]});await flush();assert.equal(c.page.data.loading,false);
});
test('invite stays bound to its selected employee',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({invitePhone:'13800000000'});
  c.page.issueInvite();await flush();c.page.selectEmployeeById(12);c.invites[0].resolve({invite_token:'SYN-TOKEN-FOR-11'});await flush();
  assert.equal(c.page.data.selectedEmployeeId,'12');assert.equal(c.page.data.inviteToken,'');c.page.copyInviteToken();assert.equal(c.copied.length,0);
});
test('duplicate invitation is suppressed and hiding discards response',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({invitePhone:'13800000000'});
  c.page.issueInvite();c.page.issueInvite();await flush();assert.equal(c.invites.length,1);
  c.page.onHide();c.invites[0].resolve({invite_token:'SYN-TOKEN'});await flush();
  assert.equal(c.page.data.inviteToken,'');assert.equal(c.page.data.items.length,0);assert.equal(c.page.data.canEdit,false);
});
test('same invitation can be copied only in its original context',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({invitePhone:'13800000000'});
  c.page.issueInvite();await flush();c.invites[0].resolve({invite_token:'SYN-VALID'});await flush();
  c.page.copyInviteToken();assert.deepEqual(c.copied,['SYN-VALID']);
  c.page.onTextInput({currentTarget:{dataset:{field:'invitePhone'}},detail:{value:'13800000001'}});
  c.page.copyInviteToken();assert.deepEqual(c.copied,['SYN-VALID']);
});
test('property management does not imply tenant invitation permission',async()=>{
  const c=setup(false);await c.boot();assert.equal(c.page.data.canEdit,true);assert.equal(c.page.data.canInvite,false);
  c.page.selectEmployeeById(11);c.page.setData({invitePhone:'13800000000'});c.page.issueInvite();await flush();assert.equal(c.invites.length,0);
});
test('write target is captured before asynchronous execution',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.updateEmployee();c.page.selectEmployeeById(12);await flush();
  assert(c.updates.every(item=>item.id===11));
});
test('authorization denial clears issued credentials and revalidates scope',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({invitePhone:'13800000000'});
  c.page.issueInvite();await flush();c.invites[0].resolve({invite_token:'SYN-VALID'});await flush();
  c.slowWrites();c.page.updateEmployee();await flush();c.deny();
  c.updates[0].reject(Object.assign(Error('denied'),{statusCode:403,code:'IAM.FORBIDDEN'}));await flush();
  assert.equal(c.page.data.inviteToken,'');assert.equal(c.page.data.canInvite,false);assert.equal(c.page.data.canEdit,false);
  c.page.copyInviteToken();assert.equal(c.copied.length,0);assert.equal(c.meCalls(),2);
});

test('late authorization denial cannot clear a new scope',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.slowWrites();c.page.updateEmployee();await flush();
  c.page.onPropertyChange({detail:{value:'1'}});await flush();
  c.updates[0].reject(Object.assign(Error('old denied'),{statusCode:403}));await flush();
  assert.equal(c.page.data.propertyId,'2');assert.equal(c.page.data.canEdit,true);assert.equal(c.meCalls(),1);
});

test('validation error preserves the editable form',async()=>{
  const c=setup();await c.boot();c.page.selectEmployeeById(11);c.page.setData({editCapacity:'3'});
  c.slowWrites();c.page.updateEmployee();await flush();
  c.updates[0].reject(Object.assign(Error('invalid capacity'),{statusCode:400}));await flush();
  assert.equal(c.page.data.editCapacity,'3');assert.equal(c.page.data.canEdit,true);assert.equal(c.meCalls(),1);
});

test('hidden bootstrap does not issue identity reads',async()=>{
  const c=setup();await c.boot();c.page.onHide();await c.page.bootstrap();assert.equal(c.meCalls(),1);
});

test('hidden list response cannot repopulate employee data',async()=>{
  const c=setup();await c.boot();c.slow();c.page.load();await flush();c.page.onHide();
  c.requests[0].resolve({items:[employee(11)]});await flush();assert.equal(c.page.data.items.length,0);assert.equal(c.page.data.selected,null);
  c.page.onUnload();assert.equal(c.page.data.inviteToken,'');
});
(async()=>{let failed=0;for(const [name,work]of tests){try{await work();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+' '+e.message);}}
  if(failed)process.exitCode=1;else console.log('employee lifecycle contracts passed '+tests.length);
})().catch(e=>{console.error(e);process.exitCode=1;});
