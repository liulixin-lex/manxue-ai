let guestRecordsPage = 1, guestRecordsPages = 1, guestRecordsBusy = false, guestRecordsRequest = 0;
const guestRecordLabels = {queued:'排队中', running:'检测中', passed:'通过', invalid:'校验未通过', error:'执行异常'};
const guestRecordNames = {pelican:'鹈鹕', candy:'糖果'};
const guestRecordStages = {generation:'生成', validation:'校验', task:'任务', request:'请求'};
const guestRecordDate = value => value ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false}).format(new Date(value*1000)) : '尚未开始';
function guestRecordDuration(start, end) {
  return start ? `${Math.max(0, (end || Date.now()/1000)-start).toFixed(1)} 秒${end ? '' : '（进行中）'}` : '等待执行';
}
function guestRecordBadge(status) {
  const safeStatus = Object.hasOwn(guestRecordLabels, status) ? status : 'neutral';
  return `<span class="badge ${safeStatus}">${escapeHTML(guestRecordLabels[status] || '未知状态')}</span>`;
}
function guestRecordDetails(record) {
  const configuration = record.diagnostics_available
    ? `总预算 ${record.timeout_seconds} 秒 · 输出上限 ${record.max_output_tokens} token · 最多重试 ${record.retry_count} 次`
    : '历史记录未保存协议、预算、错误码和重试明细；已有结果与耗时已保留。';
  return `<details class="guest-record-details"><summary>查看详情</summary><div class="guest-record-detail-body"><p>${escapeHTML(configuration)}</p>${Object.entries(record.tests).map(([name, test]) => `<section aria-label="${escapeHTML(guestRecordNames[name] || name)}诊断"><h3>${escapeHTML(guestRecordNames[name] || name)} · ${escapeHTML(guestRecordLabels[test.status] || test.status)}</h3><p>${escapeHTML(guestRecordDuration(test.started, test.finished))} · ${Number(test.attempts || 0)} 次请求${test.stage ? ` · ${escapeHTML(guestRecordStages[test.stage] || test.stage)}阶段` : ''}</p>${test.error ? `<p class="guest-record-error">${escapeHTML(test.error)}</p>` : ''}${test.error_code ? `<p>错误码 <code>${escapeHTML(test.error_code)}</code></p>` : ''}${test.generation_usage?.input_tokens !== undefined ? `<p>输入 ${Number(test.generation_usage.input_tokens)} / 输出 ${Number(test.generation_usage.output_tokens || 0)} token</p>` : ''}${test.attempts_log?.length ? `<ol class="guest-record-attempts">${test.attempts_log.map(attempt => `<li>第 ${Number(attempt.attempt)} 次 · ${escapeHTML(guestRecordLabels[attempt.status] || attempt.status)} · ${Number(attempt.duration_seconds || 0).toFixed(1)} 秒${attempt.error_code ? ` · <code>${escapeHTML(attempt.error_code)}</code>` : ''}${attempt.retry_skipped ? ' · 剩余预算不足，跳过重试' : ''}</li>`).join('')}</ol>` : ''}</section>`).join('')}</div></details>`;
}
function renderGuestRecords(data) {
  guestRecordsPage = data.page; guestRecordsPages = data.pages;
  $('guest-records-summary').textContent = `共 ${data.total} 条 · 按提交时间倒序 · 北京时间`;
  $('guest-records-page').textContent = `第 ${data.page} / ${data.pages} 页`;
  $('guest-records-empty').hidden = data.items.length > 0;
  $('guest-records-empty').textContent = $('guest-records-status').value === 'all' && $('guest-records-test').value === 'all'
    ? '当前没有访客检测记录。访客提交检测后，可在这里查看执行情况。'
    : '没有符合筛选条件的记录。请选择其他状态或检测项目。';
  $('guest-records-table-wrap').hidden = !data.items.length;
  $('guest-records-list').innerHTML = data.items.map(record => `<tr><td data-label="提交时间 / 任务"><time>${escapeHTML(guestRecordDate(record.submitted))}</time><small class="guest-record-id">${escapeHTML(record.id)}</small></td><td data-label="模型 / 接口"><strong>${escapeHTML(record.model)}</strong><small>${escapeHTML(record.api_masked)} · ${escapeHTML(record.effort)}</small><small>${record.protocol === 'responses' ? 'Responses' : record.protocol === 'chat' ? 'Chat Completions' : '协议未记录'}</small></td><td data-label="检测结果">${guestRecordBadge(record.status)}<div class="guest-record-tests">${Object.entries(record.tests).map(([name, test]) => `<span>${escapeHTML(guestRecordNames[name] || name)}：${escapeHTML(guestRecordLabels[test.status] || test.status)}</span>`).join('')}</div></td><td data-label="执行耗时">${escapeHTML(guestRecordDuration(record.started, record.finished))}<small>${record.started ? `排队 ${Math.max(0,record.started-record.submitted).toFixed(1)} 秒` : '尚未开始'}</small></td><td data-label="诊断详情">${guestRecordDetails(record)}</td></tr>`).join('');
}
function guestRecordsControls() {
  for (const id of ['guest-records-refresh', 'guest-records-status', 'guest-records-test']) $(id).disabled = guestRecordsBusy;
  $('guest-records-prev').disabled = guestRecordsBusy || guestRecordsPage <= 1;
  $('guest-records-next').disabled = guestRecordsBusy || guestRecordsPage >= guestRecordsPages;
  $('guest-records-refresh').textContent = guestRecordsBusy ? '读取中…' : '刷新记录';
}
async function refreshGuestRecords(page = guestRecordsPage) {
  if (!token || guestRecordsBusy) return;
  const request = ++guestRecordsRequest;
  guestRecordsBusy = true; guestRecordsControls();
  $('guest-records').setAttribute('aria-busy', 'true');
  $('guest-records-error').hidden = true;
  $('guest-records-loading').hidden = false;
  try {
    const query = new URLSearchParams({page, status:$('guest-records-status').value, test:$('guest-records-test').value});
    const data = await api(`/api/admin/guest-records?${query}`);
    if (token && request === guestRecordsRequest) renderGuestRecords(data);
  } catch (error) {
    if (token && request === guestRecordsRequest) {
      $('guest-records-error').textContent = `${error.message}。请点击“刷新记录”重试。`;
      $('guest-records-error').hidden = false;
    }
  } finally {
    if (request === guestRecordsRequest) {
      guestRecordsBusy = false; guestRecordsControls();
      $('guest-records-loading').hidden = true;
      $('guest-records').setAttribute('aria-busy', 'false');
    }
  }
}
function clearGuestRecords() {
  guestRecordsRequest++; guestRecordsBusy = false; guestRecordsPage = 1; guestRecordsPages = 1;
  $('guest-records-list').replaceChildren();
  $('guest-records-summary').textContent = '登录后读取记录';
  $('guest-records-page').textContent = '第 1 / 1 页';
  $('guest-records-status').value = 'all'; $('guest-records-test').value = 'all';
  for (const id of ['guest-records-table-wrap', 'guest-records-empty', 'guest-records-error', 'guest-records-loading']) $(id).hidden = true;
  $('guest-records').setAttribute('aria-busy', 'false'); guestRecordsControls();
}
$('guest-records-refresh').addEventListener('click', () => refreshGuestRecords());
$('guest-records-status').addEventListener('change', () => refreshGuestRecords(1));
$('guest-records-test').addEventListener('change', () => refreshGuestRecords(1));
$('guest-records-prev').addEventListener('click', () => refreshGuestRecords(guestRecordsPage-1));
$('guest-records-next').addEventListener('click', () => refreshGuestRecords(guestRecordsPage+1));
