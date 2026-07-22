let token = localStorage.getItem('surveil_holdings_token') || '';
let holdings = [];
let pendingPayload = null;
let pendingPreviewToken = '';
let loadedHoldings = false;
let holdingsOperationId = 0;
let holdingsBusyMode = '';
// 拖拽排序时记录被拖动行的原始下标，null 表示当前未拖动。
let dragIndex = null;
let codeDefaultKeywords = [];
let managedRelations = [];
let editingRelationId = null;
let signalRowsCache = [];
let editingSignalFeedback = null;
let sourceProfileCache = {categories: [], profiles: []};
let ruleCenterCache = {rules: []};
let eventSourceOptionsLoaded = false;
let ruleShadowReportCache = {items: []};

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
  const showShadow = Boolean(document.getElementById('showShadowUnits')?.checked);
  const showLegacy = Boolean(document.getElementById('showLegacyUnits')?.checked);
  const allTasks = tasks || [];
  const visibleTasks = allTasks.filter(task => {
    if (task.lifecycle === 'shadow' && !showShadow) return false;
    if (task.lifecycle === 'legacy_cutover' && !showLegacy) return false;
    return true;
  });
  const hiddenShadow = allTasks.filter(task => task.lifecycle === 'shadow').length;
  const hiddenLegacy = allTasks.filter(task => task.lifecycle === 'legacy_cutover').length;
  const summary = document.getElementById('healthUnitSummary');
  if (summary) {
    const parts = [`展示 ${visibleTasks.length} / ${allTasks.length} 个逻辑任务`];
    if (!showShadow && hiddenShadow) parts.push(`隐藏影子任务 ${hiddenShadow} 个`);
    if (!showLegacy && hiddenLegacy) parts.push(`隐藏历史兼容任务 ${hiddenLegacy} 个`);
    summary.textContent = parts.join('；');
  }
  const order = ['fetching_persistent', 'fetching_scheduled', 'processing_scheduled', 'infrastructure', 'fetching_shadow', 'fetching_legacy', 'other'];
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
  if (name === 'events') loadEventsView();
  if (name === 'feedback') loadFeedbackQuality();
  if (name === 'signals') loadSignals();
  if (name === 'relations') loadRelationManager();
  if (name === 'sources') {
    loadSourceProfiles();
    loadHealthSummary();
  }
  if (name === 'health') loadHealth();
  if (name === 'keywords') loadKeywords();
  if (name === 'rules') loadRuleCenter();
  if (name === 'rule-shadow') loadRuleShadowReports();
  if (name === 'settings') {
    loadSettings();
  }
  if (name === 'holdings' && !loadedHoldings) reloadData();
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

function eventSourceFilterValue(profile) {
  if (profile.id === 'x_serenity') return 'x:serenity';
  return String(profile.id || '').trim();
}

