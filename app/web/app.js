const $ = (id) => document.getElementById(id);
const labels = {passed: '鹈鹕通过', invalid: '鹈鹕未通过', error: '执行异常', uncertain: '待复核', queued: '排队中', running: '检测中', legacy: '历史单项'};
const candyLabels = {passed:'成功',invalid:'降智',error:'请求失败',running:'检测中',queued:'排队中',not_run:'未检测'};
const candyBadge = status => `<span class="badge candy-status ${escapeHTML(status)}">${candyLabels[status] || '未检测糖果'}</span>`;
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = (seconds, full = false) => seconds ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', ...(full ? {year:'numeric'} : {}), month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(seconds * 1000)) : '—';
let state, records = [], galleryLimit = 6, galleryTotal = 0, galleryFilter = 'all', polling = false, guestBusy = false, clockOffset = 0, detailSequence = 0;

// Animate navigation, newly revealed cards, and actual new results only.
const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
const motionTargets = new Set();
function reveal(target) {
  if (!window.gsap || motionQuery.matches || !target || target.hidden) return;
  gsap.killTweensOf(target); motionTargets.add(target);
  gsap.fromTo(target,{autoAlpha:0,y:6},{autoAlpha:1,y:0,duration:.2,ease:'power2.out',overwrite:true,clearProps:'opacity,visibility,transform',onComplete:()=>motionTargets.delete(target)});
}
function stopMotion() {
  if (!window.gsap) return;
  for (const target of motionTargets) { gsap.killTweensOf(target); gsap.set(target,{clearProps:'opacity,visibility,transform'}); }
  motionTargets.clear();
}
motionQuery.addEventListener('change',stopMotion);
window.addEventListener('pagehide',stopMotion);
function selectView(view, updateURL = true, animate = true) {
  if (view !== 'guest') view='observatory';
  stopMotion();
  document.querySelectorAll('[data-view]').forEach(tab=>{
    const selected=tab.dataset.view===view;
    tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected ? 0 : -1;
    $('view-'+tab.dataset.view).hidden=!selected;
  });
  if (updateURL) history.replaceState(null,'','#'+view);
  if (view === 'observatory' && state) renderCandyGrid();
  if (animate) reveal($('view-'+view));
}
document.querySelectorAll('[data-view]').forEach((tab,index)=>{
  tab.addEventListener('click',()=>selectView(tab.dataset.view));
  tab.addEventListener('keydown',event=>{
    const tabs=[...document.querySelectorAll('[data-view]')];
    const offsets={ArrowRight:1,ArrowLeft:-1,Home:-index,End:tabs.length-1-index};
    if (!(event.key in offsets)) return;
    event.preventDefault();
    const next=tabs[(index+offsets[event.key]+tabs.length)%tabs.length];
    next.focus();selectView(next.dataset.view);
  });
});
window.addEventListener('hashchange',()=>selectView(location.hash.slice(1),false));
selectView(location.hash.slice(1),false,false);

