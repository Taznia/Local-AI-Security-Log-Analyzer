'use strict';
const $ = id => document.getElementById(id);
let job = null, health = null, filter = 'all', search = '', uploading = false, busy = false;
const emptyFindingsHTML = $('findingsList').innerHTML;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => Number(n || 0).toLocaleString();
const time = seconds => seconds < 60 ? `${Math.round(seconds)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : message; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function alertMessage(message) { $('alert').textContent = message; $('alert').classList.toggle('hidden', !message); }
function rememberJob() {
  localStorage.setItem('tracewatch-job', job.id);
  const url = new URL(window.location.href); url.searchParams.set('analysis', job.id);
  window.history.replaceState(null, '', url);
}

async function refreshHealth() {
  try {
    health = await api('/api/health');
    $('modelStatus').textContent = health.status === 'ready' ? 'Local model connected' : health.status === 'error' ? 'Model setup required' : 'Loading local model…';
    $('modelLight').className = 'status-light ' + health.status;
    $('ram').textContent = `${Math.round(health.rss_bytes / 1048576)} MiB RAM`;
    $('modelName').textContent = 'Qwen3.5 2B · Q4_K_M · 2,048 context';
    if (health.model_error && !job?.error) alertMessage(health.model_error);
    updateStart();
  } catch {
    $('modelStatus').textContent = 'Backend unavailable'; $('modelLight').className = 'status-light error';
    health = null; updateStart();
  }
}

function updateStart() {
  const active = job && ['queued','running'].includes(job.status);
  $('startButton').disabled = !job || job.status !== 'uploaded' || health?.status !== 'ready' || uploading || busy;
  $('startButton').firstElementChild.textContent = uploading ? 'Uploading & validating…' : active ? 'Analysis in progress' : job?.status === 'completed' ? 'Analysis complete' : health?.status === 'loading' ? 'Waiting for local model…' : 'Start analysis';
}

async function chooseFile(file) {
  if (!file || uploading) return;
  if (!/\.(log|txt)$/i.test(file.name)) { alertMessage('Choose a .log or .txt file.'); return; }
  if (file.size > 8 * 1024 * 1024) { alertMessage('File is too large. Maximum upload is 8 MiB.'); return; }
  if (!file.size) { alertMessage('The selected file is empty.'); return; }
  uploading = true; alertMessage(''); updateStart();
  try {
    const form = new FormData(); form.append('file', file);
    job = await api('/api/jobs', {method:'POST', body:form});
    rememberJob(); filter = 'all'; search = ''; $('search').value = '';
    renderJob(); await refreshHistory();
  } catch (error) { alertMessage(error.message); }
  finally { uploading = false; updateStart(); $('fileInput').value = ''; }
}

async function startJob() {
  if (!job || busy) return;
  busy = true; updateStart(); alertMessage('');
  try { await api(`/api/jobs/${job.id}/start`, {method:'POST'}); await refreshJob(); }
  catch (error) { alertMessage(error.message); }
  finally { busy = false; updateStart(); }
}

async function refreshJob() {
  if (!job) return;
  const id = job.id;
  try { const next = await api(`/api/jobs/${id}`); if (job.id !== id) return; job = next; renderJob(); }
  catch (error) { alertMessage(error.message); }
}

function renderJob() {
  if (!job) return;
  $('dropTitle').textContent = job.filename;
  $('dropSubtitle').textContent = `${fmt(job.lines)} lines · ${(job.bytes/1024).toFixed(1)} KiB · click to replace`;
  const labels = {uploaded:'FILE VALIDATED',queued:'QUEUED',running:'ANALYZING',completed:'COMPLETE',failed:'SAVED · RETRY AVAILABLE',interrupted:'CHECKPOINT SAVED'};
  $('jobBadge').textContent = labels[job.status] || job.status;
  $('jobBadge').className = 'badge ' + job.status;
  const completed = job.status === 'completed';
  const phases = ['splitting','analyzing','correlating'];
  const current = phases.indexOf(job.phase);
  document.querySelectorAll('.pipeline-step').forEach((step, i) => {
    const done = completed || (current > i && job.status !== 'uploaded');
    const active = job.status === 'running' && current === i;
    step.classList.toggle('done', done); step.classList.toggle('current', active);
    step.querySelector('.step-node').textContent = done ? '✓' : i+1;
    step.querySelector('.step-state').textContent = done ? '✓' : active ? '●' : '—';
  });
  const percent = completed ? 100 : job.phase === 'correlating' ? 94 : job.total ? Math.round(job.completed / job.total * 88) : job.status === 'running' ? 3 : 0;
  $('progressPercent').textContent = percent + '%'; $('progressBar').style.width = percent + '%';
  const progress = {splitting:'Counting tokens & planning chunks',analyzing:'Reading, extracting & saving signals',correlating:`Connecting evidence · ${job.reviews_done || 0} cases reviewed`,complete:'Investigation complete',queued:'Waiting for the analysis worker'};
  $('progressLabel').textContent = job.status === 'uploaded' ? 'File validated. Ready to investigate.' : job.status === 'failed' ? 'Analysis stopped. Completed work is safe.' : job.status === 'interrupted' ? 'Resume to continue from your checkpoint.' : progress[job.phase] || 'Queued';
  $('chunkCount').textContent = `${job.completed} / ${job.total || '—'} chunks processed`;
  $('elapsed').textContent = `${time(job.elapsed)} elapsed`;
  $('chunkGrid').innerHTML = (job.chunks || []).map((chunk, i) => `<span class="chunk-cell ${chunk.status === 'done' ? 'done' : i === job.completed && job.phase === 'analyzing' && job.status === 'running' ? 'active' : ''}" title="Chunk ${chunk.idx+1} · lines ${chunk.lines[0]}–${chunk.lines.at(-1)} · ${chunk.prompt_tokens || '—'} input tokens"></span>`).join('');
  $('resumeArea').classList.toggle('hidden', !['failed','interrupted'].includes(job.status));
  if (job.error) alertMessage(job.error);
  else if (job.warnings?.length) alertMessage(job.warnings.join(' '));
  else alertMessage('');
  const summary = job.report?.summary;
  $('metricFindings').textContent = summary ? fmt(summary.findings) : '—';
  $('metricHigh').textContent = summary ? fmt((summary.severity_counts.High || 0) + (summary.severity_counts.Critical || 0)) : '—';
  $('metricLinks').textContent = summary ? fmt(summary.cross_chunk_findings) : '—';
  $('metricLines').textContent = completed ? fmt(job.lines) : job.completed && job.chunks ? fmt(Math.max(...job.chunks.filter(c=>c.status==='done').flatMap(c=>c.lines), 0)) : '—';
  $('uniqueNote').textContent = summary ? `${fmt(summary.unique_events)} unique · ${summary.duplicate_lines} duplicates` : 'original evidence retained';
  $('findingCount').textContent = summary ? summary.findings : 0;
  $('exportButton').classList.toggle('disabled', !job.report); $('exportButton').setAttribute('aria-disabled', String(!job.report));
  $('exportButton').href = job.report ? `/api/jobs/${job.id}/report` : '#';
  $('reportCaveat').classList.toggle('hidden', !job.report);
  $('auditSection').classList.toggle('hidden', !job.total);
  $('reportSubtitle').textContent = job.report?.partial ? 'Partial report · more cases are still being reviewed.' : completed ? `${fmt(job.lines)} lines. ${job.total} chunks. One connected report.` : 'One report, built from the whole file.';
  renderFindings(); updateStart();
}

function renderFindings() {
  document.querySelectorAll('.tab').forEach(el => el.classList.toggle('active', el.dataset.filter === filter));
  if (!job?.report) { $('findingsList').innerHTML = emptyFindingsHTML; return; }
  const findings = job.report.findings.filter(f => (filter === 'all' || filter === 'linked' && f.cross_chunk || filter === 'High' && ['High','Critical'].includes(f.severity)) && `${f.entity} ${f.attack_type} ${f.account} ${f.reason}`.toLowerCase().includes(search.toLowerCase()));
  if (!findings.length) { $('findingsList').innerHTML = `<div class="empty-state"><div class="empty-illustration"><span>✓</span></div><h3>${job.report.findings.length ? 'No findings match this view.' : 'No supported security findings.'}</h3><p>${job.report.findings.length ? 'Try another filter or search term.' : 'The model did not identify an evidence-backed attack.<br>This does not guarantee the file is free from attacks.'}</p></div>`; return; }
  $('findingsList').innerHTML = findings.map((f, i) => `<article class="finding"><div class="finding-header"><div class="finding-title"><span class="finding-number">${String(i+1).padStart(2,'0')}</span><div><h3>${escapeHTML(f.attack_type)}</h3><div class="finding-identifiers"><code>${escapeHTML(f.entity)}</code>${f.account ? `<span>Account: ${escapeHTML(f.account)}</span>` : ''}<span>${escapeHTML(f.scope === '*' ? 'Correlated by IP' : f.scope)}</span></div></div></div><span class="severity ${escapeHTML(f.severity)}">${escapeHTML(f.severity.toUpperCase())}</span></div><p class="finding-reason">${escapeHTML(f.reason)}</p><div class="finding-action"><strong>Suggested action</strong> &nbsp; ${escapeHTML(f.suggested_action)}</div><div class="finding-bottom"><button class="evidence-button" data-evidence="${escapeHTML(f.id)}">↗ View evidence · ${f.all_evidence_lines.length} lines</button>${f.cross_chunk ? `<span class="link-badge">⌁ Linked across ${f.chunks.length} chunks</span>` : ''}<span class="event-note">${f.unique_events} unique events${f.duplicate_lines.length ? ` · ${f.duplicate_lines.length} duplicate ignored` : ''} · ${escapeHTML(f.status)}</span></div></article>`).join('');
  $('findingsList').querySelectorAll('[data-evidence]').forEach(button => button.addEventListener('click', () => showEvidence(button.dataset.evidence)));
}

async function showEvidence(id) {
  const finding = job.report.findings.find(f => f.id === id);
  if (!finding) return;
  $('evidenceContent').innerHTML = `<p class="evidence-note">${escapeHTML(finding.attack_type)} · ${escapeHTML(finding.entity)}<br>Original physical line numbers. Duplicate copies refer to their canonical event.<br>Linked lines: ${finding.all_evidence_lines.map(n=>'L'+n).join(', ')}<br>Chunks: ${finding.chunks.join(', ')}</p>` + finding.evidence.map(e => `<div class="source-row"><div class="source-label"><strong>LINE ${e.line}</strong><span>${escapeHTML(e.time || 'Timestamp unavailable')}${e.canonical_line !== e.line ? ` · Duplicate of L${e.canonical_line}` : ''}</span></div><pre>${escapeHTML(e.text)}</pre></div>`).join('');
  $('evidenceDialog').showModal();
}

async function refreshHistory() {
  try {
    const jobs = await api('/api/jobs'); $('historyCount').textContent = jobs.length;
    $('historyList').innerHTML = jobs.length ? jobs.map(j=>`<div class="history-row"><div>${escapeHTML(j.filename)}<small>${new Date(j.created).toLocaleString()} · ${j.lines} lines · ${escapeHTML(j.status)}</small></div><button data-job="${j.id}">Open ↗</button></div>`).join('') : '<p class="evidence-note">Your saved analyses will appear here.</p>';
    $('historyList').querySelectorAll('[data-job]').forEach(button=>button.addEventListener('click',async()=>{
      try { job = await api(`/api/jobs/${button.dataset.job}`); rememberJob(); renderJob(); $('historyPanel').classList.add('hidden'); }
      catch(error){alertMessage(error.message);}
    }));
  } catch {}
}

async function showAudit() {
  $('auditBody').classList.toggle('hidden');
  if ($('auditBody').classList.contains('hidden') || !job) return;
  try {
    const calls = await api(`/api/jobs/${job.id}/audit`);
    const max = calls.length ? Math.max(...calls.map(c=>c.prompt_tokens+c.output_budget)) : 0;
    $('auditBody').innerHTML = `<div class="audit-content"><p>Largest reserved call: <strong>${max} / 2,048 tokens</strong> + 24-token safety margin. Output is reserved before adding log text. ${job.call_failures || 0} failed attempts retained in the audit.</p><p>Peak observed process RAM: ${(job.peak_rss/1048576).toFixed(0)} MiB. Backend + owned model process; operating-system memory is separate.</p><table><thead><tr><th>Stage</th><th>Input</th><th>Reply reserve</th><th>Actual reply</th><th>Time</th><th>Attempt</th><th>Status</th></tr></thead><tbody>${calls.map(c=>`<tr><td>${escapeHTML(c.stage)}</td><td>${c.prompt_tokens}</td><td>${c.output_budget}</td><td>${c.output_tokens ?? '—'}</td><td>${c.duration.toFixed(1)}s</td><td>${c.attempt}</td><td>${escapeHTML(c.status)}</td></tr>`).join('')}</tbody></table></div>`;
  } catch(error) {alertMessage(error.message);}
}

$('fileInput').addEventListener('change',e=>chooseFile(e.target.files[0]));
['dragenter','dragover'].forEach(event=>$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('drag');}));
['dragleave','drop'].forEach(event=>$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('drag');if(event==='drop')chooseFile(e.dataTransfer.files[0]);}));
$('dropzone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('fileInput').click();}});
$('startButton').addEventListener('click',startJob); $('resumeButton').addEventListener('click',startJob);
$('newAnalysis').addEventListener('click',()=>$('fileInput').click());
$('historyNav').addEventListener('click',()=>{$('historyPanel').classList.toggle('hidden');refreshHistory();});
$('closeHistory').addEventListener('click',()=>$('historyPanel').classList.add('hidden'));
$('overviewNav').addEventListener('click',()=>{$('historyPanel').classList.add('hidden');window.scrollTo({top:0,behavior:'smooth'});});
$('closeDialog').addEventListener('click',()=>$('evidenceDialog').close());
$('evidenceDialog').addEventListener('click',e=>{if(e.target===$('evidenceDialog'))$('evidenceDialog').close();});
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;renderFindings();}));
$('search').addEventListener('input',e=>{search=e.target.value;renderFindings();});
$('auditToggle').addEventListener('click',showAudit);

async function boot() {
  await refreshHealth(); await refreshHistory();
  const requested = new URLSearchParams(window.location.search).get('analysis');
  const saved = requested && /^[a-f0-9]{32}$/.test(requested) ? requested : localStorage.getItem('tracewatch-job');
  if (saved) { try {job=await api(`/api/jobs/${saved}`);rememberJob();renderJob();}catch{localStorage.removeItem('tracewatch-job');} }
  setInterval(async()=>{await refreshHealth();if(job && ['running','queued'].includes(job.status))await refreshJob();},2000);
}
boot();