async function loadEventSourceOptions() {
  if (eventSourceOptionsLoaded) return;
  const select = document.getElementById('eventSource');
  const selected = select.value;
  let data = sourceProfileCache;
  if (!Array.isArray(data.profiles) || !data.profiles.length) {
    data = await api('/api/source-profiles');
    sourceProfileCache = data;
  }
  const groups = new Map();
  (data.profiles || []).forEach(profile => {
    const value = eventSourceFilterValue(profile);
    if (!value) return;
    const label = profile.category_label || '其他来源';
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push({value, profile});
  });
  select.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = '全部来源';
  select.appendChild(all);
  groups.forEach((items, label) => {
    const group = document.createElement('optgroup');
    group.label = label;
    items.forEach(({value, profile}) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${profile.name || value}（${value}）${profile.enabled === false ? ' - 已停用' : ''}`;
      group.appendChild(option);
    });
    select.appendChild(group);
  });
  if ([...select.options].some(option => option.value === selected)) {
    select.value = selected;
  }
  eventSourceOptionsLoaded = true;
}

async function loadEventsView() {
  try {
    await loadEventSourceOptions();
  } catch (err) {
    showStatus(`来源下拉加载失败：${err.message}`, 'err');
  }
  await loadEvents();
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
    breakdown.push('<div class="list-row"><strong>文章重要性</strong></div>');
    (data.article_importance || []).forEach(item => breakdown.push(`<div class="list-row">${badge(item.key)} <span class="summary">${item.count}</span></div>`));
    breakdown.push('<div class="list-row"><strong>飞书状态</strong></div>');
    (data.deliveries || []).forEach(item => breakdown.push(`<div class="list-row">${escapeHtml(item.key)} <span class="summary">${item.count}</span></div>`));
    document.getElementById('overviewBreakdown').innerHTML = breakdown.join('') || '<div class="list-row">暂无统计。</div>';
    document.getElementById('overviewLatest').innerHTML = ['<div class="list-row"><strong>最近事件</strong></div>', ...(data.latest || []).map(item => `
      <div class="list-row">
        <div>${badge(item.importance)} <strong>${escapeHtml(shortText(item.title, 120))}</strong></div>
        <div class="hint">${escapeHtml(item.source)} / ${escapeHtml(item.kind)} / ${formatTime(item.seen_at)}</div>
      </div>
    `)].join('');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadEvents() {
  try {
    const params = new URLSearchParams();
    const startDate = document.getElementById('eventFromDate').value;
    const endDate = document.getElementById('eventToDate').value;
    const timeBasis = document.getElementById('eventTimeBasis').value;
    const source = document.getElementById('eventSource').value.trim();
    const feedback = document.getElementById('eventFeedback').value.trim();
    const q = document.getElementById('eventQuery').value.trim();
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
    if (source) params.set('source', source);
    if (feedback) params.set('feedback', feedback);
    if (q) params.set('q', q);
    if (document.getElementById('eventIncludeBaseline').checked) params.set('include_baseline', '1');
    const data = await api('/api/events?' + params.toString());
    document.getElementById('eventTimeHeader').textContent = timeBasis === 'published' ? '原文发布时间' : '采集/处理时间';
    const feedbackSummary = data.feedback_summary || {};
    document.getElementById('eventFeedbackSummary').innerHTML = [
      `可反馈且已投递 ${feedbackSummary.delivered || 0}`,
      `已反馈 ${feedbackSummary.labelled || 0}`,
      `特别有用 ${feedbackSummary.high_value || 0}`,
      `重复 ${feedbackSummary.duplicate || 0}`,
      `无效 ${feedbackSummary.invalid || 0}`,
    ].map(text => `<span>${escapeHtml(text)}</span>`).join('');
    const rows = document.getElementById('eventRows');
    rows.innerHTML = (data.events || []).map(item => `
      <tr>
        <td>${formatTime(timeBasis === 'published' ? (item.published_at || item.seen_at) : (item.seen_at || item.published_at))}${item.published_at && timeBasis !== 'published' ? `<div class="hint">原文：${formatTime(item.published_at)}</div>` : ''}${item.seen_at && timeBasis === 'published' ? `<div class="hint">采集：${formatTime(item.seen_at)}</div>` : ''}</td>
        <td>${escapeHtml(item.source || '')}</td>
        <td>${escapeHtml(item.kind || '')}${item.baseline_only ? '<div class="hint">基线</div>' : ''}</td>
        <td class="summary-cell">
          <div><strong>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || '')}</a>` : escapeHtml(item.title || '')}</strong></div>
          <div>${escapeHtml(shortText(item.summary || '', 220))}</div>
        </td>
        <td>${badge(item.importance)}<div class="hint">${escapeHtml(item.classification || '')}</div></td>
        <td>${escapeHtml(item.delivery_status || '')}${item.push ? '<div class="hint">push</div>' : ''}</td>
        <td>${feedbackBadge(item)}</td>
      </tr>
    `).join('') || '<tr><td colspan="7">没有匹配事件。</td></tr>';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadSignals() {
  try {
    const params = new URLSearchParams();
    const source = document.getElementById('signalSource').value.trim();
    const symbol = document.getElementById('signalSymbol').value.trim();
    const verdict = document.getElementById('signalVerdict').value.trim();
    const importance = document.getElementById('signalImportance').value.trim();
    const q = document.getElementById('signalQuery').value.trim();
    if (source) params.set('source', source);
    if (symbol) params.set('symbol', symbol);
    if (verdict) params.set('verdict', verdict);
    if (importance) params.set('importance', importance);
    if (q) params.set('q', q);
    const data = await api('/api/signals?' + params.toString());
    document.getElementById('signalMetrics').innerHTML = ((data.summary || {}).cards || []).map(item => `
      <div class="metric">
        <div class="label">${escapeHtml(item.label)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </div>
    `).join('');
    signalRowsCache = data.signals || [];
    document.getElementById('signalRows').innerHTML = signalRowsCache.map((item, index) => {
      const returns = item.returns || {};
      const returnText = [`1d ${formatPct(returns['1d'])}`, `3d ${formatPct(returns['3d'])}`, `5d ${formatPct(returns['5d'])}`].join('<br>');
      const title = item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || '')}</a>` : escapeHtml(item.title || '');
      return `
        <tr>
          <td>${badge(item.verdict || item.outcome_status || '-')}<div class="hint">${escapeHtml(item.error_type || '')}</div><div class="hint">${escapeHtml(item.review_type || '')}</div></td>
          <td><strong>${escapeHtml(item.symbol || item.name || '-')}</strong><div class="hint">${escapeHtml(item.name || '')}</div></td>
          <td>${returnText}<div class="hint">runup ${formatPct(item.max_runup)} / dd ${formatPct(item.max_drawdown)}</div></td>
          <td class="summary-cell">
            <div><strong>${title}</strong></div>
            <div>${escapeHtml(shortText(item.thesis || '', 180))}</div>
            <div class="hint">${escapeHtml(shortText(item.review_text || '', 220))}</div>
          </td>
          <td>${escapeHtml(item.source || '')}<div>${badge(item.importance || '')}</div><div class="hint">${formatTime(item.created_at)}</div></td>
          <td>${escapeHtml(item.target_role || '')}<div class="hint">${escapeHtml(shortText(item.relation_type || item.relation_reason || '', 120))}</div></td>
          <td><button onclick="openSignalFeedback(${index})">修正</button></td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="7">没有匹配信号。</td></tr>';
    const scores = ((data.summary || {}).source_scores || []);
    document.getElementById('signalSourceScores').innerHTML = ['<div class="list-row"><strong>来源评分（近 30 日）</strong></div>', ...scores.map(item => `
      <div class="list-row">
        <strong>${escapeHtml(item.source || '')}</strong>
        <span class="summary">样本 ${item.signal_count} / 命中 ${formatRate(item.hit_rate)} / 未兑现 ${formatRate(item.false_positive_rate)}</span>
        <div class="hint">平均方向收益：${escapeHtml(item.avg_excess_return ?? '-')}</div>
      </div>
    `)].join('');
    await loadRelations();
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function openSignalFeedback(index) {
  const item = signalRowsCache[index];
  if (!item) return;
  editingSignalFeedback = item;
  document.getElementById('signalFeedbackVerdict').value = item.verdict || 'miss';
  document.getElementById('signalFeedbackErrorType').value = item.error_type || 'stale_or_price_in';
  document.getElementById('signalFeedbackText').value = item.review_text || '';
  let lessons = '';
  try {
    const parsed = item.lessons_json ? JSON.parse(item.lessons_json) : {};
    if (Array.isArray(parsed.lessons)) lessons = parsed.lessons.join('\n');
  } catch (err) {}
  document.getElementById('signalFeedbackLessons').value = lessons;
  document.getElementById('signalFeedbackMeta').textContent = `${item.symbol || '-'} / ${item.title || ''}`;
  document.getElementById('signalFeedbackModal').style.display = 'flex';
}

function closeSignalFeedback() {
  editingSignalFeedback = null;
  document.getElementById('signalFeedbackModal').style.display = 'none';
}

async function saveSignalFeedback() {
  if (!editingSignalFeedback) return;
  try {
    const payload = {
      signal_id: editingSignalFeedback.id,
      target_id: editingSignalFeedback.target_id || null,
      symbol: editingSignalFeedback.symbol || '',
      verdict: document.getElementById('signalFeedbackVerdict').value,
      error_type: document.getElementById('signalFeedbackErrorType').value,
      review_text: document.getElementById('signalFeedbackText').value.trim(),
      lessons: document.getElementById('signalFeedbackLessons').value.trim()
    };
    await api('/api/signal-feedback', {method: 'POST', body: JSON.stringify(payload)});
    closeSignalFeedback();
    await loadSignals();
    showStatus('已保存人工复盘反馈。');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadRelations() {
  try {
    const params = new URLSearchParams();
    const q = document.getElementById('relationQuery') ? document.getElementById('relationQuery').value.trim() : '';
    if (q) params.set('q', q);
    const data = await api('/api/signal-relations?' + params.toString());
    document.getElementById('relationRows').innerHTML = (data.relations || []).map(item => `
      <tr>
        <td><strong>${escapeHtml(item.symbol || '')}</strong><div class="hint">${escapeHtml(item.symbol_name || '')}</div></td>
        <td><strong>${escapeHtml(item.related_symbol || '')}</strong><div class="hint">${escapeHtml(item.related_name || '')}</div></td>
        <td>${badge(item.impact_direction || '')}<div class="hint">${escapeHtml(item.confidence || '')}</div></td>
        <td class="summary-cell">
          <div>${escapeHtml(item.relation_type || '')} / ${escapeHtml(item.theme || '')}</div>
          <div class="hint">${escapeHtml(shortText(item.reason || '', 180))}</div>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="4">暂无关系配置。可复制 config/stock_relations.example.json 为私有 config/stock_relations.json 后导入。</td></tr>';
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
    await loadRelationSuggestions();
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

async function backfillRelations() {
  if (!confirm('确认重跑最近 N 天信号抽取？这会按当前关系映射补充 related_stock。')) return;
  try {
    const days = Number(document.getElementById('relationBackfillDays').value || 7);
    const data = await api('/api/relations/backfill', {method: 'POST', body: JSON.stringify({days})});
    showStatus(`回填完成：最近 ${data.days} 天，${JSON.stringify(data.counts)}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadRelationSuggestions() {
  try {
    const status = document.getElementById('relationSuggestionStatus') ? document.getElementById('relationSuggestionStatus').value : 'pending';
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    const data = await api('/api/relation-suggestions?' + params.toString());
    document.getElementById('relationSuggestionRows').innerHTML = (data.suggestions || []).map(item => `
      <tr>
        <td>${badge(item.status || '')}<div class="hint">${formatTime(item.updated_at)}</div></td>
        <td><strong>${escapeHtml(item.symbol || '')}</strong><div class="hint">${escapeHtml(item.symbol_name || '')}</div></td>
        <td><strong>${escapeHtml(item.related_symbol || '')}</strong><div class="hint">${escapeHtml(item.related_name || '')}</div></td>
        <td class="summary-cell">
          <div>${escapeHtml(item.relation_type || '')} / ${escapeHtml(item.theme || '')} / ${escapeHtml(item.confidence || '')}</div>
          <div class="hint">${escapeHtml(shortText(item.reason || '', 220))}</div>
        </td>
        <td>
          ${item.status === 'pending' ? `<button onclick="acceptSuggestion(${item.id})">确认</button><button class="danger" onclick="rejectSuggestion(${item.id})">拒绝</button>` : '-'}
        </td>
      </tr>
    `).join('') || '<tr><td colspan="5">暂无候选关系。</td></tr>';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function acceptSuggestion(id) {
  try {
    const data = await api('/api/relation-suggestions/accept', {method: 'POST', body: JSON.stringify({id})});
    await loadRelationManager();
    showStatus(`候选关系已确认并同步 JSON 快照：${(data.snapshot || {}).path || ''}`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function rejectSuggestion(id) {
  try {
    await api('/api/relation-suggestions/reject', {method: 'POST', body: JSON.stringify({id})});
    await loadRelationSuggestions();
    showStatus('候选关系已拒绝。');
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
  const select = document.getElementById('sourceProfileCategory');
  const current = select.value;
  select.innerHTML = '<option value="">全部来源</option>' + (categories || []).map(item => `
    <option value="${escapeHtml(item.id || '')}">${escapeHtml(item.label || item.id || '')}（${escapeHtml(item.count || 0)}）</option>
  `).join('');
  select.value = current;
}

function sourceProfileSearchText(item) {
  return [
    item.category_label, item.name, item.id, item.source_type, item.fetch_range,
    item.filter_policy, item.frequency, item.runtime_shape, item.pipeline,
    item.fetcher, item.publisher_role, item.tavily_policy, item.proxy_profile, item.text_length_policy,
    item.provider, item.operation_mode,
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
    frequency: item.frequency || '',
    publisher_role: item.publisher_role || '',
    skeptic_enabled: Boolean(item.skeptic_enabled),
    web_evidence_enabled: Boolean(item.web_evidence_enabled),
    proxy_profile: item.proxy_profile || '',
    provider: item.provider || '',
    operation_mode: item.operation_mode || '',
    notes: item.notes || ''
  }));
}

function isFailingSourceProfile(item) {
  return item.enabled !== false && item.health_status === 'failing';
}

function renderSourceProfiles() {
  const category = document.getElementById('sourceProfileCategory').value;
  const q = document.getElementById('sourceProfileQuery').value.trim().toLowerCase();
  const rows = (sourceProfileCache.profiles || []).filter(item => {
    if (category && item.category !== category) return false;
    if (q && !sourceProfileSearchText(item).includes(q)) return false;
    return true;
  }).sort((left, right) => Number(isFailingSourceProfile(right)) - Number(isFailingSourceProfile(left)));
  document.getElementById('sourceProfileRows').innerHTML = rows.map(item => {
    const health = item.health_status === 'unknown' ? '未记录' : item.health_status;
    const isFailing = isFailingSourceProfile(item);
    const healthDetail = item.last_error ? `<div class="hint">${escapeHtml(shortText(item.last_error, 120))}</div>` : '';
    const healthTime = isFailing && item.last_failure_at ? `<div class="hint">最近失败：${escapeHtml(formatTime(item.last_failure_at))}</div>` : '';
    const gates = [
      item.skeptic_enabled ? 'Skeptic' : '无 Skeptic',
      item.web_evidence_enabled ? 'Tavily 可触发' : '无 Tavily'
    ].join('<br>');
    const services = (item.service_units || []).map(unit => `<span class="badge">${escapeHtml(unit)}</span>`).join(' ');
    const modified = item.config_modified ? '<div class="hint source-dirty">本地覆盖</div>' : '';
    const enabledChecked = item.enabled !== false ? 'checked' : '';
    const skepticChecked = item.skeptic_enabled ? 'checked' : '';
    const evidenceChecked = item.web_evidence_enabled ? 'checked' : '';
    const providerControls = item.provider ? `
      <div style="margin-top:6px">
        <div class="hint">采集 provider</div>
        <input class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="provider" value="${escapeHtml(item.provider || '')}" oninput="updateSourceProfileDraft(this)">
      </div>
      <div style="margin-top:6px">
        <div class="hint">运行模式</div>
        <select class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="operation_mode" onchange="updateSourceProfileDraft(this)">
          <option value="report_only" ${item.operation_mode === 'report_only' ? 'selected' : ''}>只报告（不决策/不投递）</option>
          <option value="live" ${item.operation_mode === 'live' ? 'selected' : ''}>正式运行</option>
        </select>
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
          <input class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="frequency" value="${escapeHtml(item.frequency || '')}" oninput="updateSourceProfileDraft(this)">
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
        <td>
          <div class="source-checks">
            <label><input type="checkbox" data-source-id="${escapeHtml(item.id || '')}" data-field="skeptic_enabled" onchange="updateSourceProfileDraft(this)" ${skepticChecked}> Skeptic</label>
            <label><input type="checkbox" data-source-id="${escapeHtml(item.id || '')}" data-field="web_evidence_enabled" onchange="updateSourceProfileDraft(this)" ${evidenceChecked}> Tavily</label>
          </div>
          <div class="hint">${gates}</div>
          <div class="hint">${escapeHtml(item.tavily_policy || '')}</div>
        </td>
        <td class="summary-cell">
          <div>${escapeHtml(item.fetch_range || '')}</div>
          <div class="hint">${escapeHtml(item.filter_policy || '')}</div>
          <div class="hint">${escapeHtml(item.fetcher || '')}</div>
          <div class="hint">${services}</div>
          ${providerControls}
          <div style="margin-top:6px">
            <input class="source-control" data-source-id="${escapeHtml(item.id || '')}" data-field="proxy_profile" value="${escapeHtml(item.proxy_profile || '')}" oninput="updateSourceProfileDraft(this)">
          </div>
          <textarea class="source-notes" data-source-id="${escapeHtml(item.id || '')}" data-field="notes" oninput="updateSourceProfileDraft(this)">${escapeHtml(item.notes || '')}</textarea>
        </td>
      </tr>
    `;
  }).join('') || '<tr><td colspan="8">没有匹配信息源。</td></tr>';
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
    renderSourceProfileMetrics(sourceProfileCache.categories);
    renderSourceCategoryOptions(sourceProfileCache.categories);
    renderSourceProfiles();
    setSourceProfileDirty(false);
    const hint = document.getElementById('sourceProfileConfigHint');
    if (hint) {
      hint.textContent = `${data.runtime_note || '已读取信息源实际运行配置。'} 配置文件：${sourceProfileCache.config_path || '-'}；已存在本地覆盖配置。`;
    }
    const saved = data.save_result || {};
    showStatus(`信息源配置已保存：停用 ${saved.disabled_count || 0} 个，覆盖 ${saved.override_count || 0} 个。页面已按实际运行配置刷新；频率/代理暂仅记录。`);
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

function sameKeywordList(a, b) {
  const left = (a || []).map(item => String(item || '').trim()).filter(Boolean);
  const right = (b || []).map(item => String(item || '').trim()).filter(Boolean);
  if (left.length !== right.length) return false;
  return left.every((item, index) => item === right[index]);
}

async function loadKeywords() {
  try {
    const data = await api('/api/media-keywords');
    codeDefaultKeywords = data.code_default_keywords || data.default_keywords || [];
    document.getElementById('baseKeywords').value = keywordListToText(data.base_keywords || data.default_keywords || []);
    document.getElementById('includeKeywords').value = keywordListToText(data.include_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('baseOverrideStatus').textContent = data.base_keywords_overridden ? '已自定义' : '使用代码默认';
    document.getElementById('defaultKeywords').innerHTML = codeDefaultKeywords.map(item => `<span class="badge" style="margin:2px">${escapeHtml(item)}</span>`).join('');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function resetBaseKeywords() {
  document.getElementById('baseKeywords').value = keywordListToText(codeDefaultKeywords);
  showStatus('已把基础关键词恢复为代码默认词，点击保存后生效。');
}

async function saveKeywords() {
  try {
    const baseKeywords = keywordTextToList(document.getElementById('baseKeywords').value);
    const payload = {
      base_keywords: sameKeywordList(baseKeywords, codeDefaultKeywords) ? [] : baseKeywords,
      include_keywords: keywordTextToList(document.getElementById('includeKeywords').value),
      exclude_keywords: keywordTextToList(document.getElementById('excludeKeywords').value)
    };
    const data = await api('/api/media-keywords', {method: 'POST', body: JSON.stringify(payload)});
    codeDefaultKeywords = data.code_default_keywords || data.default_keywords || codeDefaultKeywords;
    document.getElementById('baseKeywords').value = keywordListToText(data.base_keywords || data.default_keywords || []);
    document.getElementById('includeKeywords').value = keywordListToText(data.include_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('baseOverrideStatus').textContent = data.base_keywords_overridden ? '已自定义' : '使用代码默认';
    showStatus(`媒体关键词已保存。基础 ${(data.base_keywords || data.default_keywords || []).length} 个，额外包含 ${(data.include_keywords || []).length} 个，排除 ${(data.exclude_keywords || []).length} 个。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function ruleFieldId(ruleId, fieldKey) {
  return `rule-${ruleId}-${fieldKey}`;
}

function renderRuleField(rule, field) {
  const id = ruleFieldId(rule.id, field.key);
  const help = field.help ? `<div class="hint">${escapeHtml(field.help)}</div>` : '';
  if (field.type === 'bool') {
    return `
      <div class="setting-field">
        <label><span>${escapeHtml(field.label || field.key)}</span></label>
        <label><input id="${escapeHtml(id)}" data-rule-id="${escapeHtml(rule.id)}" data-rule-key="${escapeHtml(field.key)}" data-rule-type="bool" type="checkbox" ${field.value ? 'checked' : ''}> 启用</label>
        ${help}
      </div>
    `;
  }
  if (field.type === 'list') {
    return `
      <div class="setting-field">
        <label><span>${escapeHtml(field.label || field.key)}</span></label>
        <textarea id="${escapeHtml(id)}" data-rule-id="${escapeHtml(rule.id)}" data-rule-key="${escapeHtml(field.key)}" data-rule-type="list" style="min-height:72px" placeholder="每行一个">${escapeHtml(keywordListToText(field.value || []))}</textarea>
        ${help}
      </div>
    `;
  }
  return `
    <div class="setting-field">
      <label><span>${escapeHtml(field.label || field.key)}</span></label>
      <input id="${escapeHtml(id)}" data-rule-id="${escapeHtml(rule.id)}" data-rule-key="${escapeHtml(field.key)}" data-rule-type="int" type="number" value="${escapeHtml(field.value ?? '')}" min="${escapeHtml(field.min ?? '')}" max="${escapeHtml(field.max ?? '')}">
      ${help}
    </div>
  `;
}

function renderRuleCenter() {
  const rules = ruleCenterCache.rules || [];
  const total = rules.length;
  const enabled = rules.filter(rule => (rule.fields || []).find(field => field.key === 'enabled')?.value !== false).length;
  const recent = rules.reduce((sum, rule) => sum + Number((rule.stats || {}).matches_30d || 0), 0);
  document.getElementById('ruleCenterMetrics').innerHTML = [
    {label: '硬规则', value: total, hint: '代码定义的确定性规则'},
    {label: '当前启用', value: enabled, hint: '可在本页启停'},
    {label: '近 30 天命中', value: recent, hint: '按规则命中 JSON 汇总'}
  ].map(item => `<section class="metric"><div class="label">${escapeHtml(item.label)}</div><div class="value">${escapeHtml(item.value)}</div><div class="hint">${escapeHtml(item.hint)}</div></section>`).join('');
  document.getElementById('ruleCenterRows').innerHTML = rules.map(rule => {
    const stats = rule.stats || {};
    const last = stats.last_match || {};
    const fields = rule.fields || [];
    const left = fields.slice(0, Math.ceil(fields.length / 2));
    const right = fields.slice(Math.ceil(fields.length / 2));
    return `
      <section class="panel" style="margin-top:12px">
        <div class="section-title">
          <div>
            <h3 style="margin:0">${escapeHtml(rule.name || rule.id || '')}</h3>
            <div class="hint">${escapeHtml(rule.group || '')} / ${escapeHtml(rule.runtime || '')}</div>
          </div>
          <div>${badge(rule.execution_mode_label || rule.execution_mode || '')} ${badge('近30天 ' + String(stats.matches_30d || 0) + ' 次')}</div>
        </div>
        <div class="summary-cell">${escapeHtml(rule.description || '')}</div>
        <div class="settings-grid" style="margin-top:10px">
          <section class="settings-card">${left.map(field => renderRuleField(rule, field)).join('')}</section>
          <section class="settings-card">
            ${right.map(field => renderRuleField(rule, field)).join('')}
            <div class="hint" style="margin-top:12px">最近命中：${last.title ? escapeHtml(shortText(last.title, 160)) : '暂无'}${last.published_at ? '；' + escapeHtml(formatTime(last.published_at)) : ''}</div>
          </section>
        </div>
      </section>
    `;
  }).join('') || '<section class="panel">暂无规则定义。</section>';
}

async function loadRuleCenter() {
  try {
    const data = await api('/api/rule-center');
    ruleCenterCache = data;
    renderRuleCenter();
    document.getElementById('ruleCenterHint').textContent =
      `${data.runtime_note || ''} 私有覆盖：${data.config_path || '-'}；${data.has_local_override ? '已存在覆盖' : '当前使用代码默认'}。`;
    await loadRuleAudit();
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function ruleCenterPayloadFromDom() {
  const rules = {};
  (ruleCenterCache.rules || []).forEach(rule => { rules[rule.id] = {}; });
  document.querySelectorAll('[data-rule-id][data-rule-key]').forEach(input => {
    const ruleId = input.dataset.ruleId;
    const key = input.dataset.ruleKey;
    const type = input.dataset.ruleType;
    if (!rules[ruleId]) rules[ruleId] = {};
    if (type === 'bool') rules[ruleId][key] = Boolean(input.checked);
    else if (type === 'list') rules[ruleId][key] = keywordTextToList(input.value);
    else rules[ruleId][key] = Number(input.value || 0);
  });
  return {rules};
}

async function saveRuleCenter() {
  try {
    const data = await api('/api/rule-center', {method: 'POST', body: JSON.stringify(ruleCenterPayloadFromDom())});
    ruleCenterCache = data;
    renderRuleCenter();
    await loadRuleAudit();
    showStatus('规则中心配置已保存并写入审计记录。新资讯会动态读取，无需重启服务。');
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadRuleAudit() {
  try {
    const data = await api('/api/rule-center/audit');
    document.getElementById('ruleAuditRows').innerHTML = (data.items || []).map(item => {
      const changes = (item.changes || []).map(change => {
        const rule = (ruleCenterCache.rules || []).find(row => row.id === change.rule_id);
        return `<div><strong>${escapeHtml((rule || {}).name || change.rule_id || '')}</strong>：${escapeHtml(shortText(JSON.stringify(change.after || {}), 220))}</div>`;
      }).join('');
      return `<tr><td>${escapeHtml(formatTime(item.changed_at || ''))}</td><td>${escapeHtml(item.actor || '')}</td><td class="summary-cell">${changes || '-'}</td></tr>`;
    }).join('') || '<tr><td colspan="3">暂无规则修改记录。</td></tr>';
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function runRuleSimulation() {
  try {
    const days = Number(document.getElementById('ruleSimulationDays').value || 7);
    const data = await api('/api/rule-center/simulate', {method: 'POST', body: JSON.stringify({days})});
    document.getElementById('ruleSimulationRows').innerHTML = (data.results || []).map(item => {
      const matches = (item.matches || []).map(match => `<div><strong>${escapeHtml(match.name || match.rule_id || '')}</strong><div class="hint">${escapeHtml(shortText(match.reason || '', 180))}</div></div>`).join('');
      return `<tr><td>${escapeHtml(formatTime(item.published_at || ''))}</td><td>${escapeHtml(item.source || '')}</td><td class="summary-cell"><strong>${escapeHtml(item.title || '')}</strong><div style="margin-top:6px">${matches}</div></td></tr>`;
    }).join('') || `<tr><td colspan="3">最近 ${data.days || days} 天扫描 ${data.scanned || 0} 条，没有命中当前硬规则。</td></tr>`;
    showStatus(`Dry-run 完成：扫描 ${data.scanned || 0} 条，命中 ${data.matched || 0} 条；未发送飞书。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function loadInvestmentBankThemeRules() {
  try {
    const data = await api('/api/investment-bank-theme-rules');
    document.getElementById('investmentBankThemeEnabled').checked = Boolean(data.enabled);
    document.getElementById('investmentBankThemeMinScore').value = data.min_evidence_score || 2;
    document.getElementById('investmentBankThemeDedupDays').value = data.dedup_lookback_days || 14;
    document.getElementById('investmentBankThemeSecondary').checked = Boolean(data.allow_secondary_sources);
    document.getElementById('investmentBankThemeBanks').value = keywordListToText(data.allowed_banks || []);
    document.getElementById('investmentBankThemeKeywords').value = keywordListToText(data.extra_theme_keywords || []);
    document.getElementById('investmentBankThemeActions').value = keywordListToText(data.extra_action_keywords || []);
    document.getElementById('investmentBankThemeRuleHint').textContent =
      `本地配置：${data.path || '-'}；${data.has_local_override ? '已存在本地覆盖' : '当前使用代码默认配置'}。`;
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

async function saveInvestmentBankThemeRules() {
  try {
    const payload = {
      enabled: document.getElementById('investmentBankThemeEnabled').checked,
      min_evidence_score: Number(document.getElementById('investmentBankThemeMinScore').value || 2),
      dedup_lookback_days: Number(document.getElementById('investmentBankThemeDedupDays').value || 14),
      allow_secondary_sources: document.getElementById('investmentBankThemeSecondary').checked,
      allowed_banks: keywordTextToList(document.getElementById('investmentBankThemeBanks').value),
      extra_theme_keywords: keywordTextToList(document.getElementById('investmentBankThemeKeywords').value),
      extra_action_keywords: keywordTextToList(document.getElementById('investmentBankThemeActions').value)
    };
    const data = await api('/api/investment-bank-theme-rules', {method: 'POST', body: JSON.stringify(payload)});
    await loadInvestmentBankThemeRules();
    showStatus(`国际投行重大主题策略已保存：最低证据分 ${data.min_evidence_score}，去重 ${data.dedup_lookback_days} 天。下一条新资讯会自动读取。`);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

function ruleShadowUsageText(usage) {
  if (!usage || typeof usage !== 'object') return '';
  const prompt = Number(usage.prompt_tokens || 0);
  const completion = Number(usage.completion_tokens || 0);
  const total = Number(usage.total_tokens || 0);
  if (!prompt && !completion && !total) return '';
  return `token：输入 ${prompt}，输出 ${completion}，合计 ${total}`;
}

function ruleShadowEvaluationStatusLabel(status) {
  return {
    completed: '已完成',
    not_admitted: '未通过范围准入',
    insufficient_input: '正文不足',
    model_unavailable: '大模型不可用',
    invalid_output: '大模型输出无效',
    evidence_invalid: '原文证据校验失败',
    conflict: '判断结果冲突'
  }[status] || '其他无法比较';
}

function ruleShadowComparisonStatusLabel(status) {
  return {
    action_compared: '已比较 action',
    both_not_admitted: '双方均未准入',
    admission_difference: '准入不一致',
    model_validation_failed: '大模型判断或校验失败'
  }[status] || '状态未记录';
}

function ruleShadowEngineLabel(engine) {
  if (String(engine || '').startsWith('llm_rule_decision')) return '大模型候选';
  if (engine === 'rule_core_v1') return '新规则候选';
  return engine || '对比判断';
}

function ruleShadowEvidence(item) {
  const evidence = Array.isArray(item.candidate_rule_evidence) ? item.candidate_rule_evidence : [];
  return evidence.slice(0, 8).map(row => {
    const ruleId = row && typeof row === 'object' ? row.rule_id || '' : '';
    const quote = row && typeof row === 'object' ? row.quote || '' : '';
    return quote ? `<div class="hint">原文证据：${escapeHtml(ruleId ? `${ruleId}：${quote}` : quote)}</div>` : '';
  }).join('');
}

function ruleShadowDecisionCell(item, prefix) {
  const action = item[`${prefix}_action`] || 'none';
  const importance = item[`${prefix}_importance`] || '';
  const reason = item[`${prefix}_reason`] || '';
  const rules = item[`${prefix}_rule_ids`] || [];
  const comparisonStatus = item.comparison_status || (item.comparable === false ? 'model_validation_failed' : 'action_compared');
  if (prefix === 'candidate' && comparisonStatus === 'model_validation_failed') {
    const status = ruleShadowEvaluationStatusLabel(item.evaluation_status || 'unknown');
    const failure = item.failure_reason || reason || '未记录失败原因';
    return `
      <div>${badge('无法比较')} ${badge(status)}</div>
      <div class="summary-cell" style="margin-top:6px">${escapeHtml(failure)}</div>
      <div class="hint">未生成候选 action</div>
    `;
  }
  if (prefix === 'candidate' && comparisonStatus === 'both_not_admitted') {
    return `
      <div>${badge(ruleShadowComparisonStatusLabel(comparisonStatus))} ${badge(ruleShadowEvaluationStatusLabel(item.evaluation_status || 'not_admitted'))}</div>
      <div class="summary-cell" style="margin-top:6px">${escapeHtml(reason || '双方均未通过范围准入')}</div>
      <div class="hint">未调用大模型，未生成候选 action</div>
    `;
  }
  if (prefix === 'candidate' && comparisonStatus === 'admission_difference' && action === 'none') {
    return `
      <div>${badge(ruleShadowComparisonStatusLabel(comparisonStatus))} ${badge(ruleShadowEvaluationStatusLabel(item.evaluation_status || 'not_admitted'))}</div>
      <div class="summary-cell" style="margin-top:6px">${escapeHtml(reason || '两套范围准入判断不一致')}</div>
      <div class="hint">未生成候选 action</div>
    `;
  }
  return `
    <div>${badge(action)} ${importance ? badge(importance) : ''} ${prefix === 'candidate' && comparisonStatus === 'admission_difference' ? badge('准入不一致') : ''}</div>
    <div class="summary-cell" style="margin-top:6px">${escapeHtml(reason || '未记录原因')}</div>
    <div class="hint">${rules.length ? escapeHtml(rules.join('，')) : '未命中规则'}</div>
    ${prefix === 'candidate' ? ruleShadowEvidence(item) : ''}
  `;
}

const ruleShadowActionRank = {none: 0, ignore: 1, archive: 2, daily: 3, push: 4};

function ruleShadowAction(item, prefix) {
  const action = String(item[`${prefix}_action`] || 'none').toLowerCase();
  return Object.prototype.hasOwnProperty.call(ruleShadowActionRank, action) ? action : 'none';
}

function ruleShadowChange(item) {
  if ((item.comparison_status || (item.comparable === false ? 'model_validation_failed' : 'action_compared')) !== 'action_compared') return 'unavailable';
  const current = ruleShadowAction(item, 'current');
  const candidate = ruleShadowAction(item, 'candidate');
  if (current === candidate) return 'same';
  return ruleShadowActionRank[candidate] > ruleShadowActionRank[current] ? 'upgrade' : 'downgrade';
}

const ruleShadowMultiSelectIds = [
  'ruleShadowComparisonStatus',
  'ruleShadowChange',
  'ruleShadowCurrentAction',
  'ruleShadowCandidateAction',
  'ruleShadowRuleVersion',
  'ruleShadowEvaluationStatus',
];

function ruleShadowSelectedValues(id) {
  const root = document.getElementById(id);
  return new Set(Array.from(root?.querySelectorAll('input[type="checkbox"]:checked') || []).map(input => input.value));
}

function ruleShadowFilterMatches(selected, value) {
  return selected.size === 0 || selected.has(value);
}

function updateRuleShadowMultiSelectLabel(id) {
  const root = document.getElementById(id);
  const summary = root?.querySelector('summary');
  if (!summary) return;
  const checked = Array.from(root.querySelectorAll('input[type="checkbox"]:checked'));
  if (!checked.length) {
    summary.textContent = summary.dataset.defaultLabel || '全部';
  } else if (checked.length === 1) {
    summary.textContent = checked[0].closest('label')?.textContent.trim() || checked[0].value;
  } else {
    summary.textContent = `已选 ${checked.length} 项`;
  }
}

function ruleShadowMultiSelectChanged(id) {
  updateRuleShadowMultiSelectLabel(id);
  renderRuleShadowRows();
}

function renderRuleShadowRows() {
  const items = Array.isArray(ruleShadowReportCache.items) ? ruleShadowReportCache.items : [];
  const comparisonStatuses = ruleShadowSelectedValues('ruleShadowComparisonStatus');
  const changes = ruleShadowSelectedValues('ruleShadowChange');
  const currentActions = ruleShadowSelectedValues('ruleShadowCurrentAction');
  const candidateActions = ruleShadowSelectedValues('ruleShadowCandidateAction');
  const ruleVersions = ruleShadowSelectedValues('ruleShadowRuleVersion');
  const evaluationStatuses = ruleShadowSelectedValues('ruleShadowEvaluationStatus');
  const filtered = items.filter(item =>
    ruleShadowFilterMatches(comparisonStatuses, item.comparison_status || (item.comparable === false ? 'model_validation_failed' : 'action_compared')) &&
    ruleShadowFilterMatches(changes, ruleShadowChange(item)) &&
    ruleShadowFilterMatches(currentActions, ruleShadowAction(item, 'current')) &&
    ruleShadowFilterMatches(candidateActions, ruleShadowAction(item, 'candidate')) &&
    ruleShadowFilterMatches(
      ruleVersions,
      (item.is_latest_candidate_version ?? item.is_latest_rule_core_version) === true ? 'latest' : 'earlier'
    ) &&
    ruleShadowFilterMatches(
      evaluationStatuses,
      ['completed', 'not_admitted', 'insufficient_input', 'model_unavailable', 'invalid_output', 'evidence_invalid', 'conflict'].includes(item.evaluation_status || 'unknown')
        ? (item.evaluation_status || 'unknown')
        : 'unknown'
    )
  );

  document.getElementById('ruleShadowFilterSummary').textContent = `显示 ${filtered.length} / ${items.length} 条`;
  document.getElementById('ruleShadowRows').innerHTML = filtered.map(item => {
    const safeUrl = safeExternalUrl(item.url);
    const title = safeUrl
      ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || '')}</a>`
      : escapeHtml(item.title || '');
    const candidateVersion = item.candidate_version || item.rule_core_version || '版本无法确认';
    const candidateEngine = ruleShadowEngineLabel(item.candidate_engine || 'rule_core_v1');
    const model = item.model ? `模型：${item.model}` : '';
    const provider = item.provider ? `服务地址：${item.provider}` : '';
    const usage = ruleShadowUsageText(item.usage);
    const elapsed = Number(item.elapsed_seconds || 0) > 0 ? `耗时：${Number(item.elapsed_seconds).toFixed(2)} 秒` : '';
    const inputFieldLabels = {title: '标题', summary: '摘要', full_text: '正文'};
    const providedFields = Array.isArray(item.provided_fields)
      ? item.provided_fields.map(field => inputFieldLabels[field] || field).join('、')
      : '';
    const inputFields = providedFields ? `判断依据：${providedFields}` : '';
    const originalBodyChars = Number(item.body_original_chars || 0);
    const providedBodyChars = Number(item.body_provided_chars || 0);
    const bodyInput = originalBodyChars > 0
      ? `正文：提供 ${providedBodyChars} / 原文 ${originalBodyChars} 字${item.body_truncated ? '（已截断）' : ''}`
      : '';
    const bodySource = item.body_source ? `正文来源：${item.body_source}` : '';
    const candidateMeta = [
      `判断方式：${candidateEngine}`,
      `版本：${candidateVersion}`,
      model,
      provider,
      inputFields,
      bodyInput,
      bodySource,
      Number(item.model_calls || 0) > 1 ? `大模型调用：${Number(item.model_calls)} 次（首次全部未命中后重新判断）` : '',
      usage,
      elapsed,
    ].filter(Boolean);
    return `
      <tr>
        <td>${escapeHtml(item.source || '')}<div class="hint">${escapeHtml(item.source_group || '')}</div><div class="hint">${escapeHtml(formatTime(item.comparison_generated_at || ''))}</div></td>
        <td><strong>${title}</strong><div class="hint">${escapeHtml(item.item_id || '')}</div></td>
        <td>${ruleShadowDecisionCell(item, 'current')}</td>
        <td>${ruleShadowDecisionCell(item, 'candidate')}<div class="hint" style="margin-top:6px">${candidateMeta.map(escapeHtml).join('<br>')}</div></td>
      </tr>
    `;
  }).join('') || '<tr><td colspan="4">没有符合当前筛选条件的文章。</td></tr>';
}

function resetRuleShadowFilters() {
  ruleShadowMultiSelectIds.forEach(id => {
    const root = document.getElementById(id);
    root?.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = false; });
    if (root) root.open = false;
    updateRuleShadowMultiSelectLabel(id);
  });
  renderRuleShadowRows();
}

async function loadRuleShadowReports(reportDate='') {
  try {
    const query = reportDate ? `?date=${encodeURIComponent(reportDate)}` : '';
    const data = await api(`/api/rule-shadow-reports${query}`);
    const selector = document.getElementById('ruleShadowDate');
    const selected = data.selected_date || '';
    selector.innerHTML = (data.reports || []).map(item => `
      <option value="${escapeHtml(item.date || '')}" ${item.date === selected ? 'selected' : ''}>${escapeHtml(item.date || '')}</option>
    `).join('') || '<option value="">暂无报告</option>';

    const report = data.report || {};
    const counts = report.counts || {};
    const unableToCompare = Number(counts.model_validation_failures ?? counts.unable_to_compare ?? Object.values(counts.skipped || {}).reduce((sum, value) => sum + Number(value || 0), 0));
    const usage = counts.usage || {};
    const currentSummary = (data.reports || []).find(item => item.date === selected) || {};
    const candidateLabel = report.candidate_label || '对比判断';
    document.getElementById('ruleShadowCandidateHeader').textContent = candidateLabel;
    document.getElementById('ruleShadowMetrics').innerHTML = [
      ['全部文章', counts.items ?? ((counts.compared || 0) + unableToCompare)],
      ['可比较文章', counts.compared || 0],
      ['双方均未准入', counts.both_not_admitted || 0],
      ['准入不一致', counts.admission_differences || 0],
      ['action 不一致', counts.action_changes || 0],
      ['最新版本文章', counts.latest_candidate_items ?? counts.latest_rule_items ?? 0],
      ['涉及 push', currentSummary.push_changes || 0],
      ['大模型判断或校验失败', unableToCompare],
      ['token 合计', usage.total_tokens || 0],
      ['飞书提醒', currentSummary.notification_status || '-']
    ].map(item => `<section class="metric"><div class="label">${escapeHtml(item[0])}</div><div class="value">${escapeHtml(item[1])}</div></section>`).join('');
    const rebuild = report.rebuild || {};
    const rebuildNotice = rebuild.source === 'stored_comparison_reports' && rebuild.candidate_re_evaluated === false
      ? '<span>本报告仅重新汇总已保存的现有生产判断和对比判断，没有重新执行对比判断。</span>'
      : '';
    document.getElementById('ruleShadowWindow').innerHTML = report.window_start
      ? `<span>统计区间：${escapeHtml(formatTime(report.window_start))} 至 ${escapeHtml(formatTime(report.window_end))}</span><span>报告生成：${escapeHtml(formatTime(report.generated_at))}</span><span>对比判断：${escapeHtml(candidateLabel)}</span>${rebuildNotice}`
      : '<span>暂无日期报告。</span>';
    ruleShadowReportCache = report;
    renderRuleShadowRows();
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
  if (hasPrefix('WEB_EVIDENCE_') || hasAny(['TAVILY_API_KEY', 'BRAVE_SEARCH_API_KEY'])) {
    lines.push('Tavily/Web Evidence：新 collector 为 timer one-shot，下一轮会读取配置；如需马上验证，在任务健康页立即运行 surveil-research-collector.timer、surveil-official-collector.timer、surveil-news-collector.timer。');
  }
  if (hasPrefix('LLM_') || hasPrefix('OPENAI_')) {
    lines.push('大模型配置：重启常驻的 surveil-x-stream.service、surveil-sina-flash.service；研究机构/官网/新闻媒体 collector 下一轮自动读取，也可立即运行对应 timer。');
  }
  if (hasPrefix('VALUE_DIRECTORY_')) {
    lines.push('价值目录：下一次每天 08:00 timer 会读取新配置；如需马上验证，在任务健康页立即运行 surveil-value-directory.timer。');
  }
  if (hasPrefix('X_')) {
    lines.push('X 配置：重启 surveil-x-stream.service。');
  }
  if (hasPrefix('SINA_')) {
    lines.push('新浪配置：重启 surveil-sina-flash.service；可选立即运行 surveil-sina-stock-news.timer。');
  }
  if (hasPrefix('IFIND_')) {
    lines.push('iFinD 行情/兼容配置：依赖该接口的任务下一轮读取；公司公告来源已迁移到巨潮资讯。');
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

document.getElementById('eventFromDate').value = todayString();
document.getElementById('eventToDate').value = todayString();
showView('overview');
loadHealthSummary();
setInterval(() => {
  if (!document.hidden) loadHealthSummary();
}, 60000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadHealthSummary();
});
