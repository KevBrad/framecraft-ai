# Framecraft competitive and technical research

Research checked **12 November 2026**. This report informs the existing local application; it is not a claim of competitor parity or superiority. Sources are primary vendor documentation, official repositories and research papers. Public pages were inspected without paid accounts or uploading test videos to competitors.

## Evidence rules

- **Observed:** a page response, local source inspection or a recorded local experiment.
- **Documented:** a vendor or project maintainer states the capability; implementation quality has not been independently established.
- **Inference:** a design implication derived from the evidence.
- **Hypothesis:** a proposition requiring an experiment.
- **Proposal:** an original Framecraft engineering choice. Similar broad ideas may exist elsewhere; no novelty or patent claim is implied.

An accessible marketing page is evidence of a current public offering, not proof that signup, billing, ingestion or rendering works. Internal model architectures, event accuracy, caption error rates, ranking calibration, render failure rates and resource costs remain unknown unless explicitly documented below. No competitor received an independent end-to-end benchmark in this work. Prices and plan entitlements are deliberately excluded because no purchase decision depends on this study.

## Representative capability matrix

All links were accessed on the date above. Features in the middle column are **documented vendor claims**. The last column is a Framecraft engineering inference, not an evaluation score.

| Product / current public status | Documented focus and workflow | Engineering implication / unresolved evidence |
|---|---|---|
| [OpusClip / ClipAnything](https://www.opus.pro/clipanything) — page accessible | Visual, audio and sentiment cues; prompt-directed moments, including content beyond speech | Add visual evidence and preserve provenance. Whether it consistently recognizes silent events or complete stories is untested here; no private model architecture is inferred. |
| [Vizard](https://vizard.ai/blog/why-clips-used-to-get-cut-off-mid-sentence-and-how-we-fixed-it) — dated 15 July 2026 article accessible | Describes separate opening/end selection, protecting a natural conclusion before duration adjustment, completeness checks, relative ranking and overlap removal | Separate candidate discovery from boundary validation. Treat completeness as a review gate. Its claimed publishability is not a measured Framecraft comparison. |
| [Klap](https://klap.app/) — page accessible | File/YouTube-link entry and scene-dependent layouts including split screen, screencast and gaming | Layout should depend on content. A competitor's link field does not establish its legal ingestion agreement or authorize Framecraft to reproduce it. |
| [Spikes Studio](https://www.spikes.studio/) — page accessible | Long-video clip generation with link/file entry; currently also promotes a micro-drama offering | Do not assume that every listed genre has equal quality. Import internals and completeness accuracy unknown. |
| [quso.ai](https://quso.ai/) — page accessible | AI clips, subtitles, repurposing, editing and social planning/scheduling | Review/edit/publish is a broader product workflow. Scheduling is not required to establish local clip quality. |
| [Reap](https://reap.video/) — page accessible | Clipping, prompts/API/MCP, captions, transcription, text-based editing, reframing and scheduling | Extensible interfaces are useful, but editing actions need persistent, replayable state. No API or account tested. |
| [Munch / Munch Studio](https://www.munchstudio.com/) — observed redirect from getmunch.com | Current destination markets automated social-media marketing from a business website | Original Munch clipper availability is **unconfirmed**. Do not reuse historical feature tables as current evidence. |
| [Descript / Underlord](https://www.descript.com/clips) — page accessible | Clip creation within a transcript-oriented editing platform; captions, multicam and audio/editing tools are exposed | Editable transcripts and clear corrective controls matter as much as first-pass selection. Speaker accuracy and exported quality not measured here. |
| [Submagic](https://www.submagic.co/) — page accessible | Magic Clips, captions, trimming, zoom, B-roll, transitions, sound effects and publishing | Styling should be independently adjustable. Adding effects does not demonstrate better moment discovery. |
| [Captions](https://captions.ai/) — page accessible | AI editing with selectable presentation styles | Offer restrained and expressive presentation choices. Evidence for long-video event discovery is insufficient in this review. |
| [CapCut](https://www.capcut.com/tools/long-video-to-shorts) — tool page accessible | Long-video clips, editable captions and review before export; lists podcasts, education, commentary and speeches as suitable inputs | Keep human correction easy and avoid equating generated captions with accurate timings. Feature availability by account/region untested. |
| [VEED](https://support.veed.io/en/articles/11652474-how-to-use-our-clips-feature) — help article dated 21 August 2026 accessible | Speech-oriented clipping, reframing, subtitles and trimming; requires 1 minute to 3 hours of speech; explicitly says YouTube-link import is unavailable | Source duration and speech duration differ. Framecraft should say whether media is available, not equate metadata with import success. No third-party downloading workaround is adopted. |
| [Riverside](https://riverside.com/) — riverside.fm redirects here | Recording, editing, repurposing and distribution for podcasts, interviews, webinars and livestreams | Creator-owned originals simplify authorized import; source quality should survive processing. No claim about undocumented ranking internals. |
| [Eklipse](https://eklipse.gg/features/) — page accessible | Gaming stream highlights, live/VOD clipping, custom game clip settings, editing and publishing | Game-specific context and HUD preservation deserve their own evaluation. Claimed game coverage is not proof of kill/win detection accuracy. |
| [Medal](https://medal.tv/) — page accessible | Local game capture, user-triggered recent-event clipping and sharing | Capture-time intent is valuable supervision. Recording/clipping is distinct from retrospective autonomous event selection. |
| [Framedrop](https://framedrop.ai/) — fetch returned HTTP 502 | Current capability could not be verified | **Operating status unknown.** A failed fetch is not evidence of discontinuation. No implementation decision depends on it. |
| [StreamLadder](https://www.streamladder.com/) — page accessible | Stream-to-social clipping and styling for Twitch, Kick and YouTube creators | Preserve gameplay and camera regions when converting aspect ratio. Provider agreements, supported source details and measured accuracy unknown. |

The repeated, useful patterns are separate discovery and editing stages; context-aware boundaries; layouts appropriate to the source; and fast correction of a generated proposal. Public score labels and “viral” claims provide no calibrated probability of audience response. Framecraft must expose the evidence behind its heuristic scores and avoid manufacturing that certainty.

## Technical approaches and resource fit

The observed machine has 16 GiB RAM and an Intel i7-11800H. An RTX 3070 Laptop GPU is detected, but this release retains the tested local CPU inference path. GPU model loading, usable VRAM and CUDA compatibility have not been benchmarked for these proposals. Costs below are qualitative engineering expectations unless explicitly measured in the accompanying genre study.

| Primary source / method | Finding and practical cost | Decision |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Maintainer documents CTranslate2 inference, CPU int8, word timestamps and batching. Its own tables show that increasing batching trades memory for speed; those numbers are not this computer's benchmark. | Preserve existing chunked CPU int8 transcription and checkpointing. Do not restore whole-file FFT/audio allocation or increase concurrency without measurement. |
| [OpenCV VideoCapture](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html) | Sequential grab/retrieve supports incremental local decode. Runtime still grows with source decode duration even if retained samples are capped. | Reuse installed OpenCV, a small proxy and bounded low-resolution samples. Keep source timestamps and record decode failures. |
| [PySceneDetect detectors](https://www.scenedetect.com/docs/latest/api/detectors.html) | Content and adaptive detectors measure color/frame changes; adaptive differences can reduce fast-motion false cuts. These signals detect boundaries, not story meaning. | Use existing lightweight frame/histogram measurements first; reserve a controlled PySceneDetect comparison before adding another dependency. |
| [WhisperX](https://github.com/m-bain/whisperX) | Adds phoneme alignment and speaker diarization; maintainer notes overlap/alignment limitations and language-specific models. Diarization requires model access acceptance/token. Adds model downloads and inference cost. | Defer as an optional adapter. Benchmark timing error, overlap and memory against current ASR before adoption; do not infer active speaker from face presence. |
| [FFmpeg](https://ffmpeg.org/ffmpeg.html) | Explicit stream mapping, filtering and decode/transcode tools support controlled media pipelines. A valid container header alone does not establish that all output frames decode. | Keep argument arrays and local inputs; decode exported audio/video before atomic publication. Render checks do not establish editorial quality. |
| [LongVILA paper](https://arxiv.org/abs/2408.10188) | Long-context video-language research combines model/system changes; paper demonstrates computationally demanding distributed training and long-video tasks. Research benchmark performance is not clip-editor performance. | Defer local long-video VLM deployment. A future bounded candidate-only adapter needs licence, hardware, hallucination and temporal-grounding evaluation. |
| [InternVideo2 paper](https://arxiv.org/abs/2403.15377) | Video representation, cross-modal training and video dialogue support research into retrieval/understanding. Paper results do not measure this user's genres or hardware. | Consider embeddings for later retrieval/diversity experiments, not as an untested replacement for working processing. Review code and individual checkpoint licences separately. |

Additional audio-event classifiers, OCR, object tracking and action models may help distinguish applause, score changes or important screen regions. They were not installed or independently evaluated in this work. Loudness alone is not laughter; motion alone is not a kill; a cut is not a narrative payoff. These are explicit constraints on product labels, not merely research caveats.

## Framecraft decisions supported by the research

Source inspection confirms a new versioned `backend/intelligence.py` module alongside the existing speech selector. Its current approach retains up to 6,000 visual samples, uses frames no larger than 320 x 180 for measurements, reports coarse local genre regions and combines visual proposals with existing speech candidates. Metadata and filenames are not the genre classifier. Caches are bounded and validated; the default v3 path remains available. This is a conservative evidence layer, not universal video understanding.

The current gaps are semantic event identity, plot/character relationships, calibrated genre confidence, human-validated highlight quality, true speaker association, protected HUD/slide region detection and broad genre benchmark coverage. Full-frame fit avoids crop loss but can make small text harder to read in portrait output. Those gaps should be visible to reviewers rather than hidden behind high scores.

Priorities are: (1) preserve reliable import/transcription/export and user edits, (2) allow genuine visual-only inputs without inventing transcript success, (3) preserve setup and ending where known, (4) display source-grounded observations and uncertainty, and (5) measure selection quality before changing the default engine. See [Genre Intelligence Study](GENRE_INTELLIGENCE_STUDY.md) for proposals, experiments and the limits of the current real-media evidence.

## Ingestion boundary

Public competitor interfaces do not provide evidence of an ingestion licence. Framecraft retains a distinction between a URL describing a video and an authorized retrievable media source. YouTube metadata must not be reported as downloaded media. Creator uploads, approved direct media, an authorized Drive connection or a previously registered original can supply actual media; new YouTube ingestion adapters require an explicit approved mechanism. This research does not recommend scraping streams, reproducing proprietary integrations or bypassing restrictions.

## Release evidence

Use the current `docs/TEST_REPORT.md` and machine-readable benchmark artifacts for executed checks. This report does not convert historical test counts into current results. The initial five-input v4 study is a feasibility check; it cannot support a claim that Framecraft beats competitors or produces professional clips across all genres. An independent human reference set, controlled settings, repeated runs and blind review are required for such claims.
