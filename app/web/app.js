const $ = (id) => document.getElementById(id);
const labels = {passed: '鹈鹕通过', invalid: '鹈鹕未通过', error: '执行异常', uncertain: '待复核', queued: '排队中', running: '检测中', legacy: '历史单项'};
const candyLabels = {passed:'成功',invalid:'降智',error:'请求失败',running:'检测中',queued:'排队中',not_run:'未检测'};
const candyBadge = status => `<span class="badge candy-status ${escapeHTML(status)}">${candyLabels[status] || '未检测糖果'}</span>`;
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = (seconds, full = false) => seconds ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', ...(full ? {year:'numeric'} : {}), month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(seconds * 1000)) : '—';
let state, records = [], galleryPage = 1, galleryPages = 1, galleryTotal = 0, polling = false, guestBusy = false, clockOffset = 0, detailSequence = 0;

// Motion is limited to user-triggered transitions; polling never replays it.
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
  if (!['candy','pelican','guest'].includes(view)) view='candy';
  stopMotion();
  document.querySelectorAll('[data-view]').forEach(tab=>{
    const selected=tab.dataset.view===view;
    tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected ? 0 : -1;
    $('view-'+tab.dataset.view).hidden=!selected;
  });
  if (updateURL) history.replaceState(null,'','#'+view);
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

function badge(status) { return `<span class="badge ${escapeHTML(status)}">${escapeHTML(labels[status] || '尚未检测')}</span>`; }

function qualityTag(tests = {}, corner = true) {
  const review = tests.pelican?.review;
  // Candy answers and unconfirmed visual opinions cannot veto the picture.
  if (review?.status !== 'invalid' || (review.version || 0) < 4 || !review.confirmed_failures?.length) return '';
  const reason = escapeHTML(review.reason || '核心画面与要求不符，查看审核证据');
  return `<span class="${corner ? 'quality-corner' : 'quality-flag'}" title="${reason}" aria-label="AI 复核：主体不符。${reason}"><span>AI 复核：主体不符</span></span>`;
}

function reviewDetail(tests = {}) {
  const review = tests.pelican?.review;
  if (!review) return tests.pelican ? '<p class="field-note">本记录尚无视觉审核结果。</p>' : '';
  const names = {pelican:'鹈鹕形态',bicycle:'自行车主体',riding:'骑乘构图',motion:'骑行动作',scene:'场景',nonce_visible:'可见校验码',loop:'循环连续性'};
  const v4 = review.version >= 4;
  const core = ['pelican','bicycle','riding'];
  const checks = Object.entries(review.checks || {}).filter(([key]) => !v4 || core.includes(key)).map(([key,value]) => `${names[key] || key}：${value === true ? '可辨识' : value === false && review.confirmed_failures?.includes(key) ? '两次复核不符' : '待复核'}`);
  const details = v4 ? ['motion','scene','nonce_visible','loop'].filter(key => key in (review.checks || {})).map(key => `${names[key]}：${review.checks[key] === true ? '已观察到' : review.checks[key] === false ? '有改善建议' : '证据不足'}`) : [];
  const confirmation = {confirmed:'两次请求均指出同一主体问题；仍属于模型意见，可查看截图核对。',disagreement:'两次主体判断有分歧，保留为待复核。',unavailable:'未完成第二次主体复核，未判为不合格。',legacy_not_confirmed:'旧规则的主体否定意见尚未按新标准重新审核，保留为待复核。'}[review.confirmation?.status];
  const previous = review.previous_review;
  const history = previous ? `<details><summary>查看原审核记录 · v${escapeHTML(previous.version || '旧版')}</summary><p>按 v4 主体标准重新归类，沿用已有观察结果，未重新请求模型；原结论与证据保留。</p><pre>${escapeHTML(JSON.stringify({status:previous.status,reason:previous.reason,checks:previous.checks,evidence:previous.evidence},null,2))}</pre></details>` : '';
  return `<div class="review-detail"><strong>视觉审核${v4 ? ' · 主体识别 v4' : ''}${review.judge_mode === 'independent' ? ' · 独立裁判' : review.judge_mode === 'self' ? ' · 同模型自评' : ''}</strong><p>${escapeHTML(review.reason || (review.status === 'passed' ? '主体要求通过' : review.status === 'running' ? '截图已保存，正在审核' : '尚无最终结论'))}</p><p>${escapeHTML(checks.join(' · '))}</p>${details.length ? `<p>细节观察（不否决主体）：${escapeHTML(details.join(' · '))}</p>` : ''}${confirmation ? `<p>${escapeHTML(confirmation)}</p>` : ''}${history}${review.stage ? `<p>失败阶段：${escapeHTML(review.stage)} · ${escapeHTML(review.error_code || '')}</p>` : ''}</div>`;
}

