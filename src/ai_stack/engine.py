"""One task at a time; both providers concurrently; durable state before I/O."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import sqlite3
import threading
import uuid

from .config import DATABASE, ROOT, load
from .credentials import SERVICE, backend
from .providers import generate, cost
from .storage import initialize
from .conversations import Conversations, migrate


def now():
    return datetime.now(timezone.utc).isoformat()


def request_id(value):
    """Accept only the canonical text of a UUID: lowercase, hyphenated, 36 characters.

    uuid.UUID() alone also accepts upper case, braces, a urn:uuid: prefix and misplaced or missing
    hyphens, so several spellings of one UUID would each be stored as a separate request."""
    if str(uuid.UUID(value)) != value:
        raise ValueError('Invalid request ID.')
    return value


class Conflict(Exception):
    pass


class Busy(Conflict):
    """Another run holds the lock; its task row may not exist yet."""


class Limit(Exception):
    pass


class Engine(Conversations):
    def __init__(self, db=DATABASE, config=None, keys=None, generator=generate, initialize_db=True):
        self.db = db
        self.config = load() if config is None else config
        self.generator = generator
        self.lock = threading.Lock()
        self.pricing = json.loads((ROOT / 'config/pricing.json').read_text())
        self.models = {p: self.config['models'][p + '_worker']['model_id'] for p in ('openai', 'anthropic')}
        if any(m not in self.pricing['rates_per_million'] for m in self.models.values()):
            raise ValueError('Each model needs verified pricing.')
        if initialize_db:
            initialize(db)
        self.keys = {}
        if keys is not None:
            self.keys = keys
        else:
            store = backend()
            for p in self.models:
                try:
                    self.keys[p] = store.get_password(SERVICE, p)
                except Exception:
                    self.keys[p] = None
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, selection TEXT NOT NULL, prompt TEXT NOT NULL,
                  max_output INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                  provider TEXT NOT NULL, role TEXT NOT NULL, model TEXT NOT NULL,
                  resolved_model TEXT, status TEXT NOT NULL, text TEXT, error TEXT,
                  usage_json TEXT, cost_usd REAL, reserved_usd REAL NOT NULL,
                  pricing_json TEXT NOT NULL, latency_ms INTEGER, finish_reason TEXT,
                  created_at TEXT NOT NULL, completed_at TEXT);
            ''')
            migrate(c)
            # Sole-process launcher lock must be held before constructing Engine.
            c.execute("UPDATE calls SET status='unknown', error='interrupted_usage_unknown' WHERE status='in_flight'")
            c.execute("UPDATE tasks SET status='interrupted' WHERE status='running'")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=5000')
        try:
            with c:
                yield c
        finally:
            c.close()

    def get(self, task_id):
        with self.connect() as c:
            task = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task:
                return None
            result = dict(task)
            result['calls'] = [dict(r) for r in c.execute('SELECT * FROM calls WHERE task_id=? ORDER BY provider', (task_id,))]
            for call in result['calls']:
                call['usage'] = json.loads(call.pop('usage_json') or 'null')
                call.pop('pricing_json')
            context = c.execute('SELECT conversation_id,selection_json,notes_snapshot FROM task_context WHERE task_id=?',(task_id,)).fetchone()
            if context:
                result['context'] = {'conversation_id':context['conversation_id'], 'selection':json.loads(context['selection_json']), 'notes_included':bool(context['notes_snapshot'])}
            return result

    def history(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute('SELECT id FROM tasks ORDER BY created_at DESC LIMIT 20')]
            used = c.execute('SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM calls').fetchone()[0]
        return {'tasks': [self.get(i) for i in ids], 'allowance_used_usd': used,
                'allowance_usd': 2.0, 'models': self.models,
                'key_ready': {p: bool(self.keys.get(p)) for p in self.models}}

    def preview(self, conversation_id, selection, prompt, selected_ids, include_notes):
        if selection not in ('openai','anthropic','both') or not isinstance(prompt,str) or not prompt.strip():
            raise ValueError('Choose a provider and enter a prompt.')
        if len(prompt.encode('utf-8'))>8000:
            raise Limit('Prompt exceeds 8,000 UTF-8 bytes.')
        providers = ['openai','anthropic'] if selection=='both' else [selection]
        prompts, notes, ids = self.prepare_context(conversation_id, selected_ids, include_notes, prompt, providers)
        fingerprint = hashlib.sha256(json.dumps(prompts,sort_keys=True).encode()).hexdigest()
        reserves = self.reservations(prompts, 1024)
        with self.connect() as c:
            used = c.execute('SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM calls').fetchone()[0]
        allowed = sum(reserves.values()) <= .25 and used + sum(reserves.values()) <= 2
        return {'prompts':prompts,'context_hash':fingerprint,'selected_ids':ids,
                'reserved_usd':reserves, 'within_allowance':allowed,
                'allowance_remaining_usd':max(0,2-used),
                'notes_included':bool(notes),'notes_snapshot':notes,'input_bytes':{p:len(v.encode('utf-8')) for p,v in prompts.items()}}

    def reservations(self, prompts, max_output):
        reserves = {}
        for p, text in prompts.items():
            r = self.pricing['rates_per_million'][self.models[p]]
            # Byte-based allowance includes the entire serialized context.
            reserves[p] = ((len(text.encode('utf-8')) + 1024) * max(r['input'], r['cache_write']) + max_output * r['output']) / 1_000_000
        return reserves

    def run(self, task_id, selection, prompt, max_output=1024, *, conversation_id=None,
            selected_ids=None, include_notes=False, context_hash=None):
        if not isinstance(task_id,str) or not isinstance(prompt,str) or type(max_output) is not int:
            raise ValueError('Invalid request.')
        request_id(task_id)
        selected_ids = [] if selected_ids is None else selected_ids
        if (not isinstance(selected_ids,list) or len(selected_ids)>20
                or any(not isinstance(i,str) for i in selected_ids)
                or len(set(selected_ids))!=len(selected_ids)
                or type(include_notes) is not bool
                or (conversation_id is not None and not isinstance(conversation_id,str))
                or (context_hash is not None and not isinstance(context_hash,str))):
            raise ValueError('Invalid selected exchanges.')
        if conversation_id is None and (selected_ids or include_notes or context_hash is not None):
            raise ValueError('Choose a conversation before adding context.')
        signature = json.dumps({'ids':sorted(selected_ids),'notes':include_notes,'hash':context_hash},sort_keys=True)
        if selection not in ('openai', 'anthropic', 'both') or not prompt.strip():
            raise ValueError('Choose a provider and enter a prompt.')
        if len(prompt.encode('utf-8')) > 8000 or not 16 <= max_output <= 1024:
            raise Limit('Limit: 8,000 input bytes and 1,024 output tokens per provider.')
        if not self.lock.acquire(blocking=False):
            raise Busy('A request is already running. Wait before submitting again.')
        try:
            previous = self.get(task_id)
            if previous:
                if (previous['selection'], previous['prompt'], previous['max_output']) != (selection, prompt, max_output):
                    raise Conflict('This request ID already belongs to a different prompt.')
                with self.connect() as c:
                    saved = c.execute('SELECT * FROM task_context WHERE task_id=?',(task_id,)).fetchone()
                if saved:
                    if saved['conversation_id']!=conversation_id or saved['selection_json']!=signature:
                        raise Conflict('This request ID belongs to different conversation context.')
                elif conversation_id is not None or selected_ids or include_notes or context_hash is not None:
                    raise Conflict('This request ID belongs to an independent request.')
                return previous
            preview = self.preview(conversation_id, selection, prompt, selected_ids, include_notes)
            if conversation_id is not None and context_hash != preview['context_hash']:
                raise Conflict('Context changed. Review the preview again before sending.')
            prompts = preview['prompts']
            notes_snapshot = preview['notes_snapshot']
            providers = ['openai', 'anthropic'] if selection == 'both' else [selection]
            reserves = self.reservations(prompts, max_output)
            for p in providers:
                if not self.keys.get(p):
                    raise Limit(f'{p}: Keychain key unavailable. Restart after key setup.')
            if sum(reserves.values()) > .25:
                raise Limit('This request exceeds the US$0.25 prototype estimate limit.')
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                used = c.execute('SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM calls').fetchone()[0]
                if used + sum(reserves.values()) > 2:
                    raise Limit('US$2 prototype allowance reached; review usage before increasing it.')
                c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (task_id, selection, prompt, max_output, 'running', now()))
                if conversation_id is not None:
                    c.execute('INSERT INTO task_context VALUES (?,?,?,?,?)', (task_id,conversation_id,signature,notes_snapshot,json.dumps(prompts)))
                for p in providers:
                    c.execute('INSERT INTO calls (id,task_id,provider,role,model,status,reserved_usd,pricing_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                              (str(uuid.uuid4()), task_id, p, 'primary', self.models[p], 'in_flight', reserves[p], json.dumps(self.pricing), now()))
            def call(p):
                try:
                    output = self.generator(p, self.models[p], prompts[p], self.keys[p], max_output)
                except Exception:
                    output = dict(status='unknown', error='call_failed_usage_unknown', text='', usage=None)
                estimated = cost(p, output.get('usage'), self.pricing['rates_per_million'][self.models[p]])
                with self.connect() as c:
                    c.execute('UPDATE calls SET status=?,text=?,error=?,usage_json=?,cost_usd=?,resolved_model=?,latency_ms=?,finish_reason=?,completed_at=? WHERE task_id=? AND provider=?',
                              (output['status'], output.get('text',''), output.get('error'), json.dumps(output.get('usage')), estimated,
                               output.get('resolved_model'), output.get('latency_ms'), output.get('finish_reason'), now(), task_id, p))
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(call, providers))
            with self.connect() as c:
                c.execute("UPDATE tasks SET status='finished' WHERE id=?", (task_id,))
            return self.get(task_id)
        finally:
            self.lock.release()
