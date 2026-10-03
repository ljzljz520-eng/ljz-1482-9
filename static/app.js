const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));
const state = {projectId:null, dashboard:null, sources:[], channels:[], reviewers:[]};

async function api(path, options={}) {
  const res = await fetch(path, {
    headers: {'Content-Type':'application/json', ...(options.headers||{})},
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), {data, status:res.status});
  return data;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function jval(v, fallback){ try{return JSON.parse(v||'')}catch{return fallback} }
function fmt(o){ return JSON.stringify(o,null,2) }
function selectOptions(items, labelFn, valueFn=x=>x.id, blank='') {
  return (blank?`<option value="">${esc(blank)}</option>`:'') + items.map(x=>`<option value="${esc(valueFn(x))}">${esc(labelFn(x))}</option>`).join('');
}
async function loadAll(){
  [state.sources, state.channels, state.reviewers] = await Promise.all([
    api('/api/sources'), api('/api/channels'), api('/api/reviewers')
  ]);
  renderReference();
  const projects = await api('/api/projects');
  if (!state.projectId || !projects.some(p=>p.id===state.projectId)) state.projectId = projects[0]?.id || null;
  $('#projectSelect').innerHTML = selectOptions(projects, p=>p.title);
  $('#projectSelect').value = state.projectId || '';
  if (state.projectId) await loadDashboard();
}
function renderReference(){
  const sourceVersions = state.sources.flatMap(s=>s.versions.map(v=>({...v, source_title:s.title})));
  $('[name=source_version_id]').innerHTML = selectOptions(sourceVersions, v=>`${v.source_title} @ ${v.version}`, v=>v.id, '固定来源版本…');
  const channelVersions = state.channels.flatMap(c=>(c.versions||[]).map(v=>({...v, channel_name:c.name})));
  $('[name=channel_version_id]').innerHTML = selectOptions(channelVersions, v=>`${cRegion(v)} ${v.channel_name} v${v.version}${v.expired?'(已过期)':''}`, v=>v.id, '求助渠道版本…');
  $('[name=reviewer_id]').innerHTML = selectOptions(state.reviewers, r=>`${r.name}（${r.role}）`, r=>r.id);
  $('#sources').innerHTML = state.sources.map(s=>`<div class="item"><b>${esc(s.title)}</b><div class="muted">${s.versions.map(v=>`v${esc(v.version)} ${esc(v.content_hash.slice(0,12))}`).join('；')}</div></div>`).join('');
  $('#reviewers').innerHTML = state.reviewers.map(r=>`<span class="pill">${esc(r.name)} · ${esc(r.role)}</span>`).join('');
  $('#channels').innerHTML = state.channels.map(c=>`<div class="item"><b>${esc(c.name)}</b> ${esc(c.region)}${(c.current&&c.current.expired)?'<span class="pill bad">当前版本已过期</span>':''}<div class="muted">${(c.versions||[]).map(v=>`v${v.version}: 至 ${esc(v.valid_until)} / ${esc(v.verification_basis)}`).join('<br>')}</div></div>`).join('');
}
function cRegion(v){ return v.region ? `[${v.region}]` : '' }
async function loadDashboard(){
  const d = await api(`/api/projects/${state.projectId}/dashboard`);
  state.dashboard = d;
  const p = d.project;
  $('#projectForm').title.value = p.title;
  $('#projectForm').intended_audience.value = p.intended_audience;
  $('#projectForm').professional_content.value = p.professional_content;
  $('#projectForm').expression_constraints_json.value = JSON.stringify(p.expression_constraints||[], null, 2);
  $('#projectForm').source_version_id.value = p.source_version_id || '';
  $('#projectForm').channel_version_id.value = p.channel_version_id || '';
  renderDashboard();
}
function renderDashboard(){
  const d = state.dashboard; if(!d) return;
  const r = d.readiness;
  const missing = d.missing_information.length ? `<b>缺失信息：</b>${d.missing_information.map(esc).join('\n')}` : '基础信息完整';
  $('#missingBox').className = 'notice ' + (d.missing_information.length ? '' : 'good');
  $('#missingBox').textContent = missing;
  const snap = d.snapshots[0];
  $('#snapshotMeta').innerHTML = snap ? `<span class="pill ${r.ok?'good':'warn'}">${snap.id}</span> <span class="muted">${esc(snap.fingerprint.slice(0,16))}</span>` : '尚无快照';
  $('[name=character_id]').innerHTML = selectOptions(d.segments.flatMap(()=>[]), x=>x.name); // placeholder refreshed below
  const chars = (d.characters || [...new Map(d.segments.filter(s=>s.character_id).map(s=>[s.character_id,{id:s.character_id,name:s.character_name,active:1}])).values()]).filter(c=>c.active===1);
  $('[name=character_id]').innerHTML = selectOptions(chars, c=>`${c.name}（${c.role||'角色'}）`, c=>c.id, '无角色（字幕/旁白）');
  const allChars = d.characters || [];
  $('#replaceOldChar').innerHTML = selectOptions(allChars.filter(c=>c.active===1), c=>`旧：${c.name}`, c=>c.id, '选择要替换的旧角色');
  $('#replaceNewChar').innerHTML = selectOptions(allChars.filter(c=>c.active===1), c=>`新：${c.name}`, c=>c.id, '选择新角色');
  $('#segments').innerHTML = d.segments.map(s=>`<li draggable="true" data-id="${s.id}"><span class="pill">${s.position}</span><span class="pill">${esc(s.kind)}</span>${s.character_name?`<span class="pill warn">${esc(s.character_name)}</span>`:''}<b> ${esc(s.text)}</b><div class="muted">${esc(s.context_note||'')}</div></li>`).join('');
  $('#revisions').innerHTML = d.revisions.length ? d.revisions.map(x=>`<div class="item">段${x.position}: ${esc(x.old_text)} → ${esc(x.new_text)} <span class="muted">(${esc(x.reviewer_name||'')}, ${esc(x.reason)})</span></div>`).join('') : '<div class="muted">暂无修订</div>';
  const segmentOptions = selectOptions(d.segments, s=>`${s.position}. ${s.kind}: ${s.text.slice(0,24)}`, s=>s.id, '整片（不选段落）');
  $('[name=segment_id]').innerHTML = segmentOptions;
  const pending = d.pending_review_scope.length ? `${d.pending_review_scope.length} 段待审/已失效` : '所有段落均有当前有效批准';
  $('#snapshotInfo').innerHTML = `<div class="notice ${r.ok?'good':'warn'}"><b>待审范围：</b>${esc(pending)}
<b>阻断项：</b>${(r.blocking||[]).map(esc).join(', ')||'无'}
<b>提示：</b>${(r.warnings||[]).map(esc).join('\n')}</div>`;
  const snapId = snap?.id;
  $('#reviews').innerHTML = '';
  if (snapId) api(`/api/snapshots/${snapId}`).then(x=>{
    $('#reviews').innerHTML = x.reviews.map(rv=>`<div class="item"><span class="pill ${rv.status==='approved'?'good':rv.status==='withdrawn'?'bad':'warn'}">${esc(rv.status)}</span> ${esc(rv.scope)} ${rv.segment_id?esc(rv.segment_id):'整片'} <span class="muted">${esc(rv.reviewer_name)} · ${esc(rv.created_at)}</span><div>内部意见：${esc(rv.internal_comment||'无')}</div>${rv.status==='approved'?`<button class="secondary" data-withdraw="${rv.id}">撤回</button>`:''}</div>`).join('') + x.invalidations.map(i=>`<div class="muted">失效：${esc(i.rule_code)} — ${esc(i.reason)}</div>`).join('');
  });
  $('#hits').className='notice warn';
  $('#hits').textContent = `词表命中 ${r.vocabulary_hit_count||0} 个，只能提示人工复核；未命中不代表安全。`;
  $('#jobResults').textContent = '';
  api(`/api/projects/${state.projectId}/jobs`).then(jobs=>$('#jobResults').textContent = fmt(jobs));
  api(`/api/projects/${state.projectId}/exports`).then(exports=>renderExports(exports));
  $('#publications').innerHTML = d.publications.map(p=>`<div class="item"><span class="pill ${p.status==='duplicate_rejected'?'bad':p.status==='published'?'good':'warn'}">${p.status}</span>${esc(p.channel_name)} / ${esc(p.request_key)}<div class="muted">${p.id}</div></div>`).join('');
}
async function renderExports(exports){
  $('#exports').innerHTML = (await Promise.all(exports.map(async e=>{
    let grantHtml='';
    if (e.status==='ready') {
      grantHtml=`<form class="inline" data-grant="${e.id}"><input name="granted_to" placeholder="站内用户" required><input name="ttl_hours" type="number" placeholder="TTL小时" /><button>授权下载</button></form><div class="download"></div>`;
    }
    return `<div class="item"><span class="pill ${e.status==='ready'?'good':e.status==='failed'?'bad':'warn'}">${e.status}</span>${e.id}<div class="muted">hash: ${esc(e.package_hash||'')}</div>${grantHtml}<div>${esc(e.failure_reason||'')}</div></div>`;
  }))).join('');
}
function currentSnapshotId(){ return state.dashboard?.snapshots?.[0]?.id; }

$('#refreshBtn').onclick=loadAll;
$('#projectSelect').onchange=e=>{state.projectId=e.target.value;loadDashboard()};
$('#sourceForm').onsubmit=async e=>{e.preventDefault();const f=e.target;await api('/api/sources',{method:'POST',body:Object.fromEntries(new FormData(f))});f.reset();await loadAll()};
$('#reviewerForm').onsubmit=async e=>{e.preventDefault();const f=e.target;await api('/api/reviewers',{method:'POST',body:Object.fromEntries(new FormData(f))});f.reset();await loadAll()};
$('#channelForm').onsubmit=async e=>{e.preventDefault();const f=e.target;await api('/api/channels',{method:'POST',body:Object.fromEntries(new FormData(f))});f.reset();await loadAll()};
$('#vocabForm').onsubmit=async e=>{e.preventDefault();const f=e.target;const fd=Object.fromEntries(new FormData(f));await api('/api/vocabularies',{method:'POST',body:{name:fd.name, terms:fd.terms.split(',').map(x=>x.trim()).filter(Boolean)}});f.reset();await loadDashboard()};
$('#projectForm').onsubmit=async e=>{
  e.preventDefault(); const f=e.target; const fd=Object.fromEntries(new FormData(f));
  const body={...fd, expression_constraints:jval(fd.expression_constraints_json,[])};
  delete body.expression_constraints_json;
  if(!state.projectId){ const p=await api('/api/projects',{method:'POST',body}); state.projectId=p.id; }
  else await api(`/api/projects/${state.projectId}`,{method:'PATCH',body});
  await api(`/api/projects/${state.projectId}/snapshots`,{method:'POST',body:{reason:'WEB_EDITOR_SAVED'}});
  await loadAll();
};
$('#characterForm').onsubmit=async e=>{e.preventDefault();const fd=Object.fromEntries(new FormData(e.target));await api(`/api/projects/${state.projectId}/characters`,{method:'POST',body:fd});e.target.reset();await loadDashboard()};
$('#segmentForm').onsubmit=async e=>{e.preventDefault();const f=e.target;const fd=Object.fromEntries(new FormData(f));if(fd.kind!=='dialogue')fd.character_id='';await api(`/api/projects/${state.projectId}/segments`,{method:'POST',body:fd});f.reset();await loadDashboard()};
$('#reorderBtn').onclick=async()=>{ const ids=$$('#segments li').map(x=>x.dataset.id); await api(`/api/projects/${state.projectId}/reorder`,{method:'POST',body:{segment_ids:ids}}); await loadDashboard(); };
$('#replaceCharBtn').onclick=async()=>{
  const oldId=$('#replaceOldChar').value, newId=$('#replaceNewChar').value;
  if(!oldId||!newId||oldId===newId) return alert('请选择两个不同角色');
  await api(`/api/projects/${state.projectId}/characters/${oldId}/replace`,{method:'PATCH',body:{new_character_id:newId}});
  await loadDashboard();
};
$('#snapshotBtn').onclick=async()=>{await api(`/api/projects/${state.projectId}/snapshots`,{method:'POST',body:{reason:'MANUAL_SNAPSHOT'}});await loadDashboard()};
$('#reviewForm').onsubmit=async e=>{e.preventDefault();const f=e.target;const fd=Object.fromEntries(new FormData(f));if(fd.scope==='whole')fd.segment_id='';await api(`/api/snapshots/${currentSnapshotId()}/reviews`,{method:'POST',body:fd});f.reset();await loadDashboard()};
$$('.topbar,main')[0];
$$('button[data-job]').forEach(btn=>btn.onclick=async()=>{try{await api(`/api/projects/${state.projectId}/jobs/${btn.dataset.job}`,{method:'POST',body:{snapshot_id:currentSnapshotId(),request_key:'default'}});await loadDashboard();}catch(e){alert(e.message+'\n'+fmt(e.data.details||{}))}});
$('#runWorker').onclick=async()=>{ try{ await api('/api/projects/'+state.projectId+'/jobs',{}); }catch{} const r=await fetch('/__worker_once'); if(!r.ok) alert('需要由维护者运行：python worker.py --once'); await loadDashboard(); };
$('#exportForm').onsubmit=async e=>{e.preventDefault();const fd=Object.fromEntries(new FormData(e.target));try{await api(`/api/projects/${state.projectId}/exports`,{method:'POST',body:{snapshot_id:currentSnapshotId(),request_key:fd.request_key}});alert('已排队；请运行 worker 处理并复验')}catch(e){alert('导出被阻断：\n'+fmt(e.data.details||e.message))}await loadDashboard()};
$('#publishForm').onsubmit=async e=>{e.preventDefault();const fd=Object.fromEntries(new FormData(e.target));try{const r=await api(`/api/projects/${state.projectId}/publish`,{method:'POST',body:{snapshot_id:currentSnapshotId(),...fd}});alert('发布请求已排队')}catch(e){alert((e.status===409?'重复请求：':'被阻断：')+'\n'+fmt(e.data.details||e.message))}await loadDashboard()};
document.addEventListener('click', async e=>{
  const w=e.target.closest('[data-withdraw]');
  if(w){ await api(`/api/reviews/${w.dataset.withdraw}/withdraw`,{method:'PATCH'}); await loadDashboard(); }
});
document.addEventListener('submit', async e=>{
  const form=e.target.closest('[data-grant]'); if(!form)return; e.preventDefault();
  const id=form.dataset.grant; const fd=Object.fromEntries(new FormData(form)); if(fd.ttl_hours==='')delete fd.ttl_hours;
  const g=await api(`/api/exports/${id}/grant`,{method:'POST',body:fd});
  form.nextElementSibling.innerHTML = `Token: ${esc(g.token)}<br><a href="/api/exports/${id}/download?token=${encodeURIComponent(g.token)}">站内下载（可撤销）</a> <button class="danger" data-revoke="${esc(g.token)}">撤销</button>`;
});
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-revoke]'); if(!b)return;
  await api(`/api/downloads/${encodeURIComponent(b.dataset.revoke)}/revoke`,{method:'PATCH'});
  b.closest('.download').textContent='下载权限已撤销';
});
// HTML5 drag reorder
let dragged=null;
document.addEventListener('dragstart',e=>{if(e.target.matches('#segments li')){dragged=e.target;e.target.style.opacity=.4}});
document.addEventListener('dragend',()=>{if(dragged)dragged.style.opacity=1;dragged=null;$$('#segments li').forEach(x=>x.style.borderTop='')});
document.addEventListener('dragover',e=>{e.preventDefault();const li=e.target.closest('#segments li');if(li&&dragged&&li!==dragged)li.style.borderTop='3px solid #2458d3'});
document.addEventListener('drop',e=>{const li=e.target.closest('#segments li');if(li&&dragged){li.style.borderTop='';li.before(dragged)}});
loadAll().catch(err=>alert(err));
