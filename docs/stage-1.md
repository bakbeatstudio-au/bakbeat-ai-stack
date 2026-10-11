# Stage 1 — local two-provider chat

Run from the project directory (see [setup-guide.md](setup-guide.md) for first-time setup):

```sh
./ai-stack serve
```

Open `http://127.0.0.1:8765` on the same Mac. Stop a foreground server with Control-C in its terminal.
No LaunchAgent or automatic startup is installed. Only one app process can own the database; another
server or smoke-test process refuses to start while it is running.

## Implemented

- Manual OpenAI / Claude / Both; quick prompts and named project conversations.
- Explicitly selected earlier exchanges and project notes, reviewed before sending.
  Both mode keeps each provider's previous answers separate.
- OpenAI `gpt-5.6-sol`, reasoning none; Claude `claude-fable-5-1`, adaptive
  thinking at low effort. Fable requires adaptive thinking. No tools supplied.
- Responses API with `store=false`; Anthropic Messages API. These settings
  do not override either provider's account-level data retention policies.
- Current prompt limited to 8,000 UTF-8 bytes; combined prompt/context to 32,000
  bytes per provider. Output is capped at 1,024 tokens per provider,
  including any reasoning tokens. A truncated response is marked incomplete.
- Requests and calls committed before network I/O; independent results in Both.
- Provider, role, requested and returned model, latency, finish reason, raw usage,
  estimated USD cost and the exact pricing snapshot recorded per call.
- SQLite on internal storage, WAL, short transactions and a five-second busy timeout.
- Idempotent task IDs: a repeated request returns the stored result. Requests
  with the same ID and different content are rejected. No automatic retries.
- After a crash, unfinished calls become unknown and remain reserved against
  allowance. They are never silently replayed.
- One active task at a time. Both-mode provider calls run concurrently.
- Exact localhost Host/Origin checks, per-process browser request token,
  no cross-origin access, restrictive CSP and text-only rendering of answers.
- Startup reads keys into memory from the login Keychain; no keys in the browser or logs.

## Cost controls

Pricing is stored in `config/pricing.json` (observed on 2026-09-20) and copied into every call
record. Costs are estimates, not provider invoices. Rates may change; check before increasing
limits or changing models.

The context preview displays a conservative reservation for each provider. Each request reserves
an estimate based on the complete serialized UTF-8 input plus overhead and the output cap.
Requests above US$0.25 are rejected. The prototype allows US$2 total recorded/reserved usage
across its database, not per day or month. A failed or interrupted call without usage retains its
reservation. This is an application safeguard, not an account-wide provider billing limit.

`./ai-stack smoke-test` reuses existing successful connection-test results for the same model; if
missing, it makes a capped 256-output-token call for that provider under a stable request ID.
Stop the server first.

## Limits

Quick prompt mode shows the latest 20 requests; older records remain in SQLite. Opening a
conversation shows all its exchanges. History and saved notes are sent only when explicitly
selected and reviewed. Up to 20 complete exchanges may be selected; none are silently dropped to
fit limits. See [projects-conversations.md](projects-conversations.md). Browser refresh does not
cancel an in-progress server request. If connection status is unclear, refresh history before
creating a new request. There is no durable worker queue, streaming, cross-review, router, tools,
LAN access or automatic startup.

Prompts and answers remain in the local database until a retention policy is implemented. The
browser temporarily keeps an unresolved request in session storage to preserve its ID on reload.

## Projects and conversations

Projects, conversations and exact context snapshots are stored in the same SQLite database. Project
notes live in that database; no filesystem ingestion, retrieval or external assets are involved.
Cloud payloads remain plain text through the provider adapters.