function testSummary(tests = {}, internal = true) {
  const names = {pelican:'鹈鹕',candy:'糖果'};
  const statuses = {passed:'通过',invalid:'未通过',error:'执行异常',uncertain:'证据不足',queued:'排队中',running:'检测中',not_run:'未检测'};
  const review = tests.pelican?.review;
  const reviewLabels = {passed:review?.version >= 4 ? '主体通过' : '通过',invalid:'主体不符',error:'执行异常',uncertain:'待复核'};
  return '<div class="test-results">' + Object.entries(tests).map(([name, result]) => `<span class="test-result ${escapeHTML(result.status)}">${names[name] || name} · ${name === 'pelican' && !internal && result.status === 'passed' ? '基础校验通过 · 未视觉审核' : statuses[result.status] || '未检测'}${result.attempts > 1 ? ` · 尝试 ${result.attempts} 次` : ''}</span>`).join('') + (internal && tests.pelican ? `<span class="test-result ${escapeHTML(review?.status || '')}">视觉审核 · ${reviewLabels[review?.status] || (tests.pelican.status === 'running' ? '待完成' : '未审核')}</span>` : '') + '</div>';
}
function candyDetail(result, prompt, open = false) {
  if (!result) return '';
  const rule = result.scoring_version >= 3 ? '规则 v3：最后一行的明确最终答案为 21；不按推理中出现的数字判分。' : '历史规则：回答中出现独立数字 21 即通过。';
  return `<details class="candy-detail" ${open ? 'open' : ''}><summary>糖果题回答与判分</summary><p class="field-note">${rule}</p><pre>${escapeHTML(result.output || result.error || (result.status === 'not_run' ? '旧记录未进行糖果测试' : '等待回答'))}</pre>${prompt ? `<details><summary>本轮糖果题原文</summary><pre>${escapeHTML(prompt)}</pre></details>` : ''}</details>`;
}

function renderState() {
  const s = state.stats, config = state.settings;
  $('rate').innerHTML = `${s.completed ? (s.passed / s.completed * 100).toFixed(1) : '—'}<small>%</small>`;
  $('rate-note').textContent = s.completed ? `${s.passed} / ${s.completed} 次糖果题通过 · 规则 v3` : s.legacy ? `${s.legacy} 轮旧版结果不计入 v3 通过率` : '等待第一份检测结果';
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
  $('footer-frequency').textContent = `每 15 秒同步记录 · 后台每 ${config.interval_minutes} 分钟检测`;
  $('run').disabled = guestBusy || !config.guest_enabled;
  if (!guestBusy) $('run').innerHTML = config.guest_enabled ? '<span aria-hidden="true">▷</span> 开始检测' : '访客检测暂未开放';
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

function candyIcon(status) {
  const paths = {passed:'M5 12l4 4L19 6',invalid:'M12 6v7m0 4h.01',error:'M6 6l12 12M18 6L6 18',running:'M12 5v7l4 2',queued:'M12 5v7l4 2'};
  return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[status] || 'M7 12h10'}"/></svg>`;
}

function candySquare(status, label, onClick) {
  const button = document.createElement('button');
  const safeStatus = Object.hasOwn(candyLabels,status) ? status : 'not_run';
  button.type = 'button'; button.className = `candy-square ${safeStatus}`;
  button.title = label; button.setAttribute('aria-label',label+'，查看糖果题详情');
  button.innerHTML = candyIcon(safeStatus);
  button.addEventListener('click',onClick);
  return button;
}

