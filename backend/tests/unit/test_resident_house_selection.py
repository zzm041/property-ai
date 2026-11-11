"""023 scoped address projection and real page-handler simulations, not UI evidence."""
from pathlib import Path
from datetime import UTC, datetime
from types import SimpleNamespace
import subprocess
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql

from app.repository.property import PropertyRepository
from app.service.identity.resident import ResidentBindingService

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1);
const tick=()=>new Promise(r=>setImmediate(r));
const defer=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});return{resolve,reject,promise};};
const row=n=>({relation_id:n,house_id:n+100,property_name:'花园',property_address:'合成街1号',building_name:'3幢',unit_name:'2单元',room_no:String(n)});
let page,calls=[],posts=[],navigation=[],saved={},rows=[row(1),row(2)];
const api={houses:(after,limit)=>{calls.push({after,limit});return Promise.resolve({items:rows,next_cursor:null});},
 contactDefault:()=>Promise.resolve({phone:'+8613800000123'}),
 createWorkOrder:(body,key)=>{posts.push({body,key});return Promise.resolve({id:601});}};
const auth={getSelectedHouseId:()=>101,setSelectedHouseId:id=>saved.house=id,setLastWorkOrderId:id=>saved.order=id};
vm.runInNewContext(fs.readFileSync(path.join(root,'miniprogram/pages/work-orders/create.js'),'utf8'),{
 Page:p=>page=p,require:n=>n==='../../services/api'?api:n==='../../services/auth'?auth:require(path.join(root,'miniprogram/pages/work-orders',n)),
 wx:{navigateTo:p=>navigation.push(p.url)}});
