"""Paging handler simulations, distinct from actual DevTools evidence."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1),paging=require(path.join(root,'miniprogram/utils/paging'));
const rows=(start,n,key='id')=>Array.from({length:n},(_,i)=>({[key]:start+i,name:String(start+i),room_no:String(start+i)}));
const tick=()=>new Promise(r=>setImmediate(r));
const defer=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});return {resolve,reject,promise};};
async function main(){
 if(scenario==='boundaries'){
   let p=paging.appendPage([],{items:rows(1,50)},0,50);assert.equal(p.nextCursor,50);
   p=paging.appendPage(p.items,{items:[]},50,50);assert.equal(p.nextCursor,0);assert.equal(p.items.length,50);
   p=paging.appendPage(rows(1,50),{items:rows(51,1)},50,50);assert.equal(p.items.length,51);assert.equal(p.nextCursor,0);
   p=paging.appendPage([{relation_id:1}],{items:rows(5,1,'relation_id'),next_cursor:null},1,50,'relation_id');assert.equal(p.items.length,2);
   return;
 }
 if(scenario==='invalid'){
   for(const payload of [{items:rows(2,1),next_cursor:1},{items:rows(2,1),next_cursor:9},{items:[],next_cursor:2},{items:rows(2,1),next_cursor:'bad'},{items:rows(2,1),next_cursor:-1},{items:[{id:3},{id:2}]},{items:[{id:1}]},{items:[{id:NaN}]}])assert.throws(()=>paging.appendPage([],payload,1,50));return;
 }
 let page,calls=[],pending=null;
 const catalog=(kind,parent,after=0)=>{calls.push({kind,parent,after});return pending&&after?pending.promise:Promise.resolve({items:after?rows(after+1,1):rows(1,50)});};
 const api={catalogProperties:after=>catalog('property',null,after),catalogBuildings:(id,a)=>catalog('building',id,a),catalogUnits:(id,a)=>catalog('unit',id,a),catalogHouses:(id,a)=>catalog('house',id,a),houses:(a=0)=>Promise.resolve({items:rows(a+1,a?1:50,'relation_id'),next_cursor:a?null:50}),myBindRequests:()=>Promise.resolve({items:[]}),bindRequests:(p,t,a=0)=>catalog('manager',p,a)};
 const manager=scenario==='manager';
 vm.runInNewContext(fs.readFileSync(path.join(root,manager?'miniprogram/pages/supervisor/bindings.js':'miniprogram/pages/houses/houses.js'),'utf8'),{Page:p=>page=p,require:n=>n==='../../services/api'?api:require(path.join(root,'miniprogram/pages/houses',n)),wx:{}});
 page.setData=v=>Object.assign(page.data,v);
 if(manager){
   page.setData({propertyId:'7',tenantId:'8'});await page.load();assert.equal(page.data.items.length,50);assert.equal(page.data.nextCursor,50);
   pending=defer();const a=page.loadMore();page.loadMore();await tick();assert.equal(calls.filter(c=>c.after===50).length,1);
   pending.reject(Error('network'));await a;assert.equal(page.data.items.length,50);assert.equal(page.data.nextCursor,50);assert.equal(page.data.loading,false);
   pending=null;await page.loadMore();assert.equal(page.data.items.length,51);assert.equal(page.data.nextCursor,0);
   page.onProperty({detail:{value:'9'}});assert.equal(page.data.items.length,0);assert.equal(page.data.nextCursor,0);return;
 }
 await page.reload();assert.equal(page.data.items.length,50);assert.equal(page.data.selectedHouseId,1);
 if(scenario==='active'){await page.loadMoreActive();assert.equal(page.data.items.length,51);assert.equal(page.data.activeCursor,0);return;}
 const level=scenario.startsWith('stale')?'house':scenario;
 const list={property:'properties',building:'buildings',unit:'units',house:'houses'}[level];
 const e={currentTarget:{dataset:{level}}};
 pending=defer();const a=page.loadMoreCatalog(e);page.loadMoreCatalog(e);await tick();
 assert.equal(calls.filter(c=>c.kind===level&&c.after===50).length,1);assert.equal(page.data.selectedHouseId,1);
 if(scenario.startsWith('stale')){
   await page.onProperty({detail:{value:1}});
   if(scenario==='stale-error')pending.reject(Error('old page error'));else pending.resolve({items:rows(999,1)});
   await a;assert.equal(page.data.houses.length,50);assert.equal(page.data.selectedHouseId,1);assert.equal(page.data.error,'');return;
 }
 pending.resolve({items:rows(51,1)});await a;assert.equal(page.data[list].length,51);assert.equal(page.data[level+'Cursor'],0);assert.equal(page.data.selectedHouseId,1);assert.equal(page.data.moreLoading,false);
 if(level==='house'){page.onHousePick({detail:{value:50}});assert.equal(page.data.selectedHouseId,51);}
}
main().then(()=>console.log('paging handler simulation PASS')).catch(e=>{console.error(e);process.exitCode=1});
'''


@pytest.mark.parametrize('scenario', ['boundaries','invalid','property','building','unit','house','active','manager','stale-success','stale-error'])
def test_existing_api_cursor_paging(scenario):
    result=subprocess.run(['node','-e',SCRIPT,str(ROOT),scenario],capture_output=True,text=True,timeout=15)
    assert result.returncode==0, result.stdout+result.stderr
    assert 'paging handler simulation PASS' in result.stdout
