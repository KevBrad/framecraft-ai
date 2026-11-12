# Framecraft genre intelligence study

Updated **12 November 2026**. This study distinguishes implemented mechanics, recorded experiments, unverified hypotheses and deferred work. Its scope is the existing local Framecraft application. The v4 evidence engine is experimental and opt-in; the established v3 speech workflow remains the default.

## What genre intelligence must preserve

A useful clip contains an understandable event or thought, including the necessary setup and payoff. The strongest speech, fastest image change or loudest sound is not necessarily that event. The same measurement can mean a winning action, camera shake, a title transition or an irrelevant interruption. Automatic selection therefore needs an evidence record, a conservative fallback and editable boundaries.

The implementation currently offers `auto`, `general`, `podcast`, `gaming`, `cinematic`, `education`, `sports`, `comedy` and `visual` routes. Automatic labels use recognized vocabulary, speech coverage and measured image change. They are uncalibrated heuristics. A user override expresses editorial intent; a value of 1 for an override does not mean perfect automatic classification. Cinematic selection can be requested manually; there is no implemented semantic cinematic classifier.

## Genre requirements and coverage

These are **engineering requirements**, not claims that every row is fully implemented. Similar categories deliberately share a route rather than becoming disconnected applications. “Unbenchmarked” means no representative real source for that category was independently evaluated in this work.

| Requested category | Important event/context; editing constraint | Current route and evidence |
|---|---|---|
| Podcasts / interviews | Question, answer and conclusion; maintain turn-taking and avoid severed words | Existing speech selection plus podcast/general. One 39-second private interview tested; no labelled speaker or WER evaluation. |
| Gaming gameplay | Setup, action, outcome; preserve HUD, aim/trajectory and relevant screen area | Gaming/visual with manual route available. One 70.83-second silent racing sample tested. No kill, win or score recognition. |
| Gaming livestreams / VODs | Distinguish menus, chatting, gameplay and reactions over time | Coarse 30-second local routes provide a starting point; full mixed livestream unbenchmarked. |
| Cinematic / fictional scenes | Character intent, setup and consequence; avoid breaking shots or emotional beats | Manual cinematic route and cut hints. A 52.21-second already-edited trailer tested; plot understanding unimplemented. |
| Movies / television excerpts | Maintain dialogue context and chronology; source authorization remains separate | General/cinematic. No representative full scene benchmark. |
| Animation / anime-style footage | Similar narrative needs, abrupt stylized motion can mimic a cut | Sintel provides one animation example; not a representative animation benchmark. |
| Sports / competition | Identify play, outcome and replay; retain scoreboard and field context | Sports requires lexical/motion evidence or override. Event semantics and sports footage unbenchmarked. |
| Educational video | Claim, explanation, example and conclusion; keep diagrams visible | Education route and full-frame fit. Continuous 15- and 60-minute lecture excerpts tested. |
| Tutorials / screen recordings | Preserve ordered steps and the relevant control/text; avoid tiny unreadable crops | Education/general; no OCR or demonstrated step-order model. Unbenchmarked. |
| Documentaries | Preserve attribution, causal context and narration/image relationship | General with speech and visual evidence; no documentary story model. Unbenchmarked. |
| News / commentary | Keep qualifications, dates, speaker attribution and counterpoints | General speech route; factual/narrative completeness needs review. Unbenchmarked. |
| Comedy / stand-up | Setup, timing, punchline and audience response | Comedy lexical route; no verified laughter detector. Unbenchmarked. |
| Vlogs / lifestyle | Distinguish an event from transitions; preserve personal context | General/visual fallback. Unbenchmarked. |
| Travel / adventure | Reveal, action or destination context; scenery alone may not tell a story | General/visual fallback; no landmark or adventure event recognition. Unbenchmarked. |
| Product reviews | Preserve test conditions, result and caveat; keep product detail readable | General/education; no product or evaluative-claim classifier. Unbenchmarked. |
| Technology demonstrations | Show action and visible result in the correct order | Education/visual; no screen-region protection. Unbenchmarked. |
| Reactions / commentary | Link the reaction to its stimulus; preserve both relevant views | General; active-speaker and stimulus association unimplemented. Unbenchmarked. |
| Music / performance | Preserve musical phrasing and choreography; avoid treating lyrics as discussion | Visual/general fallback; beat/phrase segmentation unimplemented. Unbenchmarked. |
| Events / conferences | Preserve speaker, claim, slide and audience context | Lecture samples cover a narrow speaking case only; event transitions unbenchmarked. |
| Silent / low-dialogue content | Select actual visible activity without fabricating speech | Visual proposals operate without an audio stream; silent gameplay and sparse-dialogue trailer tested. Semantic importance remains unknown. |
| Mixed-format content | Recognize local changes without imposing one source-wide label | Multilabel evidence and coarse regional routing exist; precision and transition timing unmeasured. |

## Implemented evidence architecture

