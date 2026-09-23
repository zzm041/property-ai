"""Execute page handlers with an explicit simulated API, not UI acceptance."""
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_reopen_page_reason_history_and_inflight_guards():
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = process.argv[1];
let page, finishReopen, finishReload;
let reopenCalls = [], confirmCalls = 0, deferReload = false;
const response = {
  work_order: { id: 74, version: 6, status: 'WAIT_CONFIRM' },
  processes: [{id: 11, cycle_no: 1}, {id: 12, cycle_no: 2}],
  receipts: [{id: 21, process_id: 11, method: 'old receipt'}, {id: 22, process_id: 12, method: 'new receipt'}],
  feedbacks: [{id: 31, process_id: 11, unresolved_flag: true, comment: 'still leaking'}],
};
const api = {
  workOrder: () => deferReload ? new Promise(resolve => { finishReload = () => resolve(response); }) : Promise.resolve(response),
  reopenWorkOrder: (id, body) => {
    reopenCalls.push({id, body});
    return new Promise(resolve => { finishReopen = resolve; });
  },
  confirmWorkOrder: () => { confirmCalls++; return Promise.resolve(); },
};
vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/pages/work-orders/detail.js'), 'utf8'), {
  Page: definition => { page = definition; },
  require: name => name === '../../services/api' ? api : name === '../../services/auth' ? { setLastWorkOrderId() {} } : require(path.join(root, 'miniprogram/utils/ui.js')),
  wx: {}, setTimeout, clearTimeout,
});
page.setData = values => Object.assign(page.data, values);
page.id = 74;
(async () => {
  await page.reload();
  assert.equal(page.data.processHistory.length, 2);
  assert.equal(page.data.processHistory[0].receipt.method, 'old receipt');
  assert.equal(page.data.processHistory[0].feedbacks[0].comment, 'still leaking');
  assert.equal(page.data.processHistory[1].feedbacks.length, 0);
  page.onReopenReason({detail:{value:'  '}});
  page.reopen();
  assert.equal(reopenCalls.length, 0);
  assert.match(page.data.error, /1至255/);
  page.onReopenReason({detail:{value:'x'.repeat(256)}});
  page.reopen();
  assert.equal(reopenCalls.length, 0);
  page.onReopenReason({detail:{value:'  Still leaking at the joint  '}});
  const pending = page.reopen();
  assert.equal(page.data.loading, true);
  page.reopen();
  page.confirm();
  await Promise.resolve();
  assert.equal(reopenCalls.length, 1);
  assert.equal(confirmCalls, 0);
  assert.equal(reopenCalls[0].id, 74);
  assert.equal(reopenCalls[0].body.version, 6);
  assert.equal(reopenCalls[0].body.reason, 'Still leaking at the joint');
  deferReload = true;
  finishReopen();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.data.loading, true, 'guard stays held until refreshed detail arrives');
  response.work_order = {id:74, version:7, status:'PROCESSING'};
  response.processes.push({id:13, cycle_no:3});
  finishReload();
  await pending;
  assert.equal(page.data.loading, false);
  assert.equal(page.data.canReopen, false);
  assert.equal(page.data.reopenReason, '');
  assert.equal(page.data.processHistory.length, 3);
  assert.equal(page.data.processHistory[0].receipt.method, 'old receipt');
  assert.equal(page.data.processHistory[2].receipt, null);
  console.log('page handler simulation PASS; no browser/API/DB operation');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run(
        ["node", "-e", script, str(REPO_ROOT)], capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "page handler simulation PASS" in completed.stdout