function renderCandyGrid() {
  const runs = candyRuns();
  $('candy-grid-summary').textContent = `${runs.length} 次糖果题 · 每格一次 · 规则 v${state.scoring_version}`;
  $('candy-empty').hidden = runs.length > 0;
  const signature = JSON.stringify(runs.map(r => [r.id,r.candy_status,r.tests?.candy?.finished]));
  if ($('candy-grid').dataset.signature === signature) return;
  $('candy-grid').dataset.signature = signature;
  const fragment = document.createDocumentFragment();
  for (const r of runs) {
    const button = candySquare(r.candy_status,`${date(r.started)} · 糖果题 #${r.id} · ${candyLabels[r.candy_status] || '未检测'}`,() => showDetail(r.id,'candy'));
    button.dataset.run = r.id;
    const inspect=()=>{ $('candy-hover').textContent=button.title; };
    button.addEventListener('mouseenter',inspect);button.addEventListener('focus',inspect);
    fragment.append(button);
  }
  if (!runs.length) for (let i=0;i<24;i++) {
    const placeholder=document.createElement('span');placeholder.className='candy-square empty';placeholder.setAttribute('aria-hidden','true');fragment.append(placeholder);
  }
  $('candy-grid').replaceChildren(fragment);
}

function testDuration(run, kind) {
  const test=run.tests?.[kind];
  return test?.finished ? `${Math.max(0,test.finished-(test.started || run.started)).toFixed(1)} 秒` : ['running','queued'].includes(test?.status) ? '检测中' : '未记录耗时';
}

function imageURL(id) { return `/api/runs/${id}/svg`; }

async function attachImage(img, id) {
  try { img.addEventListener('error',()=>{img.alt='动画暂时无法加载，请刷新重试';},{once:true}); img.src = await imageURL(id); }
  catch { img.alt = '动画加载失败，请刷新重试'; }
}