Code inspected: `backend/intelligence.py`, version `multimodal-evidence-v4.1`. Shared preprocessing, transcription, persisted projects and export remain in the existing pipeline.

1. Analyze a local proxy sequentially. Keep at most 6,000 timestamped samples; the nominal half-second interval grows for longer videos. Resized measurement frames are at most 320 x 180. This bounds retained evidence, but decoding still costs time proportional to source duration.
2. Record image difference, histogram change, brightness, detail and possible cuts. These are observations, not semantic labels. Silence or missing audio is represented honestly.
3. Combine speech coverage and transcript vocabulary with visual measurements for conservative global and 30-second local genre evidence. Insufficient evidence uses a general route; the user can override the route.
4. Reuse the v3 speech selector. Generate additional activity-centered visual proposals with surrounding context and cut hints. Respect speech boundaries when possible; do not manufacture a sentence to justify a visual proposal.
5. Attach observations, selection basis, limitations and boundary notes. Use restrained caption defaults and full-frame fit for relevant routes. Existing saved edits must survive reanalysis.
6. Cache small visual evidence with source/proxy size, modification time and engine version. Validate finite values, ordering and timeline coverage; publish cache files atomically. The visual cache allows route experiments without repeating pixel analysis.

Size/mtime fingerprints are useful local invalidation signals, not cryptographic content identity. They must not become a claim that an adversarially modified file is identical. ASR checkpoints remain separate from visual caches. Neither cache should override a saved user edit.

## Five original engineering proposals and current scope

These proposals are original design combinations for this project, not claims of research novelty. Costs are expectations until isolated profiling exists.

| Proposal | User value and implementation status | Complexity / cost / risk | Acceptance experiment |
|---|---|---|---|
| Shared evidence timeline | Implemented bounded visual timeline alongside speech/audio evidence; future providers can add timestamped observations | Medium; sequential proxy decode and small retained data. Sparse samples can miss very short actions | Compare identical source runs, cap retained samples, test corrupt/truncated caches, and check timestamp bounds. |
| Context envelopes around candidate events | Implemented conservative surrounding windows/cut hints and inherited speech boundaries; not a semantic setup/payoff model | Medium; cheap after analysis. Extra context may include menus or irrelevant aftermath | Blind source/clip review; count missing setups, severed endings and irrelevant tails, not merely clip count. |
| Genre-aware preservation defaults | Implemented full-frame fit and restrained styling for suitable routes | Low inference cost. Letterboxing preserves content but can reduce readability on a phone | Check HUD/slide visibility and phone-size legibility separately; compare fit/crop with the same candidate. |
| Evidence and uncertainty on every proposal | Implemented selection basis, measured observations and explicit limitations | Low cost; explanatory text must not turn a proxy metric into an invented event | Test that motion is never labelled as a kill/goal, override confidence is labelled as override, and no-audio input has no invented transcript. |
| Reusable analysis with controlled A/B selection | Implemented versioned cache and retained v3 selector enable repeatable route comparison | Medium persistence/testing work, avoids repeated decode. Stale/mismatched caches can corrupt comparisons | Freeze source, transcript and settings; invalidate modified media/version; report changed candidates and quality separately from faster cache hits. |

Deferred higher-cost work includes actual game-event/HUD OCR adapters, sports temporal grounding, screen-region protection, shot/character story graphs, trained audio-event recognition, active-speaker association, optional forced alignment and semantic video embeddings. Candidate-only VLM review is more plausible than sending an entire hour to a large model, but it remains unimplemented and needs licence, privacy, hardware and hallucination tests.

