let providers = [], editingProvider = null, pendingProviderSelection = null;
function showReviewSettings(data) {
  $('review-enabled').checked = data.review_enabled !== false;
  $('review-mode').value = data.review_mode || 'self';
  pendingProviderSelection = data.review_provider_id ? String(data.review_provider_id) : '';
  $('review-provider').value = pendingProviderSelection;
  updateReviewMode();
}
function updateReviewMode() {
  const enabled = $('review-enabled').checked;
  const external = $('review-mode').value === 'external';
  $('review-options').hidden = !enabled;
  for (const id of ['review-mode','review-provider','review-effort','review-format','review-timeout']) $(id).disabled = !enabled;
  $('review-enabled-note').textContent = enabled ? '保存后，新检测将进行鹈鹕判定。' : '保存后，新画面标为「仅展示」，不调用审核模型；请求失败仍会提示，糖果题照常判定。';
  $('external-review-fields').hidden = !external;
  $('review-provider').required = enabled && external;
  $('review-mode-note').textContent = external ? '将生成画面的截图交给所选外部模型审核。' : '由本轮生成模型读取截图并按统一标准自评。';
}
function renderProviders() {
  $('add-provider').disabled = busy;
  $('provider-list').innerHTML = providers.length ? providers.map(p => `<article class="provider-row"><div class="provider-info"><h3>${escapeHTML(p.name)}</h3><p>${escapeHTML(p.model)} · ${p.protocol === 'chat' ? 'Chat Completions' : 'Responses'}</p><p>${escapeHTML(p.base_url)} · 密钥 ${escapeHTML(p.api_key_masked)}</p><p class="provider-verification ${p.verification?.status === 'passed' ? 'passed' : ''}">${escapeHTML(p.verification?.message || '尚未验证图片读取')}</p></div><div class="action-buttons"><button type="button" class="button secondary" data-provider-edit="${p.id}" ${busy ? 'disabled' : ''}>编辑</button><button type="button" class="button secondary" data-provider-probe="${p.id}" ${busy ? 'disabled' : ''}>验证读图</button></div></article>`).join('') : '<p class="field-note">尚未配置外部审核提供商。</p>';
}
async function refreshProviders() {
  providers = await api('/api/admin/review-providers');
  const select = $('review-provider');
  const value = pendingProviderSelection ?? select.value;
  select.replaceChildren(new Option('选择提供商', ''));
  providers.forEach(p => select.add(new Option(p.name + ' · ' + p.model, p.id)));
  select.value = value;
  pendingProviderSelection = null;
  renderProviders();
}
function editProvider(id = null) {
  const provider = providers.find(p => p.id === id);
  editingProvider = id;
  $('provider-title').textContent = provider ? '编辑审核提供商' : '新增审核提供商';
  $('provider-name').value = provider?.name || '';
  $('provider-model').value = provider?.model || '';
  $('provider-protocol').value = provider?.protocol || 'chat';
  $('provider-effort').value = provider?.effort || 'omit';
  $('provider-token-field').value = provider?.token_field || 'max_tokens';
  for (const name of ['url', 'key']) {
    const input = $('provider-' + name);
    privacy.clear(input); input.required = !provider;
    input.placeholder = provider ? '留空保留已保存的值' : name === 'url' ? 'https://example.com/v1' : '填写审核提供商的 API Key';
  }
  $('provider-saved-url').textContent = provider ? '已保存：' + provider.base_url : '';
  $('provider-saved-key').textContent = provider ? '已保存：' + provider.api_key_masked : '';
  $('provider-message').textContent = '';
  $('provider-editor').hidden = false;
  $('provider-token-row').hidden = $('provider-protocol').value !== 'chat';
  $('provider-editor').scrollIntoView({block:'start', behavior:'auto'});
  $('provider-name').focus({preventScroll:true});
}
function closeProviderEditor() {
  privacy.clear($('provider-url')); privacy.clear($('provider-key'));
  $('provider-editor').hidden = true; editingProvider = null;
  $('add-provider').focus({preventScroll:true});
}
function clearProviders() {
  closeProviderEditor(); providers = []; pendingProviderSelection = null;
  $('provider-list').replaceChildren(); $('review-provider').replaceChildren(new Option('选择提供商',''));
}
$('review-mode').addEventListener('change', updateReviewMode);
$('review-enabled').addEventListener('change', updateReviewMode);
$('provider-protocol').addEventListener('change', () => $('provider-token-row').hidden = $('provider-protocol').value !== 'chat');
$('add-provider').addEventListener('click', () => editProvider());
$('provider-cancel').addEventListener('click', closeProviderEditor);
$('provider-list').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || busy) return;
  if (button.dataset.providerEdit) return editProvider(Number(button.dataset.providerEdit));
  if (button.dataset.providerProbe) action(async () => {
    const output = $('provider-feedback');
    output.textContent = '正在验证两张随机图片，最多等待 60 秒…'; output.classList.remove('error');
    try {
      const result = await api('/api/admin/review-providers/' + button.dataset.providerProbe + '/probe', {});
      output.textContent = result.message + (result.status === 'passed' ? '。此检查验证图片输入，实际审核仍以截图和证据为准。' : '。请检查接口或模型配置。');
      output.classList.toggle('error', result.status !== 'passed');
      await refreshProviders();
    } catch (error) { output.textContent = error.message; output.classList.add('error'); }
  });
});
$('provider-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !$('provider-form').reportValidity()) return;
  busy = true; $('provider-save').disabled = true; $('provider-cancel').disabled = true; $('admin-save').disabled = true;
  renderNodes(); renderProviders();
  const values = {name:$('provider-name').value.trim(), model:$('provider-model').value.trim(), base_url:privacy.read($('provider-url')), api_key:privacy.read($('provider-key')), protocol:$('provider-protocol').value, effort:$('provider-effort').value, token_field:$('provider-token-field').value};
  try {
    const result = await api('/api/admin/review-providers' + (editingProvider === null ? '' : '/' + editingProvider), values);
    closeProviderEditor();
    await refreshProviders();
    if (!$('review-provider').value) $('review-provider').value = String(result.id);
    $('provider-feedback').textContent = '提供商已保存。选择外部模型审核后，保存系统配置即可启用。';
    $('provider-feedback').classList.remove('error');
    $('review-title').scrollIntoView({block:'center'});
  } catch (error) { $('provider-message').textContent = error.message; $('provider-message').classList.add('error'); }
  finally {
    values.api_key = ''; values.base_url = ''; busy = false;
    $('provider-save').disabled = false; $('provider-cancel').disabled = false; $('admin-save').disabled = false;
    renderNodes(); renderProviders();
  }
});
function showPromotionSettings(data) {
  $('promotion-enabled').checked = Boolean(data.promotion_enabled);
  for (const key of ['title', 'description', 'url', 'action']) $('promotion-' + key + '-input').value = data['promotion_' + key] || '';
  updatePromotionPreview();
}
function promotionValues() {
  const result = {promotion_enabled:$('promotion-enabled').checked};
  for (const key of ['title', 'description', 'url', 'action']) result['promotion_' + key] = $('promotion-' + key + '-input').value.trim();
  return result;
}
function updatePromotionPreview() {
  const values = promotionValues();
  for (const key of ['title', 'action', 'url']) $('promotion-' + key + '-input').required = values.promotion_enabled;
  $('promotion-preview-title').textContent = values.promotion_title || 'GGUUAI API';
  $('promotion-preview-description').textContent = values.promotion_description;
  $('promotion-preview-action').textContent = values.promotion_action || '访问中转站';
}
for (const key of ['title', 'description', 'url', 'action']) $('promotion-' + key + '-input').addEventListener('input', updatePromotionPreview);
$('promotion-enabled').addEventListener('change', updatePromotionPreview);
