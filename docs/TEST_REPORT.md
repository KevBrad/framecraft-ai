# Test report

Historical publication evidence remains in [VERIFIED_RESULTS.md](VERIFIED_RESULTS.md).

## 12 November 2026 — experimental genre-aware release

- Continued the same repaired application with v3 speech selection still the default; v4.2 is explicitly opt-in.
- Final local full suite: **332 passed + 4 subtests in 68.54 s**; fresh TypeScript/Vite production build passed.
- Final browser: **9 passed checks**, zero errors; actual v4.2 edit/render/download/reopen, desktop and 390-pixel layouts.
- Five actual media runs, including continuous **15- and 60-minute** lecture inputs. Hour job cancelled after five completed ASR cores, resumed to **120 completed cores**, and exported successfully. Peak sampled private memory was **1,333.86 MiB**; wall time including cancellation/retry was **385.50 s**.
- Real silent game and sparse-dialogue cinematic paths completed. Output review exposed menu/static-tail false positives; generic v4.2 activity checks reduced the tested game to one **44.3–58.0 s** suggestion, retaining previous edits. These are activity heuristics, not semantic event recognition.
- All tested new exports passed full sequential FFmpeg decoding. Actual 1080p portrait and 16:9 landscape edits exported. Visual review confirmed the private 39-second sample already contains burned-in captions; use captions-off to avoid doubling them.
- A real regression run exposed non-atomic compound API reads. Deterministic concurrency tests reproduced the stale project/new failed-job combination. Project list/detail/upload/import response reads now synchronize with the worker transition lock; the real missing-model→retry path passed.
- Production startup and compiled v4 interface verified on loopback port 8000. After restart, **7 table hashes, 251 pre-existing source/export file hashes and private configuration remained unchanged**; original one project/five clips retained.
- Live Drive OAuth remains unverified without a configured real client/consent. No YouTube restrictions bypassed. No human-labelled genre/event precision, caption WER or competitor superiority measured.

See [complete release evidence](V4_RELEASE_REPORT.md), [sanitized benchmark numbers](benchmarks/v4-summary.json), [architecture](V4_ARCHITECTURE.md) and [genre research](research/GENRE_INTELLIGENCE_STUDY.md). Historical results above retain their original dates and scope.
