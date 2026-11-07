const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
function setup(){let def;const buildings=Array.from({length:51},(_,i)=>({id:i+1,name:'楼栋'+(i+1)}));
 const units=Array.from({length:51},(_,i)=>({id:i+101,building_id:51,name:'单元'+(i+1)}));
 let target={scope_type:'UNIT',audience:'RESIDENT',building_id:51,unit_id:151};const writes=[];
 const api={me:()=>Promise.resolve({scopes:[{scope_type:'TENANT',tenant_id:7,permissions:['announcement:draft','announcement:publish']}]}),catalogProperties:()=>Promise.resolve({items:[{id:1,tenant_id:7}]}),adminAnnouncements:()=>Promise.resolve({items:[]}),
 catalogBuildings:(_p,after=0,limit=50)=>Promise.resolve({items:buildings.filter(x=>x.id>after).slice(0,limit)}),
 catalogUnits:(_b,after=0,limit=50)=>Promise.resolve({items:units.filter(x=>x.id>after).slice(0,limit)}),
 adminAnnouncement:id=>Promise.resolve({id,property_id:1,tenant_id:7,status:'DRAFT',title:'合成公告',content:'正文',scopes:[target]}),
 updateAnnouncement(id,body){writes.push({id,body});return Promise.reject(Error('stop before any reload'));}};
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/supervisor/announcements.js'),'utf8'),{Page(v){def=v;},module:{exports:{}},wx:{},require(n){return n==='../../services/api'?api:require(n.replace('../../','../'));}});
 const page={...def,data:JSON.parse(JSON.stringify(def.data)),setData(v){Object.assign(this.data,v);}};return {page,api,writes,target(v){target=v;},async boot(){page.onLoad({propertyId:'1'});if(page.onShow)page.onShow();await flush();}};}
const tests=[];const test=(n,f)=>tests.push([n,f]);
test('existing target after first 50 buildings and units is retained',async()=>{const c=setup();await c.boot();await c.page.loadSelected(9);await flush();assert.equal(c.page.data.buildings.length,51);assert.equal(c.page.targetBody().building_id,51);assert.equal(c.page.targetBody().unit_id,151);});
test('missing old building cannot silently select the first target',async()=>{const c=setup();await c.boot();c.target({scope_type:'BUILDING',audience:'RESIDENT',building_id:999});await c.page.loadSelected(9);await flush();assert.equal(c.page.data.selected,null);assert(c.page.data.error);c.page.update();await flush();assert.equal(c.writes.length,0);});
test('missing old unit cannot silently select the first unit',async()=>{const c=setup();await c.boot();c.target({scope_type:'UNIT',audience:'RESIDENT',building_id:51,unit_id:999});await c.page.loadSelected(9);await flush();assert.equal(c.page.data.selected,null);assert(c.page.data.error);});
test('unknown scope must fail closed',async()=>{const c=setup();await c.boot();c.target({scope_type:'UNKNOWN',audience:'RESIDENT'});await c.page.loadSelected(9);await flush();assert.equal(c.page.data.selected,null);assert(c.page.data.error);});
test('catalog pagination rejects a nonadvancing cursor',async()=>{const c=setup();c.api.catalogBuildings=()=>Promise.resolve({items:Array.from({length:50},(_,i)=>({id:i+1})),next_cursor:50});await c.boot();assert(c.page.data.error);assert.equal(c.page.data.canDraft,false);});
(async()=>{let fail=0;for(const[n,f]of tests){try{await f();console.log('PASS '+n);}catch(e){fail++;console.log('FAIL '+n+': '+e.message);}}console.log(`${tests.length-fail}/${tests.length} announcement target checks passed`);if(fail)process.exitCode=1;})();
