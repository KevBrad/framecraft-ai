# Verified engineering results

Audit date: 29 October 2026. This summary separates checks rerun for publication from retained release evidence. It contains no user transcripts, private project names, machine-specific paths, credentials or raw databases.

## Current checks

- **290 automated tests and four additional caption-layout subtests passed** in 78.73 seconds, with no skips or failures. Command: `python -m pytest backend tests -q` using the existing configured environment. The suite includes real media/API/FFmpeg processing, plus deterministic and mocked transport/OAuth tests. A test count is engineering evidence, not market traction.
- **TypeScript and Vite production build passed** using `npm --prefix frontend run build -- --outDir <isolated-build-directory>`. The live application's compiled assets were not replaced by this check.
- **Existing application state remained unchanged**: before/after full-table row hashes matched for all seven persistence tables at SQLite schema 2. The current project original and four retained reusable library originals also retained matching SHA-256 hashes. No core algorithm was edited during this audit.

## Historical long-video processing

Hosted verification after publication also passed: [source CI](https://github.com/KevBrad/framecraft-ai/actions/runs/40841728996) ran 177 lightweight tests with two explicit media-test exclusions in 11.10 seconds and built the frontend. The [manual clean Windows media workflow](https://github.com/KevBrad/framecraft-ai/actions/runs/40841863684) prepared documented public fixtures and passed all 290 tests plus four subtests in 64.01 seconds. This adds clean-environment reproducibility; it is not a new 15-/60-minute benchmark.

The following results come from the **23 October 2026 release evidence**, re-inspected on 29 October. The complete long recordings were **not reprocessed in this publication pass**.

|Source duration|Import/upload plus analysis|Timestamped words|Peak sampled private memory|
|---|---:|---:|---:|
|809.03 s|101.20 s|2,750|1341.02 MiB|
|900.07 s|104.08 s|2,131|1309.41 MiB|
|3600.07 s|311.62 s|9,224|1397.46 MiB|

The interview source was supplied for authorized private processing and is excluded from publication. The 15- and 60-minute tests used continuous excerpts of a [USGS public-domain lecture](https://www.usgs.gov/media/videos/pubtalk-022024-using-communication-science-communicate-usgs-science), without repeated short segments. Timings include validation, real CPU speech recognition, selection and framing, and exclude later export. Memory was sampled across service descendants every 100 ms, rather than read from an exact Job Object high-water counter. Workstation load was not controlled. These numbers are not cross-product speed benchmarks.

Two resulting lecture story exports were probed again on 29 October: real 1080 x 1920 H.264/AAC MP4s at 30 fps, lasting 51.0667 and 54.6333 seconds. They are selected stories from long sources, not the complete 15-/60-minute recordings. Their existence and container properties do not independently establish editorial quality.

## Browser evidence

Release artifacts record **nine new real workflow checks and eleven existing browser checks** on 23 October, including real upload/analysis/edit/render/download, ZIP and saved-edit persistence. Separate archive-generation and read-only production checks also passed. These browser checks have not been rerun by this evidence audit; do not label them fresh 29 October browser results. A separate current safe-demo capture can provide new UI evidence if the publishing workflow performs it.

## Architecture verified against source

React/TypeScript and Vite provide the editor; FastAPI serves the interface and APIs. SQLite WAL schema 2 stores projects, edits, exports, jobs, imports, source originals and reference associations. A durable import consumer validates downloaded media before creating a project. A separate analysis consumer launches a gated subprocess with a Windows Job Object memory limit. The worker normalizes audio, feeds bounded PCM windows to faster-whisper, derives an extractive topic map and heuristic highlights, samples OpenCV faces/scenes, then renders saved edit specifications through FFmpeg/libass. ffprobe validates real files. There is no Redis, PostgreSQL, hosted AI service or general active-speaker model in this implementation.

The local balanced model is tiny.en with beam 3; optional quality uses a cached multilingual base model with beam 5. Both use CPU int8. Caption corrections clear stale word estimates. Five newer caption presets coexist with five retained legacy styles. The director samples at most 2,400 frames at a work dimension bounded to 640 pixels. New analyses distinguish current selections from preserved previous edits.

## Memory-allocation case study

The historical test report records three saved worker errors involving a 243 MiB complex128 array shaped `(1, 79376, 201)`. The report describes a previous whole-WAV path into speech feature extraction. That is historical application evidence; this audit did not rerun the pre-repair implementation or establish an upstream-library defect.

Current code visibly reads **30-second cores with two seconds of context per side**, converts bounded PCM to float32 and assigns words to a core by their timestamp midpoint. It writes completed cores atomically and resumes them after interruption. Native thread pools stay bounded. The documented repair kept the **2,048 MiB worker budget**. This supports the engineering explanation that bounding feature-extractor input controls recording-length-dependent memory pressure. The exact internal array allocation path remains an inference from historical evidence.

## Claims to avoid

Do not describe scores as validated virality probabilities, test counts as customers, a cached model as an external AI service, safe fit as active-speaker recognition, word estimates as forced alignment, metadata lookup as a YouTube media download, or historical runs as newly executed tests. Source quality limits still apply to 1080p upscaling. Live Google Drive authorization, broad competitive superiority, customer demand and commercial traction remain unestablished.
