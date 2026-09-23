"""Real page handlers with deterministic simulated API; not UI acceptance."""
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from app.schema.resident import CreateResidentBindRequest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const [root, scenario] = process.argv.slice(1);
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b}); return {promise,resolve,reject}; };
const event = value => ({detail:{value}});
const row = id => ({id, name:String(id), room_no:String(id)});
let calls = [], history = [{id:8,house_id:1,relation_type:'TENANT',status:'REJECTED',review_reason:'Proof incomplete',reviewed_at:'2026-09-23T00:00:00Z'}];
const api = {
  houses: () => Promise.resolve({items:[]}),
  myBindRequests: () => Promise.resolve({items:history,next_cursor:null}),
  catalogProperties: () => Promise.resolve({items:[row(1),row(2)]}),
  catalogBuildings: id => Promise.resolve({items:[row(id*10)]}),
  catalogUnits: id => Promise.resolve({items:[row(id*10)]}),
  catalogHouses: id => Promise.resolve({items:[row(id*10)]}),
  createBindRequest: body => {calls.push(body);return Promise.resolve({id:9});},
};
let page;
const manager = scenario.startsWith('manager');
vm.runInNewContext(fs.readFileSync(path.join(root, manager ? 'miniprogram/pages/supervisor/bindings.js' : 'miniprogram/pages/houses/houses.js'),'utf8'), {
  Page: value => {page=value;},
  require: name => name === '../../services/api' ? api : require(path.join(root,'miniprogram/pages/houses',name)), wx:{},
});
page.setData = value => Object.assign(page.data,value);
async function main() {
  if (manager) {
    const pending = deferred(), reload = deferred();
    page.setData({items:[{id:9,status:'PENDING'}]});
    api.rejectBindRequest = (id,reason) => {calls.push({id,reason});return pending.promise;};
    api.approveBindRequest = id => {calls.push({id});return pending.promise;};
    api.bindRequests = () => reload.promise;
    const e = {currentTarget:{dataset:{id:9}}};
    if(scenario==='manager-reason') {
      page.reject(e); assert.equal(calls.length,0);
      page.onRejectReason(event(' '.repeat(2))); page.reject(e); assert.equal(calls.length,0);
      page.onRejectReason(event('x'.repeat(501)));page.reject(e);assert.equal(calls.length,0);
      page.onRejectReason(event('  Specific reason  '));
      const result=page.reject(e);page.reject(e);page.approve(e);await tick();
      assert.deepEqual(calls,[{id:9,reason:'Specific reason'}]);
      pending.resolve();await tick();assert.equal(page.data.loading,true);
      reload.resolve({items:[]});await result;assert.equal(page.data.loading,false);assert.equal(page.data.rejectReason,'');
    } else {
      page.approve({currentTarget:{dataset:{id:77}}});assert.equal(calls.length,0);
      const result=page.approve(e);page.approve(e);await tick();assert.equal(calls.length,1);
      pending.reject(Error('conflict'));await result;assert.equal(page.data.loading,false);assert.equal(page.data.error,'conflict');
    }
    return;
  }
  await page.reload();
  assert.equal(page.data.selectedHouseId,1000);
  if(scenario.startsWith('relation:')) {
    const i=Number(scenario.split(':')[1]);
    page.onRelation(event(i));await page.applyBind();
    assert.equal(calls[0].relation_type,['OWNER','TENANT','FAMILY','AUTHORIZED_OTHER'][i]);
    assert.equal(calls[0].house_id,1000);assert.equal(Object.keys(calls[0]).length,2);
  } else if(scenario==='history') {
    assert.equal(page.data.requests[0].review_reason,'Proof incomplete');assert.equal(page.data.requests[0].relationLabel,'租户');
    assert.equal(page.data.requests[0].statusLabel,'已驳回');
    history.push({id:9,house_id:1,relation_type:'FAMILY',status:'APPROVED'});
    await page.reload();assert.equal(page.data.requests.length,2);assert.equal(page.data.requests[0].review_reason,'Proof incomplete');
    const template=fs.readFileSync(path.join(root,'miniprogram/pages/houses/houses.wxml'),'utf8');
    for(const key of ['item.review_reason','item.reviewed_at','item.relationLabel','onRelation']) assert(template.includes(key));
  } else if(scenario==='invalid') {
    page.applyBind();assert.equal(calls.length,0);assert.match(page.data.error,/关系/);
    page.onRelation(event(4));page.applyBind();assert.equal(calls.length,0);
    page.onRelation(event(1));page.onHousePick(event(-1));page.applyBind();assert.equal(calls.length,0);
  } else if(scenario==='submit-guard') {
    const post=deferred(), list=deferred();
    api.createBindRequest=body=>{calls.push(body);return post.promise;};
    page.onRelation(event(1));const result=page.applyBind();page.applyBind();await tick();assert.equal(calls.length,1);
    api.myBindRequests=()=>list.promise;post.resolve();await tick();assert.equal(page.data.loading,true);page.applyBind();assert.equal(calls.length,1);
    list.resolve({items:history});await result;assert.equal(page.data.loading,false);
    api.createBindRequest=()=>Promise.reject(Error('duplicate pending'));await page.applyBind();assert.equal(page.data.loading,false);assert.equal(page.data.error,'duplicate pending');
  } else if(scenario.startsWith('race:')) {
    const level=scenario.split(':')[1], old=deferred(), fresh=deferred();
    const config={property:['properties','onProperty','catalogBuildings'],building:['buildings','onBuilding','catalogUnits'],unit:['units','onUnit','catalogHouses']}[level];
    page.setData({[config[0]]:[row(1),row(2)]});
    api[config[2]]=id=>id===1?old.promise:fresh.promise;
    page.onRelation(event(1));
    const a=page[config[1]](event(0));assert.equal(page.data.selectedHouseId,0);page.applyBind();assert.equal(calls.length,0);
    await tick();const b=page[config[1]](event(1));await tick();fresh.resolve({items:[row(22)]});await b;
    const chosen=page.data.selectedHouseId;assert(chosen>0);assert.equal(page.data.catalogLoading,false);
    old.resolve({items:[row(11)]});await a;assert.equal(page.data.selectedHouseId,chosen);assert.equal(page.data.catalogLoading,false);
  } else if(scenario==='stale-error') {
    const old=deferred();api.catalogBuildings=id=>id===1?old.promise:Promise.resolve({items:[row(20)]});
    const a=page.onProperty(event(0));await tick();await page.onProperty(event(1));old.reject(Error('old network failure'));await a;
    assert.equal(page.data.selectedHouseId,2000);assert.equal(page.data.error,'');
  } else if(scenario==='empty-error-unload') {
    api.catalogBuildings=()=>Promise.resolve({items:[]});await page.onProperty(event(0));assert.equal(page.data.selectedHouseId,0);assert.equal(page.data.houses.length,0);
    api.catalogBuildings=()=>Promise.reject(Error('offline'));await page.onProperty(event(1));assert.equal(page.data.selectedHouseId,0);assert.equal(page.data.catalogLoading,false);assert.equal(page.data.error,'offline');
    const late=deferred();api.catalogBuildings=()=>late.promise;const p=page.onProperty(event(0));await tick();page.onUnload();late.resolve({items:[row(99)]});await p;assert.equal(page.data.selectedHouseId,0);
  } else if(scenario==='pagination') {
    api.myBindRequests=id=>Promise.resolve(id===0?{items:[history[0]],next_cursor:8}:{items:[history[0],{id:9}],next_cursor:null});
    assert.equal((await page.loadBindRequests()).length,2);
    api.myBindRequests=()=>Promise.resolve({items:[],next_cursor:8});
    await assert.rejects(page.loadBindRequests(8),/游标/);
  } else throw Error('unknown scenario');
}
main().then(()=>console.log('handler simulation PASS')).catch(e=>{console.error(e);process.exitCode=1});
'''


@pytest.mark.parametrize("scenario", [
    "relation:0", "relation:1", "relation:2", "relation:3", "history", "invalid",
    "submit-guard", "race:property", "race:building", "race:unit", "stale-error",
    "empty-error-unload", "pagination", "manager-reason", "manager-conflict",
])
def test_binding_page_handlers(scenario):
    result = subprocess.run(["node", "-e", SCRIPT, str(ROOT), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "handler simulation PASS" in result.stdout


@pytest.mark.parametrize("relation", ["OWNER", "TENANT", "FAMILY", "AUTHORIZED_OTHER"])
def test_existing_relation_schema_accepts_all_frozen_types(relation):
    assert CreateResidentBindRequest(house_id=47, relation_type=relation).relation_type.value == relation


def test_unknown_relation_is_not_defaulted_to_owner():
    with pytest.raises(ValidationError):
        CreateResidentBindRequest(house_id=47, relation_type="GUEST")
