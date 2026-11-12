# Framecraft v4 experimental release — 12 November 2026

This release extends the existing local application. It does not replace the repaired v3 pipeline, database, source registry, importer, saved edits or exports. **Speech stories remains the default.** Select **Genre-aware discovery · experimental** to include measured visual activity and conservative content routing.

## Delivered behavior

- Versioned audiovisual evidence from actual decoded frames, recognized speech and existing audio-energy measurements. At most 6,000 small samples; no whole-video pixel array or full-recording spectrogram.
- Nine content choices, automatic evidence labels and 30-second regional routes. Gameplay, silent footage and sparse-dialogue cinematic clips can produce editable suggestions. A manual genre choice is explicitly labelled as the user's choice.
- Existing speech stories reused for dialogue. Education/comedy choices adjust existing extractive selection; visual routes add measured context windows. Scores and labels are heuristic, not calibrated event detection or predicted reach.
- Static/black inputs and text prompts without supporting speech cannot manufacture visual matches. Genuine completed no-speech recognition differs from model/recognition failure. Errors remain retryable and honest.
- Explainable observations and limitations in the desktop/mobile interface; reanalysis retains old edits and exposes previous selections. Cancelling the settings dialog restores the saved settings.
- Conservative full-frame fit for relevant visual/slide genres, restrained captions and editable aspect ratio. Use **16:9 + Fit** for slides and widescreen scenes; portrait Fit preserves composition but can make content too small.
- Every new export passes codec/duration/dimension checks and a complete sequential FFmpeg decode before publication. This detects technical corruption; it does not establish editorial quality or lip-sync accuracy.

## Executed verification

Final local suite: **332 passed + 4 caption-layout subtests in 68.54 seconds**, with no skips. This includes the existing intake/security/editor/export checks, 40 new visual/integration cases and two deterministic concurrent-status regressions. The earlier intermittent status-read failure was reproduced and fixed before this final run.

The exact lightweight CI command also passed **210 tests, with six media cases explicitly deselected**, against the sanitized source checkout. TypeScript/Vite production build passed. The real browser workflow passed **9 checks**, with zero browser errors, on both the initial v4.1 and final v4.2 gameplay project. It exercised opt-in controls, all genre choices, 390-pixel layout, real evidence, reanalysis cancellation, saved edits, render, playback metadata, download and persistence after reload. No media/API response was mocked.

Five real input files completed upload → analysis → saved edit → MP4 render → download through the existing API in a separate test data directory. The initial long benchmark ran on 12 November Malaysia time (11 November UTC). The final v4.2 refinement ran later on 12 November Malaysia time. Existing v3 comparison used the same recognized transcript, except the silent v3 failure was exercised through an actual API job.

| Source | Duration | Recognized words | Initial v3 / v4.1 candidates | Upload + analysis | Export | Sampled peak private memory |
|---|---:|---:|---:|---:|---:|---:|
| Silent SuperTuxKart recording | 70.83 s | 0 | 0 / 2 | 9.25 s | 7.80 s | 416.96 MiB |
| Sintel cinematic trailer | 52.21 s | 20 | 0 / 2 | 10.25 s | 7.53 s | 1,242.18 MiB |
| USGS continuous lecture excerpt | 15 min | 2,131 | 4 / 4 | 76.39 s | 8.75 s | 1,267.34 MiB |
| USGS continuous lecture excerpt | 60 min | 9,224 | 4 / 4 | 385.50 s | 8.78 s | 1,333.86 MiB |
| Private user-supplied interview excerpt | 39 s | 134 | 2 / 2 | 13.34 s | 7.53 s | 1,281.54 MiB |

Measured on one Windows Intel i7-11800H computer with about 16 GiB system RAM. ASR uses cached tiny.en CPU int8. Process-tree memory was sampled every 100 ms and includes worker subprocesses; it is not an exact Job Object high-water measurement. Times vary with concurrent machine activity. No GPU performance claim is made.

The hour-long job was deliberately cancelled after five completed 30-second ASR cores, then resumed to completion. The database records cancelled and completed jobs; the final checkpoint has **120 completed cores**. Its elapsed time includes cancellation, a one-second delay and retry, so it is not an uninterrupted speed result. This is a fresh long-video run, not a reuse of the October measurements.

### Quality refinement and output review

Source-only agent review described broad gameplay regions before examining candidates: menus roughly 0–15 s, racing 15–55 s, results 55–71 s, with about five seconds of uncertainty. These are coarse activity references, **not independent human highlight ground truth**.

The initial gameplay selections included a menu transition and a long results tail. Visual review caught these defects. The final v4.2 algorithm requires sustained neighbouring movement for activity anchors and shortens a sustained low-motion ending while retaining a closing beat. Cinematic pauses and recognized speech are protected. Generic numerical regression cases cover these rules; no source timestamps are hardcoded in the engine.

