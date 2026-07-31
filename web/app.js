let token = localStorage.getItem('surveil_holdings_token') || '';
let holdings = [];
let pendingPayload = null;
let pendingPreviewToken = '';
let loadedHoldings = false;
let holdingsOperationId = 0;
let holdingsBusyMode = '';
// 拖拽排序时记录被拖动行的原始下标，null 表示当前未拖动。
let dragIndex = null;
let managedRelations = [];
let editingRelationId = null;
let sourceProfileCache = {categories: [], profiles: []};
let sourceFilterOptionsLoaded = false;
let marketOperationId = 0;
let marketAbortController = null;
let currentRulesCache = null;
const multiSelectControls = new Map();

function initializeMultiSelect(id, options, onChange) {
  const root = document.getElementById(id);
  if (!root) return;
  if (!multiSelectControls.has(id)) {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'multi-select-trigger';
    trigger.setAttribute('aria-haspopup', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    const label = document.createElement('span');
    trigger.appendChild(label);
    const menu = document.createElement('div');
    menu.className = 'multi-select-menu';
    menu.hidden = true;
    const actions = document.createElement('div');
    actions.className = 'multi-select-actions';
    const selectAll = document.createElement('button');
    selectAll.type = 'button';
    selectAll.textContent = '全选';
    selectAll.addEventListener('click', () => setMultiSelectSelection(id, 'all'));
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = '清空';
    clear.addEventListener('click', () => setMultiSelectSelection(id, 'none'));
    actions.append(selectAll, clear);
    const optionList = document.createElement('div');
    optionList.className = 'multi-select-options';
    menu.append(actions, optionList);
    root.replaceChildren(trigger, menu);
    trigger.addEventListener('click', event => {
      event.stopPropagation();
      const opening = menu.hidden;
      closeMultiSelectMenus(id);
      menu.hidden = !opening;
      root.classList.toggle('open', opening);
      trigger.setAttribute('aria-expanded', String(opening));
    });
    menu.addEventListener('click', event => event.stopPropagation());
    multiSelectControls.set(id, {root, trigger, label, menu, optionList, options: [], onChange});
  }
  const control = multiSelectControls.get(id);
  control.onChange = onChange;
  setMultiSelectOptions(id, options || []);
}

function setMultiSelectOptions(id, options) {
  const control = multiSelectControls.get(id);
  if (!control) return;
  const selected = new Set(selectedMultiSelectValues(id));
  control.options = (options || []).filter(option => option?.value).map(option => ({
    value: String(option.value),
    label: String(option.label || option.value),
    group: String(option.group || '')
  }));
  control.optionList.replaceChildren();
  let currentGroup = null;
  control.options.forEach(option => {
    if (option.group && option.group !== currentGroup) {
      const heading = document.createElement('div');
      heading.className = 'multi-select-group';
      heading.textContent = option.group;
      control.optionList.appendChild(heading);
      currentGroup = option.group;
    }
    const row = document.createElement('label');
    row.className = 'multi-select-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = option.value;
    checkbox.checked = selected.has(option.value);
    checkbox.addEventListener('change', () => {
      updateMultiSelectLabel(id);
      control.onChange?.();
    });
    const text = document.createElement('span');
    text.textContent = option.label;
    row.append(checkbox, text);
    control.optionList.appendChild(row);
  });
  if (!control.options.length) {
    const empty = document.createElement('div');
    empty.className = 'multi-select-empty';
    empty.textContent = '暂无可选项';
    control.optionList.appendChild(empty);
  }
  updateMultiSelectLabel(id);
}

function selectedMultiSelectValues(id) {
  const control = multiSelectControls.get(id);
  if (!control) return [];
  return [...control.optionList.querySelectorAll('input[type="checkbox"]:checked')].map(input => input.value);
}

function availableMultiSelectValues(id) {
  return (multiSelectControls.get(id)?.options || []).map(option => option.value);
}

function setMultiSelectSelection(id, mode) {
  const control = multiSelectControls.get(id);
  if (!control) return;
  control.optionList.querySelectorAll('input[type="checkbox"]').forEach(input => {
    input.checked = mode === 'all';
  });
  updateMultiSelectLabel(id);
  control.onChange?.();
}

function updateMultiSelectLabel(id) {
  const control = multiSelectControls.get(id);
  if (!control) return;
  const selected = selectedMultiSelectValues(id);
  const placeholder = control.root.dataset.placeholder || '全部';
  if (!selected.length) {
    control.label.textContent = placeholder;
  } else if (selected.length === 1) {
    control.label.textContent = control.options.find(option => option.value === selected[0])?.label || selected[0];
  } else {
    control.label.textContent = `已选 ${selected.length} 项`;
  }
  control.trigger.title = selected.length ? selected.map(value => control.options.find(option => option.value === value)?.label || value).join('、') : placeholder;
}

function closeMultiSelectMenus(exceptId='') {
  multiSelectControls.forEach((control, id) => {
    if (id === exceptId) return;
    control.menu.hidden = true;
    control.root.classList.remove('open');
    control.trigger.setAttribute('aria-expanded', 'false');
  });
}

document.addEventListener('click', () => closeMultiSelectMenus());
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeMultiSelectMenus();
});

function headers() {
  const h = {'Content-Type': 'application/json'};
  if (token) h['X-Holdings-Token'] = token;
  return h;
}

async function api(path, options={}) {
  const res = await fetch(path, {...options, headers: {...headers(), ...(options.headers || {})}});
  if (res.status === 401) {
    token = prompt('请输入 HOLDINGS_WEB_TOKEN') || '';
    localStorage.setItem('surveil_holdings_token', token);
    return api(path, options);
  }
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data;
}

function showStatus(text, kind='ok') {
  const el = document.getElementById('status');
  el.className = 'status ' + kind;
  el.textContent = text;
}

function setHoldingsBusy(mode='') {
  holdingsBusyMode = mode;
  const busy = Boolean(mode);
  document.querySelectorAll('#view-holdings button, #view-holdings input, #view-holdings textarea').forEach(control => {
    control.disabled = busy;
  });
  const refreshButton = document.getElementById('holdingsRefreshButton');
  const saveButton = document.getElementById('holdingsSaveButton');
  const confirmButton = document.getElementById('holdingsConfirmButton');
  const cancelButton = document.getElementById('holdingsPreviewCancelButton');
  if (refreshButton) refreshButton.textContent = mode === 'refreshing' ? '刷新中' : '刷新';
  if (saveButton) saveButton.textContent = mode === 'validating' ? '校验中' : '保存';
  if (confirmButton) {
    confirmButton.disabled = mode === 'saving';
    confirmButton.textContent = mode === 'saving' ? '保存中' : '确认保存';
  }
  if (cancelButton) cancelButton.disabled = mode === 'saving';
}

function beginHoldingsOperation(mode) {
  if (holdingsBusyMode) return 0;
  holdingsOperationId += 1;
  setHoldingsBusy(mode);
  return holdingsOperationId;
}

function endHoldingsOperation(operationId) {
  if (operationId !== holdingsOperationId) return;
  setHoldingsBusy('');
}

function splitList(value) {
  return String(value || '').split(/[，,;；\n]+/).map(s => s.trim()).filter(Boolean);
}

