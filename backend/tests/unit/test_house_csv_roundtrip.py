"""018 CSV parser/page simulations only; actual UI evidence is separate."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1);let page,clipboard,calls=[],finish,reject;
const header='building_code,building_name,unit_code,unit_name,room_no,floor,status';
const simple='B,楼栋,U,单元,101,1,ACTIVE';
const rows=[{building_code:'B',building_name:'北区,"一"\r\n二栋',unit_code:'U',unit_name:'东\n\n单元',room_no:'101',floor:null,status:'ACTIVE'},
 {building_code:'C',building_name:'西\r楼',unit_code:'V',unit_name:'乙"丙',room_no:'102',floor:-1,status:'INACTIVE'}];
const api={importHouses:b=>{calls.push(b);return new Promise((r,j)=>{finish=r;reject=j;});},
 exportHouses:()=>{calls.push('export');return Promise.resolve({columns:header.split(','),rows});}};
const scope={Page:p=>page=p,wx:{setClipboardData:o=>{clipboard=o.data;o.success();}},
 require:n=>n==='../../services/api'?api:require(path.join(root,'miniprogram/pages/supervisor',n))};
vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(root,'miniprogram/pages/supervisor/properties.js'),'utf8'),scope);
const parse=text=>JSON.parse(JSON.stringify(scope.parseImport(text)));
const csv=data=>header+'\r\n'+data.map(r=>header.split(',').map(k=>scope.csvValue(r[k])).join(',')).join('\r\n');
page.setData=v=>Object.assign(page.data,v);page.loadData=()=>Promise.resolve();page.data.canEdit=true;page.data.tenantId='1';page.data.propertyId='2';
const tick=()=>new Promise(r=>setImmediate(r));
async function main(){
 if(scenario==='roundtrip'){assert.deepEqual(parse(csv(rows)),rows);return;}
 if(scenario==='bom-blank'){assert.equal(parse('\uFEFF\r\n \t\r\n'+header+'\n'+simple+'\r\n\r\n').length,1);return;}
 if(scenario==='no-final-newline'){assert.equal(parse(header+'\r'+simple)[0].floor,1);return;}
 if(scenario==='trim-default'){const r=parse(header+'\n B ,楼栋,U,单元,101,,')[0];assert.equal(r.building_code,'B');assert.equal(r.status,'ACTIVE');assert.equal(r.floor,null);return;}
 if(scenario==='quoted-spaces'){assert.equal(parse(header+'\nB, "楼栋" \t,U,单元,101,1,active')[0].building_name,'楼栋');return;}
 if(scenario==='boundary500'){assert.equal(parse(header+'\n'+Array(500).fill(simple).join('\n')).length,500);return;}
 const bad={
  empty:'',header:'wrong\n'+simple,columns:header+'\nB,楼栋',floor:header+'\nB,楼栋,U,单元,101,1.5,ACTIVE',
  status:header+'\nB,楼栋,U,单元,101,1,DELETED',
  unclosed:header+'\nB,"楼\n栋,U,单元,101,1,ACTIVE',
  barequote:header+'\nB,楼"栋,U,单元,101,1,ACTIVE',
  trailingquote:header+'\nB,"楼栋"x,U,单元,101,1,ACTIVE',
  escapedunclosed:header+'\nB,"楼栋"",U,单元,101,1,ACTIVE',
  boundary501:header+'\n'+Array(501).fill(simple).join('\n'),
 };
 if(scenario in bad){assert.throws(()=>parse(bad[scenario]));return;}
 if(scenario==='markup'){const x=fs.readFileSync(path.join(root,'miniprogram/pages/supervisor/properties.wxml'),'utf8');assert.match(x,/<textarea[^>]*maxlength="-1"/);return;}
 if(scenario==='export-import'){
  await page.exportHouses();assert.deepEqual(parse(clipboard),rows);page.data.importText=clipboard;const pending=page.importHouses();await tick();assert.deepEqual(JSON.parse(JSON.stringify(calls[1].rows)),rows);finish({created_buildings:0,created_units:0,created_houses:0,unchanged_houses:2});await pending;assert(page.data.notice.includes('未变化 2'));return;
 }
 if(scenario==='readonly'){page.data.canEdit=false;page.data.importText=csv(rows);await page.importHouses();await page.exportHouses();assert.equal(calls.length,0);return;}
 if(scenario==='malformed-no-request'){page.data.importText=bad.unclosed;await page.importHouses();assert.equal(calls.length,0);assert(page.data.error.includes('引号'));return;}
 page.data.importText=csv(rows);const pending=page.importHouses();await tick();page.importHouses();page.exportHouses();
 page.data.properties=[{id:99,tenant_id:8,canEdit:true}];page.onPropertyChange({detail:{value:0}});
 assert.equal(calls.length,1);assert.equal(page.data.propertyId,'2');assert.equal(calls[0].property_id,2);
 if(scenario==='request-failure'){reject(Error('conflict'));await pending;assert.equal(page.data.error,'conflict');assert(!page.data.loading);return;}
 finish({created_buildings:1,created_units:1,created_houses:2,unchanged_houses:0});await pending;assert(!page.data.loading);assert(page.data.notice.includes('房屋 2'));
}
main().then(()=>console.log('018 unit simulation PASS')).catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    'roundtrip', 'bom-blank', 'no-final-newline', 'trim-default', 'quoted-spaces',
    'boundary500', 'empty', 'header', 'columns', 'floor', 'status', 'unclosed',
    'barequote', 'trailingquote', 'escapedunclosed', 'boundary501', 'markup',
    'export-import', 'readonly', 'malformed-no-request', 'request-failure',
    'duplicate-and-scope-guard',
])
def test_house_csv_roundtrip(scenario):
    result = subprocess.run(['node', '-e', SCRIPT, str(ROOT), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
