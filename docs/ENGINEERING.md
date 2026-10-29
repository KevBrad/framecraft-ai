# Engineering decisions

## Bounded media work before more features

A historical speech-recognition failure requested a 243 MiB complex128 array shaped `(1, 79376, 201)`. Saved release evidence describes a whole-WAV path. The exact internal failing allocation was not re-instrumented in this publication pass, so no upstream defect is asserted.

The current solution bounds the input to feature extraction: 30-second cores plus two seconds of context, float32 PCM and timestamp-midpoint ownership. Atomic checkpoints retain completed cores. Normalized audio identity is independent of the eventual complete transcript. Thread limits and a gated 2,048 MiB Windows Job Object address process-level pressure. Real continuous long recordings and late-source exports were recorded after the repair.

## Ranking that exposes its evidence

The newer selector builds extractive topic/question context, identifies hooks, retains nearby answers and contrasts, refines source boundaries and reranks for diversity. Initial weights are hook 20%, completeness 25%, payoff 20%, audio 10%, novelty/relevance 15% and technical 10%. These are hypotheses, not validated audience-response coefficients. Modes and lexical custom prompts change selection without inventing dialogue. Recognized-word confidence does not establish factual correctness.

## An authoritative saved edit

Text, timing, pictograms, keywords, framing and output settings belong to one saved specification. The renderer does not regenerate user corrections. Changing text clears stale word estimates. Existing edits survive new analysis generations and old exports remain accessible. Libass is authoritative; browser overlays are labelled approximate.

## Conservative visual composition

The scene director uses bounded geometric face samples, deadzones, hysteresis and cut resets. It falls back to fit when detection/crop containment is uncertain. This avoids pretending to know who is speaking. It can preserve wide content at the expense of letterboxing and does not yet understand on-screen text or create stacked layouts.

## Local product tradeoffs

SQLite, local files and two durable consumers fit a single-user tool. They do not establish tenant isolation, distributed scheduling or cloud production readiness. CPU int8 preserves portability and memory bounds; optional cached base recognition is slower and still imperfect. No external inference or paid key is mandatory.
