# Live verification sequence

1. Confirm Desktop Commander with a successful ping and a current device heartbeat. An online listing alone is insufficient.
2. Inspect the live installation and Git state. Read credentials through local_settings.load only; do not export them.
3. Run verify_elevenlabs_live.py locally. Confirm current authentication, selected voice access and quota; old results do not verify a newly saved key.
4. Back up the selected source, archive the conversational audio cache and restart only the selected app.py/zoya_desktop.py processes. Preserve the approved startup greeting and runtime databases.
5. Run relevant fixture tests, Python/JavaScript syntax checks and source-hash checks. Capture the actual desktop UI and test its primary controls.
6. Approve one bounded fresh TTS sample with the exact multilingual-v2 settings. Record duration, audio bytes, provider usage and playback separately. Never claim instant latency or human fluency without measurement/review.
7. Discover actual Skales roles and active workloads. Run bounded, read-only diagnostics against the discovered workers. Never reactivate paused work, broaden tool access, or replay uncertain submissions. A timeout is an unresolved result.
8. Export only reviewed source/tests/assets and default templates. Exclude secrets, logs, models, user memory, databases and account configuration. Compare Git blob IDs and live hashes before claiming synchronization.
9. Record Copilot/Codex invocation and CI results only when actually observed. Agent guidance files do not establish an authenticated integration.

The production_audit.py harness checks fresh local health, source hashes, a harmless text response and Skales provider readiness. It deliberately does not spend voice credits, submit agent work, restart processes or claim full end-to-end readiness. Those separately authorized live checks require a responding desktop.
