# Zoya 4K production workflow

Implemented locally on Windows and provided here for review. This repository is a reviewed subset, not a complete installer.

## Contract
1. Save a structured script with stable character descriptions, voice descriptions, timed actions and dialogue.
2. Create a scene handoff for Higgsfield. Recheck current model constraints, workspace, cost and rights before generation.
3. Record each returned provider job ID against the exact handoff request key. Never resubmit an uncertain request; poll that job ID externally.
4. Download actual completed performance into the assigned Production4K workspace. Record file SHA256, provider job ID, performance method and explicit identity/action/lip-sync/voice/pacing review.
5. Generate local Whisper word timestamps from actual scene audio. Any word mismatch stops for review; manually corrected measured timestamps can also be supplied. The workflow validates ordering, bounds and caption text. Timestamp accuracy still requires listening review.
6. Local FFmpeg assembly preserves synchronized scene audio, exports 3840x2160/30fps H.264/AAC with subtitles.
7. Technical QA checks dimensions, frame rate, duration, audible audio, decode, black and frozen intervals. Character performance is a human review, not an automated semantic guarantee.
8. Final motion, lip-sync, voice, subtitle synchronization and visual continuity review is bound to final file SHA. Only then status becomes ready. Publication stays disabled.

## Recovery
SQLite saves each stage and provider ID. Repeated request IDs reject changed scripts. A scene cannot be assigned a second provider job. Restarted local renders become render_interrupted; resume checks input hashes and reuses an existing final only after QA. No external paid generation is replayed.

## Routes
Authenticated localhost POST /api/studio/{create,status,handoff,submission,accept-scene,auto-captions,captions,render,review}.
Production console: /studio.
All media paths are relative to workspaces/Production4K and resolved against that boundary.

## External blocker
The ChatGPT Higgsfield connection is available for supervised generation. It does not install an unattended transport into Zoya. Current laptop code does not call an undocumented Higgsfield endpoint or hold Higgsfield credentials. Provider submission/wait/download remain external; full hands-free production is not accepted as complete.
Source resolution is recorded separately: exporting at 4K can upscale a lower-resolution source.

## Validation
python -m unittest test_production_studio -v
14 fixture tests cover recovery, idempotency, changed media, scope, caption matching and performance review gates. They do not prove model-generated character quality.
