"""021 pure in-app presentation/page simulations; no runtime or DB fixture."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]

SCRIPT = r'''
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1),p=require(path.join(root,'miniprogram/utils/notification-display'));
const templates=[
 ['WORK_ORDER','工单待确认','Please confirm the work order result','工单待确认','请查看处理回执，并确认问题是否已解决'],
 ['WORK_ORDER','工单 SLA 即将超时','Work order is approaching its completion deadline','工单处理时限预警','工单即将超过完成时限，请及时处理'],
 ['WORK_ORDER','工单 SLA 已超时','Work order is overdue and requires escalation','工单处理已超时','工单已超过完成时限，需要升级处理'],
 ['WORK_ORDER','Work order accept timeout','The assignment timed out and was returned for automatic dispatch','工单接单超时','原派单已超时，工单已退回自动派单流程'],
 ['WORK_ORDER','Work order response timeout','The accepted work order has not started and needs supervisor review','工单响应超时','已接单工单尚未开始处理，需要主管跟进'],
 ['RESIDENT_BIND_REQUEST','房屋绑定已通过','Your house binding request was approved','房屋绑定已通过','房屋绑定申请已通过，请查看房屋关系'],
 ['RESIDENT_BIND_REQUEST','房屋绑定未通过','Your house binding request was rejected','房屋绑定未通过','房屋绑定申请未通过，请查看审核说明'],
 ['ANNOUNCEMENT','原公告标题 English <text>','Announcement published','原公告标题 English <text>','公告已发布，请查看详情'],
];
const notice=(id=30)=>({id,tenant_id:2,property_id:7,resource_type:'WORK_ORDER',resource_id:31,title:'工单待确认',body:'Please confirm the work order result',created_at:'2026-09-23T01:02:03.123456Z',read_at:null});
if(scenario.startsWith('template')){
 const row=templates[Number(scenario.slice(8))],raw={...notice(),resource_type:row[0],title:row[1],body:row[2]},saved=JSON.stringify(raw),v=p.presentNotification(raw);
 assert.equal(v.titleLabel,row[3]);assert.equal(v.bodyLabel,row[4]);assert.equal(v.title,raw.title);assert.equal(v.body,raw.body);
 assert.equal(v.created_at,raw.created_at);assert.equal(v.createdAtLabel,'2026-09-23 09:02');assert.equal(v.readAtLabel,'');assert.equal(JSON.stringify(raw),saved);process.exit(0);
}
if(scenario==='unknown'){
 for(const raw of [{...notice(),title:'自定义内容'}, {...notice(),body:'Please confirm the work order result extra'}, {...notice(),resource_type:'ANNOUNCEMENT'}, {...notice(),resource_type:'__proto__'}, {...notice(),title:'constructor',body:'<text>用户原文</text>'}]){
  const v=p.presentNotification(raw);assert.equal(v.titleLabel,raw.title);assert.equal(v.bodyLabel,raw.body);
 }process.exit(0);
}
if(scenario==='time'){
 const a=p.presentNotification({...notice(),read_at:'2026-09-23T09:30:00+08:00'});assert.equal(a.readAtLabel,'2026-09-23 09:30');
 const b=p.presentNotification({...notice(),created_at:null,read_at:'bad-time'});assert.equal(b.createdAtLabel,'未记录');assert.equal(b.readAtLabel,'时间不可用');
 assert.equal(b.read_at,'bad-time');process.exit(0);
}
let page,readCalls=0,listCalls=[],navigation=[],resolveRead;
const me={user_id:4,scopes:[]},app={globalData:{me}};
const first={items:[notice(),notice(29)],unread_count:3,next_cursor:29};
const second={items:[notice(28)],unread_count:3,next_cursor:null};
const rawFirst=JSON.stringify(first);
const api={me:async()=>me,notifications:async(cursor)=>{listCalls.push(cursor);return cursor?second:first;},
 readNotification:()=>{readCalls++;if(scenario==='read-failure')return Promise.reject(Error('denied'));
  if(scenario==='double-tap')return new Promise(r=>resolveRead=r);
  return Promise.resolve({...notice(),read_at:'2026-09-23T02:00:00Z'});},
 workOrder:async()=>({work_order:{id:31,reporter_user_id:4,tenant_id:2,property_id:7}})};
vm.runInNewContext(fs.readFileSync(path.join(root,'miniprogram/pages/notifications/list.js'),'utf8'),{
 Page:x=>page=x,getApp:()=>app,wx:{navigateTo:x=>navigation.push(x.url),showToast(){}},
 require:n=>n==='../../services/api'?api:require(path.join(root,'miniprogram/pages/notifications',n))
});page.setData=x=>Object.assign(page.data,x);
(async()=>{
 await page.refresh();assert.equal(JSON.stringify(first),rawFirst);assert.equal(page.data.items[0].bodyLabel,templates[0][4]);assert.equal(page.data.items[0].body,templates[0][2]);
 if(scenario==='paging'){
  await page.fetchPage(true);assert.deepEqual(Array.from(page.data.items,x=>x.id),[30,29,28]);assert.equal(page.data.nextCursor,'');assert.equal(page.data.hasMore,false);
  assert(page.data.items.every(x=>/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(x.createdAtLabel)));assert.deepEqual(listCalls,['','29']);assert.equal(readCalls,0);return;
 }
 if(scenario==='invalid-cursor'){
  second.next_cursor=29;await assert.rejects(page.fetchPage(true),/游标/);assert.deepEqual(Array.from(page.data.items,x=>x.id),[30,29]);return;
 }
 const event={currentTarget:{dataset:{id:30}}};
 if(scenario==='double-tap'){
  const ongoing=page.open(event);await page.open(event);assert.equal(readCalls,1);assert.equal(page.data.openingId,'30');
  resolveRead({...notice(),read_at:'2026-09-23T02:00:00Z'});await ongoing;
 }else await page.open(event);
 if(scenario==='read-failure'){
  assert.equal(page.data.error,'denied');assert.equal(page.data.items[0].read_at,null);assert.equal(page.data.items[0].readAtLabel,'');assert.equal(page.data.unread,3);assert.equal(navigation.length,0);return;
 }
 assert.equal(readCalls,1);assert.equal(page.data.unread,2);assert.equal(page.data.items[0].readAtLabel,'2026-09-23 10:00');assert.equal(page.data.items[0].body,templates[0][2]);
 assert.equal(page.data.openingId,'');assert.equal(navigation[0],'/pages/work-orders/detail?id=31');await page.open(event);assert.equal(readCalls,1,'already-read navigation must not resubmit');
 assert.equal(JSON.stringify(first),rawFirst);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    *[f'template{i}' for i in range(8)], 'unknown', 'time', 'paging',
    'invalid-cursor', 'read-success', 'read-failure', 'double-tap',
])
def test_inbox_presentation_preserves_original_data_and_actions(scenario):
    result = subprocess.run(['node', '-e', SCRIPT, str(ROOT), scenario],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inbox_template_uses_plain_text_and_explicit_time_basis():
    template = (ROOT / 'miniprogram/pages/notifications/list.wxml').read_text(encoding='utf-8')
    assert '{{item.titleLabel}}' in template and '{{item.bodyLabel}}' in template
    assert '{{item.createdAtLabel}}' in template and '{{item.readAtLabel}}' in template
    # docs/06-API-CONTRACT.md:181 — Beijing time to the minute, no seconds and no
    # timezone suffix, so the page must render the label and never a raw stamp.
    assert 'UTC' not in template and 'rich-text' not in template
    assert '{{item.created_at}}' not in template
    # `read_at` may still be *tested* (it decides 已读/未读 and whether the label is
    # drawn), but it must never be rendered as a timestamp.
    for line in template.splitlines():
        if 'read_at' in line:
            assert 'wx:if' in line or '?' in line, line
