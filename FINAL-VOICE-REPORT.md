# Zoya final voice review — 12 October 2026

## Result
The local desktop voice foundation was repaired and rechecked on Windows. Full business automation is **not production accepted**.

| Area | Evidence | Status |
|---|---|---|
| Native microphone | Fresh heartbeat, physical microphone open, not paused/locked | Running |
| Local chat | Greeting reply in 0.084 seconds, one Ji boss | Passed for greeting only |
| Autosave | Database backup and dashboard-only screenshot created; 600-second worker running | Active; later scheduled cycle not yet observed |
| Media path | FFmpeg/ffprobe resolve after loading configured local tool path | Fixed |
| Render | 3840×2160 MP4, 2.1 seconds, audio mean −16.6 dB, burned subtitles, no black segments detected | Technical fixture passed; not a production episode |
| Memory | SQLite quick_check returned ok | Passed |
| Startup | Current-user shortcut points to installed Python and desktop entry point | Registration verified; real restart/wake/unlock acceptance pending |
| Quran | Corpus hashes and 22 recorded ayat validated | Cache passed; user hearing playback pending |
| Skales | Authenticated local provider reachable; one read-only chat test took 72.61 seconds and returned text/done only, explicitly without tools | Tool execution failed; do not mark task complete |
| Physical voice quality | Existing voice selection preserved | User listening/accent acceptance pending |
| YouTube/Amazon | Production workflows and authenticated accounts not validated in this review | Pending |

## Applied fixes
- Initialize FFmpeg path before desktop launches backend/media work.
- Launch autosave with its existing OS lock to prevent duplicate workers.
- Use Roman display spelling map rather than generic transliteration.
- Show readable Skales results rather than raw event JSON.
- Keep voice error visible when returning to listening.
- Classify Skales's explicit no-tools timeout response as needs_review, preserve reason, and never retry automatically.

## Validation
- Three targeted regression tests passed on Windows Python 3.13.
- Desktop and bridge compilation passed.
- Six readiness stages passed before the final error-display patch; desktop heartbeat checked again after restart.
- No paid API generation was requested by these diagnostics/render/test tasks.
- The fast greeting uses a direct reply route; it is not a complex generation or audible latency measurement.

## Scope and rollback
The branch contains selected voice-controller files and mocked tests, not a standalone installable application. Required installed sibling modules include conversation_style, quran_voice, app_catalog, local_settings, budget_guard and natural_voice. Keep runtime configuration out of Git.

Local checkpoints retain the prior voice-controller and bridge files plus an earlier database backup. To roll back, stop only the Zoya desktop process, restore these selected source files from the checkpoint, compile and relaunch. Do not overwrite current databases or provider settings.

GitHub hosts this review. No Copilot review, GitHub Actions run, verified ten-agent execution, full multilingual recognition or production publishing is claimed. Existing Hindi Vosk recognition remains a limitation for Urdu/English speech.