function renderGallery() {
  const visible = records;
  $('empty-state').hidden = galleryTotal > 0 || $('filter').value !== 'all';
  $('filter-empty').hidden = galleryTotal > 0 || $('filter').value === 'all';
  $('gallery-page').textContent = `第 ${galleryPage} / ${galleryPages} 页 · 共 ${galleryTotal} 条鹈鹕记录`;
  $('previous-page').disabled = polling || galleryPage <= 1;
  $('next-page').disabled = polling || galleryPage >= galleryPages;
  const signature = JSON.stringify(visible.map(r => [r.id,r.has_svg,r.tests?.pelican]));
  if ($('gallery').dataset.signature === signature) return;
  $('gallery').dataset.signature = signature;
  const focusedRun = $('gallery').contains(document.activeElement) ? document.activeElement.dataset.run : null;
  $('gallery').innerHTML = visible.map(r => {
    const pelican=r.tests?.pelican || {status:'not_run'};
    const status=pelican.status;
    return `<article class="run-card"><button class="preview" data-run="${r.id}" aria-label="${escapeHTML(date(r.started)+' '+r.scene)}，查看鹈鹕详情">${r.has_svg ? `<img data-image="${r.id}" alt="${escapeHTML(r.scene)} · 模型生成的鹈鹕骑行" loading="lazy">` : `<span class="no-preview ${escapeHTML(status)}">${status === 'running' ? '模型正在创作' : '本次未生成可展示动画'}</span>`}<span class="overlay">查看鹈鹕 ↗</span></button><div class="card-body"><div class="card-top"><time>${date(r.started)}</time>${badge(status)}</div><h3>${escapeHTML(r.scene)}</h3><p class="card-model">${escapeHTML(r.model)} / ${escapeHTML(r.effort)}</p><p>${escapeHTML(r.node_name || '历史节点')} · ${testDuration(r,'pelican')}</p><p class="review-caption">视觉审核：${escapeHTML(({passed:'主体通过',invalid:'主体不符',uncertain:'待复核',error:'审核异常',running:'审核中'})[pelican.review?.status] || (status === 'running' ? '等待生成' : '未审核'))}</p></div></article>`;
  }).join('');
  $('gallery').querySelectorAll('[data-run]').forEach(button => button.addEventListener('click', () => showDetail(Number(button.dataset.run),'pelican')));
  $('gallery').querySelectorAll('.preview').forEach((button, i) => button.insertAdjacentHTML('beforeend', qualityTag(visible[i].tests)));
  $('gallery').querySelectorAll('[data-image]').forEach(img => attachImage(img,Number(img.dataset.image)));
  if (focusedRun) $('gallery').querySelector('[data-run="'+focusedRun+'"]')?.focus({preventScroll:true});
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
      $('detail-body').innerHTML=`<div class="detail-meta">${candyBadge(candy.status)}<span>节点：${escapeHTML(r.node_name || '历史节点')}</span><span>${escapeHTML(r.model)} · ${escapeHTML(r.effort)}</span><span>${testDuration(r,'candy')}</span><span>糖果题 #${r.id}</span></div>${candy.error ? `<p class="detail-error">${escapeHTML(candy.error)}</p>` : ''}${candyDetail(candy,r.candy_prompt,true)}<details><summary>糖果题请求信息与用量</summary><pre>${escapeHTML(JSON.stringify({api:r.base_url,requested_model:r.model,returned_model:candy.returned_model,protocol:r.protocol,usage:candy.usage,attempts:candy.attempts_log},null,2))}</pre></details>`;
      return;
    }
    const pelican=r.tests?.pelican || {status:'not_run'};
    $('detail-title').textContent = `鹈鹕 · ${r.scene} · ${date(r.started)}`;
    $('detail-body').innerHTML = `${r.has_svg ? '<img class="detail-image" id="detail-image" alt="模型生成的鹈鹕骑行 SVG 动画">' : ''}<div class="detail-meta">${badge(pelican.status)}<span>节点：${escapeHTML(r.node_name || '历史节点')}</span><span>${escapeHTML(r.model)} / ${escapeHTML(r.effort)}</span><span>${r.protocol === 'responses' ? 'Responses' : 'Chat Completions'}</span><span>${testDuration(r,'pelican')}</span><span>${r.source === 'manual' ? '手动检测' : '定时检测'} #${r.id}</span></div><div class="check-grid">${[['svg','SVG 格式'],['animation','动画声明'],['nonce','校验码']].map(([key,label]) => `<span class="${r.checks[key] ? '' : 'fail'}">${r.checks[key] ? '✓' : '—'} ${label}</span>`).join('')}<span>本轮校验码：${escapeHTML(r.nonce)}</span></div>${pelican.error ? `<p class="detail-error">${escapeHTML(pelican.error)}</p>` : ''}${testSummary({pelican})}<p class="field-note">视觉审核是模型判断，可能存在误判；质量标签不代表模型身份。</p><details><summary>请求信息与用量</summary><pre>${escapeHTML(JSON.stringify({api:r.base_url,requested_model:r.model,returned_model:pelican.returned_model,effort:r.effort,protocol:r.protocol,usage:pelican.usage},null,2))}</pre></details><details><summary>本轮完整提示词</summary><pre>${escapeHTML(r.prompt)}</pre></details><details><summary>原始输出</summary><pre>${escapeHTML(pelican.output || r.output || '尚无输出')}</pre></details>${r.has_svg ? '<button class="button secondary" id="download-svg">下载 SVG 动画 ↓</button>' : ''}`;
    if (r.has_svg) {
      const frame = document.createElement('div');
      frame.className = 'review-image';
      $('detail-image').replaceWith(frame);
      frame.innerHTML = '<img class="detail-image" id="detail-image" alt="模型生成的鹈鹕骑行 SVG 动画">' + qualityTag(r.tests);
      attachImage($('detail-image'), id);
      $('download-svg').addEventListener('click', async () => {
        try { const link = document.createElement('a'); link.href = await imageURL(id); link.download = `pelican-${id}-${r.nonce}.svg`; link.click(); }
        catch (error) { message(error.message, true); }
      });
    }
    if (r.tests?.pelican?.review?.render?.frames) {
      const render = r.tests.pelican.review.render;
      const panel = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = '审核截图证据 · 点击查看时间与判分依据';
      panel.append(summary);
      const grid = document.createElement('div'); grid.className = 'evidence-grid';
      for (const frame of render.frames) {
        const figure = document.createElement('figure');
        const img = document.createElement('img'); img.loading = 'lazy';
        img.alt = `证据帧 ${frame.index}，${Number(frame.time).toFixed(3)} 秒`;
        img.src = `/api/runs/${r.id}/frames/${frame.index}`;
        img.addEventListener('error', () => { img.alt = '截图证据不可用或已过期'; });
        const caption = document.createElement('figcaption'); caption.textContent = img.alt;
        figure.append(img, caption); grid.append(figure);
      }
      panel.append(grid);
      const explanation = document.createElement('pre');
      explanation.textContent = JSON.stringify({evidence:r.tests.pelican.review.evidence,
        renderer:render.renderer_version, browser:render.browser_version,
        sampling_limited:render.sampling_limited}, null, 2);
      panel.append(explanation); $('detail-body').append(panel);
    }
    $('detail-body').insertAdjacentHTML('beforeend', (r.has_svg ? '' : qualityTag(r.tests, false)) + reviewDetail(r.tests));
  } catch (error) {
    if (sequence === detailSequence) { $('detail-title').textContent = '无法读取记录'; $('detail-body').textContent = error.message; }
  }
}

