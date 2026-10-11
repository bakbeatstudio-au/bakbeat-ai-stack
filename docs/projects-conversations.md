# Projects and conversations

## Use

1. Create a project (for example "Research" or "Planning").
2. Create or open a named conversation in that project.
3. Optionally save project notes. Saving does not send them anywhere.
4. Enter your prompt and select OpenAI, Claude or Both.
5. Tick any complete earlier exchanges you want included, and explicitly tick
   saved project notes if you want to send those too.
6. Choose **Review what will be sent**. Check each provider's exact text and
   conservative cost reservation, then choose **Send prompt**.

Unselected history and notes stay local. Each model sees only its own complete
answers from the selected exchanges. If an exchange lacks a complete answer
from a selected provider, it cannot be included for that selection. Change to
one provider or choose other exchanges. No automatic cross-review occurs.

Changing prompt, provider, context or notes invalidates the review. A notes
change from another tab is caught by a server-side fingerprint before a call.
Provider switching clears selected exchanges; selection is explicit for every
new send. The project/conversation location is remembered across page reloads.
Unsent prompts are not durable drafts and are cleared when navigating between
projects or conversations. Save project notes before navigating away.

## Limits and recovery

Up to 8,000 UTF-8 bytes in a new prompt, 20 selected exchanges and 32,000 bytes
of complete serialized input per provider. The US$0.25 per-request and
US$2 cumulative prototype limits still apply; context counts toward those limits.
The preview is an estimate, and the server rechecks allowance at send time.
The output cap remains 1,024 tokens per provider.

If interrupted, use Refresh history or Retry the saved request safely. The
original request ID is retained, preventing a duplicate call. Do not create a
new request until the original has a known state. Restarted calls with unknown
usage remain reserved and are never automatically replayed.

Existing independent history remains under Quick prompt / recent requests.
It is not automatically assigned to projects or sent as context.

## Architecture and privacy

Provider adapters and model selection are unchanged. The orchestrator sends a
single reviewed plain-text prompt containing an attributed JSON reference section
and the current request. This is explicit supplied context, not provider-hosted
conversation state or native message-array history. Exact per-provider inputs,
selected exchange IDs and the selected notes snapshot are stored with each task.
Project names and conversation titles are organizational labels, not implicit
model inputs. Provider keys remain in the Keychain; live data remains on the local machine.

Project separation is organizational, not multi-user authorization. Access is
confined to the localhost app on this machine. Routing, collaboration, tools and
LAN access are not enabled.