function joinList(value) {
  return Array.isArray(value) ? value.join('，') : '';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function badge(value) {
  const raw = String(value || '').trim();
  if (!raw) return '<span class="badge">-</span>';
  const lower = raw.toLowerCase();
  const cls = ['high', 'medium', 'low'].includes(lower) ? lower : '';
  return `<span class="badge ${cls}">${escapeHtml(raw)}</span>`;
}

function safeExternalUrl(value) {
  try {
    const parsed = new URL(String(value || ''));
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
  } catch (_) {
    return '';
  }
}

function feedbackBadge(item) {
  const state = String(item.feedback_state || 'not_applicable');
  const display = String(item.feedback_display || '不适用');
  const cls = ['high_value', 'duplicate', 'invalid', 'mixed'].includes(state) ? state : '';
  const time = item.feedback_received_at ? `<div class="hint">${formatTime(item.feedback_received_at)}</div>` : '';
  return `<span class="feedback-chip ${cls}">${escapeHtml(display)}</span>${time}`;
}

function serviceActionLabel(action) {
  const labels = {
    restart: '重启服务',
    restart_timer: '重启定时器',
    run_once: '立即运行',
    status: '仅查看'
  };
  return labels[action] || action;
}

function serviceActionButtons(unit) {
  const actions = (unit.actions || []).filter(action => action !== 'status');
  if (!actions.length) return '<span class="hint">只读</span>';
  return actions.map(action => `
    <button onclick="runServiceAction('${escapeHtml(unit.Id || '')}', '${escapeHtml(action)}')">${escapeHtml(serviceActionLabel(action))}</button>
  `).join(' ');
}

function renderHealthTasks(tasks, groupLabels) {
  const allTasks = tasks || [];
  const visibleTasks = allTasks;
  const summary = document.getElementById('healthUnitSummary');
  if (summary) {
    summary.textContent = `展示 ${visibleTasks.length} 个逻辑任务`;
  }
  const order = ['fetching_persistent', 'fetching_scheduled', 'processing_scheduled', 'infrastructure', 'other'];
  const byGroup = {};
  visibleTasks.forEach(task => {
    const group = task.group || 'other';
    if (!byGroup[group]) byGroup[group] = [];
    byGroup[group].push(task);
  });
  const rows = [];
  Object.values(byGroup).forEach(groupTasks => groupTasks.sort((left, right) =>
    Number(Boolean(right.health_issue)) - Number(Boolean(left.health_issue))
  ));
  const baseGroupOrder = [...order, ...Object.keys(byGroup).filter(group => !order.includes(group))];
  const orderedGroups = [
    ...baseGroupOrder.filter(group => (byGroup[group] || []).some(task => task.health_issue)),
    ...baseGroupOrder.filter(group => !(byGroup[group] || []).some(task => task.health_issue))
  ];
  orderedGroups.forEach(group => {
    const groupTasks = byGroup[group] || [];
    if (!groupTasks.length) return;
    rows.push(`
      <tr>
        <td colspan="8" style="background:#f8fafc; color:#334e68; font-weight:650">
          ${escapeHtml((groupLabels || {})[group] || group)} <span class="hint">${groupTasks.length} 个任务</span>
        </td>
      </tr>
    `);
    groupTasks.forEach(task => {
      const lifecycle = task.lifecycle_label ? `<div class="hint">${escapeHtml(task.lifecycle_label)}</div>` : '';
      const replacement = task.replacement ? `<div class="hint">替代：${escapeHtml(task.replacement)}</div>` : '';
      const rawLines = [];
      if (task.timer) rawLines.push(`${task.timer.Id}：${task.raw_timer_state || '-'}`);
      if (task.service) rawLines.push(`${task.service.Id}：${task.raw_service_state || '-'}`);
      const rawDetails = rawLines.length ? `
        <details class="hint" style="margin-top:4px">
          <summary>systemd 详情</summary>
          ${rawLines.map(line => `<div>${escapeHtml(line)}</div>`).join('')}
        </details>` : '';
      const nextTrigger = task.next_trigger ? `<div class="hint">下次：${escapeHtml(task.next_trigger)}</div>` : '';
      const issueClass = task.health_issue ? ' class="health-issue-row"' : '';
      rows.push(`
        <tr${issueClass}>
          <td><strong>${escapeHtml(task.label || task.Id || '')}</strong><div class="hint">${escapeHtml(task.Id || '')}</div>${rawDetails}</td>
          <td>${escapeHtml(task.unit_type || '')}${lifecycle}${replacement}</td>
          <td>${badge(task.schedule_status || '未知')}</td>
          <td>${badge(task.execution_status || '未知')}</td>
          <td>${escapeHtml(task.schedule || '')}${nextTrigger}</td>
          <td>${escapeHtml(task.NRestarts || '')}</td>
          <td>${escapeHtml(task.last_execution || '')}</td>
          <td>${serviceActionButtons(task.action_unit || {})}</td>
        </tr>
      `);
    });
  });
  return rows.join('') || '<tr><td colspan="8">暂无 systemd 任务状态。</td></tr>';
}

function shortText(value, limit=160) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= limit) return text;
  return text.slice(0, limit - 3) + '...';
}

function formatTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 19);
  return d.toLocaleString('zh-CN', {hour12: false});
}

