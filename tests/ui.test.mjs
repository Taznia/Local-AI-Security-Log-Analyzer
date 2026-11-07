import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM, VirtualConsole } from 'jsdom';

const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
const app = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const waves = await readFile(new URL('../web/vendor/silk-waves.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fakeFinding(id, severity, linked, text) {
  return {id, severity, cross_chunk: linked, attack_type: 'Test security finding', entity: '192.0.2.3',
    scope: 'test.example', account: '', reason: text, suggested_action: 'Review the original evidence.',
    all_evidence_lines: [1], evidence: [{line:1, canonical_line:1, text, time:'2026-01-01T00:00:00Z'}],
    chunks: linked ? [1,2] : [1], unique_events:1, duplicate_lines:[], status:'Suspicious activity / attempt'};
}

test('redesigned report preserves filtering, safe evidence, export, history, and active-analysis events', async () => {
  const errors = [];
  const console = new VirtualConsole(); console.on('jsdomError', e => errors.push(e));
  const id = 'a'.repeat(32);
  const payload = '<img src=x onerror="window.attacked=true">';
  const findings = [fakeFinding('high', 'High', true, payload), fakeFinding('medium', 'Medium', false, 'A second signal')];
  const job = {id, filename:'fixture.log', lines:2, bytes:100, completed:2, total:2,
    status:'completed', phase:'complete', elapsed:10, peak_rss:1000, reviews_done:2,
    call_failures:0, warnings:[], error:null, created:'2026-01-01T00:00:00Z',
    chunks:[{idx:0,lines:[1],status:'done',prompt_tokens:100},{idx:1,lines:[2],status:'done',prompt_tokens:100}],
    report:{partial:false,findings,summary:{findings:2,severity_counts:{High:1,Medium:1},cross_chunk_findings:1,unique_events:2,duplicate_lines:0}}};
  const dom = new JSDOM(html, {url:`http://127.0.0.1:8000/?analysis=${id}`, runScripts:'outside-only', pretendToBeVisual:true, virtualConsole:console});
  const w = dom.window;
  w.matchMedia = () => ({matches:false,addEventListener(){},removeEventListener(){}});
  w.scrollTo = () => {};
  w.setInterval = () => 1;
  w.HTMLDialogElement.prototype.showModal = function(){ this.setAttribute('open',''); };
  w.HTMLDialogElement.prototype.close = function(){ this.removeAttribute('open'); };
  w.fetch = async path => ({ok:true,json:async()=> {
    if(path==='/api/health') return {status:'ready',rss_bytes:1000};
    if(path==='/api/jobs') return [job];
    if(path===`/api/jobs/${id}`) return job;
    if(path.endsWith('/audit')) return [];
    throw new Error('Unexpected request '+path);
  }});
  try {
    w.eval(app);
    for(let i=0;i<5;i++) await tick();
    assert.equal(w.document.querySelectorAll('.finding').length,2);
    assert.equal(w.document.getElementById('metricFindings').textContent,'2');
    assert.equal(w.document.querySelector('.finding-reason img'),null);
    assert.ok(w.document.querySelector('.finding-reason').textContent.includes(payload));
    assert.equal(w.attacked,undefined);
    assert.ok(w.document.getElementById('exportButton').href.endsWith(`/api/jobs/${id}/report`));
    w.document.querySelector('[data-filter="High"]').click();
    assert.equal(w.document.querySelectorAll('.finding').length,1);
    assert.equal(w.document.querySelector('[data-filter="High"]').getAttribute('aria-selected'),'true');
    w.document.querySelector('[data-evidence]').click();
    assert.ok(w.document.getElementById('evidenceDialog').hasAttribute('open'));
    assert.equal(w.document.querySelector('#evidenceContent img'),null);
    assert.ok(w.document.querySelector('#evidenceContent pre').textContent.includes(payload));
    w.document.getElementById('closeDialog').click();
    assert.ok(!w.document.getElementById('evidenceDialog').hasAttribute('open'));
    w.document.querySelector('[data-filter="all"]').click();
    const input = w.document.getElementById('search'); input.value='second'; input.dispatchEvent(new w.Event('input'));
    assert.equal(w.document.querySelectorAll('.finding').length,1);
    assert.ok(w.document.querySelector('.finding-reason').textContent.includes('second'));
    w.document.getElementById('historyNav').click();
    assert.ok(!w.document.getElementById('historyPanel').classList.contains('hidden'));
    assert.equal(w.document.getElementById('historyNav').getAttribute('aria-expanded'),'true');
    w.document.getElementById('closeHistory').click();
    assert.ok(w.document.getElementById('historyPanel').classList.contains('hidden'));
    assert.ok(w.document.getElementById('overviewNav').classList.contains('active'));
    assert.equal(w.document.documentElement.dataset.analysisActive,'false');
    let runningEvent = null;
    w.addEventListener('tracewatch:analysis-state', event => {runningEvent=event.detail.running;});
    job.status='running';job.phase='analyzing';
    w.document.getElementById('historyNav').click();await tick();
    w.document.querySelector('[data-job]').click();await tick();
    assert.equal(w.document.documentElement.dataset.analysisActive,'true');
    assert.equal(runningEvent,true,'Opening an active investigation must pause the decorative background');
    assert.deepEqual(errors,[]);
  } finally { w.close(); }
});

test('real bundled Silk Waves registers, caps the canvas, and pauses for analysis, viewport and reduced motion', async () => {
  const errors = [];
  const console = new VirtualConsole(); console.on('jsdomError', e=>errors.push(e));
  const dom = new JSDOM('<!doctype html><bb-silk-waves silk-color="#afc6de" bg-colors="#ffffff"></bb-silk-waves>', {url:'http://127.0.0.1:8000/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w = dom.window, frames = new Map(), observers = [];
  let frameId = 0, draws = 0, viewportWidth = 1600;
  const media = new w.EventTarget(); media.matches = false;
  w.matchMedia = ()=>media;
  w.requestAnimationFrame = cb => {frames.set(++frameId,cb);return frameId;};
  w.cancelAnimationFrame = id => frames.delete(id);
  w.IntersectionObserver = class {constructor(cb){this.cb=cb;observers.push(this);} observe(){} disconnect(){this.disconnected=true;}};
  w.HTMLElement.prototype.getBoundingClientRect = () => ({width:viewportWidth,height:420,top:0,left:0,bottom:420,right:viewportWidth,x:0,y:0});
  w.HTMLCanvasElement.prototype.getContext = function(){
    return {fillStyle:'#afc6de',globalAlpha:1,scale(){},clearRect(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){draws++;},save(){},restore(){},createLinearGradient(){return {addColorStop(){}};}};
  };
  try {
    w.eval(waves);
    const el = w.document.querySelector('bb-silk-waves');
    await el.updateComplete; await tick();
    assert.ok(w.customElements.get('bb-silk-waves'),'The upstream custom element must actually be bundled');
    assert.ok(el.shadowRoot.querySelector('canvas'));
    assert.ok(el.canvas.width<=1280);
    assert.ok(draws>0,'A static background is drawn even before animation');
    assert.equal(frames.size,1);
    const painted=draws;
    let [id,cb]=frames.entries().next().value;frames.delete(id);cb(w.performance.now()+20);
    assert.equal(draws,painted,'Frames inside the 12 FPS interval must be skipped');
    [id,cb]=frames.entries().next().value;frames.delete(id);cb(w.performance.now()+110);
    assert.ok(draws>painted);
    w.dispatchEvent(new w.CustomEvent('tracewatch:analysis-state',{detail:{running:true}}));
    await el.updateComplete; assert.equal(frames.size,0);
    w.dispatchEvent(new w.CustomEvent('tracewatch:analysis-state',{detail:{running:false}}));
    await el.updateComplete; assert.equal(frames.size,1);
    media.matches=true;media.dispatchEvent(new w.Event('change'));assert.equal(frames.size,0);
    const beforeResize=draws;viewportWidth=700;el.resizeCanvas();
    assert.ok(draws>beforeResize,'Reduced-motion backgrounds redraw after resizing');
    media.matches=false;media.dispatchEvent(new w.Event('change'));assert.equal(frames.size,1);
    observers[0].cb([{isIntersecting:false}]);assert.equal(frames.size,0);
    observers[0].cb([{isIntersecting:true}]);assert.equal(frames.size,1);
    el.remove();assert.equal(frames.size,0);assert.ok(observers[0].disconnected);
    assert.deepEqual(errors,[]);
  } finally { w.close(); }
});
