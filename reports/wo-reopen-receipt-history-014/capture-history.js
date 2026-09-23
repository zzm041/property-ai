"use strict";
// Capture the already-completed page. No repeated business actions.
const fs = require("fs");
const path = require("path");
const assert = require("assert/strict");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const evidencePath = path.join(__dirname,"page-evidence.json");
const evidence = JSON.parse(fs.readFileSync(evidencePath,"utf8"));
let mp;
(async () => {
  mp = await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});
  const page = await mp.currentPage();
  assert.equal(page.path,"pages/work-orders/detail");
  const data = await page.data();
  assert.equal(data.workOrder.id,evidence.steps["08-closed-relogin"].work_order.id);
  assert.equal(data.workOrder.status,"CLOSED");
  const captures=[];
  for(const [cycle,name] of [[1,"09-first-cycle-history.png"],[2,"10-second-cycle-history.png"]]) {
    await mp.callWxMethod("pageScrollTo",{scrollTop:0,duration:0});
    let card=null;
    for(const item of await page.$$(".card")) if((await item.text()).startsWith(`第 ${cycle} 轮处理`)) {card=item;break;}
    assert(card,`Cycle ${cycle} card missing`);
    const offset=await card.offset();
    await mp.callWxMethod("pageScrollTo",{scrollTop:Math.max(0,Number(offset.top)-12),duration:0});
    await new Promise(resolve=>setTimeout(resolve,500));
    await mp.screenshot({path:path.join(__dirname,name)});
    captures.push({cycle,text:await card.text(),scroll_top:await page.scrollTop(),screenshot:name});
    if(!evidence.screenshots.includes(name)) evidence.screenshots.push(name);
  }
  evidence.history_visual_captures=captures;
  evidence.visual_limits=["03 records the empty-reason error in actual page state; its initial viewport does not show the error text. No fake overlay or repeated business request was used."];
  fs.writeFileSync(evidencePath,JSON.stringify(evidence,null,2));
  console.log(JSON.stringify({work_order_id:data.workOrder.id,captured:captures.length,operation:"scroll and screenshot only"}));
})().catch(error=>{console.error(String(error));process.exitCode=1;}).finally(async()=>{if(mp) await mp.disconnect();});