function todayString() {
  const d = new Date();
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function showView(name) {
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('nav.tabs button').forEach(el => el.classList.remove('active'));
  document.getElementById(`view-${name}`).classList.add('active');
  document.getElementById(`tab-${name}`).classList.add('active');
  if (name === 'overview') loadOverview();
  if (name === 'information') loadMarketItemsView();
  if (name === 'llm-decisions') loadLlmDecisionsView();
  if (name === 'rules') loadCurrentRules();
  if (name === 'feedback') loadFeedbackQuality();
  if (name === 'relations') loadRelationManager();
  if (name === 'sources') {
    loadSourceProfiles();
    loadHealthSummary();
  }
  if (name === 'health') loadHealth();
  if (name === 'keywords') loadKeywords();
  if (name === 'settings') {
    loadSettings();
  }
  if (name === 'holdings' && !loadedHoldings) reloadData();
}

function ruleMetric(label, value, compact=false) {
  return `<div class="metric"><div class="label">${escapeHtml(label)}</div><div class="value ${compact ? 'rule-metric-value' : ''}">${escapeHtml(value)}</div></div>`;
}

function ruleValueList(field) {
  const values = Array.isArray(field?.values) ? field.values : [];
  const truncation = field?.truncated ? `<div class="hint">仅显示前 ${values.length} 项，共 ${Number(field.count || values.length)} 项。</div>` : '';
  return `
    <div class="rule-field">
      <div class="rule-field-label">${escapeHtml(field?.label || '')}<span>${Number(field?.count || 0)}</span></div>
      ${values.length ? `<ul>${values.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul>` : '<div class="hint">当前没有配置项。</div>'}
      ${truncation}
    </div>
  `;
}

function renderRangeAdmissionRules(section) {
  const status = document.getElementById('rangeAdmissionStatus');
  const metrics = document.getElementById('rangeAdmissionMetrics');
  const groups = document.getElementById('rangeAdmissionGroups');
  const boundaries = document.getElementById('rangeAdmissionBoundaries');
  if (!section || section.status !== 'loaded') {
    status.className = 'status err rules-inline-status';
    status.textContent = section?.error || '范围准入规则加载失败。';
    metrics.innerHTML = '';
    groups.innerHTML = '';
    boundaries.innerHTML = '';
    return;
  }
  status.className = 'status ok rules-inline-status';
  status.textContent = '当前生产配置已通过严格校验。';
  metrics.innerHTML = [
    ruleMetric('配置版本', section.config_version || '-', true),
    ruleMetric('规则合同', section.contract_version || '-', true),
    ruleMetric('准入组', String(section.group_count || 0)),
    ruleMetric('组间关系', section.relation === 'or' ? '或' : (section.relation || '-')),
    ruleMetric('全局排除', String(section.global_exclusions?.count || 0))
  ].join('');
  const groupHtml = (section.groups || []).map((group, index) => `
    <details class="rule-definition" ${index === 0 ? 'open' : ''}>
      <summary>
        <span><strong>${escapeHtml(group.title || '')}</strong><code>${escapeHtml(group.family || '')}</code></span>
        <span class="rule-count">${Number(group.count || 0)} 项</span>
      </summary>
      <div class="rule-definition-body">
        <p>${escapeHtml(group.summary || '')}</p>
        ${(group.fields || []).map(ruleValueList).join('')}
      </div>
    </details>
  `).join('');
  const exclusions = section.global_exclusions || {};
  groups.innerHTML = groupHtml + `
    <details class="rule-definition">
      <summary><span><strong>${escapeHtml(exclusions.label || '全局排除关键词')}</strong><code>global_exclude</code></span><span class="rule-count">${Number(exclusions.count || 0)} 项</span></summary>
      <div class="rule-definition-body">
        <p>命中时排除当前信息；持仓名称、别名或代码直接命中的结果不受该全局排除影响。</p>
        ${ruleValueList(exclusions)}
      </div>
    </details>
  `;
  boundaries.innerHTML = (section.source_boundaries || []).map(boundary => `
    <div class="rule-boundary">
      <strong>${escapeHtml(boundary.title || '')}</strong>
      <p>${escapeHtml(boundary.description || '')}</p>
      ${(boundary.values || []).length ? `<div class="rule-code-values">${boundary.values.map(value => `<code>${escapeHtml(value)}</code>`).join('')}</div>` : ''}
    </div>
  `).join('');
}

function llmRuleMatches(rule, families, actions, query) {
  const applicableFamilies = rule.applicable_families || [rule.family];
  if (families.length && !families.some(family => applicableFamilies.includes(family))) return false;
  if (actions.length && !actions.some(action => (rule.allowed_actions || []).includes(action))) return false;
  if (!query) return true;
  const searchable = [
    rule.rule_id,
    rule.family,
    rule.family_label,
    ...(rule.applicable_families || []),
    ...(rule.applicable_family_labels || []),
    rule.title,
    ...Object.values(rule.action_conditions || {}),
    ...(rule.required_facts || []),
    ...(rule.exclusions || [])
  ].join(' ').toLowerCase();
  return searchable.includes(query);
}

function llmRuleTextList(label, values) {
  const items = Array.isArray(values) ? values : [];
  return `
    <div class="rule-field">
      <div class="rule-field-label">${escapeHtml(label)}<span>${items.length}</span></div>
      ${items.length ? `<ol>${items.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ol>` : '<div class="hint">无</div>'}
    </div>
  `;
}

function renderLlmRules() {
  const section = currentRulesCache?.llm_decision;
  const status = document.getElementById('llmRuleStatus');
  const metrics = document.getElementById('llmRuleMetrics');
  const list = document.getElementById('llmRuleList');
  if (!section || section.status !== 'loaded') {
    status.className = 'status err rules-inline-status';
    status.textContent = section?.error || '大模型决策规则加载失败。';
    metrics.innerHTML = '';
    list.innerHTML = '';
    return;
  }
  status.className = 'status ok rules-inline-status';
  status.textContent = '当前私有规则文件已通过严格校验。';
  const families = selectedMultiSelectValues('llmRuleFamily');
  const actions = selectedMultiSelectValues('llmRuleAction');
  const query = document.getElementById('llmRuleQuery').value.trim().toLowerCase();
  const filtered = (section.rules || []).filter(rule => llmRuleMatches(rule, families, actions, query));
  metrics.innerHTML = [
    ruleMetric('规则版本', section.version || '-', true),
    ruleMetric('全部规则', String(section.rule_count || 0)),
    ruleMetric('当前显示', String(filtered.length)),
    ruleMetric('规则族', String((section.families || []).length))
  ].join('');
  list.innerHTML = filtered.map(rule => {
    const applicableLabels = rule.applicable_family_labels || [rule.family_label || rule.family || ''];
    const actionConditions = ['push', 'daily', 'archive']
      .filter(value => Object.prototype.hasOwnProperty.call(rule.action_conditions || {}, value))
      .map(value => `<div class="rule-action-condition"><span class="badge">${escapeHtml(value)}</span><p>${escapeHtml(rule.action_conditions[value])}</p></div>`)
      .join('');
    return `
      <details class="rule-definition">
        <summary>
          <span><strong>${escapeHtml(rule.title || '')}</strong><code>${escapeHtml(rule.rule_id || '')}</code></span>
          <span class="rule-summary-badges">${applicableLabels.map(value => `<span class="badge">${escapeHtml(value)}</span>`).join('')}${(rule.allowed_actions || []).map(value => `<span class="badge">${escapeHtml(value)}</span>`).join('')}</span>
        </summary>
        <div class="rule-definition-body">
          <div class="rule-field-label">action 条件</div>
          ${actionConditions}
          ${llmRuleTextList('必需事实', rule.required_facts)}
          ${llmRuleTextList('排除条件', rule.exclusions)}
        </div>
      </details>
    `;
  }).join('') || '<div class="rule-empty">没有符合当前筛选条件的规则。</div>';
}

function populateLlmRuleFamilies(section) {
  const labels = {};
  (section.rules || []).forEach(rule => {
    const families = rule.applicable_families || [rule.family];
    const familyLabels = rule.applicable_family_labels || [rule.family_label || rule.family];
    families.forEach((family, index) => { labels[family] = familyLabels[index] || family; });
  });
  setMultiSelectOptions('llmRuleFamily', (section.families || []).map(family => ({
    value: family,
    label: labels[family] || family
  })));
}

async function loadCurrentRules(force=false) {
  if (currentRulesCache && !force) {
    renderRangeAdmissionRules(currentRulesCache.range_admission);
    renderLlmRules();
    return;
  }
  document.getElementById('rangeAdmissionStatus').className = 'status busy rules-inline-status';
  document.getElementById('rangeAdmissionStatus').textContent = '正在读取当前规则...';
  document.getElementById('llmRuleStatus').className = 'status busy rules-inline-status';
  document.getElementById('llmRuleStatus').textContent = '正在读取当前规则...';
  try {
    currentRulesCache = await api('/api/current-rules', {cache: 'no-store'});
    renderRangeAdmissionRules(currentRulesCache.range_admission);
    if (currentRulesCache.llm_decision?.status === 'loaded') populateLlmRuleFamilies(currentRulesCache.llm_decision);
    renderLlmRules();
  } catch (err) {
    currentRulesCache = null;
    renderRangeAdmissionRules({status: 'error', error: err.message});
    renderLlmRules();
  }
}

function showRulesSection(name) {
  const admission = name !== 'llm';
  document.getElementById('rulesSectionAdmission').hidden = !admission;
  document.getElementById('rulesSectionLlm').hidden = admission;
  document.getElementById('rulesTabAdmission').classList.toggle('active', admission);
  document.getElementById('rulesTabLlm').classList.toggle('active', !admission);
  document.getElementById('rulesTabAdmission').setAttribute('aria-selected', String(admission));
  document.getElementById('rulesTabLlm').setAttribute('aria-selected', String(!admission));
}

function formatPct(value) {
  if (value === null || value === undefined || value === '') return '-';
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return `${num.toFixed(2)}%`;
}

function formatRate(value) {
  if (value === null || value === undefined || value === '') return '-';
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return `${(num * 100).toFixed(0)}%`;
}

function feedbackQualityRows(rows) {
  return (rows || []).map(item => `
    <tr>
      <td>${escapeHtml(item.key || '-')}${item.low_sample ? '<div class="hint">样本不足</div>' : ''}</td>
      <td>${item.delivered || 0}</td>
      <td>${item.labelled || 0}</td>
      <td>${formatRate(item.coverage)}</td>
      <td>${item.high_value || 0} <span class="hint">${formatRate(item.high_value_rate)}</span></td>
      <td>${item.duplicate || 0} <span class="hint">${formatRate(item.duplicate_rate)}</span></td>
      <td>${item.invalid || 0} <span class="hint">${formatRate(item.invalid_rate)}</span></td>
    </tr>
  `).join('') || '<tr><td colspan="7">暂无反馈样本。</td></tr>';
}

async function loadFeedbackQuality() {
  const days = document.getElementById('feedbackDays').value || '30';
  try {
    const data = await api(`/api/feedback-quality?days=${encodeURIComponent(days)}`);
    const summary = data.summary || {};
    const metrics = [
      ['推送卡片', summary.delivered || 0],
      ['已反馈', summary.labelled || 0],
      ['反馈覆盖率', formatRate(summary.coverage)],
      ['特别有用', `${summary.high_value || 0} / ${formatRate(summary.high_value_rate)}`],
      ['重复 / 无效', `${summary.duplicate || 0} / ${summary.invalid || 0}`],
    ];
    document.getElementById('feedbackMetrics').innerHTML = metrics.map(item => `<div class="metric"><div class="label">${escapeHtml(item[0])}</div><div class="value">${escapeHtml(item[1])}</div></div>`).join('');
    document.getElementById('feedbackSourceRows').innerHTML = feedbackQualityRows(data.sources);
    document.getElementById('feedbackRuleRows').innerHTML = feedbackQualityRows(data.primary_rules);
    document.getElementById('feedbackAssociationRows').innerHTML = feedbackQualityRows(data.rule_associations);
    document.getElementById('feedbackCrossRows').innerHTML = feedbackQualityRows(data.source_primary_rules);
    document.getElementById('feedbackExampleRows').innerHTML = (data.examples || []).map(item => `
      <tr>
        <td>${escapeHtml(item.feedback_label_display || item.feedback_label || '-')}</td>
        <td>${escapeHtml(item.source || '-')}</td>
        <td>${escapeHtml((item.rule_ids || [])[0] || '未记录规则')}</td>
        <td>${escapeHtml(item.title || '-')}</td>
        <td>${formatTime(item.sent_at)}</td>
      </tr>
    `).join('') || '<tr><td colspan="5">暂无反馈样例。</td></tr>';
  } catch (err) {
    showStatus('反馈质量加载失败：' + err.message, 'err');
  }
}

function marketSourceFilterValue(profile) {
  if (profile.id === 'x_serenity') return 'x:serenity';
  return String(profile.id || '').trim();
}

async function loadSourceFilterOptions() {
  if (sourceFilterOptionsLoaded) return;
  const data = await api('/api/source-profiles');
  const options = [];
  (data.profiles || []).filter(profile => profile.enabled !== false).forEach(profile => {
    const value = marketSourceFilterValue(profile);
    if (!value) return;
    options.push({
      value,
      label: `${profile.name || value}（${value}）`,
      group: profile.category_label || '其他来源'
    });
  });
  setMultiSelectOptions('marketSource', options);
  setMultiSelectOptions('llmDecisionSource', options);
  sourceFilterOptionsLoaded = true;
}

async function loadMarketItemsView() {
  try {
    await loadSourceFilterOptions();
  } catch (err) {
    showStatus(`来源下拉加载失败：${err.message}`, 'err');
    return;
  }
  await loadMarketItems();
}

async function loadLlmDecisionsView() {
  try {
    await loadSourceFilterOptions();
  } catch (err) {
    showStatus(`来源下拉加载失败：${err.message}`, 'err');
    return;
  }
  await loadLlmDecisions();
}

async function loadOverview() {
  try {
    const data = await api('/api/overview');
    const metrics = document.getElementById('overviewMetrics');
    metrics.innerHTML = (data.cards || []).map(item => `
      <div class="metric">
        <div class="label">${escapeHtml(item.label)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </div>
    `).join('');
    const breakdown = [];
    breakdown.push('<div class="list-row"><strong>来源分布</strong></div>');
    (data.by_source || []).forEach(item => breakdown.push(`<div class="list-row">${escapeHtml(item.key)} <span class="summary">${item.count}</span></div>`));
    breakdown.push('<div class="list-row"><strong>程度分布</strong></div>');
    (data.decision_actions || []).forEach(item => breakdown.push(`<div class="list-row">${badge(item.key)} <span class="summary">${item.count}</span></div>`));
    breakdown.push('<div class="list-row"><strong>飞书状态</strong></div>');
    (data.deliveries || []).forEach(item => breakdown.push(`<div class="list-row">${escapeHtml(item.key)} <span class="summary">${item.count}</span></div>`));
    document.getElementById('overviewBreakdown').innerHTML = breakdown.join('') || '<div class="list-row">暂无统计。</div>';
    document.getElementById('overviewLatest').innerHTML = ['<div class="list-row"><strong>最近信息</strong></div>', ...(data.latest || []).map(item => `
      <div class="list-row">
        <div>${badge(item.decision_action)} <strong>${escapeHtml(shortText(item.title, 120))}</strong></div>
        <div class="hint">${escapeHtml(item.source)} / ${formatTime(item.seen_at)}</div>
      </div>
    `)].join('');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadMarketItems() {
  let operationId = 0;
  let controller = null;
  const rows = document.getElementById('marketRows');
  const queryButton = document.getElementById('marketQueryButton');
  try {
    const params = new URLSearchParams();
    const startDate = document.getElementById('marketFromDate').value;
    const endDate = document.getElementById('marketToDate').value;
    const timeBasis = document.getElementById('marketTimeBasis').value;
    const selectedSources = selectedMultiSelectValues('marketSource');
    const sources = selectedSources.length ? selectedSources : availableMultiSelectValues('marketSource');
    const feedback = selectedMultiSelectValues('marketFeedback');
    const q = document.getElementById('marketQuery').value.trim();
    if (Boolean(startDate) !== Boolean(endDate)) {
      showStatus('开始日期和结束日期必须同时填写。', 'err');
      return;
    }
    if (startDate && endDate && startDate > endDate) {
      showStatus('开始日期不能晚于结束日期。', 'err');
      return;
    }
    if (startDate && endDate) {
      params.set('from', startDate);
      params.set('to', endDate);
    }
    if (timeBasis !== 'seen') params.set('time_basis', timeBasis);
    sources.forEach(source => params.append('source', source));
    feedback.forEach(value => params.append('feedback', value));
    if (q) params.set('q', q);
    if (document.getElementById('marketIncludeBaseline').checked) params.set('include_baseline', '1');
    marketAbortController?.abort();
    controller = new AbortController();
    marketAbortController = controller;
    operationId = ++marketOperationId;
    rows.innerHTML = '<tr><td colspan="6">正在查询...</td></tr>';
    queryButton.disabled = true;
    queryButton.textContent = '查询中';
    const data = await api('/api/market-items?' + params.toString(), {signal: controller.signal});
    if (operationId !== marketOperationId) return;
    document.getElementById('marketTimeHeader').textContent = timeBasis === 'published' ? '原文发布时间' : '采集/处理时间';
    const feedbackSummary = data.feedback_summary || {};
    document.getElementById('marketFeedbackSummary').innerHTML = [
      `可反馈且已投递 ${feedbackSummary.delivered || 0}`,
      `已反馈 ${feedbackSummary.labelled || 0}`,
      `特别有用 ${feedbackSummary.high_value || 0}`,
      `重复 ${feedbackSummary.duplicate || 0}`,
      `无效 ${feedbackSummary.invalid || 0}`,
    ].map(text => `<span>${escapeHtml(text)}</span>`).join('');
    rows.innerHTML = (data.items || []).map(item => `
      <tr>
        <td>${formatTime(timeBasis === 'published' ? (item.published_at || item.seen_at) : (item.seen_at || item.published_at))}${item.published_at && timeBasis !== 'published' ? `<div class="hint">原文：${formatTime(item.published_at)}</div>` : ''}${item.seen_at && timeBasis === 'published' ? `<div class="hint">采集：${formatTime(item.seen_at)}</div>` : ''}</td>
        <td>${escapeHtml(item.source || '')}</td>
        <td class="summary-cell">
          <div><strong>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || '')}</a>` : escapeHtml(item.title || '')}</strong></div>
          <div>${escapeHtml(shortText(item.summary || '', 220))}</div>
        </td>
        <td>${badge(item.decision_action)}</td>
        <td>${escapeHtml(item.delivery_status || '')}</td>
        <td>${feedbackBadge(item)}</td>
      </tr>
    `).join('') || '<tr><td colspan="6">没有匹配信息。</td></tr>';
  } catch (err) {
    if (err.name === 'AbortError' || operationId !== marketOperationId) return;
    rows.innerHTML = '<tr><td colspan="6">查询失败，请稍后重试。</td></tr>';
    showStatus(err.message, 'err');
  } finally {
    if (operationId && operationId === marketOperationId) {
      if (marketAbortController === controller) marketAbortController = null;
      queryButton.disabled = false;
      queryButton.textContent = '查询';
    }
  }
}

function llmDecisionStatusLabel(status) {
  return {
    completed: '已完成',
    insufficient_evidence: '证据不足',
    uncertain: '历史 uncertain',
    model_unavailable: '大模型不可用',
    invalid_output: '输出无效',
    evidence_invalid: '证据无效',
    conflict: '结果冲突',
    pending: '等待判断'
  }[status] || status || '未记录';
}

function llmDecisionAssessmentHtml(assessment) {
  const judgement = String(assessment?.judgement || '');
  const action = assessment?.action ? ` ${badge(assessment.action)}` : '';
  const references = [...(assessment?.evidence || []), ...(assessment?.counterevidence || [])];
  const referenceHtml = references.map(reference => `
    <div class="hint">${escapeHtml(reference.evidence_id || '')}${reference.field ? `（${escapeHtml(reference.field)}）` : ''}：${escapeHtml(reference.quote || '')}</div>
  `).join('');
  return `
    <div class="llm-assessment">
      <div><strong>${escapeHtml(assessment?.rule_id || '未记录规则')}</strong> ${badge(judgement)}${action}</div>
      <div class="summary-cell">${escapeHtml(assessment?.reason || '未记录理由')}</div>
      ${referenceHtml || '<div class="hint">未记录证据或反证</div>'}
    </div>
  `;
}

function llmDecisionAttemptHtml(attempt, index) {
  const calls = Array.isArray(attempt?.calls) ? attempt.calls : [];
  const assessments = calls.flatMap(call => Array.isArray(call?.rule_assessments) ? call.rule_assessments : []);
  const errors = calls.flatMap(call => Array.isArray(call?.validation_errors) ? call.validation_errors : []);
  return `
    <div class="llm-attempt">
      <div><strong>第 ${index + 1} 次模型尝试</strong> ${badge(llmDecisionStatusLabel(attempt?.evaluation_status || ''))} <span class="hint">${escapeHtml(formatTime(attempt?.generated_at || ''))}</span></div>
      ${attempt?.failure_reason ? `<div class="summary-cell">${escapeHtml(attempt.failure_reason)}</div>` : ''}
      ${assessments.map(llmDecisionAssessmentHtml).join('')}
      ${errors.map(error => `<div class="hint">校验：${escapeHtml(error)}</div>`).join('')}
      ${!assessments.length && !errors.length && !attempt?.failure_reason ? '<div class="hint">没有可展示的有界判断摘要。</div>' : ''}
    </div>
  `;
}

function llmDecisionDetailsHtml(item) {
  const assessments = Array.isArray(item.rule_assessments) ? item.rule_assessments : [];
  const attempts = Array.isArray(item.attempts) ? item.attempts : [];
  const current = assessments.length
    ? `<div class="llm-detail-group"><strong>当前 DecisionResult</strong>${assessments.map(llmDecisionAssessmentHtml).join('')}</div>`
    : '';
  const history = attempts.length
    ? `<div class="llm-detail-group"><strong>模型尝试记录</strong>${attempts.map(llmDecisionAttemptHtml).join('')}</div>`
    : '<div class="hint">没有私有审计摘要可展示。</div>';
  const safeUrl = safeExternalUrl(item.url);
  return `
    <details class="llm-decision-details">
      <summary>查看判断理由和证据</summary>
      <div class="llm-detail-meta">review ${escapeHtml(item.market_review_id || '')} / ${escapeHtml(item.review_status || '')} / ${escapeHtml(item.source_item_id || '')}</div>
      ${item.decision_reason ? `<div class="summary-cell"><strong>总体理由：</strong>${escapeHtml(item.decision_reason)}</div>` : ''}
      ${safeUrl ? `<div class="hint"><a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">打开原文</a></div>` : ''}
      ${current}${history}
    </details>
  `;
}

async function loadLlmDecisions() {
  try {
    const params = new URLSearchParams();
    const startDate = document.getElementById('llmDecisionFromDate').value;
    const endDate = document.getElementById('llmDecisionToDate').value;
    if (Boolean(startDate) !== Boolean(endDate)) {
      showStatus('开始日期和结束日期必须同时填写。', 'err');
      return;
    }
    if (startDate && endDate && startDate > endDate) {
      showStatus('开始日期不能晚于结束日期。', 'err');
      return;
    }
    if (startDate && endDate) {
      params.set('from', startDate);
      params.set('to', endDate);
    }
    const selectedActions = selectedMultiSelectValues('llmDecisionAction');
    const selectedStatuses = selectedMultiSelectValues('llmDecisionStatus');
    const selectedSources = selectedMultiSelectValues('llmDecisionSource');
    const sources = selectedSources.length ? selectedSources : availableMultiSelectValues('llmDecisionSource');
    const query = document.getElementById('llmDecisionQuery').value.trim();
    selectedActions.forEach(action => params.append('action', action));
    selectedStatuses.forEach(value => params.append('status', value));
    sources.forEach(source => params.append('source', source));
    if (query) params.set('q', query);
    const data = await api('/api/llm-decisions?' + params.toString());
    const summary = data.summary || {};
    const actions = summary.actions || {};
    const statuses = summary.statuses || {};
    document.getElementById('llmDecisionMetrics').innerHTML = [
      ['当前条目', summary.rows || 0],
      ['push', actions.push || 0],
      ['daily', actions.daily || 0],
      ['archive', actions.archive || 0],
      ['证据不足', summary.current_insufficient_evidence || 0],
      ['仍 failed_retryable', summary.current_failed_retryable || 0],
      ['uncertain 评估记录', summary.uncertain_attempts || 0],
      ['模型状态', Object.entries(statuses).map(([key, value]) => `${llmDecisionStatusLabel(key)} ${value}`).join('；') || '-']
    ].map(item => `<section class="metric"><div class="label">${escapeHtml(item[0])}</div><div class="value">${escapeHtml(item[1])}</div></section>`).join('');
    document.getElementById('llmDecisionRows').innerHTML = (data.rows || []).map(item => {
      const safeUrl = safeExternalUrl(item.url);
      const title = safeUrl
        ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || '')}</a>`
        : escapeHtml(item.title || '');
      const action = item.decision_action ? badge(item.decision_action) : '<span class="badge">未生成 action</span>';
      const attempts = Number(item.attempts?.length || 0);
      return `
        <tr>
          <td>${escapeHtml(formatTime(item.review_created_at || ''))}</td>
          <td>${escapeHtml(item.source || '')}</td>
          <td class="summary-cell"><div><strong>${title}</strong></div><div class="hint">${escapeHtml(item.source_item_id || '')}</div>${llmDecisionDetailsHtml(item)}</td>
          <td>${action}</td>
          <td>${badge(llmDecisionStatusLabel(item.model_status || ''))}<div class="hint">${escapeHtml(item.review_status || '')}</div></td>
          <td>${attempts ? `${attempts} 次` : '—'}</td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="6">没有匹配的大模型决策。</td></tr>';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadRelationManager() {
  try {
    const params = new URLSearchParams();
    const q = document.getElementById('relationManageQuery') ? document.getElementById('relationManageQuery').value.trim() : '';
    const enabled = document.getElementById('relationManageEnabled') ? document.getElementById('relationManageEnabled').value : 'all';
    if (q) params.set('q', q);
    if (enabled) params.set('enabled', enabled);
    const data = await api('/api/relations?' + params.toString());
    managedRelations = data.relations || [];
    document.getElementById('relationManageRows').innerHTML = managedRelations.map(item => `
      <tr>
        <td>${badge(item.enabled ? '启用' : '停用')}<div class="hint">${formatTime(item.updated_at)}</div></td>
        <td><strong>${escapeHtml(item.symbol || '')}</strong><div class="hint">${escapeHtml(item.symbol_name || '')}</div></td>
        <td><strong>${escapeHtml(item.related_symbol || '')}</strong><div class="hint">${escapeHtml(item.related_name || '')}</div></td>
        <td>${badge(item.impact_direction || '')}<div class="hint">强度 ${escapeHtml(item.relation_strength || '-')} / 置信 ${escapeHtml(item.confidence || '-')}</div></td>
        <td class="summary-cell">
          <div>${escapeHtml(item.relation_type || '')} / ${escapeHtml(item.theme || '')}</div>
          <div class="hint">${escapeHtml(shortText(item.reason || '', 220))}</div>
          <div class="hint">${escapeHtml(item.source || '')} ${item.valid_to ? ' / 有效至 ' + escapeHtml(item.valid_to) : ''}</div>
        </td>
        <td>${escapeHtml(item.last_review_verdict || '-')}<div class="hint">hit ${item.hit_count || 0} / miss ${item.miss_count || 0}</div></td>
        <td>
          <button onclick="editRelation(${item.id})">编辑</button>
          <button onclick="toggleRelation(${item.id}, ${item.enabled ? 'false' : 'true'})">${item.enabled ? '停用' : '启用'}</button>
          <button class="danger" onclick="deleteRelationRow(${item.id})">删除</button>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="7">暂无关系映射。</td></tr>';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function clearRelationForm() {
  editingRelationId = null;
  document.getElementById('relationModalTitle').textContent = '新增关系';
  ['relSymbol','relSymbolName','relRelatedSymbol','relRelatedName','relRelationType','relTheme','relConfidence','relStrength','relSource','relValidFrom','relValidTo','relReason'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('relImpactDirection').value = 'positive';
  document.getElementById('relEnabled').checked = true;
}

function openRelationModal(item=null) {
  clearRelationForm();
  if (item) {
    editingRelationId = item.id;
    document.getElementById('relationModalTitle').textContent = '编辑关系';
    document.getElementById('relSymbol').value = item.symbol || '';
    document.getElementById('relSymbolName').value = item.symbol_name || '';
    document.getElementById('relRelatedSymbol').value = item.related_symbol || '';
    document.getElementById('relRelatedName').value = item.related_name || '';
    document.getElementById('relRelationType').value = item.relation_type || '';
    document.getElementById('relImpactDirection').value = item.impact_direction || 'uncertain';
    document.getElementById('relTheme').value = item.theme || '';
    document.getElementById('relConfidence').value = item.confidence || '';
    document.getElementById('relStrength').value = item.relation_strength || '';
    document.getElementById('relSource').value = item.source || 'web';
    document.getElementById('relValidFrom').value = item.valid_from || '';
    document.getElementById('relValidTo').value = item.valid_to || '';
    document.getElementById('relReason').value = item.reason || '';
    document.getElementById('relEnabled').checked = item.enabled !== false;
  } else {
    document.getElementById('relSource').value = 'web';
  }
  document.getElementById('relationModal').style.display = 'flex';
}

function closeRelationModal() {
  document.getElementById('relationModal').style.display = 'none';
}

function editRelation(id) {
  const item = managedRelations.find(row => Number(row.id) === Number(id));
  if (!item) {
    showStatus('没有找到这条关系。', 'err');
    return;
  }
  openRelationModal(item);
}

function relationFormPayload() {
  return {
    symbol: document.getElementById('relSymbol').value.trim(),
    symbol_name: document.getElementById('relSymbolName').value.trim(),
    related_symbol: document.getElementById('relRelatedSymbol').value.trim(),
    related_name: document.getElementById('relRelatedName').value.trim(),
    relation_type: document.getElementById('relRelationType').value.trim() || 'related',
    impact_direction: document.getElementById('relImpactDirection').value.trim(),
    theme: document.getElementById('relTheme').value.trim(),
    confidence: document.getElementById('relConfidence').value.trim(),
    relation_strength: document.getElementById('relStrength').value.trim(),
    source: document.getElementById('relSource').value.trim() || 'web',
    valid_from: document.getElementById('relValidFrom').value.trim(),
    valid_to: document.getElementById('relValidTo').value.trim(),
    reason: document.getElementById('relReason').value.trim(),
    enabled: document.getElementById('relEnabled').checked
  };
}

async function saveRelationFromModal() {
  try {
    const payload = {id: editingRelationId, relation: relationFormPayload()};
    const data = await api('/api/relations/save', {method: 'POST', body: JSON.stringify(payload)});
    closeRelationModal();
    await loadRelationManager();
    showStatus(`关系已保存并同步 JSON 快照：${(data.snapshot || {}).path || ''}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function deleteRelationRow(id) {
  if (!confirm('确认删除这条关系映射？')) return;
  try {
    const data = await api('/api/relations/delete', {method: 'POST', body: JSON.stringify({id})});
    await loadRelationManager();
    showStatus(`关系已删除并同步 JSON 快照：${(data.snapshot || {}).path || ''}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function toggleRelation(id, enabled) {
  try {
    const data = await api('/api/relations/toggle', {method: 'POST', body: JSON.stringify({id, enabled})});
    await loadRelationManager();
    showStatus(`关系已${enabled ? '启用' : '停用'}并同步 JSON 快照：${(data.snapshot || {}).path || ''}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function exportRelationJson() {
  try {
    const data = await api('/api/relations/export', {method: 'POST', body: JSON.stringify({})});
    showStatus(`已导出 ${(data.snapshot || {}).count || 0} 条关系到 ${(data.snapshot || {}).path || ''}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function importRelationJson() {
  if (!confirm('确认从私有 config/stock_relations.json 导入并覆盖同 key 关系？')) return;
  try {
    const data = await api('/api/relations/import', {method: 'POST', body: JSON.stringify({})});
    await loadRelationManager();
    showStatus(`导入完成：读取 ${data.counts.read} 条，写入 ${data.counts.imported} 条，跳过 ${data.counts.skipped} 条。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function diffRelationJson() {
  try {
    const data = await api('/api/relations/diff');
    const diff = data.diff || {};
    const text = [
      `数据库：${diff.db_count || 0} 条`,
      `JSON：${diff.json_count || 0} 条`,
      `JSON 无效行：${diff.invalid_json_rows || 0}`,
      '',
      `仅数据库存在：${(diff.only_in_db || []).length}`,
      JSON.stringify(diff.only_in_db || [], null, 2),
      '',
      `仅 JSON 存在：${(diff.only_in_json || []).length}`,
      JSON.stringify(diff.only_in_json || [], null, 2),
      '',
      `内容不同：${(diff.changed || []).length}`,
      JSON.stringify(diff.changed || [], null, 2)
    ].join('\n');
    document.getElementById('diffText').textContent = text;
    document.getElementById('diffModal').style.display = 'flex';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function runServiceAction(unit, action) {
  const label = serviceActionLabel(action);
  if (!confirm(`确认对 ${unit} 执行“${label}”？`)) return;
  try {
    const data = await api('/api/service-action', {method: 'POST', body: JSON.stringify({unit, action})});
    const targetText = data.target && data.target !== unit ? `，目标 ${data.target}` : '';
    showStatus(`${unit} 已提交“${label}”${targetText}。`);
    await loadHealth();
  } catch (err) {
    showStatus(err.message, 'err');
    await loadHealth();
  }
}

function renderSourceProfileMetrics(categories) {
  const metrics = document.getElementById('sourceProfileMetrics');
  metrics.innerHTML = (categories || []).map(item => `
    <div class="metric">
      <div class="label">${escapeHtml(item.label || item.id || '')}</div>
      <div class="value">${escapeHtml(item.count || 0)}</div>
      <div class="hint">${Number(item.failing || 0) ? '异常 ' + escapeHtml(item.failing) : '运行记录正常/待记录'}${Number(item.disabled || 0) ? '；停用 ' + escapeHtml(item.disabled) : ''}</div>
    </div>
  `).join('');
}

function renderSourceCategoryOptions(categories) {
  setMultiSelectOptions('sourceProfileCategory', (categories || []).map(item => ({
    value: item.id || '',
    label: `${item.label || item.id || ''}（${item.count || 0}）`
  })));
}

function sourceProfileSearchText(item) {
  return [
    item.category_label, item.name, item.id, item.source_type, item.fetch_range,
    item.filter_policy, item.frequency, item.runtime_shape, item.pipeline,
    item.fetcher, item.publisher_role, item.tavily_policy, item.proxy_profile, item.text_length_policy,
    item.provider,
    (item.service_units || []).join(' '), item.notes, item.enabled ? 'enabled' : 'disabled'
  ].join(' ').toLowerCase();
}

function setSourceProfileDirty(isDirty) {
  sourceProfileCache.dirty = Boolean(isDirty);
  const button = document.getElementById('sourceProfileSaveButton');
  if (button) button.disabled = !sourceProfileCache.dirty;
}

function updateSourceProfileDraft(el) {
  const sourceId = el.dataset.sourceId || '';
  const field = el.dataset.field || '';
  const item = (sourceProfileCache.profiles || []).find(profile => profile.id === sourceId);
  if (!item || !field) return;
  item[field] = el.type === 'checkbox' ? Boolean(el.checked) : el.value;
  item._draft_modified = true;
  setSourceProfileDirty(true);
}

function sourceProfilesForSave() {
  return (sourceProfileCache.profiles || []).map(item => ({
    id: item.id,
    enabled: item.enabled !== false,
    publisher_role: item.publisher_role || '',
    provider: item.provider || '',
    notes: item.notes || ''
  }));
}

function isFailingSourceProfile(item) {
  return item.enabled !== false && item.health_status === 'failing';
}

function renderSourceProfiles() {
  const categories = selectedMultiSelectValues('sourceProfileCategory');
  const enabled = document.getElementById('sourceProfileEnabled').value;
  const q = document.getElementById('sourceProfileQuery').value.trim().toLowerCase();
  const rows = (sourceProfileCache.profiles || []).filter(item => {
    if (enabled === 'enabled' && item.enabled === false) return false;
    if (enabled === 'disabled' && item.enabled !== false) return false;
    if (categories.length && !categories.includes(item.category)) return false;
    if (q && !sourceProfileSearchText(item).includes(q)) return false;
    return true;
  }).sort((left, right) => Number(isFailingSourceProfile(right)) - Number(isFailingSourceProfile(left)));
  document.getElementById('sourceProfileRows').innerHTML = rows.map(item => {
    const health = item.health_status === 'unknown' ? '未记录' : item.health_status;
    const isFailing = isFailingSourceProfile(item);
    const healthDetail = item.last_error ? `<div class="hint">${escapeHtml(shortText(item.last_error, 120))}</div>` : '';
    const healthTime = isFailing && item.last_failure_at ? `<div class="hint">最近失败：${escapeHtml(formatTime(item.last_failure_at))}</div>` : '';
    const services = (item.service_units || []).map(unit => `<span class="badge">${escapeHtml(unit)}</span>`).join(' ');
    const modified = item.config_modified ? '<div class="hint source-dirty">本地覆盖</div>' : '';
    const enabledChecked = item.enabled !== false ? 'checked' : '';
    const providerControls = item.provider ? `
      <div style="margin-top:6px">
        <div class="hint">采集 provider</div>
        <input class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="provider" value="${escapeHtml(item.provider || '')}" oninput="updateSourceProfileDraft(this)">
      </div>
    ` : '';
    return `
      <tr${isFailing ? ' class="health-issue-row"' : ''}>
        <td>${escapeHtml(item.category_label || item.category || '')}</td>
        <td>
          <input type="checkbox" data-source-id="${escapeHtml(item.id || '')}" data-field="enabled" onchange="updateSourceProfileDraft(this)" ${enabledChecked}>
          ${modified}
        </td>
        <td>
          <strong>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.name || '')}</a>` : escapeHtml(item.name || '')}</strong>
          <div class="hint">${escapeHtml(item.id || '')} / ${escapeHtml(item.source_type || '')}</div>
          <div class="hint">${escapeHtml(item.runtime_note || '')}</div>
        </td>
        <td>${badge(health)}<div class="hint">连续失败 ${escapeHtml(item.consecutive_failures || 0)}</div>${healthTime}${healthDetail}</td>
        <td>
          <div>${escapeHtml(item.frequency || '')}</div>
          <div class="hint">${escapeHtml(item.runtime_shape || '')}</div>
        </td>
        <td>
          ${escapeHtml(item.pipeline || '')}
          <div class="hint">${escapeHtml(item.text_length_policy || '')}</div>
          <select class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="publisher_role" onchange="updateSourceProfileDraft(this)">
            <option value="" ${item.publisher_role ? '' : 'selected'}>非新闻媒体转述</option>
            <option value="news_media" ${item.publisher_role === 'news_media' ? 'selected' : ''}>新闻媒体转述</option>
            <option value="government_official" ${item.publisher_role === 'government_official' ? 'selected' : ''}>政府官方</option>
            <option value="third_party_research_summary" ${item.publisher_role === 'third_party_research_summary' ? 'selected' : ''}>第三方研究汇总</option>
          </select>
        </td>
        <td class="summary-cell">
          <div>${escapeHtml(item.fetch_range || '')}</div>
          <div class="hint">${escapeHtml(item.filter_policy || '')}</div>
          <div class="hint">${escapeHtml(item.fetcher || '')}</div>
          <div class="hint">${services}</div>
          ${providerControls}
          <div style="margin-top:6px">
            <div class="hint">代理</div>
            <div>${escapeHtml(item.proxy_profile || '')}</div>
          </div>
          <textarea class="source-notes" data-source-id="${escapeHtml(item.id || '')}" data-field="notes" oninput="updateSourceProfileDraft(this)">${escapeHtml(item.notes || '')}</textarea>
        </td>
      </tr>
    `;
  }).join('') || '<tr><td colspan="7">没有匹配信息源。</td></tr>';
}

async function loadSourceProfiles() {
  try {
    const data = await api('/api/source-profiles');
    sourceProfileCache = {
      categories: data.categories || [],
      profiles: data.profiles || [],
      config_path: data.config_path || '',
      config_exists: Boolean(data.config_exists),
      runtime_note: data.runtime_note || '',
      dirty: false
    };
    sourceFilterOptionsLoaded = false;
    renderSourceProfileMetrics(sourceProfileCache.categories);
    renderSourceCategoryOptions(sourceProfileCache.categories);
    renderSourceProfiles();
    setSourceProfileDirty(false);
    const hint = document.getElementById('sourceProfileConfigHint');
    if (hint) {
      const suffix = sourceProfileCache.config_exists ? '已存在本地覆盖配置' : '尚未保存本地覆盖配置';
      hint.textContent = `${data.runtime_note || '已读取信息源实际运行配置。'} 配置文件：${sourceProfileCache.config_path || '-'}；${suffix}。`;
    }
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function saveSourceProfiles() {
  try {
    const data = await api('/api/source-profiles', {
      method: 'POST',
      body: JSON.stringify({profiles: sourceProfilesForSave()})
    });
    sourceProfileCache = {
      categories: data.categories || [],
      profiles: data.profiles || [],
      config_path: data.config_path || '',
      config_exists: Boolean(data.config_exists),
      runtime_note: data.runtime_note || '',
      dirty: false
    };
    sourceFilterOptionsLoaded = false;
    renderSourceProfileMetrics(sourceProfileCache.categories);
    renderSourceCategoryOptions(sourceProfileCache.categories);
    renderSourceProfiles();
    setSourceProfileDirty(false);
    const hint = document.getElementById('sourceProfileConfigHint');
    if (hint) {
      hint.textContent = `${data.runtime_note || '已读取信息源实际运行配置。'} 配置文件：${sourceProfileCache.config_path || '-'}；已存在本地覆盖配置。`;
    }
    const saved = data.save_result || {};
    showStatus(`信息源配置已保存：停用 ${saved.disabled_count || 0} 个，覆盖 ${saved.override_count || 0} 个。页面已按实际运行配置刷新。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function applyNavAlertBadge(badgeId, tabId, count, label) {
  const badgeEl = document.getElementById(badgeId);
  const tab = document.getElementById(tabId);
  badgeEl.classList.remove('unavailable');
  badgeEl.textContent = count > 99 ? '99+' : String(count);
  badgeEl.hidden = count === 0;
  tab.setAttribute('aria-label', count ? `${label}，${count} 项当前故障` : `${label}，无当前故障`);
}

function applyHealthSummary(summary) {
  const taskFailures = Number(summary.task_failures || 0);
  const sourceFailures = Number(summary.source_failures || 0);
  applyNavAlertBadge('healthAlertBadge', 'tab-health', taskFailures, '任务健康');
  applyNavAlertBadge('sourceAlertBadge', 'tab-sources', sourceFailures, '信息源');
  const taskDetail = document.getElementById('healthAlertSummary');
  taskDetail.hidden = taskFailures === 0;
  taskDetail.textContent = taskFailures ? `当前 ${taskFailures} 个任务异常` : '';
  const sourceDetail = document.getElementById('sourceAlertSummary');
  sourceDetail.hidden = sourceFailures === 0;
  sourceDetail.textContent = sourceFailures ? `当前 ${sourceFailures} 个异常信息源` : '';
}

function markHealthSummaryUnavailable() {
  [
    ['healthAlertBadge', 'tab-health', '任务健康'],
    ['sourceAlertBadge', 'tab-sources', '信息源']
  ].forEach(([badgeId, tabId, label]) => {
    const badgeEl = document.getElementById(badgeId);
    badgeEl.textContent = '!';
    badgeEl.hidden = false;
    badgeEl.classList.add('unavailable');
    document.getElementById(tabId).setAttribute('aria-label', `${label}状态读取失败`);
  });
}

async function loadHealthSummary() {
  try {
    applyHealthSummary(await api('/api/health/summary'));
  } catch (err) {
    markHealthSummaryUnavailable();
  }
}

async function loadHealth() {
  try {
    const data = await api('/api/health');
    applyHealthSummary(data.summary || {});
    document.getElementById('healthRows').innerHTML = renderHealthTasks(data.tasks || [], data.unit_groups || {});
    const sources = [...(data.sources || [])].sort((left, right) => {
      const issueOrder = Number(Boolean(right.health_issue)) - Number(Boolean(left.health_issue));
      if (issueOrder) return issueOrder;
      return Number(right.consecutive_failures || 0) - Number(left.consecutive_failures || 0);
    });
    document.getElementById('sourceHealthRows').innerHTML = sources.map(source => `
      <tr${source.health_issue ? ' class="health-issue-row"' : ''}>
        <td>${escapeHtml(source.monitor || '')}</td>
        <td>${escapeHtml(source.source || '')}</td>
        <td>${badge(source.status || '')}</td>
        <td>${escapeHtml(String(source.consecutive_failures || 0))}</td>
        <td>${formatTime(source.last_success_at || '')}</td>
        <td>${formatTime(source.last_failure_at || '')}</td>
        <td class="summary-cell">${escapeHtml(shortText(source.last_error || '', 180))}</td>
      </tr>
    `).join('') || '<tr><td colspan="7">暂无来源健康记录。</td></tr>';
    document.getElementById('healthLogs').innerHTML = (data.logs || []).map(log => `
      <section class="panel" style="margin-top:12px">
        <div class="list-row" style="padding:10px 12px"><strong>${escapeHtml(log.name)}</strong></div>
        <div class="log">${escapeHtml(log.tail || '')}</div>
      </section>
    `).join('');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function keywordTextToList(value) {
  return String(value || '').split(/[，,;；\n]+/).map(s => s.trim()).filter(Boolean);
}

function keywordListToText(value) {
  return Array.isArray(value) ? value.join('\n') : '';
}

async function loadKeywords() {
  try {
    const data = await api('/api/media-keywords');
    document.getElementById('semiconductorAiKeywords').value = keywordListToText(data.semiconductor_ai_keywords || []);
    document.getElementById('semiconductorAiTitleKeywords').value = keywordListToText(data.semiconductor_ai_title_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('mediaKeywordConfigVersion').textContent = data.config_version || '-';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function saveKeywords() {
  try {
    const payload = {
      semiconductor_ai_keywords: keywordTextToList(document.getElementById('semiconductorAiKeywords').value),
      semiconductor_ai_title_keywords: keywordTextToList(document.getElementById('semiconductorAiTitleKeywords').value),
      exclude_keywords: keywordTextToList(document.getElementById('excludeKeywords').value)
    };
    const data = await api('/api/media-keywords', {method: 'POST', body: JSON.stringify(payload)});
    document.getElementById('semiconductorAiKeywords').value = keywordListToText(data.semiconductor_ai_keywords || []);
    document.getElementById('semiconductorAiTitleKeywords').value = keywordListToText(data.semiconductor_ai_title_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('mediaKeywordConfigVersion').textContent = data.config_version || '-';
    showStatus(`媒体关键词已保存。主关键词 ${(data.semiconductor_ai_keywords || []).length} 个，标题限定 ${(data.semiconductor_ai_title_keywords || []).length} 个，排除 ${(data.exclude_keywords || []).length} 个。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadSettings() {
  try {
    const data = await api('/api/settings');
    const grid = document.getElementById('settingsGrid');
    grid.innerHTML = (data.groups || []).map(group => `
      <section class="settings-card">
        <h3>${escapeHtml(group.title || group.id || '')}</h3>
        <div class="hint">${escapeHtml(group.restart_hint || '')}</div>
        ${(group.fields || []).map(field => `
          <div class="setting-field">
            <label>
              <span>${escapeHtml(field.label || field.key || '')}</span>
              <span class="setting-mask">${field.sensitive ? (field.configured ? '已配置 ' + escapeHtml(field.masked || '') : '未配置') : ''}</span>
            </label>
            <input
              data-setting-key="${escapeHtml(field.key || '')}"
              data-sensitive="${field.sensitive ? '1' : '0'}"
              value="${field.sensitive ? '' : escapeHtml(field.value || '')}"
              placeholder="${escapeHtml(field.sensitive ? '留空保留现有值；输入新值覆盖' : (field.placeholder || ''))}"
              autocomplete="off"
            >
            ${field.help ? `<div class="hint">${escapeHtml(field.help)}</div>` : ''}
          </div>
        `).join('')}
      </section>
    `).join('');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function settingsRestartAdvice(changedItems) {
  const keys = (changedItems || []).map(item => item.key || '');
  const hasPrefix = prefix => keys.some(key => key.startsWith(prefix));
  const hasAny = names => keys.some(key => names.includes(key));
  const lines = [];
  if (hasPrefix('LLM_')) {
    lines.push('大模型配置：重启常驻的 surveil-x-stream.service、surveil-sina-flash.service；研究机构/官网/新闻媒体 collector 下一轮自动读取，也可立即运行对应 timer。');
  }
  if (hasPrefix('VALUE_DIRECTORY_')) {
    lines.push('价值目录：下一次 05:00 / 21:00 timer 会读取新配置；如需马上验证，在任务健康页立即运行 surveil-value-directory.timer。');
  }
  if (hasPrefix('X_')) {
    lines.push('X 配置：重启 surveil-x-stream.service。');
  }
  if (hasPrefix('SINA_')) {
    lines.push('新浪配置：重启 surveil-sina-flash.service；可选立即运行 surveil-sina-stock-news.timer。');
  }
  if (hasAny(['SURVEIL_HTTP_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'])) {
    lines.push('代理环境：重启使用代理的常驻服务；collector timer 下一轮自动读取。若修改 mihomo 配置，重启 surveil-proxy.service。');
  }
  return lines;
}

async function saveSettings() {
  try {
    const values = {};
    document.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      const sensitive = input.dataset.sensitive === '1';
      const value = input.value.trim();
      if (!key) return;
      if (sensitive && !value) return;
      values[key] = value;
    });
    const data = await api('/api/settings', {method: 'POST', body: JSON.stringify({values})});
    const changedItems = data.changed || [];
    const changed = changedItems.map(item => `${item.key}: ${item.old || '<空>'} -> ${item.new || '<空>'}`).join('\n');
    const advice = settingsRestartAdvice(changedItems);
    await loadSettings();
    showStatus(changed ? `配置已保存：\n${changed}${advice.length ? '\n\n生效建议：\n- ' + advice.join('\n- ') : '\n\n如需立即生效，请重启对应服务。'}` : '没有配置变化。');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function readRow(row, item={}) {
  return {
    ...item,
    enabled: row.querySelector('[data-field="enabled"]').checked,
    symbol: row.querySelector('[data-field="symbol"]').value.trim(),
    name: row.querySelector('[data-field="name"]').value.trim(),
    full_name: row.querySelector('[data-field="full_name"]').value.trim(),
    aliases: splitList(row.querySelector('[data-field="aliases"]').value),
    business_summary: row.querySelector('[data-field="business_summary"]').value.trim(),
    news_keywords: splitList(row.querySelector('[data-field="news_keywords"]').value),
    news_exclude_keywords: splitList(row.querySelector('[data-field="news_exclude_keywords"]').value)
  };
}

function syncRowsFromDom() {
  document.querySelectorAll('#rows tr[data-index]').forEach(row => {
    const index = Number(row.dataset.index);
    if (Number.isInteger(index) && index >= 0 && index < holdings.length) {
      holdings[index] = readRow(row, holdings[index] || {});
    }
  });
}

function currentRows() {
  syncRowsFromDom();
  return holdings.map(item => ({
    enabled: item.enabled !== false,
    symbol: String(item.symbol || '').trim(),
    name: String(item.name || '').trim(),
    full_name: String(item.full_name || '').trim(),
    aliases: splitList(Array.isArray(item.aliases) ? item.aliases.join('，') : item.aliases),
    business_summary: String(item.business_summary || '').trim(),
    news_keywords: splitList(Array.isArray(item.news_keywords) ? item.news_keywords.join('，') : item.news_keywords),
    news_exclude_keywords: splitList(Array.isArray(item.news_exclude_keywords) ? item.news_exclude_keywords.join('，') : item.news_exclude_keywords)
  }));
}

function renderTable(sync=true) {
  if (sync) syncRowsFromDom();
  const q = document.getElementById('filter').value.trim().toLowerCase();
  const body = document.getElementById('rows');
  body.innerHTML = '';
  let visible = 0;
  const hasFilter = !!q;
  holdings.forEach((item, index) => {
    const hay = JSON.stringify(item).toLowerCase();
    if (q && !hay.includes(q)) return;
    visible += 1;
    const tr = document.createElement('tr');
    tr.dataset.index = index;
    // 仅在未过滤时允许拖拽排序，避免过滤状态下拖拽打乱隐藏行的语义。
    tr.draggable = !hasFilter;
    tr.innerHTML = `
      <td class="sort-cell">
        <span class="drag-handle" title="拖动调整顺序"${hasFilter ? ' style="opacity:0.3"' : ''}>⠿</span>
        <button class="move-btn" onclick="moveRow(${index}, -1)" title="上移">↑</button>
        <button class="move-btn" onclick="moveRow(${index}, 1)" title="下移">↓</button>
      </td>
      <td class="enabled"><input data-field="enabled" type="checkbox" ${item.enabled !== false ? 'checked' : ''}></td>
      <td class="symbol"><input data-field="symbol" value="${escapeHtml(item.symbol || '')}"></td>
      <td class="name"><input data-field="name" value="${escapeHtml(item.name || '')}"></td>
      <td class="full"><textarea data-field="full_name">${escapeHtml(item.full_name || '')}</textarea></td>
      <td><textarea data-field="aliases">${escapeHtml(joinList(item.aliases))}</textarea></td>
      <td><textarea data-field="business_summary">${escapeHtml(item.business_summary || '')}</textarea></td>
      <td><textarea data-field="news_keywords">${escapeHtml(joinList(item.news_keywords))}</textarea></td>
      <td><textarea data-field="news_exclude_keywords">${escapeHtml(joinList(item.news_exclude_keywords))}</textarea></td>
      <td class="actions"><button class="danger" onclick="removeRow(${index})">删除</button></td>
    `;
    if (!hasFilter) {
      tr.addEventListener('dragstart', (ev) => {
        dragIndex = index;
        tr.classList.add('dragging');
        ev.dataTransfer.effectAllowed = 'move';
      });
      tr.addEventListener('dragend', () => {
        tr.classList.remove('dragging');
        clearDragMarkers();
      });
      tr.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        if (dragIndex === null || dragIndex === index) return;
        const rect = tr.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        clearDragMarkers();
        tr.classList.add(after ? 'drag-over-below' : 'drag-over-above');
      });
      tr.addEventListener('dragleave', () => {
        tr.classList.remove('drag-over-above', 'drag-over-below');
      });
      tr.addEventListener('drop', (ev) => {
        ev.preventDefault();
        if (dragIndex === null || dragIndex === index) return;
        const rect = tr.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        reorderHoldings(dragIndex, after ? index + 1 : index);
        clearDragMarkers();
      });
    }
    tr.addEventListener('input', () => {
      holdings[index] = readRow(tr, holdings[index] || {});
    });
    tr.addEventListener('change', () => {
      holdings[index] = readRow(tr, holdings[index] || {});
    });
    body.appendChild(tr);
  });
  document.getElementById('summary').textContent = `共 ${holdings.length} 只，显示 ${visible} 只`;
}

function clearDragMarkers() {
  document.querySelectorAll('#rows tr').forEach(tr => {
    tr.classList.remove('drag-over-above', 'drag-over-below');
  });
}

// 把 from 位置的持仓移动到 to 位置（to 是目标插入点的数组下标）。
function reorderHoldings(from, to) {
  if (from < 0 || from >= holdings.length) return;
  if (to < 0) to = 0;
  if (to > holdings.length) to = holdings.length;
  if (from === to || from + 1 === to) return;
  const moved = holdings.splice(from, 1)[0];
  const insertAt = to > from ? to - 1 : to;
  holdings.splice(insertAt, 0, moved);
  renderTable(false);
}

function moveRow(index, delta) {
  syncRowsFromDom();
  const target = index + delta;
  if (target < 0 || target >= holdings.length) return;
  const tmp = holdings[index];
  holdings[index] = holdings[target];
  holdings[target] = tmp;
  renderTable(false);
}

async function reloadData() {
  const operationId = beginHoldingsOperation('refreshing');
  if (!operationId) return;
  pendingPayload = null;
  pendingPreviewToken = '';
  showStatus('正在刷新持仓...', 'busy');
  try {
    const data = await api('/api/holdings');
    if (operationId !== holdingsOperationId) return;
    holdings = data.holdings || [];
    loadedHoldings = true;
    renderTable(false);
    showStatus('已加载持仓。');
  } catch (err) {
    if (operationId !== holdingsOperationId) return;
    showStatus(err.message, 'err');
  } finally {
    endHoldingsOperation(operationId);
  }
}

function addRow() {
  syncRowsFromDom();
  holdings.push({enabled: true, symbol: '', name: '', aliases: [], news_keywords: [], news_exclude_keywords: []});
  renderTable(false);
}

function removeRow(index) {
  if (!confirm('确认删除这只持仓？')) return;
  syncRowsFromDom();
  holdings.splice(index, 1);
  renderTable(false);
}

function openBatch() { document.getElementById('batchModal').style.display = 'flex'; }
function closeBatch() { document.getElementById('batchModal').style.display = 'none'; }
function closeDiff(force=false) {
  if (holdingsBusyMode === 'saving' && !force) return;
  document.getElementById('diffModal').style.display = 'none';
  if (!force) {
    pendingPayload = null;
    pendingPreviewToken = '';
  }
}

function parseBatchLine(line) {
  const parts = line.split(/[，,\t]+/).map(s => s.trim()).filter(Boolean);
  if (!parts.length) return null;
  const codeLike = value => /^(\d{6}(\.(SH|SZ|BJ))?|HK\d{1,5}|0?\d{4,5}\.HK)$/i.test(value);
  if (parts.length === 1) {
    const only = parts[0];
    if (codeLike(only)) return {symbol: only, name: only, enabled: true};
    return {symbol: '', name: only, enabled: true};
  }
  const [a, b] = parts;
  if (codeLike(a)) return {symbol: a, name: b, enabled: true};
  return {symbol: b, name: a, enabled: true};
}

function applyBatch() {
  syncRowsFromDom();
  const lines = document.getElementById('batchText').value.split(/\n+/);
  const parsed = lines.map(parseBatchLine).filter(Boolean);
  holdings.push(...parsed);
  document.getElementById('batchText').value = '';
  closeBatch();
  renderTable(false);
}

async function previewSave() {
  const operationId = beginHoldingsOperation('validating');
  if (!operationId) return;
  try {
    pendingPayload = currentRows();
    pendingPreviewToken = '';
    showStatus('正在校验待保存内容...', 'busy');
    const data = await api('/api/preview', {method: 'POST', body: JSON.stringify({holdings: pendingPayload})});
    if (operationId !== holdingsOperationId) return;
    // 后端 normalize_holdings_for_save 会通过新浪接口补全缺失的股票代码，
    // 这里用补全后的 holdings 回写数据和表格，让用户在预览阶段就能看到补全结果。
    if (Array.isArray(data.holdings) && data.holdings.length) {
      holdings = data.holdings;
      pendingPayload = data.holdings;
      renderTable(false);
    }
    pendingPreviewToken = String(data.preview_token || '');
    if (!pendingPreviewToken) throw new Error('保存预览缺少确认凭据，请重试。');
    const warnings = (data.warnings || []).map(item => `! ${item.message || item}`).join('\n');
    const remoteCount = Number(data.remote_checked_count || 0);
    const validationSummary = remoteCount
      ? `联网名称校验：${remoteCount} 只身份有变化的持仓。`
      : '联网名称校验：无需执行（代码、简称和别名均未变化）。';
    document.getElementById('diffText').textContent = [validationSummary, warnings ? `校验提醒：\n${warnings}` : '', data.diff_text || '没有变化。'].filter(Boolean).join('\n\n');
    document.getElementById('diffModal').style.display = 'flex';
    showStatus('校验完成，请确认保存。');
  } catch (err) {
    if (operationId !== holdingsOperationId) return;
    pendingPayload = null;
    pendingPreviewToken = '';
    showStatus(err.message, 'err');
  } finally {
    endHoldingsOperation(operationId);
  }
}

async function confirmSave() {
  if (!pendingPayload || !pendingPreviewToken) {
    showStatus('保存预览已失效，请重新点击保存。', 'err');
    closeDiff(true);
    return;
  }
  const operationId = beginHoldingsOperation('saving');
  if (!operationId) return;
  showStatus('正在保存并同步持仓...', 'busy');
  try {
    const data = await api('/api/save', {method: 'POST', body: JSON.stringify({holdings: pendingPayload, preview_token: pendingPreviewToken})});
    if (operationId !== holdingsOperationId) return;
    closeDiff(true);
    const headline = data.no_change
      ? '配置与 SQLite 均为最新，无需重复写入。'
      : (data.sync_repaired ? '配置已存在，SQLite 同步已补齐。' : '保存成功。');
    const countLabel = data.no_change ? '当前持仓' : 'SQLite 持仓';
    showStatus(`${headline}\n备份：${data.backup_path || '无'}\n${countLabel}：${data.imported_count} 只。`);
    holdings = data.holdings || holdings;
    pendingPayload = null;
    pendingPreviewToken = '';
    renderTable();
  } catch (err) {
    if (operationId !== holdingsOperationId) return;
    document.getElementById('diffText').textContent = `保存失败：${err.message}\n\n请取消后重新预览；如果只是临时错误，也可以再次确认。`;
    showStatus(err.message, 'err');
  } finally {
    endHoldingsOperation(operationId);
  }
}

initializeMultiSelect('marketSource', [], loadMarketItems);
initializeMultiSelect('marketFeedback', [
  {value: 'high_value', label: '特别有用'},
  {value: 'duplicate', label: '重复'},
  {value: 'invalid', label: '无效'},
  {value: 'unlabelled', label: '未反馈'}
], loadMarketItems);
initializeMultiSelect('llmDecisionAction', [
  {value: 'push', label: 'push'},
  {value: 'daily', label: 'daily'},
  {value: 'archive', label: 'archive'}
], loadLlmDecisions);
initializeMultiSelect('llmDecisionStatus', [
  {value: 'completed', label: '已完成'},
  {value: 'insufficient_evidence', label: '证据不足'},
  {value: 'uncertain', label: '历史 uncertain'},
  {value: 'model_unavailable', label: '大模型不可用'},
  {value: 'pending', label: '等待判断'}
], loadLlmDecisions);
initializeMultiSelect('llmDecisionSource', [], loadLlmDecisions);
initializeMultiSelect('llmRuleFamily', [], renderLlmRules);
initializeMultiSelect('llmRuleAction', [
  {value: 'push', label: 'push'},
  {value: 'daily', label: 'daily'},
  {value: 'archive', label: 'archive'}
], renderLlmRules);
initializeMultiSelect('sourceProfileCategory', [], renderSourceProfiles);

document.getElementById('marketFromDate').value = todayString();
document.getElementById('marketToDate').value = todayString();
showView('overview');
loadHealthSummary();
setInterval(() => {
  if (!document.hidden) loadHealthSummary();
}, 60000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadHealthSummary();
});
