const $ = selector => document.querySelector(selector);
const headers = {
  'Content-Type': 'application/json',
  'X-BakBeat-Token': $('meta[name="bakbeat-token"]').content,
};
const money = value => value == null ? 'usage unknown' : `US$${value.toFixed(5)}`;
const providerErrors = {
  billing_or_credits_required: 'Add API billing or credits with this provider.',
  quota_or_rate_limit: 'Provider quota or rate limit reached.',
  thinking_setting_rejected: 'This earlier test used an unsupported setting; the adapter has since been corrected.',
  http_400: 'The provider rejected this request.',
  authentication_failed: 'The provider could not authenticate. Check its Keychain key.',
  interrupted_usage_unknown: 'The app stopped during this call. Usage is unknown; it will not retry automatically.',
  timeout_usage_unknown: 'The provider timed out. Usage is unknown; check before starting a new request.',
  connection_usage_unknown: 'The connection was interrupted. Usage is unknown.',
};
let projects = [], tasks = [], selected = new Set(), review = null, pending = null, busy = false;
let savedLocation = {};
try { pending = JSON.parse(sessionStorage.getItem('pending-request')); } catch {}
try { savedLocation = JSON.parse(localStorage.getItem('bakbeat-location')) || {}; } catch {}

function element(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function notice(text) { $('#message').textContent = text; }
function option(value, text) { const el = element('option', text); el.value = value; return el; }
function project() { return projects.find(item => item.id === $('#project').value); }
function notesDirty() { return !!project() && $('#notes').value !== project().notes; }
function resetReview() {
  review = null;
  $('#send').disabled = true;
  $('#context-preview').hidden = true;
  $('#preview-text').replaceChildren();
}
const providerNames = {openai: 'OpenAI', anthropic: 'Claude', both: 'both OpenAI and Claude'};
function pendingSummary(saved) {
  const count = Array.isArray(saved.selected_ids) ? saved.selected_ids.length : 0;
  const exchanges = count ? `${count} earlier exchange${count === 1 ? '' : 's'}` : 'no earlier exchanges';
  const context = !saved.conversation_id ? 'no project notes and no earlier exchanges (quick prompt)'
    : saved.include_notes ? `project notes and ${exchanges}` : `no project notes and ${exchanges}`;
  return `Saved request for ${providerNames[saved.selection] || String(saved.selection)} with ${context}. ` +
    'Retry resends this saved request as it was saved; checkboxes on this page do not change it. The server still validates it.';
}
function controls() {
  document.querySelectorAll('#chat input,#chat textarea,#chat select,#preview,#project,#conversation,#new-project input,#new-project button,#new-conversation input,#new-conversation button,#notes-form textarea,#notes-form button,.context-choice,#refresh').forEach(el => {
    el.disabled = busy || !!pending || el.dataset.unavailable === 'true';
  });
  $('#include-notes').disabled = busy || !!pending || !$('#conversation').value;
  $('#send').disabled = busy || !!pending || !review;
  $('#retry').hidden = !pending;
  $('#retry').disabled = busy;
  $('#retry-context').hidden = !pending;
  $('#retry-context').textContent = pending ? pendingSummary(pending) : '';
  // Status refresh must remain available for an unresolved request.
  $('#refresh').disabled = busy;
}
async function action(fn) {
  if (busy) return;
  busy = true; controls();
  try { await fn(); } catch (error) { notice(error.message); }
  finally { busy = false; controls(); }
}
async function api(path, body) {
  const response = await fetch(path, {headers, ...(body === undefined ? {} : {method: 'POST', body: JSON.stringify(body)})});
  let data;
  try { data = await response.json(); }
  catch { throw Error('The app connection changed. Reload the page, then check the saved request before retrying.'); }
  if (!response.ok) { const error = Error(data.error || 'Unable to complete request. Reload the page.'); error.status = response.status; error.code = data.code; throw error; }
  return data;
}
function input() {
  return {
    conversation_id: $('#conversation').value || null,
    selection: $('#selection').value,
    prompt: $('#prompt').value,
    selected_ids: [...selected],
    include_notes: $('#include-notes').checked,
  };
}
function rememberLocation() {
  try { localStorage.setItem('bakbeat-location', JSON.stringify({project_id: $('#project').value, conversation_id: $('#conversation').value})); } catch {}
}
function clearPending() { pending = null; sessionStorage.removeItem('pending-request'); }
function render() {
  const list = $('#history'); list.replaceChildren();
  if (!tasks.length) list.append(element('p', 'Your first exchange will appear here.', 'meta'));
  const providers = $('#selection').value === 'both' ? ['openai', 'anthropic'] : [$('#selection').value];
  for (const task of tasks) {
    const card = element('article', undefined, 'task');
    card.append(element('div', `${new Date(task.created_at).toLocaleString()} · ${task.status}`, 'meta'), element('h2', task.prompt));
    if ($('#conversation').value) {
      const eligible = task.status === 'finished' && providers.every(p => task.calls.some(c => c.provider === p && c.status === 'completed' && c.text));
      const label = element('label', undefined, 'check');
      const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'context-choice';
      check.dataset.unavailable = String(!eligible); check.checked = selected.has(task.id);
      check.onchange = () => {
        if (check.checked && selected.size >= 20) { check.checked = false; notice('Choose at most 20 earlier exchanges.'); return; }
        if (check.checked) selected.add(task.id); else selected.delete(task.id);
        resetReview();
      };
      label.append(check, document.createTextNode(eligible ? 'Include this exchange in the next request' : 'Not available as context: a complete answer is needed from each selected provider.'));
      card.append(label);
    }
    const answers = element('div', undefined, 'answers');
    for (const call of task.calls) {
      const answer = element('div', undefined, 'answer');
      const usage = call.usage ? ` · ${call.usage.input_tokens} input / ${call.usage.output_tokens} output tokens` : '';
      const latency = call.latency_ms == null ? '' : ` · ${(call.latency_ms / 1000).toFixed(1)}s`;
      answer.append(element('h3', `${call.provider === 'openai' ? 'OpenAI' : 'Claude'} · ${call.model}`),
        element('pre', call.text || providerErrors[call.error] || call.error || call.status), element('div', `${call.status} · ${money(call.cost_usd)}${usage}${latency}`, 'meta'));
      answers.append(answer);
    }
    card.append(answers);
    if (task.context) card.append(element('p', `Sent with ${task.context.selection.ids.length} selected exchange(s)${task.context.notes_included ? ' and project notes' : ''}.`, 'meta'));
    list.append(card);
  }
  controls();
}
async function refresh() {
  const data = await api('/api/history');
  $('#allowance').textContent = `Prototype allowance used / reserved: ${money(data.allowance_used_usd)} of US$${data.allowance_usd.toFixed(2)}`;
  tasks = $('#conversation').value ? (await api('/api/conversation?conversation_id=' + encodeURIComponent($('#conversation').value))).tasks : (project() ? [] : data.tasks);
  if (pending) {
    try {
      const found = await api('/api/task/' + encodeURIComponent(pending.id));
      if (found.status !== 'running') { clearPending(); resetReview(); notice('Saved request finished. Its result is in history.'); }
    } catch (error) { if (error.status !== 404) throw error; }
  }
  render();
}
async function loadConversations(id) {
  resetReview(); selected.clear(); $('#include-notes').checked = false; $('#prompt').value = '';
  const current = project(); $('#project-work').hidden = !current; $('#notes').value = current?.notes || '';
  $('#conversation').replaceChildren();
  if (current) {
    const rows = (await api('/api/conversations?project_id=' + encodeURIComponent(current.id))).conversations;
    $('#conversation').append(...rows.map(c => option(c.id, c.title)));
    if (rows.some(c => c.id === id)) $('#conversation').value = id;
  }
  rememberLocation(); await refresh();
}
async function loadProjects(id, conversationId) {
  projects = (await api('/api/projects')).projects;
  $('#project').replaceChildren(option('', 'Quick prompt / recent requests'), ...projects.map(p => option(p.id, p.name)));
  $('#project').value = projects.some(p => p.id === id) ? id : '';
  await loadConversations(conversationId);
}
$('#project').onchange = () => action(() => loadConversations());
$('#conversation').onchange = () => action(async () => {
  resetReview(); selected.clear(); $('#include-notes').checked = false; $('#prompt').value = '';
  rememberLocation(); await refresh();
});
$('#new-project').onsubmit = event => {
  event.preventDefault(); action(async () => {
    const created = await api('/api/projects', {name: $('#project-name').value, notes: ''});
    $('#project-name').value = ''; await loadProjects(created.id);
    notice('Project created. Create a conversation to get started.');
  });
};
$('#new-conversation').onsubmit = event => {
  event.preventDefault(); action(async () => {
    const created = await api('/api/conversations', {project_id: $('#project').value, title: $('#conversation-title').value});
    $('#conversation-title').value = ''; await loadConversations(created.id); notice('Conversation created.');
  });
};
$('#notes-form').onsubmit = event => {
  event.preventDefault(); action(async () => {
    const current = project();
    const saved = await api('/api/projects', {id: current.id, name: current.name, notes: $('#notes').value});
    Object.assign(current, saved); resetReview(); notice('Notes saved locally. Review again before sending.');
  });
};
$('#notes').oninput = () => { resetReview(); notice('Notes have unsaved changes. Save them before reviewing your next request.'); };
$('#prompt').oninput = resetReview;
$('#selection').onchange = () => { resetReview(); selected.clear(); render(); notice('Provider selection changed. Choose any earlier exchanges to include, then review again.'); };
$('#include-notes').onchange = resetReview;
$('#preview').onclick = () => action(async () => {
  resetReview();
  if ($('#project').value && !$('#conversation').value) throw Error('Create a conversation in this project first.');
  if (notesDirty()) throw Error('Save your edited project notes before reviewing.');
  const snapshot = input(); const data = await api('/api/context-preview', snapshot);
  if (JSON.stringify(snapshot) !== JSON.stringify(input())) return;
  const area = $('#preview-text');
  const total = Object.values(data.reserved_usd).reduce((a,b) => a+b, 0);
  area.append(element('p', `Conservative reservation: ${money(total)}. Remaining prototype allowance: ${money(data.allowance_remaining_usd)}. Actual cost is recorded after each provider responds.`, 'hint'));
  for (const [provider,text] of Object.entries(data.prompts)) {
    area.append(element('h3', `${provider === 'openai' ? 'OpenAI' : 'Claude'} · ${data.input_bytes[provider]} input bytes · ${money(data.reserved_usd[provider])} reserved`), element('pre', text));
  }
  $('#context-preview').hidden = false; $('#context-preview').open = true;
  if (data.within_allowance) {
    review = {...snapshot, context_hash: data.context_hash};
    notice('Review the exact text below, then send when ready.');
  } else notice('This exceeds the US$0.25 request limit or remaining US$2 prototype allowance. Reduce context or review usage.');
});
async function submitSaved() {
  notice('Working… The request is saved before it is sent.');
  try {
    await api('/api/chat', pending); clearPending(); resetReview(); selected.clear(); $('#include-notes').checked = false;
    notice('Request finished. Select exchanges explicitly for your next request.');
  } catch (error) {
    if (error.status === 400) { clearPending(); resetReview(); }
    // Busy: the original may not have written its row yet, so a 404 proves nothing.
    else if (error.status === 409 && error.code !== 'engine_busy') {
      try { await api('/api/task/' + encodeURIComponent(pending.id)); }
      catch (check) { if (check.status === 404) { clearPending(); resetReview(); } }
    }
    notice(error.message);
  } finally { await refresh(); }
}
$('#chat').onsubmit = event => {
  event.preventDefault();
  if (!review || pending || busy) return;
  action(async () => {
    const {conversation_id,selected_ids,include_notes,context_hash,selection,prompt} = review;
    const saved = {id: crypto.randomUUID(), selection, prompt, ...(conversation_id ? {conversation_id,selected_ids,include_notes,context_hash} : {})};
    // Persist before sending; storage failure must not cause an untracked call.
    sessionStorage.setItem('pending-request', JSON.stringify(saved)); pending = saved; controls();
    await submitSaved();
  });
};
$('#retry').onclick = () => { if (pending) action(submitSaved); };
$('#refresh').onclick = () => action(refresh);
action(async () => {
  await loadProjects(savedLocation.project_id, savedLocation.conversation_id);
  if (pending?.conversation_id && $('#conversation').value !== pending.conversation_id) {
    for (const current of projects) {
      const rows = (await api('/api/conversations?project_id=' + encodeURIComponent(current.id))).conversations;
      if (rows.some(c => c.id === pending.conversation_id)) { $('#project').value = current.id; await loadConversations(pending.conversation_id); break; }
    }
  }
  if (pending) { $('#prompt').value = pending.prompt; $('#selection').value = pending.selection; notice('A saved request is unresolved. Refresh its status or retry the same request safely.'); render(); }
});