page.setData=p=>Object.assign(page.data,p);
const choose=id=>page.selectHouse({currentTarget:{dataset:{id}}});
async function main(){
 page.onLoad(scenario==='foreign-query'?{houseId:'999'}:{});
 assert.equal(page.data.houseId,'');
 if(scenario==='late-unload'){
  const pending=defer();api.houses=()=>pending.promise;const work=page.onShow();await tick();page.onUnload();pending.resolve({items:rows});await work;
  assert.equal(page.data.houses.length,0);assert.equal(page.data.houseId,'');return;
 }
 await page.onShow();await tick();page.setData({contactPhone:'+8613800000123',contactConfirmed:true});assert.deepEqual(calls,[{after:0,limit:20}]);
 if(scenario==='foreign-query'){
  assert.equal(page.data.houseId,'');choose(999);await page.submit();assert.equal(posts.length,0);assert.match(page.data.error,/本人已绑定/);return;
 }
 assert.equal(page.data.houseId,'101');assert.equal(page.data.selectedHouseLabel,'花园 · 3幢 · 2单元 · 1');
 if(scenario==='address'){
  choose(102);assert.equal(page.data.houseId,'102');assert.equal(page.data.selectedHouseLabel,'花园 · 3幢 · 2单元 · 2');
  for(const id of [-1,'NaN',999])choose(id);assert.equal(page.data.houseId,'102');page.manageHouses();assert.equal(navigation[0],'/pages/houses/houses');
  const markup=fs.readFileSync(path.join(root,'miniprogram/pages/work-orders/create.wxml'),'utf8');
  assert(!markup.includes('onHouseId'));assert(markup.includes('selectHouse'));assert(markup.includes('item.addressLabel'));return;
 }
 if(scenario==='empty'){
  rows=[];await page.onShow();assert.equal(page.data.houseId,'');assert.equal(page.data.selectedHouseLabel,'');await page.submit();assert.equal(posts.length,0);return;
 }
 if(scenario==='reload-error'){
  api.houses=()=>Promise.reject(Error('offline'));await page.reloadHouses();assert.equal(page.data.houseId,'');assert.equal(page.data.houseLoading,false);assert.equal(page.data.houseError,'offline');return;
 }
 if(scenario==='revoked'){
  rows=[row(2)];await page.onShow();assert.equal(page.data.houseId,'');assert.equal(page.data.houses.length,1);await page.submit();assert.equal(posts.length,0);return;
 }
 if(scenario==='paging'){
  rows=Array.from({length:20},(_,i)=>row(i+1));api.houses=(after,limit)=>{calls.push({after,limit});return Promise.resolve({items:after?[row(21)]:rows,next_cursor:after?null:20});};
  await page.reloadHouses();assert.equal(page.data.houseCursor,20);choose(120);await page.loadMoreHouses();assert.equal(page.data.houses.length,21);assert.equal(page.data.houseId,'120');assert.equal(page.data.houseCursor,0);
  const count=calls.length;await page.loadMoreHouses();assert.equal(calls.length,count);choose(121);assert.equal(page.data.houseId,'121');return;
 }
 if(scenario==='more-error-duplicate'){
  page.setData({houseCursor:2});const pending=defer();api.houses=(after,limit)=>{calls.push({after,limit});return pending.promise;};
  const work=page.loadMoreHouses();page.loadMoreHouses();choose(102);await tick();assert.equal(calls.length,2);assert.equal(page.data.houseId,'101');
  pending.reject(Error('retry'));await work;assert.equal(page.data.houseCursor,2);assert.equal(page.data.houses.length,2);assert.equal(page.data.houseId,'101');return;
 }
 if(scenario==='cursor-rejected'){
  page.setData({houseCursor:2});api.houses=()=>Promise.resolve({items:[row(2)],next_cursor:2});await page.loadMoreHouses();assert(page.data.houseError);assert.equal(page.data.houses.length,2);return;
 }
 if(scenario==='blank'){
  page.onText({detail:{value:'  '}});await page.submit();assert.equal(posts.length,0);assert.match(page.data.error,/说明/);return;
 }
 page.onText({detail:{value:'合成报修'}});
 if(scenario==='duplicate-submit'){
  const pending=defer();api.createWorkOrder=(body,key)=>{posts.push({body,key});return pending.promise;};
  const work=page.submit();page.submit();choose(102);await tick();assert.equal(posts.length,1);assert.equal(page.data.houseId,'101');
  pending.resolve({id:601});await work;assert.equal(posts[0].body.house_id,101);assert.equal(saved.house,101);assert.equal(saved.order,601);return;
 }
 if(scenario==='idempotent-retry'){
  api.createWorkOrder=(body,key)=>{posts.push({body,key});return Promise.reject(Error('connection lost'));};
  await page.submit();await page.submit();assert.equal(posts.length,2);assert.equal(posts[0].key,posts[1].key);assert.equal(page.data.loading,false);
  page.onText({detail:{value:'new input'}});await page.submit();assert.notEqual(posts[1].key,posts[2].key);return;
 }
 throw Error('unknown scenario');
}
main().then(()=>console.log('023 handler simulation PASS')).catch(e=>{console.error(e);process.exitCode=1});
'''


@pytest.mark.parametrize("scenario", [
    "address", "foreign-query", "empty", "reload-error", "revoked", "paging",
    "more-error-duplicate", "cursor-rejected", "blank", "duplicate-submit",
    "idempotent-retry", "late-unload",
])
def test_house_selection_handler(scenario):
    result = subprocess.run(["node", "-e", SCRIPT, str(ROOT), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_address_projection_empty_does_not_query():
    session = MagicMock()
    assert PropertyRepository(session).house_address_labels([]) == {}
    session.execute.assert_not_called()


def test_address_projection_has_composite_scope_and_only_authorized_ids():
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"house_id": 77, "property_name": "p", "property_address": "a", "building_name": "b", "unit_name": "u"}
    ]
    labels = PropertyRepository(session).house_address_labels([SimpleNamespace(id=77, tenant_id=8, property_id=9)])
    assert labels == {77: {"property_name": "p", "property_address": "a", "building_name": "b", "unit_name": "u"}}
    query = session.execute.call_args.args[0]
    sql = str(query.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    for predicate in ("units.tenant_id = houses.tenant_id", "units.property_id = houses.property_id",
                      "buildings.tenant_id = houses.tenant_id", "buildings.property_id = houses.property_id",
                      "properties.tenant_id = houses.tenant_id", "(houses.id, houses.tenant_id, houses.property_id) IN ((77, 8, 9))"):
        assert predicate in sql
    session.commit.assert_not_called()


def test_house_view_enriches_only_existing_authorized_page():
    addresses = MagicMock()
    service = ResidentBindingService(MagicMock(), property_repository=addresses)
    actor = object()
    relation, house = object(), SimpleNamespace(id=77)
    service.list_my_houses = MagicMock(return_value=((relation, house),))
    addresses.house_address_labels.return_value = {77: {"property_name": "Garden"}}
    assert service.list_my_house_views(actor=actor, after_id=31, limit=20) == ((relation, house, {"property_name": "Garden"}),)
    service.list_my_houses.assert_called_once_with(actor=actor, after_id=31, limit=20)
    addresses.house_address_labels.assert_called_once_with((house,))


def test_house_view_authorization_failure_cannot_query_labels():
    addresses = MagicMock()
    service = ResidentBindingService(MagicMock(), property_repository=addresses)
    service.list_my_houses = MagicMock(side_effect=PermissionError("inactive actor"))
    with pytest.raises(PermissionError):
        service.list_my_house_views(actor=object())
    addresses.house_address_labels.assert_not_called()


@pytest.mark.parametrize("limit,expected_cursor", [(1, 31), (20, None)])
def test_house_route_serializes_address_and_keeps_relation_cursor(limit, expected_cursor):
    from starlette.requests import Request
    from app.api.v1.resident import list_my_houses

    service = MagicMock()
    relation = SimpleNamespace(id=31, relation_type="TENANT", status="ACTIVE", valid_from=datetime(2026, 9, 23, tzinfo=UTC), valid_to=None)
    house = SimpleNamespace(id=77, tenant_id=8, property_id=9, room_no="101")
    service.list_my_house_views.return_value = ((relation, house, {"property_name": "Garden", "property_address": "Synthetic road", "building_name": "B3", "unit_name": "U2"}),)
    actor = object()
    request = Request({"type": "http", "headers": []})
    data = list_my_houses(request=request, actor=actor, service=service, after_id=10, limit=limit)["data"]
    assert data["next_cursor"] == expected_cursor
    assert data["items"][0]["house_id"] == 77
    assert data["items"][0]["relation_id"] == 31
    assert data["items"][0]["building_name"] == "B3"
    assert data["items"][0]["property_address"] == "Synthetic road"
    service.list_my_house_views.assert_called_once_with(actor=actor, after_id=10, limit=limit)
