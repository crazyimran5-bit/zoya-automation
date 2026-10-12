# Zoya engineering controls

Read README.md before making changes. This repository contains a reviewed subset of the Windows Zoya app, not a standalone automation installer.

- Verify the actual target installation, source hashes and process command lines before deploying. Checkpoints are rollback copies, never execution targets.
- Stop only app.py/zoya_desktop.py processes whose command line or working directory belongs to the selected Zoya installation.
- Use local_settings.load for Windows-encrypted credentials. Never print keys, environment dumps, cookies or decrypted settings. Do not commit runtime data, account configuration, audio, models, logs or user memory.
- Keep ElevenLabs model eleven_multilingual_v2, stability 0.35, similarity_boost 0.85, style 0.20 and use_speaker_boost true. Verify authentication, selected voice access and current quota separately.
- Preserve exact approval and budget checks. Paid/external requests need existing authorization. Publishing and uploads require explicit approval. Amazon restricted work is manual; WhatsApp/Telegram command automations stay paused.
- Keep BEGIN IMMEDIATE queue claims and persist uncertain outcomes. Never replay a timed-out handoff without reconciliation and a new appropriate approval.
- Distinguish provider readiness, tool invocation, child completion, artifact quality and end-to-end acceptance. Model prose claiming execution is not evidence.
- For code changes, run relevant fixture tests, syntax checks and a bounded live diagnostic. Report skips, unavailable integrations, stale health and untested microphone/voice quality honestly.
- Do not claim GitHub Copilot, Codex CLI or CI ran unless their actual invocation and results are recorded.
