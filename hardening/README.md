# Zoya audit fixes — staged, not installed

The owner's laptop was offline when this patch was prepared. No desktop installation, paid voice generation, model upgrade, GitHub credential change or end-to-end execution is claimed.

## Changes

- Exact ElevenLabs multilingual-v2 payload with stability 0.35, similarity boost 0.85, style 0.20 and speaker boost true. Existing voice ID retained. Normalized speech text and punctuation are included in the approval hash. Existing transport, budget and retry guards are retained.
- Native Vosk output receives conservative Unicode/whitespace cleanup. Confirmation tokens, names, numbers and scripts are preserved. This cannot repair a recognition error or make a Hindi model multilingual.
- Native voice cache now includes model and exact settings, preventing old tuning audio from being reused. Existing cached startup greetings are retained; regenerate them only after separate voice approval.
- Documented Skales tool_call/tool_result events are correlated by tool name; text/done is needs_review. Required tools can be preflighted against GET /api/cli/tools. Truncated result text is transport evidence, not complete business verification.
- SQLite queue claims use BEGIN IMMEDIATE, independent connections and a 10-second busy timeout. Result and state persist together. Interrupted running jobs require review. Cancellation survives late results. Unknown operations are never automatically retried.
- Read-only Copilot user-token diagnostic. Current connector repository access works but earlier Copilot assignment returned HTTP 403. This does not establish a malformed header. No connector credentials are accessible to this patch.

## Validation

13 tests passed on the Codex Linux Python runtime: exact payload, mocked actual HTTP transport, hash mismatch/no network, cache identity, STT preservation, SSE evidence/failure classification, bridge registry/stream integration, existing budget guard preservation, concurrent SQLite claims, restart recovery, cancellation, and pre-write installer validation. This is not Windows hardware acceptance. Tests use named historical fixtures with no real credentials.

## Installation after reconnecting

Use Python 3.13 on the laptop. Place this folder beside the installed project or in its economy-setup area. Do not copy the fixtures over the live application.

1. Run `py -3.13 install_hardening.py --root "<actual installed Zoya project>"` from this folder. This validates source shapes without changing files.
2. Stop only the Zoya desktop and backend, keeping unrelated programs running. Do not delete memory, settings, queues or keys.
3. Run the same command with `--apply`. It checkpoints source and SQLite databases, preserves unrelated modules/settings and rolls source back if a write fails. A source mismatch stops installation before mutation.
4. Restart existing Zoya desktop entry point and backend. Run existing readiness diagnostics and the hardware acceptance tests below.

The installed natural_voice.py was unavailable for a fresh inspection. The patch deliberately refuses unknown structure instead of replacing the whole live file with its older fixture. It must be reviewed against the actual source on reconnection.

## Live acceptance remaining

- Inspect current ElevenLabs transport and settings; install only if compatible.
- Hear a fresh selected-voice sample with the exact settings, verify punctuation and accent, and distinguish cached startup audio from new audio. Never promise human-like quality from parameter values alone.
- Speak Urdu/English and check transcription against what was said. Vosk Hindi quality remains a known limitation.
- Run one specifically approved non-publishing tool task. Use `dispatch(message, required_tools=('get_system_info',))` only if registry confirms that name enabled. Do not invent a direct execution endpoint: DevKit exposes chat/SSE and tool discovery; a model that cannot call tools still needs a supported tool-capable configuration.
- Confirm thread heartbeat and queue recovery on real Windows restart. Scope here is native desktop queue; all SafeEngine specialist workflows remain subject to live verification.
- No publishing/accounts/budgets are unlocked.

## Copilot 403 diagnosis

Official GitHub documentation says cloud-agent API requires a user token, not an installation token. Fine-grained issue assignment needs Metadata read plus Actions, Contents, Issues and Pull requests read/write. Classic PAT uses repo scope. Copilot must be enabled for the user/repository and an eligible paid plan is required. The connector's hidden token type/scopes and account subscription are not known.

Run `copilot_auth_diagnostic.py` only with an already authorized user credential in GH_USER_TOKEN. Never paste it in chat or Git. The script performs read-only requests and does not claim assignment succeeded. A GraphQL feature header is used only for GraphQL; it is not a fix for a REST connector's 403.

Sources:
- https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api
- https://github.com/skalesapp/devkit/blob/main/docs/api-reference.md
- https://elevenlabs.io/docs/api-reference/voices/settings/get

## Rollback

Stop Zoya desktop/backend, restore the changed source from data/audit-fix-checkpoint-*, remove helper files that did not exist before, compile and restart. Preserve current databases. Database backups are for deliberate recovery, not blind overwrite. No auto-merge or automated deployment.