async function api(path, body) {
  const response = await fetch(path, {signal:AbortSignal.timeout(20000), method:body === undefined ? 'GET' : 'POST', headers:{...(body === undefined ? {} : {'Content-Type':'application/json'})}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if (!response.ok) {
    const data = await response.json().catch(() => ({error:`服务返回异常响应（HTTP ${response.status}），请稍后重试`}));
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return path.endsWith('/svg') ? response.blob() : response.json();
}

function message(text, isError = false) {
  $('form-message').textContent = text;
  $('form-message').classList.toggle('error', isError);
}

function badge(status) { return status === 'displayed' ? '' : `<span class="badge ${escapeHTML(status)}">${escapeHTML(labels[status] || '尚未检测')}</span>`; }

function testSummary(tests = {}, internal = true) {
  const names = {pelican:'鹈鹕',candy:'糖果'};
  const statuses = {passed:'通过',invalid:'未通过',error:'执行异常',uncertain:'证据不足',queued:'排队中',running:'检测中',not_run:'未检测'};
  const results = Object.entries(tests).filter(([, result]) => result.status !== 'displayed');
  return results.length ? '<div class="test-results">' + results.map(([name, result]) => `<span class="test-result ${escapeHTML(result.status)}">${names[name] || name} · ${name === 'pelican' && !internal && result.status === 'passed' ? '基础校验通过' : statuses[result.status] || '未检测'}${result.attempts > 1 ? ` · 尝试 ${result.attempts} 次` : ''}</span>`).join('') + '</div>' : '';
}
function candyDetail(result, prompt, open = false) {
  if (!result) return '';
  const rule = result.scoring_version >= 3 ? '判分依据：最终答案为 21。' : '判分依据：回答中包含答案 21。';
  return `<details class="candy-detail" ${open ? 'open' : ''}><summary>糖果题回答与判分</summary><p class="field-note">${rule}</p><pre>${escapeHTML(result.output || result.error || (result.status === 'not_run' ? '旧记录未进行糖果测试' : '等待回答'))}</pre>${prompt ? `<details><summary>本轮糖果题原文</summary><pre>${escapeHTML(prompt)}</pre></details>` : ''}</details>`;
}

function renderPromotion() {
  const promotion = state?.promotion, link = $('promotion');
  let url;
  try { url = new URL(promotion?.url); } catch { /* Unconfigured promotion stays hidden. */ }
  const visible = Boolean(promotion?.title && promotion?.action && url && ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password);
  link.hidden = !visible;
  document.querySelector('.topbar').classList.toggle('has-promotion', visible);
  if (!visible) { link.removeAttribute('href'); return; }
  link.href = url.href;
  $('promotion-title').textContent = promotion.title;
  $('promotion-description').textContent = promotion.description || '';
  $('promotion-description').hidden = !promotion.description;
  $('promotion-action').textContent = promotion.action;
  link.setAttribute('aria-label', promotion.title + ' · ' + promotion.action + '（新窗口）');
}

function renderState() {
  renderPromotion();
  const s = state.stats, config = state.settings;
  $('rate').innerHTML = `${s.completed ? (s.passed / s.completed * 100).toFixed(1) : '—'}<small>%</small>`;
  $('rate-note').textContent = s.completed ? `${s.passed} / ${s.completed} 次通过` : s.legacy ? `${s.legacy} 条历史记录` : '等待第一份检测结果';
  $('total').innerHTML = `${s.total}<small>次</small>`;
  $('errors').innerHTML = `${s.errors + s.invalid}<small>次</small>`;
  $('error-note').textContent = `请求失败 ${s.errors} · 降智 ${s.invalid}`;
  $('model-title').textContent = config.model;
  $('site-name').textContent = `${config.node_name || '当前节点'} · ${config.base_url}`;
  $('effort-label').textContent = config.effort;
  const latest = candyRuns().at(-1);
  const currentRunning = latest?.candy_status === 'running';
  $('model-status').className = `badge candy-status ${currentRunning ? 'running' : latest?.candy_status || 'neutral'}`;
  $('model-status').textContent = currentRunning ? '糖果检测中' : latest ? candyLabels[latest.candy_status] : '尚未检测';
  $('last-run').textContent = latest ? date(latest.started) : '尚无记录';
  $('next-run').textContent = config.enabled ? date(config.next_run) : '尚未启用';
  $('schedule-note').textContent = config.enabled ? `每 ${config.interval_minutes} 分钟自动检测` : '站点自动检测已暂停';
  $('frequency').textContent = `每 ${config.interval_minutes} 分钟`;
  $('footer-frequency').textContent = `每 ${config.interval_minutes} 分钟检测`;
  $('run').disabled = guestBusy || !config.guest_enabled;
  if (!guestBusy) $('run').innerHTML = config.guest_enabled ? '开始检测' : '访客检测暂未开放';
  renderCandyGrid();
  updateClock();
}

function updateClock() {
  if (!state?.settings.enabled) { $('countdown').textContent = '— : —'; return; }
  const left = Math.max(0, Math.ceil(state.settings.next_run - (Date.now() / 1000 + clockOffset)));
  $('countdown').textContent = left ? `${String(Math.floor(left / 60)).padStart(2,'0')} : ${String(left % 60).padStart(2,'0')}` : state.candy_running ? '糖果检测中' : '即将开始';
}

function candyRuns() {
  return state.timeline.filter(r => r.node_id === state.settings.active_node_id && r.test_version >= state.scoring_version && r.candy_status !== 'not_run').sort((a,b) => a.started-b.started || a.id-b.id);
}

// A fixed three-row window; the neutral final slot is never counted as a result.
function candySquare(status, label, onClick) {
  const button=document.createElement('button');
  const safeStatus=Object.hasOwn(candyLabels,status) ? status : 'not_run';
  button.type='button';button.className=`candy-square ${safeStatus}`;
  button.title=label;button.setAttribute('aria-label',label+'，查看详情');
  button.addEventListener('click',onClick);
  return button;
}
function candyColumns(grid) {
  return Math.max(3,Math.min(32,Math.floor(grid.clientWidth/24) || Number(grid.dataset.columns) || 12));
}
function scrollCandyResults(grid, oldIds, newIds) {
  if (!oldIds.length || oldIds.at(-1)===newIds.at(-1) || !newIds.some(id=>!oldIds.includes(id)) || !window.gsap || motionQuery.matches || document.hidden || !grid.getClientRects().length) return;
  const targets=[...grid.querySelectorAll('[data-run]:not([data-pending])')];
  targets.forEach(target=>{gsap.killTweensOf(target);motionTargets.add(target);});
  gsap.fromTo(targets,{y:24,opacity:.45},{y:0,opacity:1,duration:.32,ease:'power3.out',overwrite:true,clearProps:'transform,opacity',onComplete:()=>targets.forEach(target=>motionTargets.delete(target))});
}
function renderCandyGrid() {
  const grid=$('candy-grid');
  if (!state || !grid.clientWidth) return;
  const runs=candyRuns(),columns=candyColumns(grid),capacity=columns*3;
  const completed=runs.filter(r=>!['running','queued'].includes(r.candy_status)).slice(-(capacity-1));
  const pending=runs.filter(r=>['running','queued'].includes(r.candy_status)).at(-1);
  const pendingLabel=pending ? '检测中' : state.settings.enabled ? '等待下一次检测' : '检测已暂停';
  $('candy-grid-summary').textContent=completed.length ? `最近 ${completed.length} 次` : '';
  $('candy-empty').hidden=runs.length>0;
  const signature=JSON.stringify([columns,state.settings.active_node_id,pending?.id,pendingLabel,completed.map(r=>[r.id,r.candy_status,r.tests?.candy?.finished])]);
  if (grid.dataset.signature===signature) return;
  const oldIds=[...grid.querySelectorAll('[data-run]:not(.running):not(.queued)')].map(el=>el.dataset.run);
  const oldNode=grid.dataset.node;
  const focused=grid.contains(document.activeElement) ? document.activeElement.dataset.run || 'pending' : null;
  const oldButtons=new Map([...grid.querySelectorAll('[data-run]')].map(el=>[el.dataset.run,el]));
  grid.querySelectorAll('.candy-square').forEach(el=>{if(window.gsap) gsap.killTweensOf(el);motionTargets.delete(el);el.style.removeProperty('transform');el.style.removeProperty('opacity');});
  grid.dataset.signature=signature;grid.dataset.columns=columns;grid.dataset.node=state.settings.active_node_id;
  grid.style.setProperty('--columns',columns);
  const fragment=document.createDocumentFragment();
  for(let i=0;i<capacity-completed.length-1;i++) {
    const space=document.createElement('span');space.className='candy-square empty';space.setAttribute('aria-hidden','true');fragment.append(space);
  }
  for(const r of completed) {
    const label=`${date(r.started)} · ${candyLabels[r.candy_status] || '未检测'}`;
    const button=oldButtons.get(String(r.id)) || candySquare(r.candy_status,label,()=>showDetail(r.id,'candy'));
    button.className=`candy-square ${r.candy_status}`;button.dataset.run=r.id;button.title=label;button.setAttribute('aria-label',label+'，查看详情');button.tabIndex=-1;
    if(!button.dataset.inspect) {
      const inspect=()=>{$('candy-hover').textContent=button.title;};
      button.addEventListener('mouseenter',inspect);button.addEventListener('focus',inspect);button.dataset.inspect='true';
    }
    fragment.append(button);
  }
  let tail;
  if(pending) {tail=candySquare('running',pendingLabel,()=>showDetail(pending.id,'candy'));tail.dataset.run=pending.id;tail.tabIndex=-1;}
  else {tail=document.createElement('span');tail.className='candy-square queued';tail.setAttribute('role','img');tail.setAttribute('aria-label',pendingLabel);tail.title=pendingLabel;}
  tail.dataset.pending='true';fragment.append(tail);grid.replaceChildren(fragment);
  const buttons=[...grid.querySelectorAll('button')];
  const focusTarget=buttons.find(el=>el.dataset.run===focused) || buttons.at(-1);
  if(focusTarget) {focusTarget.tabIndex=0;if(focused) focusTarget.focus({preventScroll:true});}
  $('candy-hover').textContent=focused && focusTarget ? focusTarget.title : '';
  if(oldNode===String(state.settings.active_node_id)) scrollCandyResults(grid,oldIds,completed.map(r=>String(r.id)));
}
$('candy-grid').addEventListener('mouseleave',()=>{if(!$('candy-grid').contains(document.activeElement)) $('candy-hover').textContent='';});
$('candy-grid').addEventListener('keydown',event=>{
  const grid=event.currentTarget,buttons=[...grid.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
  const offsets={ArrowRight:3,ArrowLeft:-3,ArrowDown:1,ArrowUp:-1,Home:-index,End:buttons.length-1-index};
  if(index<0 || !(event.key in offsets)) return;
  event.preventDefault();const next=buttons[Math.max(0,Math.min(buttons.length-1,index+offsets[event.key]))];
  buttons.forEach(el=>el.tabIndex=el===next ? 0 : -1);next?.focus();
});
const candyResize=new ResizeObserver(()=>{if(state) renderCandyGrid();});
candyResize.observe($('candy-grid'));

function testDuration(run, kind) {
  const test=run.tests?.[kind];
  return test?.finished ? `${Math.max(0,test.finished-(test.started || run.started)).toFixed(1)} 秒` : ['running','queued'].includes(test?.status) ? '检测中' : '未记录耗时';
}

function imageURL(id) { return `/api/runs/${id}/svg`; }

async function attachImage(img, id) {
  try { img.addEventListener('error',()=>{img.alt='动画暂时无法加载，请刷新重试';},{once:true}); img.src = imageURL(id)+'?display=1'; }
  catch { img.alt = '动画加载失败，请刷新重试'; }
}

function renderGallery(animateNew = false) {
  const visible=records.slice(0,galleryLimit),gallery=$('gallery');
  $('empty-state').hidden=galleryTotal>0 || $('filter').value!=='all';
  $('filter-empty').hidden=galleryTotal>0 || $('filter').value==='all';
  $('gallery-page').textContent=galleryTotal ? `已展示 ${visible.length} / ${galleryTotal} 幅` : '';
  $('show-more').hidden=visible.length>=galleryTotal;
  $('show-more').disabled=polling;
  const existing=new Map([...gallery.children].map(el=>[el.dataset.id,el]));
  const focusedRun=gallery.contains(document.activeElement) ? document.activeElement.dataset.run : null;
  const created=[],desired=[];
  for(const r of visible) {
    const signature=JSON.stringify([r.id,r.has_svg,r.scene,r.model,r.effort,r.tests?.pelican]);
    let card=existing.get(String(r.id));
    if(!card) {card=document.createElement('article');card.className='run-card';card.dataset.id=r.id;created.push(card);}
    if(card.dataset.signature!==signature) {
      card.dataset.signature=signature;
      const pelican=r.tests?.pelican || {status:'not_run'},status=pelican.status;
      card.innerHTML=`<button class="preview" data-run="${r.id}" aria-label="${escapeHTML(date(r.started)+' '+r.scene)}，查看详情">${r.has_svg ? `<img data-image="${r.id}" alt="${escapeHTML(r.scene)} · 鹈鹕骑行" loading="lazy">` : `<span class="no-preview ${escapeHTML(status)}">${status==='running' ? '正在创作' : '暂未生成画面'}</span>`}<span class="overlay">查看作品 ↗</span></button><div class="card-body"><div class="card-top"><time>${date(r.started)}</time>${badge(status)}</div><h3>${escapeHTML(r.scene)}</h3><p class="card-model">${escapeHTML(r.model)} / ${escapeHTML(r.effort)}</p><p>${escapeHTML(r.node_name || '历史节点')} · ${testDuration(r,'pelican')}</p></div>`;
      const button=card.querySelector('[data-run]');button.addEventListener('click',()=>showDetail(r.id,'pelican'));
      if(r.has_svg) attachImage(card.querySelector('[data-image]'),r.id);
    }
    desired.push(card);
  }
  const keep=new Set(desired);
  for(const card of [...gallery.children]) if(!keep.has(card)) {if(window.gsap) gsap.killTweensOf(card);motionTargets.delete(card);card.remove();}
  desired.forEach((card,index)=>{if(gallery.children[index]!==card) gallery.insertBefore(card,gallery.children[index] || null);});
  if(focusedRun && document.activeElement?.dataset.run!==focusedRun) gallery.querySelector('[data-run="'+focusedRun+'"]')?.focus({preventScroll:true});
  if(animateNew && created.length && window.gsap && !motionQuery.matches && !document.hidden) {
    created.forEach(card=>motionTargets.add(card));
    gsap.fromTo(created,{opacity:.3,y:18},{opacity:1,y:0,duration:.36,stagger:.045,ease:'power3.out',clearProps:'opacity,transform',onComplete:()=>created.forEach(card=>motionTargets.delete(card))});
  }
}

async function showDetail(id, kind = 'pelican') {
  const sequence = ++detailSequence;
  $('detail-title').textContent = '读取检测记录…';
  $('detail-body').replaceChildren();
  if (!$('detail-dialog').open) { $('detail-dialog').showModal(); reveal($('detail-dialog')); }
  try {
    const r = await api(`/api/runs/${id}`);
    if (sequence !== detailSequence) return;
    if (kind === 'candy') {
      const candy=r.tests?.candy || {status:'not_run'};
      $('detail-title').textContent=`糖果题 · ${date(r.started)}`;
      $('detail-body').innerHTML=`<div class="detail-meta">${candyBadge(candy.status)}<span>节点：${escapeHTML(r.node_name || '历史节点')}</span><span>${escapeHTML(r.model)} · ${escapeHTML(r.effort)}</span><span>${testDuration(r,'candy')}</span><span>糖果题 #${r.id}</span></div>${candy.error ? `<p class="detail-error">${escapeHTML(candy.error)}</p>` : ''}${candyDetail(candy,r.candy_prompt,true)}`;
      return;
    }
    const pelican=r.tests?.pelican || {status:'not_run'};
    $('detail-title').textContent = `鹈鹕 · ${r.scene} · ${date(r.started)}`;
    $('detail-body').innerHTML = `${r.has_svg ? '<img class="detail-image" id="detail-image" alt="模型生成的鹈鹕骑行 SVG 动画">' : ''}<div class="detail-meta">${badge(pelican.status)}<span>节点：${escapeHTML(r.node_name || '历史节点')}</span><span>${escapeHTML(r.model)} / ${escapeHTML(r.effort)}</span><span>${r.protocol === 'responses' ? 'Responses' : 'Chat Completions'}</span><span>${testDuration(r,'pelican')}</span><span>${r.source === 'manual' ? '手动检测' : '定时检测'} #${r.id}</span></div><div class="check-grid" ${pelican.evaluation_level === 'display' ? 'hidden' : ''}>${[['svg','SVG 格式'],['animation','动画声明'],['nonce','校验码']].map(([key,label]) => `<span class="${r.checks[key] ? '' : 'fail'}">${r.checks[key] ? '✓' : '—'} ${label}</span>`).join('')}<span>本轮校验码：${escapeHTML(r.nonce)}</span></div>${pelican.error ? `<p class="detail-error">${escapeHTML(pelican.error)}</p>` : ''}${testSummary({pelican})}<details><summary>本轮完整提示词</summary><pre>${escapeHTML((r.prompt || '').replace(/提示词版本[：:]\s*\d+[。.]?\s*/g,''))}</pre></details><details><summary>原始输出</summary><pre>${escapeHTML(pelican.output || r.output || '尚无输出')}</pre></details>${r.has_svg ? '<button class="button secondary" id="download-svg">下载 SVG 动画 ↓</button>' : ''}`;
    if (r.has_svg) {
      attachImage($('detail-image'), id);
      $('download-svg').addEventListener('click', async () => {
        try { const link = document.createElement('a'); link.href = await imageURL(id); link.download = `pelican-${id}-${r.nonce}.svg`; link.click(); }
        catch (error) { message(error.message, true); }
      });
    }
  } catch (error) {
    if (sequence === detailSequence) { $('detail-title').textContent = '无法读取记录'; $('detail-body').textContent = error.message; }
  }
}

async function refresh(reset = false, expand = false) {
  if(polling) return false;
  polling=true;
  const filter=$('filter').value;
  const requestedLimit=reset || filter!==galleryFilter ? 6 : galleryLimit+(expand ? 6 : 0);
  $('refresh').disabled=true;$('show-more').disabled=true;$('filter').disabled=true;
  if(expand) $('show-more').textContent='加载中…';
  try {
    const loadPages=async()=>{
      const pages=[];
      const count=Math.ceil(requestedLimit/12);
      for(let start=0;start<count;start+=4) {
        const batch=Array.from({length:Math.min(4,count-start)},(_,i)=>api('/api/runs?test=pelican&page='+(start+i+1)+'&status='+encodeURIComponent(filter)));
        pages.push(...await Promise.all(batch));
      }
      return pages;
    };
    const [newState,pages,guests]=await Promise.all([api('/api/state'),loadPages(),api('/api/guest/results')]);
    renderGuestResults(guests);state=newState;clockOffset=state.server_time-Date.now()/1000;
    galleryLimit=requestedLimit;galleryFilter=filter;galleryTotal=pages[0].total;
    records=[...new Map(pages.flatMap(page=>page.items).map(r=>[r.id,r])).values()];
    renderState();renderGallery(expand);
    const v=state.visual_stats;
    if(v) $('visual-stats').textContent=`当前节点 · 24 小时内 ${v.total} 次${state.settings.review_enabled === false ? '' : `，${v.passed} 次通过${v.invalid ? '，'+v.invalid+' 次未通过' : ''}${v.uncertain ? '，'+v.uncertain+' 次待复核' : ''}`}${v.errors ? '，'+v.errors+' 次异常' : ''}`;
    $('gallery-loading').hidden=true;$('sync-error').hidden=true;
    $('connection').textContent=state.candy_running ? '正在检测' : state.settings.enabled ? '自动检测已启用' : '自动检测已暂停';
    $('connection').className='connection online';
    return true;
  } catch(error) {
    $('connection').textContent=error.message==='请输入管理口令' ? '等待解锁' : '服务连接失败';
    $('connection').className='connection offline';
    $('sync-error').textContent=state ? '同步失败，保留上次记录。请刷新重试。' : '暂时无法读取记录，请刷新重试。';
    $('sync-error').hidden=false;$('gallery-loading').hidden=true;
    return false;
  } finally {polling=false;$('refresh').disabled=false;$('filter').disabled=false;$('show-more').disabled=false;$('show-more').textContent='展示更多';}
}

privacy.bind(document);
let guestSignature = "";
$('guest-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (guestBusy || !$('guest-form').reportValidity()) return;
  const values = {base_url:privacy.read($('base-url')), api_key:privacy.read($('api-key')), model:$('model').value.trim(), effort:$('effort').value, protocol:$('protocol').value, test_type:$('test-type').value,retry_count:Number($('guest-retries').value)};
  if (!values.base_url || !values.api_key) { message('请填写你自己的 API 地址和 API Key。', true); return; }
  guestBusy = true; $('run').disabled = true; $('run').textContent = '正在提交…';
  message('正在提交访客任务…');
  try {
    const result = await api('/api/guest/run', values);
    guestSignature = '';
    await refresh();
    message(`任务 #${result.id} 已提交`);
  } catch (error) { message(error.message, true); }
  finally {
    values.base_url = ''; values.api_key = '';
    guestBusy = false;
    $('run').disabled = state?.settings.guest_enabled === false;
    $('run').textContent = state?.settings.guest_enabled === false ? '访客检测暂未开放' : '开始检测';
  }
});

function renderGuestResults(results) {
  const signature=JSON.stringify(results);
  if (signature === guestSignature) return;
  guestSignature=signature;
  const candies=results.filter(r => r.tests?.candy).sort((a,b) => (a.submitted || a.started)-(b.submitted || b.started));
  const pelicans=results.filter(r => r.tests?.pelican);
  $('guest-candy-grid').replaceChildren(); $('guest-results').replaceChildren();
  $('guest-candy-count').textContent=candies.length;
  $('guest-pelican-count').textContent=pelicans.length;
  $('guest-candy-empty').hidden=candies.length>0;
  $('guest-pelican-empty').hidden=pelicans.length>0;
  for (const r of candies) {
    const candy=r.tests.candy;
    $('guest-candy-grid').append(candySquare(candy.status,`${date(r.submitted || r.started)} · ${r.model} · ${candyLabels[candy.status] || '未检测'}`,() => {
      detailSequence++;
      $('detail-title').textContent=`访客糖果题 · ${date(r.submitted || r.started)}`;
      $('detail-body').innerHTML=`<div class="detail-meta">${candyBadge(candy.status)}<span>${escapeHTML(r.model)} · ${escapeHTML(r.effort)}</span><span>${escapeHTML(r.api_masked || '未记录')}</span></div>${candyDetail(candy,undefined,true)}${candy.error ? `<p class="detail-error">${escapeHTML(candy.error)}</p>` : ''}`;
      if (!$('detail-dialog').open) { $('detail-dialog').showModal(); reveal($('detail-dialog')); }
    }));
  }
  pelicans.forEach(addGuestPelican);
}
function addGuestPelican(result) {
  const pelican=result.tests.pelican;
  const card=document.createElement('article');card.className='guest-result';
  card.innerHTML=`<div class="guest-result-head"><strong>访客鹈鹕</strong>${badge(pelican.status)}</div>${testSummary({pelican},false)}<p>${date(result.submitted || result.started)} · ${escapeHTML(result.model)} · ${escapeHTML(result.effort)}</p><p>API：${escapeHTML(result.api_masked || '未记录')}</p><p>${escapeHTML(result.scene)} · 校验码${result.checks.nonce ? '一致' : ['queued','running'].includes(pelican.status) ? '待检测' : '未通过'}</p>${pelican.error ? `<p class="guest-error">${escapeHTML(pelican.error)}</p>` : ''}${result.has_svg ? '<button class="guest-preview" aria-label="放大访客鹈鹕动画"><img alt="访客测试生成的鹈鹕动画"></button><a class="guest-download" download="guest-pelican.svg">下载 SVG ↓</a>' : ''}`;
  if (result.has_svg) {
    const url=`/api/guest/results/${result.id}/svg`;
    card.querySelector('img').src=url+'?display=1';card.querySelector('a').href=url;
    card.querySelector('.guest-preview').addEventListener('click',() => {
      detailSequence++;
      $('detail-title').textContent=`访客鹈鹕 · ${result.scene}`;
      $('detail-body').innerHTML=`<div class="review-image"><img class="detail-image" alt="访客测试生成的鹈鹕动画"></div>${testSummary({pelican},false)}<p class="field-note">API：${escapeHTML(result.api_masked || '未记录')}</p>`;
      $('detail-body').querySelector('img').src=url+'?display=1';
      if (!$('detail-dialog').open) { $('detail-dialog').showModal(); reveal($('detail-dialog')); }
    });
  }
  $('guest-results').append(card);
}

$('refresh').addEventListener('click', () => refresh());
$('filter').addEventListener('change', async () => { if(await refresh(true)) reveal($('gallery')); });
$('show-more').addEventListener('click',()=>refresh(false,true));
$('close-detail').addEventListener('click', () => { detailSequence++; $('detail-dialog').close(); });
$('detail-dialog').addEventListener('cancel', () => detailSequence++);
setInterval(updateClock, 1000);
setInterval(()=>{if(!document.hidden) refresh();}, 15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden) refresh();else stopMotion();});
refresh();
const linkedRun = new URLSearchParams(location.search).get('run');
if (/^[1-9]\d{0,17}$/.test(linkedRun || '')) {
  const kind=new URLSearchParams(location.search).get('test') === 'candy' ? 'candy' : 'pelican';
  selectView(kind,false,false);showDetail(Number(linkedRun),kind);
}
