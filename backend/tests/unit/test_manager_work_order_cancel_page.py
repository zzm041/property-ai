"""017 page control simulations. These are unit tests, not actual UI acceptance."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1);let page,calls=[],mutation,queueWait=null;
const tick=()=>new Promise(r=>setImmediate(r));
const defer=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});return {resolve,reject,promise};};
let records=[{id:41,status:'ASSIGNED',version:3,property_id:2,tenant_id:1}];
const api={
 supervisorQueue:(p,a,v)=>{calls.push(['queue',p,a,v]);return queueWait?queueWait.promise:Promise.resolve({items:records,next_cursor:null});},
 staffDaily:()=>Promise.resolve({items:[]}),
 cancelWorkOrder:(id,body)=>{calls.push(['cancel',id,body]);return mutation.promise;},
 assignWorkOrder:()=>Promise.resolve({}),decideWorkOrder:()=>Promise.resolve({}),
};
vm.runInNewContext(fs.readFileSync(path.join(root,'miniprogram/pages/supervisor/queue.js'),'utf8'),{
 Page:p=>page=p,require:n=>n==='../../services/api'?api:require(path.join(root,'miniprogram/pages/supervisor',n)),
});
page.setData=v=>Object.assign(page.data,v);
async function main(){
 page.onLoad({propertyId:'2',tenantId:'1'});await page.load();
 if(scenario==='stale'){
  queueWait=defer();const pending=page.load();page.onProperty({detail:{value:'8'}});
  queueWait.resolve({items:records,next_cursor:null});await pending;
  assert.equal(page.data.items.length,0);assert.equal(page.data.selectedId,'');return;
 }
 if(scenario==='view'){
  await page.changeView({currentTarget:{dataset:{view:'unstarted'}}});assert.equal(calls.at(-1)[3],'unstarted');
  page.select({currentTarget:{dataset:{id:41}}});assert(page.data.canCancel);
  await page.load();assert.equal(page.data.selectedId,'');return;
 }
 if(scenario==='illegal'){
  for(const status of ['PROCESSING','WAIT_CONFIRM','COMPLETED','CANCELLED','CLOSED']){
   page.data.items=[{...records[0],status}];page.select({currentTarget:{dataset:{id:41}}});page.askCancel();page.cancelSelected();
   assert.equal(page.data.canCancel,false);assert.equal(page.data.cancelConfirm,false);
  }
  assert.equal(calls.filter(c=>c[0]==='cancel').length,0);return;
 }
 if(scenario==='dispatch-refresh'){
  page.data.items=[{...records[0],status:'PENDING_ASSIGNMENT'}];page.select({currentTarget:{dataset:{id:41}}});page.data.employeeId='9';
  page.assign();await tick();await tick();assert.equal(calls.filter(c=>c[0]==='queue').length,2);return;
 }
 page.select({currentTarget:{dataset:{id:41}}});page.onCancelReason({detail:{value:' duplicate '}});page.askCancel();
 if(scenario==='dismiss'){page.dismissCancel();page.cancelSelected();assert(!calls.some(c=>c[0]==='cancel'));return;}
 mutation=defer();const first=page.cancelSelected();page.cancelSelected();await tick();
 assert.equal(calls.filter(c=>c[0]==='cancel').length,1);
 assert.equal(calls.at(-1)[2].reason,'duplicate');assert.equal(calls.at(-1)[2].version,3);
 page.select({currentTarget:{dataset:{id:99}}});page.onProperty({detail:{value:'99'}});assert.equal(page.data.selectedId,41);assert.equal(page.data.propertyId,'2');
 if(scenario==='failure'){
  mutation.reject(Error('stale version; refresh'));await first;assert(page.data.error.includes('stale'));assert(!page.data.cancelConfirm);assert(!page._cancelBusy);return;
 }
 if(scenario==='refresh-error')queueWait={promise:Promise.reject(Error('refresh failed'))};
 records=[];mutation.resolve({id:41,status:'CANCELLED'});await first;
 assert.equal(page.data.selectedId,'');assert(page.data.success.includes('41'));assert(!page.data.loading);assert(!page._cancelBusy);
 if(scenario==='refresh-error')assert(page.data.error.includes('refresh failed'));else assert.equal(page.data.items.length,0);
 page.cancelSelected();assert.equal(calls.filter(c=>c[0]==='cancel').length,1);
}
main().then(()=>console.log('017 page simulation PASS')).catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['stale', 'view', 'illegal', 'dispatch-refresh', 'dismiss', 'failure', 'success', 'refresh-error'])
def test_manager_cancel_page_guards(scenario):
    result = subprocess.run(['node', '-e', SCRIPT, str(ROOT), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
