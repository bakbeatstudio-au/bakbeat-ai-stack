# BakBeat AI Stack

A local, single-user web app for sending a prompt to OpenAI, Claude, or both, keeping named
projects and resumable conversations, and tracking estimated cost. It runs only on `127.0.0.1`
and stores its data in a local SQLite database.

> **Status: initial core release.** This repository contains the core application and its offline
> test suite.

## What the core provides

- Manual provider selection: OpenAI, Claude, or both (independent answers).
- Named projects and conversations; earlier exchanges and project notes are sent only when you
  select them and review the exact text first.
- Persisted request/response history with per-call model, latency, token usage and estimated cost.
- Idempotent requests (no automatic retries), spend reservations and per-request / cumulative limits.
- A localhost browser interface served by the app itself.

Not included: routing, cross-review between models, model tool use, streaming, LAN access.

## Architecture (high level)

| Part | Location | Role |
|---|---|---|
| CLI and launcher | `ai-stack`, `src/ai_stack/__main__.py` | `doctor`, `init-db`, `setup-key`, `check-providers`, `serve`, `smoke-test` |
| Configuration | `config/settings.example.toml`, `src/ai_stack/config.py` | fail-closed validation (localhost only, later-stage features disabled) |
| Credentials | `src/ai_stack/credentials.py` | API keys live in the macOS login Keychain only |
| Storage | `src/ai_stack/storage.py`, `conversations.py` | private SQLite database (WAL), projects, conversations, context snapshots |
| Providers | `src/ai_stack/provider_contract.py`, `providers.py`, `connectivity.py` | request/adapter contract; OpenAI and Anthropic adapters |
| Engine | `src/ai_stack/engine.py` | request accounting, idempotency, cost reservation |
| Server and UI | `src/ai_stack/server.py`, `runtime.py`, `src/ai_stack/web/` | FastAPI localhost server and browser interface |
| Pricing | `config/pricing.json` | price table used for cost estimates (estimates, not invoices) |

## Requirements

- macOS (the credential backend is the macOS Keychain, and the database directory must be on
  internal storage). One test also needs macOS JavaScriptCore (`jsc`).
- Python 3.13 (the version used for development and testing).
- An OpenAI and/or Anthropic API key. Using the providers incurs charges on your account.

## Install

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

## Configure

The application reads `config/settings.example.toml` and rejects unsafe values. The server host must
stay `127.0.0.1`, the credential backend must stay `macos_keychain`, and the `[features]` flags must
all stay `false`. The database path is fixed to
`~/Library/Application Support/BakBeat AI Stack/data/ai-stack.sqlite3`. Model IDs and prices come from
the same file and `config/pricing.json`. There is no separate user settings file in this release.

Create the private data directory (current user only), then check and initialise:

```sh
mkdir -p -m 700 "$HOME/Library/Application Support/BakBeat AI Stack/data"
./ai-stack doctor
./ai-stack init-db
```

## Use

Enter each API key at the hidden prompt (never on the command line):

```sh
./ai-stack setup-key openai
./ai-stack setup-key anthropic
./ai-stack check-providers   # read-only authentication check; no text generation
./ai-stack serve             # then open http://127.0.0.1:8765
```

Stop the server with Control-C. See [docs/setup-guide.md](docs/setup-guide.md),
[docs/stage-1.md](docs/stage-1.md) and [docs/projects-conversations.md](docs/projects-conversations.md).

## Tests

```sh
.venv/bin/python -m pytest -q
```

The suite is offline: it uses fake providers and temporary databases and makes no network calls.

## Developer utility

`scripts/backup.py --target DIRECTORY` creates a verified recovery checkpoint (a Git bundle of the project
plus a consistent copy of the local database) in a directory you choose. It requires the project to be a clean
Git work tree and the data directory described above. It is not needed to run the app.

## Licence

Copyright © 2026 Chris Anderson.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE). Third-party dependencies are
installed separately and remain under their own licences.

## Contributing

This project is not accepting external code contributions or pull requests at this time.

## Security

Please report suspected security vulnerabilities privately through GitHub's private vulnerability reporting
for this repository (see [SECURITY.md](SECURITY.md)). Please do not report vulnerabilities in public issues.
