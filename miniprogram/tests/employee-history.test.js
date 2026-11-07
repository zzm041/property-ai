const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function setup(allowed=true){
  let definition;const requests=[];
  const api={me:()=>Promise.resolve({scopes:[{scope_type:'PROPERTY',tenant_id:7,property_id:9,
    permissions:['employee:read',...(allowed?['work_order:dispatch']:[])]}]}),
    catalogProperties:()=>Promise.resolve({items:[{id:9,tenant_id:7,name:'synthetic'}]}),
    employeeCatalog:()=>Promise.resolve({departments:[],duties:[],skills:[]}),staffDaily:()=>Promise.resolve({items:[]}),
    employees:()=>Promise.resolve({items:[11,12].map(id=>({employee:{id,tenant_id:7,employee_no:'SYN-'+id,status:'ACTIVE',max_capacity:1},assignments:[],duties:[],skills:[]}))}),
    employeeWorkHistory:(...args)=>new Promise((resolve,reject)=>requests.push({args,resolve,reject}))};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/supervisor/staff.js'),'utf8'),{
    Page:p=>{definition=p;},wx:{},require:name=>name==='../../services/api'?api:require(name.replace('../../','../'))});
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData:v=>Object.assign(page.data,v)};
  return {page,requests,async boot(){page.onLoad({propertyId:'9'});page.onShow();await flush();page.selectEmployeeById(11);}};
}
function reply(employee=11,items=[{process_id:31,work_order_id:41,category:'PLUMBING',current_order_status:'WAIT_CONFIRM',handling_mode:'EXTERNAL',reported_participant_employee_id:null,reported_resolved:true,latest_feedback:null}]){
  return {employee_id:employee,tenant_id:7,property_id:9,items,next_cursor:null};
}
(async()=>{
  const a=setup();await a.boot();assert.equal(a.page.data.canReadHistory,true);assert.equal(a.requests.length,0);
  a.page.loadWorkHistory();a.page.loadWorkHistory();await flush();assert.equal(a.requests.length,1);
  a.requests[0].resolve(reply());await flush();assert.equal(a.page.data.workHistory.length,1);
  assert(a.page.data.workHistory[0].handlingText.includes('支援'));assert(a.page.data.workHistory[0].feedbackText.includes('未记录'));
  a.page.selectEmployeeById(12);assert.equal(a.page.data.workHistory.length,0);
  a.page.loadWorkHistory();await flush();a.page.onHide();a.requests[1].resolve(reply(12));await flush();assert.equal(a.page.data.workHistory.length,0);
  const b=setup(false);await b.boot();assert.equal(b.page.data.canReadHistory,false);b.page.loadWorkHistory();await flush();assert.equal(b.requests.length,0);
  const c=setup();await c.boot();c.page.loadWorkHistory();await flush();c.page.selectEmployeeById(12);c.page.loadWorkHistory();await flush();
  c.requests[0].reject(Error('old failure'));await flush();assert.equal(c.page.data.historyLoading,true);assert.equal(c.page.data.historyError,'');
  c.requests[1].resolve(reply(12));await flush();assert.equal(c.page.data.historyLoading,false);
  const d=setup();await d.boot();d.page.loadWorkHistory();await flush();d.requests[0].resolve(reply(12));await flush();assert.equal(d.page.data.workHistory.length,0);assert(d.page.data.historyError);
  const e=setup();await e.boot();e.page.loadWorkHistory();await flush();const first=reply();first.next_cursor=31;e.requests[0].resolve(first);await flush();
  e.page.loadMoreHistory();await flush();assert.equal(e.requests[1].args[3],31);e.requests[1].resolve(reply(11,[]));await flush();assert.equal(e.page.data.workHistory.length,1);
  console.log('employee history lifecycle contracts passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
