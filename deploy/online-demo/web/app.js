'use strict';
const el = (id) => document.getElementById(id);
let timer = null, expiryTimer = null, polls = 0, submitted = false, active = !document.hidden, generation = 0;
const categoryNames = {ELECTRICAL:'电气维修', PLUMBING:'给排水维修', HVAC:'暖通维修', CLEANING:'保洁', SECURITY:'安防', OTHER:'其他问题'};
const priorityNames = {LOW:'一般', MEDIUM:'普通', HIGH:'优先', URGENT:'紧急'};
async function api(path, data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {method:data === undefined ? 'GET':'POST', credentials:'same-origin', cache:'no-store', signal:controller.signal,
      headers:data === undefined ? {} : {'Content-Type':'application/json'}, body:data === undefined ? undefined : JSON.stringify(data)});
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(value.error?.message || '暂时无法获取结果，请稍后查看。');
      error.code = value.error?.code;
      throw error;
    }
    return value;
  } finally { clearTimeout(timeout); }
}
function notice(text) { el('notice').textContent = text; }
function stopPolling() { clearTimeout(timer); timer = null; }
function current(gen) { return active && gen === generation; }
function mark(id, kind) { el('step-' + id).className = kind; }
function lockReport() { el('scenario-fieldset').disabled = true; el('submit-button').disabled = true; }
function closed() {
  stopPolling(); clearTimeout(expiryTimer); lockReport(); el('access-button').disabled = true;
  el('availability').textContent = '本轮体验已结束'; el('refresh-button').disabled = true;
}
function showSubmitted(moveFocus = false) {
  submitted = true; lockReport(); mark('report','done'); mark('analysis','current');
  el('report-state').textContent = '报修已保存'; el('analysis-state').textContent = '正在等待分析结果';
  el('progress-summary').textContent = '工单已提交，处理进度会自动更新。'; el('refresh-button').hidden = false;
  if (moveFocus) { el('progress-panel').focus({preventScroll:true}); el('progress-panel').scrollIntoView({block:'start'}); }
}
async function refresh() {
  if (!submitted || !active) return;
  const gen = generation;
  stopPolling(); el('refresh-button').disabled = true;
  let terminal = false;
  try {
    const value = await api('/order');
    if (!current(gen)) return;
    el('details').hidden = false;
    el('category').textContent = value.analysis_status === 'COMPLETED' ? (categoryNames[value.category] || '已识别，待物业确认分类') : '等待分析';
    el('priority').textContent = value.analysis_status === 'COMPLETED' ? (priorityNames[value.priority] || '待确认') : '等待分析';
    el('created-at').textContent = value.created_at;
    mark('analysis', value.analysis_status === 'COMPLETED' ? 'done' : 'current');
    el('analysis-state').textContent = value.analysis_status === 'COMPLETED' ? '已完成问题识别' : '正在等待分析结果';
    if (['LOW_CONFIDENCE','STALE','UNKNOWN'].includes(value.analysis_status)) {
      mark('analysis','issue'); el('analysis-state').textContent = value.analysis_status === 'STALE' ? '信息已更新，分析待核实' : '识别结果需要物业核实';
    } else if (value.analysis_status === 'FAILED') { mark('analysis','issue'); el('analysis-state').textContent = '自动分析未完成'; }
    const phases = {
      ANALYZING:['等待分析完成','工单已保存，正在等待分析结果。','',false],
      REVIEW:['等待物业核实后安排','本次分析需要物业核实，工单已保留。','issue',true],
      FAILED:['已保留工单，等待物业跟进','本次未完成自动派单，工单没有丢失。','issue',true],
      UNASSIGNED:['暂未自动派单，等待物业安排','问题分析已完成，人员安排尚未确认。','',false],
      AUTO_ASSIGNED:['已自动派单，等待工作人员接单','本次体验已完成自动识别与派单。','done',true],
      MANUAL_ASSIGNED:['物业已安排工作人员，等待接单','工单已由物业安排工作人员。','done',true],
      ACCEPTED:['工作人员已接单','工单已接单，等待开始处理。','done',true],
      PROCESSING:['工作人员正在处理','工单正在处理中。','done',true],
      WAIT_CONFIRM:['处理已完成，等待住户确认','工单等待住户确认处理结果。','done',true],
      COMPLETED:['工单已完成','本工单已完成。','done',true],
      CANCELLED:['工单已撤销','本工单已撤销。','',true],
      CLOSED:['工单已关闭','本工单已关闭。','',true]
    };
    const phase = phases[value.phase] || ['状态待核实','暂时无法确认处理阶段。','issue',true];
    el('assignment-state').textContent = phase[0]; el('progress-summary').textContent = phase[1]; mark('assignment',phase[2]); terminal = phase[3];
    notice('');
  } catch (error) {
    if (!current(gen)) return;
    notice(error.message || '暂时无法获取结果。');
    if (error.code === 'CLOSED') { closed(); return; }
  } finally { if (current(gen) && el('availability').textContent !== '本轮体验已结束') el('refresh-button').disabled = false; }
  if (!current(gen)) return;
  polls += 1;
  if (!terminal && polls < 120) timer = setTimeout(() => refresh(), 5000);
  else if (!terminal) notice('进度更新已暂停。可稍后点击“刷新进度”，无需重复报修。');
}
async function loadStatus() {
  if (!active) return;
  const gen = generation;
  try {
    const value = await api('/status');
    if (!current(gen)) return;
    if (!value.open) { closed(); return; }
    el('availability').textContent = '本轮体验已开启'; el('access-button').disabled = false;
    clearTimeout(expiryTimer);
    if (Number.isFinite(value.expires_at)) expiryTimer = setTimeout(closed, Math.max(0, value.expires_at * 1000 - Date.now()));
    if (value.authenticated) {
      el('access-form').hidden = true;
      el('scenario-fieldset').disabled = !value.available || value.attempted;
      el('submit-button').disabled = !value.available || value.attempted;
    }
    if (value.submitted) { showSubmitted(); await refresh(); }
    else if (value.attempted) notice('提交结果待核实，本轮不重复提交，请联系演示负责人。');
    else if (!value.available) notice('本轮体验额度已用完，请等待下次开放。');
  } catch (_) {
    if (!current(gen)) return;
    lockReport(); el('access-button').disabled = true; el('availability').textContent = '体验服务未开启';
    notice('暂时无法连接体验服务，请等待演示负责人开启后刷新页面。');
  }
}
el('access-form').addEventListener('submit', async (event) => {
  event.preventDefault(); el('access-button').disabled = true;
  const gen = generation;
  try { await api('/session', {code:el('access-code').value}); if(!current(gen)) return; el('access-code').value = ''; notice(''); await loadStatus(); }
  catch (error) { if(!current(gen)) return; notice(error.message); if(error.code === 'CLOSED') closed(); else el('access-button').disabled = false; }
});
el('report-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (el('submit-button').disabled) return;
  const scenario = document.querySelector('input[name="scenario"]:checked').value;
  const gen = generation;
  lockReport(); notice('正在保存报修，请勿重复提交。');
  try { await api('/order', {scenario}); if(!current(gen)) return; notice(''); showSubmitted(true); await refresh(); }
  catch (error) { if(!current(gen)) return; notice(error.code ? error.message : '连接中断，提交结果待核实。请刷新本页查看，不要重复报修。'); if(error.code === 'CLOSED') closed(); }
});
el('refresh-button').addEventListener('click', () => { polls = 0; refresh(); });
function hide() { active = false; generation += 1; stopPolling(); clearTimeout(expiryTimer); }
function resume() { if (active || document.hidden) return; active = true; generation += 1; loadStatus(); }
window.addEventListener('pagehide', hide);
window.addEventListener('pageshow', resume);
document.addEventListener('visibilitychange', () => { if(document.hidden) hide(); else resume(); });
loadStatus();
