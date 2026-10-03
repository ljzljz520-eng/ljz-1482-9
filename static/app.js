/* 心理科普短片策划平台 — 前端 */
const S = {
  projects: [], pid: null, tab: 'overview',
  detail: null, segments: [], reviews: [], reviewers: [], sources: [],
  characters: [], glossary: [], ghits: null, channels: [], pchannels: [],
  exports: [], publishes: [], snapshots: [],
};

async function api(path, method = 'GET', data) {
  const opts = { method, headers: {} };
  if (data !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data); }
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.error || ('HTTP ' + r.status)); e.payload = j; throw e; }
  return j;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const $ = sel => document.querySelector(sel);
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; setTimeout(() => t.style.display = 'none', 3500); }
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); await reload(); return r; }
  catch (e) { toast('错误：' + e.message + (e.payload && e.payload.missing_info ? '（缺失 ' + e.payload.missing_info.length + ' 项）' : '')); }
}

async function loadProjects() {
  S.projects = await api('/api/projects');
  const ul = $('#project-list');
  ul.innerHTML = S.projects.map(p =>
    `<li data-id="${p.id}" class="${p.id === S.pid ? 'active' : ''}">
       ${esc(p.title)}
       <span class="badges">缺失 ${p.missing_count} · 待审 ${p.pending_segments} · 整片${p.whole_review_valid ? '✓' : '✗'}</span>
     </li>`).join('');
  ul.querySelectorAll('li').forEach(li => li.onclick = () => { S.pid = +li.dataset.id; reload(); });
  $('#side-hint').textContent = S.pid ? '当前项目 #' + S.pid : '请选择或创建项目';
}

async function reload() {
  await loadProjects();
  if (!S.pid) { document.querySelectorAll('.tab').forEach(t => t.innerHTML = '<p class="hint">请先选择项目</p>'); return; }
  S.detail = await api(`/api/projects/${S.pid}`);
  if (S.tab === 'segments') {
    [S.segments, S.sources, S.characters] = await Promise.all([
      api(`/api/projects/${S.pid}/segments`), api('/api/sources'), api(`/api/projects/${S.pid}/characters`)]);
  } else if (S.tab === 'review') {
    [S.reviews, S.reviewers, S.snapshots, S.segments] = await Promise.all([
      api(`/api/projects/${S.pid}/reviews`), api('/api/reviewers'),
      api(`/api/projects/${S.pid}/snapshots`), api(`/api/projects/${S.pid}/segments`)]);
  } else if (S.tab === 'glossary') {
    [S.glossary, S.ghits] = await Promise.all([api('/api/glossary'), api(`/api/projects/${S.pid}/glossary`)]);
  } else if (S.tab === 'channels') {
    [S.channels, S.pchannels] = await Promise.all([api('/api/help-channels'), api(`/api/projects/${S.pid}/channels`)]);
  } else if (S.tab === 'exports') {
    const ex = await api(`/api/projects/${S.pid}/exports`);
    S.exports = ex.exports; S.publishes = ex.publish_requests;
  }
  render();
}

