const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
let definition,userId=7,cycle=9,failSubmit=false,readMode='ready',pendingRead;
const calls=[];
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/staff-orders/detail.js'),'utf8'),{
  Page(value){definition=value;}, wx:{},
  require(name){
    if(name==='../../services/api') return {
      me(){return Promise.resolve({user_id:userId});},
      workOrder(id){
        const result={work_order:{id,status:'PROCESSING',process_reason:'NONE',version:4},processes:[{id:cycle}]};
        if(readMode==='fail')return Promise.reject(Error('reload unavailable'));
        if(readMode==='pending')return new Promise(resolve=>{pendingRead=()=>resolve(result);});
        return Promise.resolve(result);
      },
      resolveWorkOrder(id,body){calls.push({id,body});return failSubmit?Promise.reject(Error('temporary failure')):Promise.resolve();}
    };
    if(name==='../../services/auth')return {setLastWorkOrderId(){}};
    if(name==='../../utils/work-order-display')return require('../utils/work-order-display');
    if(name==='../../utils/ui')return {workOrderOf(item){return item.work_order;},withLoading(page,work){return Promise.resolve().then(work);}};
    throw Error(name);
  }
});
async function page(){
  const p={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(value){Object.assign(this.data,value);}};
  p.onLoad({id:'41'});await p.onShow();p.setData({problemType:'水管',method:'合成检修'});return p;
}
(async()=>{
  let p=await page();
  assert.equal(p.data.handlingIndex,0);
  p.resolve();await flush();
  assert(!('handling_mode' in calls[0].body));
  p=await page();p.onHandlingMode({detail:{value:'3'}});p.resolve();await flush();
  assert.equal(calls.length,1);assert.match(p.data.error,/说明/);
  p.onHandlingNote({detail:{value:' 合成支援队负责阀门更换 '}});
  failSubmit=true;p.resolve();await flush();
  assert.equal(p.data.handlingIndex,3);assert.equal(p.data.handlingNote,' 合成支援队负责阀门更换 ');
  p.onHide();assert.equal(p.data.handlingNote,'');
  await p.onShow();assert.equal(p.data.handlingIndex,3);assert.equal(p.data.handlingNote,' 合成支援队负责阀门更换 ');
  p.onHide();readMode='pending';const staleShow=p.onShow();await flush();p.onHide();
  pendingRead();await staleShow;readMode='ready';await p.onShow();
  assert.equal(p.data.handlingIndex,3);assert.equal(p.data.handlingNote,' 合成支援队负责阀门更换 ');
  p.onHide();readMode='fail';await p.onShow();p.onHide();readMode='ready';await p.onShow();
  assert.equal(p.data.handlingIndex,3);assert.equal(p.data.handlingNote,' 合成支援队负责阀门更换 ');
  failSubmit=false;p.resolve();await flush();
  assert.equal(calls[2].body.handling_mode,'EXTERNAL');
  assert.equal(calls[2].body.handling_note,'合成支援队负责阀门更换');
  assert.equal(p.data.handlingIndex,0);assert.equal(p.data.handlingNote,'');
  p=await page();p.onHandlingMode({detail:{value:'1'}});p.resolve();await flush();
  assert.equal(calls[3].body.handling_mode,'SELF');
  p=await page();p.onHandlingMode({detail:{value:'2'}});p.onHandlingNote({detail:{value:'internal'}});
  p.onHide();userId=8;await p.onShow();assert.equal(p.data.handlingIndex,0);assert.equal(p.data.handlingNote,'');
  p.onHandlingMode({detail:{value:'2'}});p.onHandlingNote({detail:{value:'new cycle must not inherit'}});
  p.onHide();cycle=10;await p.onShow();assert.equal(p.data.handlingIndex,0);assert.equal(p.data.handlingNote,'');
  p.onLoad({id:'42'});assert.equal(p.id,42);assert.equal(p.data.handlingIndex,0);
  p.onUnload();assert.equal(p._handlingDraft,null);assert.equal(p._handlingContext,null);
  console.log('receipt handling: validation, failure preservation, same-context restore, identity/order/cycle isolation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
