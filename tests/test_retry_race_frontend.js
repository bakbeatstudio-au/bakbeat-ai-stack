// Executes the real app.js under macOS JavaScriptCore (jsc) with a fake DOM,
// fetch and storage. Usage: jsc test_retry_race_frontend.js -- <app.js> <scenario>
const [appPath, scenario] = arguments;

function fakeElement(name) {
  return {
    name, value: '', textContent: '', disabled: false, hidden: false, checked: false, open: false,
    content: 'fake-token', className: '', dataset: {}, children: [],
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); },
  };
}
const nodes = new Map();
globalThis.document = {
  querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, fakeElement(selector));
    return nodes.get(selector);
  },
  querySelectorAll() { return [...nodes.values()]; },
  createElement(tag) { return fakeElement(tag); },
  createTextNode(text) { return {text}; },
};
document.querySelector('#selection').value = 'openai';

function fakeStorage(initial) {
  const store = {...initial};
  return {
    store,
    getItem: key => key in store ? store[key] : null,
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: key => { delete store[key]; },
  };
}
globalThis.localStorage = fakeStorage({});
let uuidCount = 0;
globalThis.crypto = {randomUUID: () => `00000000-0000-4000-8000-00000000000${++uuidCount}`};

const requests = [];
let routes = {};
function reply(status, body) { return {status, body}; }
globalThis.fetch = async (path, options = {}) => {
  const body = options.body === undefined ? undefined : JSON.parse(options.body);
  requests.push({path, method: options.method || 'GET', body});
  const route = Object.keys(routes).find(prefix => path.startsWith(prefix));
  if (!route) throw Error('Unexpected fetch ' + path);
  const {status, body: data} = routes[route](body, path);
  return {ok: status >= 200 && status < 300, status, json: async () => JSON.parse(JSON.stringify(data))};
};
const baseRoutes = {
  '/api/projects': () => reply(200, {projects: []}),
  '/api/history': () => reply(200, {tasks: [], allowance_used_usd: 0, allowance_usd: 2}),
  '/api/context-preview': () => reply(200, {prompts: {openai: 'Hello'}, context_hash: 'hash', reserved_usd: {openai: 0.01},
    allowance_remaining_usd: 2, input_bytes: {openai: 5}, within_allowance: true}),
  '/api/task/': () => reply(404, {error: 'Request not found.'}),
};
const busyReply = () => reply(409, {error: 'Another request is running.', code: 'engine_busy'});
const conflictReply = () => reply(409, {error: 'The context changed.', code: 'request_conflict'});
const finished = body => reply(200, {id: body.id, status: 'finished', calls: []});