/* ---------------- 概览 ---------------- */
function renderOverview() {
  const d = S.detail, rs = d.review_status, ps = d.pending_scope;
  $('#tab-overview').innerHTML = `
    <div class="panel"><h3>项目信息（适用人群 / 表述限制）</h3>
      <div class="row"><label>标题</label><input id="p-title" value="${esc(d.project.title)}" style="width:240px"></div>
      <div class="row"><label>适用人群</label><input id="p-audience" value="${esc(d.project.audience || '')}" style="width:240px">
        <span class="hint">变更适用人群会使整片批准与已排队导出失效</span></div>
      <div class="row"><label>表述限制</label><input id="p-limits" value="${esc(d.project.expression_limits || '')}" style="width:360px"></div>
      <div class="row"><button id="btn-save-project">保存</button></div>
    </div>
    <div class="panel"><h3>缺失信息（导出前必须清零）</h3>
      ${d.missing_info.length ? `<ul class="missing">${d.missing_info.map(m => `<li>${esc(m.message)}</li>`).join('')}</ul>`
        : '<span class="tag ok">无缺失</span>'}
    </div>
    <div class="panel"><h3>待审范围</h3>
      ${ps.segments_pending.length ? `<ul class="pending">${ps.segments_pending.map(s =>
        `<li>片段#${s.segment_id}（${s.kind}，修订${s.revision_no}）：${esc(s.reason)}</li>`).join('')}</ul>`
        : '<span class="tag ok">片段均已获批</span>'}
      <p>整片审核：${ps.whole_review_valid ? '<span class="tag ok">有效</span>' : '<span class="tag bad">缺失/已失效</span>'}
        ${ps.whole_stale_reviews ? `<span class="tag warn">${ps.whole_stale_reviews} 条整片批准已失效</span>` : ''}</p>
    </div>
    <div class="panel"><h3>段落级审核 vs 整片审核</h3>
      <table><tr><th>片段</th><th>类型</th><th>修订</th><th>前后文</th><th>段落级批准</th></tr>
        ${rs.segment_level.segments.map(s => `<tr>
          <td>#${s.segment_id}</td><td>${s.kind}</td><td>r${s.revision_no}</td>
          <td>${s.context.prev ?? '—'} ↔ ${s.context.next ?? '—'}</td>
          <td>${s.approved ? '<span class="tag ok">有效</span>' : '<span class="tag bad">待审</span>'}
              ${s.stale_approvals ? `<span class="tag warn">${s.stale_approvals} 条已失效</span>` : ''}</td></tr>`).join('')}
      </table>
      <p>整片审核：${rs.whole_film.approved ? '<span class="tag ok">有效</span>' : '<span class="tag bad">无效</span>'}
        <span class="hint">语境哈希 ${rs.whole_film.current_context_hash.slice(0, 12)}…</span></p>
      <ul class="hint">${rs.comparison.map(c => `<li>${esc(c)}</li>`).join('')}</ul>
    </div>`;
  $('#btn-save-project').onclick = () => act(() =>
    api(`/api/projects/${S.pid}`, 'PATCH', {
      title: $('#p-title').value, audience: $('#p-audience').value,
      expression_limits: $('#p-limits').value,
    }), '已保存（若受众/限制变更，相关批准与导出已失效）');
}