Technical rationale and primary sources: [OpenCV sequential video capture](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html), [PySceneDetect boundary detectors](https://www.scenedetect.com/docs/latest/api/detectors.html), [faster-whisper CPU int8](https://github.com/SYSTRAN/faster-whisper), [WhisperX alignment and diarization](https://github.com/m-bain/whisperX), [FFmpeg processing](https://ffmpeg.org/ffmpeg.html). These support engineering mechanisms; they do not establish highlight quality.

## Initial real-media experiment

The following is the **initial v4.1 benchmark completed 12 November 2026 Malaysia time (11 November UTC)**, read from `work/framecraft4/benchmark.json`; its date field uses the UTC date. Later fixes or re-runs must be reported separately in the current test report; these values must not silently become measurements of newer code. Each export time is for the benchmark's selected export, not the whole source or every possible clip. Memory is the harness's sampled process private-memory peak, not total machine RAM, GPU memory or a guaranteed upper bound.

| Source | Duration | Recognized words | v3 / v4 proposals | v4 analysis | Selected export | Sampled private peak |
|---|---:|---:|---:|---:|---:|---:|
| Silent SuperTuxKart gameplay | 70.83 s | 0 | 0 / 2 | 9.25 s | 7.80 s | 416.96 MiB |
| Sintel trailer | 52.21 s | 20 | 0 / 2 | 10.25 s | 7.53 s | 1,242.18 MiB |
| USGS lecture excerpt | 900.07 s | 2,131 | 4 / 4 | 76.39 s | 8.75 s | 1,267.34 MiB |
| USGS lecture excerpt | 3,600.07 s | 9,224 | 4 / 4 | 385.50 s | 8.78 s | 1,333.86 MiB |
| Private user interview | 39.00 s | 134 | 2 / 2 | 13.34 s | 7.53 s | 1,281.54 MiB |

The hour run included intentional cancellation after five completed ASR cores and a retry. It is a resume/reliability measurement, not an uninterrupted speed comparison. The v3 audio-free path rejected the silent source; the zero records lack of supported speech candidates, not a timed successful v3 processing run. Other v3/v4 candidate comparisons share available transcription evidence; this is not an independent cold-start engine speed comparison.

The private interview currently measures 39 seconds. Historical reports about a different, longer version of the similarly named file must not be applied to this run. All outputs remain local. The two lecture inputs are continuous excerpts, not artificially repeated speech loops. The trailer is already professionally edited, so it is a weak proxy for raw cinematic scene discovery.

### What the experiment establishes

The new path can produce actual clips from the tested silent and sparse-dialogue inputs while the tested lecture/interview paths retain candidates. Long-input processing remained within the observed memory range and the intentional resume exercised persisted work. These are functional feasibility results. More proposals are not automatically better proposals.

Source-only agent review recorded broad intervals before inspecting algorithm candidates: gameplay menus/transition around 0–15 seconds, racing around 15–55 seconds, and results around 55–71 seconds; the trailer had coarse visual passages around 4–12, 16–28 and 32–44 seconds, followed by titles. Sampling uncertainty was approximately 4–5 seconds. These are **agent-reviewed broad regions, not human ground truth or labels of the best highlights**.

The initial top gameplay proposal was 44.3–66.3 seconds and therefore included a substantial results-screen tail. That is a real editorial weakness and motivates a separately tested inactivity/tail rule; it must not be concealed by a successful MP4 render. An overlap statistic against the broad racing region could quantify coverage, but it would not measure highlight precision or user preference.

### Claims not supported

No measured genre precision/recall, event-detection accuracy, caption word error rate, forced-alignment error, speaker-association accuracy, professional editorial acceptance rate or audience engagement result exists for this study. There was no independent human-labelled highlight set and no blind competitor comparison. No “universal intelligence,” “viral probability” or competitor-superiority claim follows from these five files.

## Next acceptance gates

1. Freeze a representative source set, hashes, settings, engine versions and human reference intervals before selection. Include hard negatives: menus, credits, static slides, camera shake, loud irrelevant sound, overlapping speech and very short actions.
2. Have at least two independent human reviewers annotate necessary setup, event/payoff and unacceptable boundaries. Record disagreement; do not treat one editor's preferred cut as objective universal truth.
3. Compare v3/v4 candidates with the same source/transcript, separate from cold/warm runtime runs. Report event coverage, false positives, duplicate rate, boundary completeness and human preference by genre. Precision/recall denominators must be defined before results are computed.
4. Check caption text/timing on a manually transcribed subset; inspect HUD, slide and face visibility at phone resolution. Decode every tested export fully, check duration/streams and review beginning/middle/end. Technical decode integrity and editorial quality are separate gates.
5. Exercise cancellation, retry, malformed caches, source changes, no audio and unavailable optional models. Saved edits, existing projects and original media must remain intact. Do not turn a recoverable analysis failure into empty successful output.
6. Keep v4 opt-in until broader evidence shows a useful quality gain without transcription, editing or export regressions. Prefer returning fewer defensible clips over filling a requested count with weak footage.

## Source rights and reproducibility

- Gameplay provenance: [SuperTuxKart benchmark recording on Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Benchmark_SuperTux_Kart_Base_Profile_2025.07.04_-_10.19.10.02.webm). The manifest records CC BY-SA 4.0, recording attribution to PantheraLeo1359531 and relevant underlying game-asset notices. Any public derivative package needs attribution and applicable share-alike treatment; the application's code licence does not replace media rights.
- Trailer provenance: [Blender Foundation sharing terms](https://durian.blender.org/sharing/) and [official Sintel trailer media](https://download.blender.org/durian/trailer/sintel_trailer-1080p.mp4). Manifest records CC BY 3.0 with logo/trademark exclusions and Blender attribution.
- Lecture provenance: [USGS PubTalk: Using Communication Science to Communicate USGS Science](https://www.usgs.gov/media/videos/pubtalk-022024-using-communication-science-communicate-usgs-science). Existing local continuous excerpts were reused for long-input evaluation.
- The user-provided interview is private test media and must not be committed or uploaded publicly.

Source hashes/licence notes are in `work/framecraft4/media/manifest.json`; broad source-only review is in `work/framecraft4/media/reference-review/agent-reference-intervals.json`. Keep private absolute paths, media, model files, OAuth material and runtime databases out of any public repository. The current `docs/TEST_REPORT.md` is the authority for final executed verification after subsequent fixes.
