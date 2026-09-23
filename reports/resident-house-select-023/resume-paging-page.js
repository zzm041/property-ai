"use strict";
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const ROOT='D:/property-ai',PRIVATE=path.join(ROOT,'tmp/resident-house-select-023/continuation-context.json'),OUT=path.join(__dirname,'continuation-page.json');
const ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8')),e=JSON.parse(fs.readFileSync(OUT,'utf8'));let mp,logging=false;
assert(e.steps['04-existing-order-relogin']&&e.steps['auth-multi']&&e.steps.pagination_counts.join(',')==='20,40,51');
assert(!e.steps['05-multiple-houses-last-selection']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function save(){fs.writeFileSync(OUT,JSON.stringify(e,null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function ready(p,test){for(let i=0;i<150;i++){let d=await p.data();if(!d.loading&&!d.houseLoading&&test(d))return d;await sleep(100);}throw Error('state timeout');}
async function button(p,label){for(const x of await p.$$('button'))if((await x.text()).trim()===label)return x;throw Error('missing '+label);}
async function snap(p,name){let d=await p.data();e.steps[name]={path:p.path,houses:d.houses,houseId:d.houseId,houseCursor:d.houseCursor,selectedHouseLabel:d.selectedHouseLabel};save();await mp.screenshot({path:path.join(__dirname,name+'.png')});e.screenshots.push(name+'.png');save();}
async function main(){
 mp=await require(process.env.MP_AUTOMATOR_MODULE).connect({wsEndpoint:'ws://127.0.0.1:9440'});let p=await mp.currentPage();assert.equal(p.path,'pages/work-orders/create');let d=await ready(p,s=>s.houses.length===51);console.log(JSON.stringify({phase:'resume-current-51',selected:d.houseId}));
 await mp.exposeFunction('recordHouse023Resume',r=>{if(!r.url.startsWith('http://127.0.0.1:8083/api/v1/me/houses'))return;e.requests.push({path:r.url.slice('http://127.0.0.1:8083/api/v1'.length),method:'GET',status:r.status,request_id:r.id,data:r.data,source:'real origin-forwarded wx.request; continuation',received_at_utc:new Date().toISOString()});save();});
 await mp.mockWxMethod('request',function(o){const self=this;if(!o.url.startsWith('http://127.0.0.1:8083/api/v1/')||((o.method||'GET')!=='GET'&&!o.url.includes('/api/v1/auth/')))throw Error('No business writes');return new Promise((resolve,reject)=>self.origin({...o,success(r){recordHouse023Resume({url:o.url,status:r.statusCode,id:r.data.request_id,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});logging=true;
 const choices=await p.$$('.house-choice'),last=choices[choices.length-1],offset=await last.offset();await mp.callWxMethod('pageScrollTo',{scrollTop:Math.max(0,offset.top-150),duration:0});await sleep(400);await last.tap();d=await ready(p,s=>Number(s.houseId)===ctx.foreign_house_id);await snap(p,'05-multiple-houses-last-selection');
 await mp.callWxMethod('pageScrollTo',{scrollTop:999999,duration:0});await sleep(300);await (await button(p,'刷新房屋')).tap();d=await ready(p,s=>s.houses.length===20);assert.equal(d.houseId,'');await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});await snap(p,'06-last-page-preference-requires-reload');
 while(d.houseCursor){const before=d.houses.length;const more=await button(p,'加载更多房屋');const where=await more.offset();await mp.callWxMethod('pageScrollTo',{scrollTop:Math.max(0,where.top-100),duration:0});await sleep(150);await more.tap();d=await ready(p,s=>s.houses.length>before);}
 assert.equal(Number(d.houseId),ctx.foreign_house_id);e.steps.restored_last_page_selection={house_id:Number(d.houseId),count:d.houses.length};e.paging_resume_note='First offscreen last-house tap/read was too early; no submit occurred. Continued same loaded51 page using actual scroll/tap and waited for real state.';e.paging_error=e.last_error;delete e.last_error;save();console.log(JSON.stringify({status:'PAGING_REMAINING_COMPLETE',count:d.houses.length}));
}
main().catch(err=>{e.last_error=String(err.stack||err);save();console.error(e.last_error);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