The final real reanalysis took **5.36 seconds** with existing preprocessing available. It returned **one** defensible activity proposal, **44.3–58.0 seconds**, instead of filling the requested count with weak windows. Its former ending was 66.3 seconds. All previous saved clip edits/versions remained intact. The new 13.7-second portrait MP4 decoded fully; the final browser test rendered and downloaded this final candidate again. A short results-screen beat still remains and semantic relevance still requires review.

The first benchmark produced five 1080×1920 exports. Three additional actual saved edits produced **1920×1080** cinematic and lecture exports, with full-decode verification. Representative frames from the beginning, middle and end were inspected. Full-frame fit preserved the game HUD and slide contents. Widescreen examples improve slide readability; no automatic OCR/layout understanding is claimed. Low-resolution sources remain low detail after upscaling. The current private interview is a pre-edited 39-second source with embedded captions; disable Framecraft captions to avoid a duplicate layer. It is not the historical 13-minute source from the memory-failure report.

Audio streams were checked for presence, codec and duration and decoded in full. This was not a blinded listening or word-error-rate study. The trailer's near-black opening comes from the source; scene and narrative quality are not inferred from decoder success.

### Preservation and compatibility

Before modifying code, a consistent SQLite snapshot, source backup and private configuration backup were made outside the application data directory. Read-only comparison found **all seven production table row hashes unchanged**, **251 pre-existing source/export files unchanged**, and the backed-up private configuration unchanged. The production workspace contains one project and five clips; test projects remain separate. No schema migration was needed. The existing import/SSRF/OAuth/registry/queue tests remain in the full suite.

A final regression run exposed an intermittent status read: a request could observe old project fields and a newly failed job across separate SELECT statements. The release fix synchronizes project detail/list snapshots with the worker's existing transition lock. A deterministic concurrency regression covers the failure, and the real missing-model → retry path is rerun. This is a consistency repair, not a delayed polling workaround.

### Capability boundaries

| Capability | Release status |
|---|---|
| Bounded local transcription, checkpoints, cancel/retry | Implemented; real 15/60-minute runs verified |
| Authorized upload/direct URL/creator-source reuse | Existing implementation retained; regression covered |
| YouTube metadata vs processable media distinction | Retained; no stream scraping or restriction bypass |
| Google Drive OAuth/picker/download | Existing implementation retained; live consent still unverified without a real configured client |
| Silent/low-dialogue measured activity | Functional experimental path, verified on limited footage |
| Cinematic, lecture and interview workflow | Actual inputs/exports verified; semantic editorial quality not established |
| Sports/comedy/tutorial/other specialist accuracy | Route controls and heuristics exist; representative real benchmarks incomplete |
| Game kills/wins, goals, plot/payoff, HUD OCR, laughter identity | Not implemented; image/audio changes never labelled as these events |
| Forced alignment, reliable diarization/active speaker, heavy VLM | Deferred pending model, resource and accuracy validation |
| Human editorial acceptance, caption WER, genre precision/recall | Not measured; no competitor-superiority claim |

The release is a tested incremental research implementation, not completion of universal video intelligence. The 21-genre research matrix explicitly distinguishes implemented signals from proposed capabilities.

## Reproduce and use

Run the existing `scripts/start.ps1` from the same project folder; open `http://127.0.0.1:8000`. For visual material, choose the experimental analysis approach, select a content type if known, and leave text focus empty for visual-only suggestions. Review evidence, open the editor, correct boundaries/captions, choose 16:9 for wide material if appropriate, save and render. Existing projects keep their saved engine until explicitly reanalyzed.

Run `python -m pytest backend tests -q` inside the configured virtual environment after preparing the documented speech/face fixtures. Run `npm --prefix frontend run build`. The portable `frontend/tests/browser-v4.mjs` accepts `FRAMECRAFT_URL`, `FRAMECRAFT_TEST_PROJECT_ID`, `FRAMECRAFT_TEST_DISPOSABLE=1` and `FRAMECRAFT_ARTIFACTS`; use an isolated test workspace. The [architecture guide](V4_ARCHITECTURE.md) describes all prerequisites and controls.

Public numeric evidence is in [v4-summary.json](benchmarks/v4-summary.json). Raw local logs, private source media, model caches, OAuth files and runtime databases are deliberately outside the public source checkout. The [genre study](research/GENRE_INTELLIGENCE_STUDY.md) provides source provenance and study limits; [competitive research](research/COMPETITIVE_RESEARCH_2026.md) separates vendor documentation from actual observations. Research was based on official public information; no competitor implementation was copied.