async function refresh(page = galleryPage) {
  if (polling) return;
  polling = true;
  $('refresh').disabled = true; $('previous-page').disabled = true; $('next-page').disabled = true; $('filter').disabled = true;
  try {
    const [newState, fresh, guests] = await Promise.all([api('/api/state'), api(`/api/runs?test=pelican&page=${page}&status=${encodeURIComponent($('filter').value)}`), api('/api/guest/results')]);
    renderGuestResults(guests);
    state = newState;
    clockOffset = state.server_time - Date.now()/1000;
    galleryPage = fresh.page; galleryPages = fresh.pages; galleryTotal = fresh.total;
    records = fresh.items;
    renderState(); renderGallery();
    const v = state.visual_stats;
    if (v) $('visual-stats').textContent = `当前节点最近 24 小时：${v.total} 次检测，${v.passed} 次主体通过，${v.invalid} 次主体不符，${v.uncertain} 次待复核，${v.errors} 次执行异常。画廊展示全部节点记录。`;
    $('gallery-loading').hidden=true;
    $('sync-error').hidden=true;
    $('connection').textContent = state.candy_running ? '糖果检测进行中' : state.settings.enabled ? '自动检测已启用' : '自动检测已暂停';
    $('connection').className = 'connection online';
  } catch (error) {
    $('connection').textContent = error.message === '请输入管理口令' ? '等待解锁' : '服务连接失败';
    $('connection').className = 'connection offline';
    $('sync-error').textContent=state ? '本次同步失败，正在显示上次成功读取的记录。可点击刷新重试。' : '暂时无法读取检测记录，请稍后刷新重试。';
    $('sync-error').hidden=false;
    $('gallery-loading').hidden=true;
  } finally { polling = false; $('refresh').disabled = false; $('filter').disabled = false; if(state) renderGallery(); }
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
    card.querySelector('img').src=url;card.querySelector('a').href=url;
    card.querySelector('.guest-preview').addEventListener('click',() => {
      detailSequence++;
      $('detail-title').textContent=`访客鹈鹕 · ${result.scene}`;
      $('detail-body').innerHTML=`<div class="review-image"><img class="detail-image" alt="访客测试生成的鹈鹕动画"></div>${testSummary({pelican},false)}<p class="field-note">API：${escapeHTML(result.api_masked || '未记录')}</p>`;
      $('detail-body').querySelector('img').src=url;
      if (!$('detail-dialog').open) { $('detail-dialog').showModal(); reveal($('detail-dialog')); }
    });
  }
  $('guest-results').append(card);
}

$('refresh').addEventListener('click', () => refresh());
$('filter').addEventListener('change', async () => { await refresh(1); reveal($('gallery')); });
$('previous-page').addEventListener('click', async () => { await refresh(galleryPage - 1); reveal($('gallery')); });
$('next-page').addEventListener('click', async () => { await refresh(galleryPage + 1); reveal($('gallery')); });
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
