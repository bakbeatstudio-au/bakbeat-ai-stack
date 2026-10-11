# Setup guide

Run commands from the project directory. For the overview see the [README](../README.md); for run
instructions and limits see [stage-1.md](stage-1.md).

## Install and verify

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

`requirements.lock` pins the complete resolved environment, including the test dependencies.

Create the private data directory, then validate the setup and initialise the database:

```sh
mkdir -p -m 700 "$HOME/Library/Application Support/BakBeat AI Stack/data"
./ai-stack doctor
./ai-stack init-db
.venv/bin/python -m pytest -q
```

`doctor` validates the configuration and confirms that the database directory is private to your user
and on internal storage. It does not read credentials or contact any provider. `init-db` creates an
idempotent schema-version table, enables WAL and checks SQLite integrity. Task and call tables are
created when the application runs.

## Enter API keys privately

Run these commands yourself in a terminal. Enter each API key only at the hidden prompt, never as part of
the command and never in chat. macOS may ask for login Keychain access; complete that prompt directly.
The default user Keychain must be the login Keychain. This helper does not change Keychain settings.

```sh
./ai-stack setup-key openai
./ai-stack setup-key anthropic
```

Each command stores and reads back the key to verify local storage, then prints only success. This does
not verify provider account access and does not make API calls. Existing keys are never overwritten by
this helper; if one provider is already saved, run only the other command.

Keys live under Keychain service `com.bakbeat.ai-stack`, accounts `openai` and `anthropic`. The explicit
macOS backend prevents file-storage fallbacks. Keychain access is associated with the Python executable;
this is not an isolation boundary between arbitrary scripts running as your macOS user.

## Check provider authentication

```sh
./ai-stack check-providers
```

This read-only check authenticates to each provider's model-list endpoint without generating text. It
sends keys only to fixed HTTPS provider endpoints, disables redirects and environment proxy inheritance,
and prints sanitized results. It does not print keys, request headers, raw errors or raw response bodies.
It does not prove billing readiness or generation access.