function assert(condition, message) { if (!condition) throw Error('Assertion failed: ' + message); }
function same(a, b, message) { assert(JSON.stringify(a) === JSON.stringify(b), `${message}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }
async function settle() { for (let i = 0; i < 500; i++) await null; }
const $$ = selector => document.querySelector(selector);
const chats = () => requests.filter(r => r.path === '/api/chat');
function stored() { return JSON.parse(sessionStorage.getItem('pending-request')); }

async function boot(session = {}, location) {
  globalThis.sessionStorage = fakeStorage(session);
  if (location) localStorage.setItem('bakbeat-location', JSON.stringify(location));
  load(appPath);
  await settle();
  assert(!busy, 'startup settled');
}
async function reviewPrompt(text = 'Hello') {
  $$('#prompt').value = text;
  $$('#preview').onclick();
  await settle();
  assert(review, 'review is ready');
}
async function send() {
  $$('#chat').onsubmit({preventDefault() {}});
  await settle();
}
function assertPendingKept(expected) {
  same(pending, expected, 'pending request in memory');
  same(stored(), expected, 'pending request in sessionStorage');
  assert($$('#send').disabled, 'new Send stays disabled');
  assert($$('#preview').disabled, 'new review stays disabled');
  assert(!$$('#retry').hidden && !$$('#retry').disabled, 'same-ID retry is available');
  assert(!$$('#refresh').disabled, 'status refresh is available');
}

// Project/conversation fixtures for saved-context summaries.
const studio = {id: 'p1', name: 'Studio', notes: 'Saved notes'};
const earlier = {id: 't1', prompt: 'Earlier', status: 'finished', created_at: '2026-09-25T00:00:00Z', context: null,
  calls: ['openai', 'anthropic'].map(provider => ({provider, model: 'fake', status: 'completed', text: 'answer'}))};
const projectRoutes = {
  ...baseRoutes,
  '/api/projects': () => reply(200, {projects: [studio]}),
  '/api/conversations?': () => reply(200, {conversations: [{id: 'c1', title: 'Plan'}]}),
  '/api/conversation?': () => reply(200, {tasks: [earlier]}),
};
const inStudio = {project_id: 'p1', conversation_id: 'c1'};
function savedContext(overrides) {
  return {id: '22222222-2222-4222-8222-222222222222', selection: 'anthropic', prompt: 'Continue',
    conversation_id: 'c1', selected_ids: ['t1'], include_notes: true, context_hash: 'saved-hash', ...overrides};
}
const retryNote = ' Retry resends this saved request as it was saved; checkboxes on this page do not change it. The server still validates it.';
function assertSummary(expected) {
  const summary = $$('#retry-context');
  assert(!summary.hidden, 'summary visible');
  same(summary.textContent, expected + retryNote, 'summary text');
}
function assertNoSummary() {
  const summary = $$('#retry-context');
  assert(summary.hidden && summary.textContent === '', 'summary hidden and cleared: ' + summary.textContent);
}
async function summaryCase(saved, location, expected) {
  routes = {...projectRoutes};
  await boot({'pending-request': JSON.stringify(saved)}, location);
  assertPendingKept(saved);
  assertSummary(expected);
  assert(chats().length === 0, 'no automatic send');
}

const scenarios = {
  async busy_absent_row_keeps_pending() {
    routes = {...baseRoutes, '/api/chat': busyReply};
    await boot();
    await reviewPrompt();
    await send();
    assert(chats().length === 1, 'one chat request');
    const saved = chats()[0].body;
    same(saved, {id: '00000000-0000-4000-8000-000000000001', selection: 'openai', prompt: 'Hello'}, 'saved payload');
    assertPendingKept(saved);
    // Ordinary new sends stay blocked.
    await send();
    assert(chats().length === 1, 'blocked send made no request');
    // Refresh while the row is still absent keeps the saved request.
    $$('#refresh').onclick();
    await settle();
    assertPendingKept(saved);
    assert(chats().length === 1, 'refresh made no chat request');
    // Manual retry reuses the same ID and payload, and resolves on success.
    routes['/api/chat'] = finished;
    $$('#retry').onclick();
    await settle();
    assert(chats().length === 2, 'retry sent once');
    same(chats()[1].body, saved, 'retry reuses identical payload');
    assert(pending === null && stored() === null, 'pending cleared after success');
  },

  async api_error_keeps_code() {
    routes = {...baseRoutes, '/api/chat': busyReply};
    await boot();
    try { await api('/api/chat', {id: 'x'}); assert(false, 'api should throw'); }
    catch (error) {
      assert(error.status === 409, 'status preserved');
      assert(error.code === 'engine_busy', 'code preserved: ' + error.code);
    }
  },

  async reload_with_absent_row_keeps_pending() {
    const saved = {id: '11111111-1111-4111-8111-111111111111', selection: 'openai', prompt: 'Saved'};
    routes = {...baseRoutes};
    await boot({'pending-request': JSON.stringify(saved)});
    assertPendingKept(saved);
    assert($$('#prompt').value === 'Saved', 'prompt restored');
    assert(chats().length === 0, 'no automatic send');
  },

  async stale_context_allows_fresh_review() {
    routes = {...baseRoutes, '/api/chat': conflictReply};
    await boot();
    await reviewPrompt();
    await send();
    assert(pending === null && stored() === null, 'unpersisted stale request cleared');
    assert(!$$('#preview').disabled, 'review available again');
    await reviewPrompt('Hello again');
    assert(!$$('#send').disabled, 'fresh review can be sent');
  },

  async recorded_conflict_keeps_pending() {
    routes = {...baseRoutes, '/api/chat': conflictReply,
      '/api/task/': (_, path) => reply(200, {id: decodeURIComponent(path.split('/').pop()), status: 'running', calls: []})};
    await boot();
    await reviewPrompt();
    await send();
    assertPendingKept(chats()[0].body);
  },

  async reload_shows_saved_context_then_retry_sends_exact_payload() {
    const saved = savedContext();
    let duringStartup;
    routes = {...projectRoutes, '/api/task/': () => {
      duringStartup = {busy, retryDisabled: $$('#retry').disabled, hidden: $$('#retry-context').hidden, text: $$('#retry-context').textContent};
      return reply(404, {error: 'Request not found.'});
    }};
    await boot({'pending-request': JSON.stringify(saved)}, inStudio);
    assert(duringStartup && duringStartup.busy && duringStartup.retryDisabled, 'status lookup ran while Retry was not yet actionable');
    assert(!duringStartup.hidden && duringStartup.text.startsWith('Saved request for Claude with project notes and 1 earlier exchange.'),
      'summary shown before Retry is actionable: ' + JSON.stringify(duringStartup));
    // Visible checkboxes are cleared on reload; the summary describes the saved request instead.
    assert(selected.size === 0 && !$$('#include-notes').checked, 'visible choices cleared');
    assertPendingKept(saved);
    assertSummary('Saved request for Claude with project notes and 1 earlier exchange.');
    assert(chats().length === 0, 'no automatic send');
    // Visible state that differs from the saved request must not leak into Retry.
    $$('#include-notes').checked = false; $$('#selection').value = 'openai'; $$('#prompt').value = 'Different';
    routes['/api/chat'] = finished;
    $$('#retry').onclick();
    await settle();
    assert(chats().length === 1, 'retry sent once');
    same(chats()[0].body, saved, 'retry reuses exact saved payload, hash and ID');
    assert(pending === null && stored() === null, 'pending cleared after success');
    assertNoSummary();
  },

  async summary_quick_prompt() {
    await summaryCase({id: '33333333-3333-4333-8333-333333333333', selection: 'openai', prompt: 'Quick'}, undefined,
      'Saved request for OpenAI with no project notes and no earlier exchanges (quick prompt).');
  },

  async summary_notes_only() {
    await summaryCase(savedContext({selected_ids: [], selection: 'both'}), inStudio,
      'Saved request for both OpenAI and Claude with project notes and no earlier exchanges.');
  },

  async summary_exchanges_only() {
    await summaryCase(savedContext({selected_ids: ['t1', 't2'], include_notes: false, selection: 'openai'}), inStudio,
      'Saved request for OpenAI with no project notes and 2 earlier exchanges.');
  },

  async summary_notes_and_exchanges() {
    await summaryCase(savedContext({selected_ids: ['t1', 't2', 't3']}), inStudio,
      'Saved request for Claude with project notes and 3 earlier exchanges.');
  },

  async summary_survives_busy_and_404_then_stale_clears() {
    const saved = savedContext();
    const expected = 'Saved request for Claude with project notes and 1 earlier exchange.';
    routes = {...projectRoutes, '/api/chat': busyReply};
    await boot({'pending-request': JSON.stringify(saved)}, inStudio);
    $$('#retry').onclick();
    await settle();
    assertPendingKept(saved);
    assertSummary(expected);
    $$('#refresh').onclick();
    await settle();
    assertPendingKept(saved);
    assertSummary(expected);
    // Definitive stale-context rejection with no stored row.
    routes['/api/chat'] = conflictReply;
    $$('#retry').onclick();
    await settle();
    assert(chats().length === 2, 'two manual retries only');
    same(chats()[1].body, saved, 'second retry reused saved payload');
    assert(pending === null && stored() === null, 'stale request cleared');
    assertNoSummary();
    assert(!$$('#preview').disabled, 'review available again');
  },

  async refresh_completion_clears_summary_and_next_request_is_fresh() {
    routes = {...projectRoutes};
    await boot({'pending-request': JSON.stringify(savedContext())}, inStudio);
    assertSummary('Saved request for Claude with project notes and 1 earlier exchange.');
    routes['/api/task/'] = (_, path) => reply(200, {id: decodeURIComponent(path.split('/').pop()), status: 'finished', calls: []});
    $$('#refresh').onclick();
    await settle();
    assert(pending === null && stored() === null, 'finished request cleared');
    assertNoSummary();
    // A new request without context must not show the previous request's context.
    routes['/api/task/'] = baseRoutes['/api/task/'];
    routes['/api/chat'] = busyReply;
    $$('#selection').value = 'openai';
    await reviewPrompt('Next');
    await send();
    const next = chats()[0].body;
    same(next, {id: '00000000-0000-4000-8000-000000000001', selection: 'openai', prompt: 'Next',
      conversation_id: 'c1', selected_ids: [], include_notes: false, context_hash: 'hash'}, 'new saved payload');
    assertPendingKept(next);
    assertSummary('Saved request for OpenAI with no project notes and no earlier exchanges.');
  },

  async bad_request_clears_pending() {
    routes = {...baseRoutes, '/api/chat': () => reply(400, {error: 'Request rejected.'})};
    await boot();
    await reviewPrompt();
    await send();
    assert(pending === null && stored() === null, 'rejected request cleared');
  },
};

(async () => {
  try {
    if (!scenarios[scenario]) throw Error('Unknown scenario ' + scenario);
    await scenarios[scenario]();
    print('PASS ' + scenario);
  } catch (error) {
    print('FAIL ' + scenario + ': ' + error.message + '\n' + error.stack);
  }
})();