/* ---------------- 片段与来源 ---------------- */
function renderSegments() {
  const kindOpts = [['subtitle', '字幕'], ['narration', '旁白'], ['character_line', '角色台词']]
    .map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
  const charOpts = '<option value="">（无角色）</option>' + S.characters.filter(c => !c.replaced_by)
    .map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const srcOpts = '<option value="">（未固定来源）</option>' + S.sources.flatMap(s =>
    s.versions.map(v => `<option value="${s.id}:${v.version}">${esc(s.title)} v${v.version}</option>`)).join('');
  $('#tab-segments').innerHTML = `
    <div class="panel"><h3>片段（字幕 / 旁白 / 角色台词）</h3>
      <table><tr><th>#</th><th>类型</th><th>角色</th><th>文本</th><th>来源版本</th><th>修订</th><th>批准</th><th>操作</th></tr>
      ${S.segments.map((s, i) => `<tr>
        <td>${s.id}</td><td>${s.kind_label}</td><td>${esc(s.character || '—')}</td>
        <td>${esc(s.text)}</td>
        <td>${s.source_title ? esc(s.source_title) + ' v' + s.source_version : '<span class="tag bad">未固定</span>'}</td>
        <td>r${s.revision_no}</td>
        <td>${s.approved ? '<span class="tag ok">有效</span>' : '<span class="tag bad">待审</span>'}</td>
        <td>
          <button class="ghost" data-act="up" data-i="${i}" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="ghost" data-act="down" data-i="${i}" ${i === S.segments.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="ghost" data-act="edit" data-id="${s.id}">改文本</button>
        </td></tr>`).join('')}
      </table>
      <p class="hint">↑↓ 为剪辑重排：重排后上下文改变，脱离语境的批准立即失效。</p>
      <h3>新增片段</h3>
      <div class="row"><select id="seg-kind">${kindOpts}</select>
        <select id="seg-char">${charOpts}</select>
        <select id="seg-src">${srcOpts}</select></div>
      <div class="row"><input id="seg-text" placeholder="片段文本" style="width:480px">
        <button id="btn-add-seg">添加</button></div>
    </div>
    <div class="panel"><h3>角色</h3>
      <table><tr><th>ID</th><th>名称</th><th>状态</th><th>操作</th></tr>
      ${S.characters.map(c => `<tr><td>${c.id}</td><td>${esc(c.name)}</td>
        <td>${c.replaced_by ? `<span class="tag warn">已被 #${c.replaced_by} 替换</span>` : '<span class="tag ok">在用</span>'}</td>
        <td>${c.replaced_by ? '' : `<button class="ghost" data-act="replace" data-id="${c.id}">替换</button>`}</td></tr>`).join('')}
      </table>
      <div class="row"><input id="char-name" placeholder="新角色名"><button id="btn-add-char">添加角色</button></div>
      <p class="hint">角色替换会使该角色全部台词片段修订号提升，需重新审阅。</p>
    </div>
    <div class="panel"><h3>专业来源（不可变版本）</h3>
      <table><tr><th>ID</th><th>标题</th><th>出版方</th><th>版本</th><th>操作</th></tr>
      ${S.sources.map(s => `<tr><td>${s.id}</td><td>${esc(s.title)}</td><td>${esc(s.publisher)}</td>
        <td>${s.versions.map(v => 'v' + v.version).join('、')}</td>
        <td><button class="ghost" data-act="attach-src" data-id="${s.id}">关联到项目</button>
            <button class="ghost" data-act="new-ver" data-id="${s.id}">新增版本</button></td></tr>`).join('')}
      </table>
      <div class="row"><input id="src-title" placeholder="来源标题"><input id="src-pub" placeholder="出版方">
        <input id="src-content" placeholder="首版内容" style="width:280px"><button id="btn-add-src">新建来源</button></div>
    </div>`;
  $('#btn-add-seg').onclick = () => {
    const sv = $('#seg-src').value.split(':');
    act(() => api(`/api/projects/${S.pid}/segments`, 'POST', {
      kind: $('#seg-kind').value, text: $('#seg-text').value,
      character_id: $('#seg-char').value ? +$('#seg-char').value : null,
      source_id: sv[0] ? +sv[0] : null, source_version: sv[1] ? +sv[1] : null,
    }), '片段已添加');
  };
  $('#btn-add-char').onclick = () => act(() =>
    api(`/api/projects/${S.pid}/characters`, 'POST', { name: $('#char-name').value }), '角色已添加');
  $('#btn-add-src').onclick = () => act(() =>
    api('/api/sources', 'POST', { title: $('#src-title').value, publisher: $('#src-pub').value, content: $('#src-content').value }), '来源已创建');
  document.querySelectorAll('#tab-segments [data-act]').forEach(b => b.onclick = () => {
    const actName = b.dataset.act;
    if (actName === 'up' || actName === 'down') {
      const i = +b.dataset.i, j = actName === 'up' ? i - 1 : i + 1;
      const ids = S.segments.map(s => s.id);
      [ids[i], ids[j]] = [ids[j], ids[i]];
      act(() => api(`/api/projects/${S.pid}/reorder`, 'POST', { segment_ids: ids }), '已重排：相关批准已失效');
    } else if (actName === 'edit') {
      const s = S.segments.find(x => x.id === +b.dataset.id);
      const t = prompt('新文本（将生成新修订，旧批准失效）', s.text);
      if (t !== null) act(() => api(`/api/segments/${s.id}`, 'PATCH', { text: t }), '已修改');
    } else if (actName === 'replace') {
      const n = prompt('替换为新角色名：');
      if (n) act(() => api(`/api/characters/${b.dataset.id}/replace`, 'POST', { new_name: n }), '角色已替换，相关台词需重审');
    } else if (actName === 'attach-src') {
      act(() => api(`/api/projects/${S.pid}/sources`, 'POST', { source_id: +b.dataset.id }), '已关联');
    } else if (actName === 'new-ver') {
      const c = prompt('新版本内容：');
      if (c) act(() => api(`/api/sources/${b.dataset.id}/versions`, 'POST', { content: c }), '已新增版本（已导出成果仍引用旧固定版本）');
    }
  });
}

/* ---------------- 审阅 ---------------- */
function renderReview() {
  const cur = S.snapshots.find(s => s.current);
  const revOpts = S.reviewers.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
  const segOpts = S.segments.map(s => `<option value="${s.id}">#${s.id} ${s.kind_label} r${s.revision_no}</option>`).join('');
  $('#tab-review').innerHTML = `
    <div class="panel"><h3>审阅快照（字幕/旁白/角色台词共用同一快照）</h3>
      <button id="btn-snapshot">按当前语境创建快照</button>
      <table><tr><th>ID</th><th>语境哈希</th><th>创建时间</th><th>状态</th></tr>
      ${S.snapshots.map(s => `<tr><td>${s.id}</td><td>${s.context_hash.slice(0, 16)}…</td><td>${s.created_at}</td>
        <td>${s.current ? '<span class="tag ok">当前</span>' : '<span class="tag warn">已过期</span>'}</td></tr>`).join('')}
      </table>
    </div>
    <div class="panel"><h3>提交审阅 ${cur ? `（快照 #${cur.id}）` : '（需先创建快照）'}</h3>
      <div class="row"><select id="rv-reviewer">${revOpts}</select>
        <select id="rv-scope"><option value="segment">段落级</option><option value="whole">整片</option></select>
        <select id="rv-segment">${segOpts}</select>
        <select id="rv-decision"><option value="approved">通过</option><option value="needs_changes">需修改</option><option value="rejected">不通过</option></select></div>
      <div class="row"><input id="rv-public" placeholder="公开备注（可进入公开包）" style="width:320px">
        <input id="rv-internal" placeholder="内部意见（不进入公开包）" style="width:320px">
        <button id="btn-review" ${cur ? '' : 'disabled'}>提交</button></div>
      <p class="note">内部审阅意见仅供站内协作，绝不进入公开导出包。</p>
    </div>
    <div class="panel"><h3>审阅记录</h3>
      <table><tr><th>ID</th><th>范围</th><th>片段</th><th>审阅人</th><th>结论</th><th>公开备注</th><th>内部意见</th><th>状态</th><th></th></tr>
      ${S.reviews.map(r => `<tr><td>${r.id}</td><td>${r.scope === 'whole' ? '整片' : '段落'}</td>
        <td>${r.segment_id ?? '—'}</td><td>${esc(r.reviewer_name)}</td><td>${r.decision}</td>
        <td>${esc(r.comment_public || '')}</td><td>${esc(r.comment_internal || '')}</td>
        <td>${r.status === 'active' ? '<span class="tag ok">有效</span>' : '<span class="tag bad">已撤回</span>'}</td>
        <td>${r.status === 'active' ? `<button class="danger" data-w="${r.id}">撤回</button>` : ''}</td></tr>`).join('')}
      </table>
    </div>
    <div class="panel"><h3>审阅人</h3>
      <div class="row"><input id="rev-name" placeholder="姓名"><button id="btn-add-rev">添加</button></div>
    </div>`;
  $('#btn-snapshot').onclick = () => act(() => api(`/api/projects/${S.pid}/snapshots`, 'POST'), '快照已创建');
  $('#btn-review').onclick = () => act(() => api('/api/reviews', 'POST', {
    project_id: S.pid, snapshot_id: cur.id, reviewer_id: +$('#rv-reviewer').value,
    scope: $('#rv-scope').value, segment_id: +$('#rv-segment').value,
    decision: $('#rv-decision').value, comment_public: $('#rv-public').value,
    comment_internal: $('#rv-internal').value,
  }), '审阅已提交');
  $('#btn-add-rev').onclick = () => act(() => api('/api/reviewers', 'POST', { name: $('#rev-name').value }), '已添加');
  document.querySelectorAll('[data-w]').forEach(b => b.onclick = () => {
    if (confirm('撤回后依赖该快照的导出将失效，确认？'))
      act(() => api(`/api/reviews/${b.dataset.w}/withdraw`, 'POST'), '已撤回');
  });
}

/* ---------------- 词表 ---------------- */
function renderGlossary() {
  $('#tab-glossary').innerHTML = `
    <div class="panel"><h3>词表命中（仅为复核线索）</h3>
      <p class="note">${esc(S.ghits.disclaimer)}</p>
      ${S.ghits.hits.length ? `<table><tr><th>片段</th><th>修订</th><th>词条</th><th>分类</th><th>说明</th></tr>
        ${S.ghits.hits.map(h => `<tr><td>#${h.segment_id}</td><td>r${h.revision_no}</td>
          <td>${esc(h.term)}</td><td>${esc(h.category)}</td><td>${esc(h.note || '')}</td></tr>`).join('')}</table>`
        : '<p class="hint">当前无命中 —— 不代表内容安全，仍需人工审阅。</p>'}
    </div>
    <div class="panel"><h3>词条管理</h3>
      <table><tr><th>ID</th><th>词条</th><th>分类</th><th>说明</th></tr>
      ${S.glossary.map(t => `<tr><td>${t.id}</td><td>${esc(t.term)}</td><td>${esc(t.category)}</td><td>${esc(t.note || '')}</td></tr>`).join('')}
      </table>
      <div class="row"><input id="term" placeholder="词条"><input id="term-cat" placeholder="分类" value="general">
        <input id="term-note" placeholder="说明"><button id="btn-add-term">添加</button></div>
    </div>`;
  $('#btn-add-term').onclick = () => act(() =>
    api('/api/glossary', 'POST', { term: $('#term').value, category: $('#term-cat').value, note: $('#term-note').value }), '已添加');
}

/* ---------------- 求助渠道 ---------------- */
function renderChannels() {
  const vtag = v => v === 'valid' ? '<span class="tag ok">有效</span>' : v === 'expired' ? '<span class="tag bad">已过期</span>' : '<span class="tag warn">未生效</span>';
  $('#tab-channels').innerHTML = `
    <div class="panel"><h3>项目关联渠道</h3>
      ${S.pchannels.length ? `<table><tr><th>名称</th><th>地区</th><th>联系方式</th><th>有效期</th><th>核验依据</th><th>状态</th></tr>
        ${S.pchannels.map(c => `<tr><td>${esc(c.name)}</td><td>${esc(c.region)}</td><td>${esc(c.contact)}</td>
          <td>${c.valid_from} ~ ${c.valid_until}</td><td>${esc(c.verification_basis)}</td><td>${vtag(c.validity)}</td></tr>`).join('')}</table>`
        : '<p class="hint">尚未关联渠道（缺失信息）。</p>'}
    </div>
    <div class="panel"><h3>渠道库（维护者提供）</h3>
      <table><tr><th>ID</th><th>名称</th><th>地区</th><th>有效期</th><th>状态</th><th></th></tr>
      ${S.channels.map(c => `<tr><td>${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.region)}</td>
        <td>${c.valid_from} ~ ${c.valid_until}</td><td>${vtag(c.validity)}</td>
        <td><button class="ghost" data-attach="${c.id}">关联到项目</button></td></tr>`).join('')}
      </table>
      <h3>新增渠道</h3>
      <div class="row"><input id="ch-name" placeholder="名称"><input id="ch-region" placeholder="地区">
        <input id="ch-contact" placeholder="联系方式"></div>
      <div class="row"><input id="ch-from" type="date"><input id="ch-until" type="date">
        <input id="ch-maint" placeholder="维护者"></div>
      <div class="row"><input id="ch-basis" placeholder="核验依据（来源+核验方式+日期）" style="width:420px">
        <button id="btn-add-ch">添加渠道</button></div>
      <p class="hint">渠道过期会使已排队/就绪导出失效；导出包只包含当前有效渠道。</p>
    </div>`;
  $('#btn-add-ch').onclick = () => act(() => api('/api/help-channels', 'POST', {
    name: $('#ch-name').value, region: $('#ch-region').value, contact: $('#ch-contact').value,
    valid_from: $('#ch-from').value, valid_until: $('#ch-until').value,
    verification_basis: $('#ch-basis').value, maintainer: $('#ch-maint').value,
  }), '渠道已添加');
  document.querySelectorAll('[data-attach]').forEach(b => b.onclick = () =>
    act(() => api(`/api/projects/${S.pid}/channels`, 'POST', { channel_id: +b.dataset.attach }), '已关联'));
}

/* ---------------- 导出与发布 ---------------- */
function renderExports() {
  const stag = s => ({ queued: '<span class="tag info">已排队</span>', ready: '<span class="tag ok">就绪</span>', invalidated: '<span class="tag bad">已失效</span>', published: '<span class="tag info">已发布</span>' }[s]);
  $('#tab-exports').innerHTML = `
    <div class="panel"><h3>导出</h3>
      <div class="row"><button id="btn-export">排队导出</button>
        <button class="ghost" id="btn-worker">运行工作器（生成脚本预览与素材清单）</button></div>
      <p class="hint">导出要求：缺失信息为零、全部片段在当前语境获批、整片审核有效。公开包不含内部审阅意见。</p>
      <table><tr><th>ID</th><th>快照</th><th>状态</th><th>失效原因</th><th>下载授权</th><th>操作</th></tr>
      ${S.exports.map(e => `<tr><td>${e.id}</td><td>#${e.snapshot_id}</td><td>${stag(e.status)}</td>
        <td>${esc(e.invalid_reason || '')}</td>
        <td>${e.grants.map(g => `<span class="tag ${g.revoked_at ? 'bad' : 'ok'}">${esc(g.user_token)}${g.revoked_at ? '（已撤销）' : ''}</span>`).join('') || '—'}</td>
        <td>
          <button class="ghost" data-grant="${e.id}">授权</button>
          <button class="ghost" data-revoke="${e.id}">撤销</button>
          <button class="ghost" data-dl="${e.id}">下载</button>
          <button class="ghost" data-pub="${e.id}">发布</button>
        </td></tr>`).join('')}
      </table>
    </div>
    <div class="panel"><h3>发布请求（幂等去重）</h3>
      <table><tr><th>ID</th><th>导出</th><th>幂等键</th><th>状态</th><th>时间</th></tr>
      ${S.publishes.map(p => `<tr><td>${p.id}</td><td>#${p.export_id}</td><td>${esc(p.idempotency_key)}</td><td>${p.status}</td><td>${p.created_at}</td></tr>`).join('')}
      </table>
      <p class="hint">同一幂等键重复提交不会产生重复发布。</p>
    </div>
    <div class="panel"><h3>下载结果</h3><pre id="dl-out">（尚未下载）</pre></div>`;
  $('#btn-export').onclick = () => act(() => api(`/api/projects/${S.pid}/exports`, 'POST'), '已排队');
  $('#btn-worker').onclick = () => act(() => api('/api/worker/run', 'POST'), '工作器已运行');
  document.querySelectorAll('[data-grant]').forEach(b => b.onclick = () => {
    const t = prompt('授权用户标识：'); if (t) act(() => api(`/api/exports/${b.dataset.grant}/grants`, 'POST', { user_token: t }), '已授权');
  });
  document.querySelectorAll('[data-revoke]').forEach(b => b.onclick = () => {
    const t = prompt('撤销哪个用户标识：'); if (t) act(() => api(`/api/exports/${b.dataset.revoke}/grants/${encodeURIComponent(t)}/revoke`, 'POST'), '已撤销');
  });
  document.querySelectorAll('[data-dl]').forEach(b => b.onclick = async () => {
    const t = prompt('下载用户标识：'); if (!t) return;
    try {
      const pkg = await api(`/api/exports/${b.dataset.dl}/download?token=${encodeURIComponent(t)}`);
      $('#dl-out').textContent = JSON.stringify(pkg, null, 2);
    } catch (e) { toast('下载失败：' + e.message); }
  });
  document.querySelectorAll('[data-pub]').forEach(b => b.onclick = () => {
    const k = prompt('幂等键（重复提交同一键不会重复发布）：', 'pub-' + Date.now());
    if (k) act(async () => {
      const r = await api(`/api/projects/${S.pid}/publish`, 'POST', { export_id: +b.dataset.pub, idempotency_key: k });
      toast(r.duplicate ? '重复请求：已返回既有发布记录 #' + r.publish_request.id : '发布请求已受理 #' + r.publish_request.id);
    });
  });
}

/* ---------------- 框架 ---------------- */
function render() {
  if (!S.pid) return;
  ({ overview: renderOverview, segments: renderSegments, review: renderReview,
     glossary: renderGlossary, channels: renderChannels, exports: renderExports })[S.tab]();
}
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => {
  S.tab = b.dataset.tab;
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tab').forEach(t => t.classList.add('hidden'));
  $('#tab-' + S.tab).classList.remove('hidden');
  reload();
});
$('#btn-create-project').onclick = async () => {
  const title = $('#new-project-title').value.trim(); if (!title) return;
  const r = await api('/api/projects', 'POST', { title });
  S.pid = r.id; $('#new-project-title').value = ''; reload();
};
reload();
