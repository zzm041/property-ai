const assert=require('assert');
const {presentStaffHistory}=require('../utils/work-order-display');
const logs=[{id:1,event_code:'CREATE',actor_type:'RESIDENT',actor_user_id:2},
  ...Array.from({length:159},(_,i)=>({id:i+2,event_code:'AUTO_DISPATCH_WAIT',actor_type:'SYSTEM',created_at:'2026-11-07T00:00:00Z'})),
  {id:161,event_code:'AI_TASK_ATTEMPT',actor_type:'SYSTEM'},
  {id:162,event_code:'ACCEPT',actor_type:'STAFF',actor_user_id:7},
  {id:163,event_code:'PROCESS_UPDATE',actor_type:'STAFF',actor_user_id:8,reason:'前一位员工的现场说明'},
  {id:164,event_code:'AI_TASK_FAILED',actor_type:'SYSTEM'},
  {id:165,event_code:'UNKNOWN_FUTURE_EVENT',actor_type:'SUPERVISOR',reason:'不能静默丢弃新事件'}];
assert.equal(typeof presentStaffHistory,'function','staff history must separate business events from diagnostic noise');
const original=JSON.stringify(logs),history=presentStaffHistory({logs},7);
assert.equal(history.businessLogs.length,5);
assert.equal(history.systemLogs.length,160);
assert.equal(history.systemGroups.find(x=>x.eventCode==='AUTO_DISPATCH_WAIT').count,159);
assert.equal(history.businessLogs[0].id,165);
assert.equal(history.businessLogs.find(x=>x.id===162).actorLabel,'本人');
assert.equal(history.businessLogs.find(x=>x.id===163).actorLabel,'其他工作人员');
assert.equal(history.businessLogs.find(x=>x.id===163).reason,logs[162].reason);
assert(history.businessLogs.some(x=>x.event_code==='AI_TASK_FAILED'));
assert.equal(JSON.stringify(logs),original,'raw audit must not be changed');
assert.equal(presentStaffHistory({logs:[]},7).businessLogs.length,0);
console.log('history noise isolation, actor distinction, ordering, unknown/failure preservation and immutability passed');
