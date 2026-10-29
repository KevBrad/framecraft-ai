# Pitch deck content and sources

12 slides. Engineering validation is distinct from business hypotheses.

## Slide 1



Long-form conversations.

Editable short-form stories.

AI-powered video clipping with inspectable choices

and a working local editing pipeline.

WORKING LOCAL PRODUCT / OCTOBER 2026

a complete moment

Original workflow illustration

Source notes: Project stage: working local prototype. Project creator account confirmed by GitHub API as KevBrad. No commercial traction or external funding has been established.

## Slide 2

Editing starts before the first cut

The hardest moment to find is often the one that still makes sense on its own.

Review

footage

Find the

moment

Trim the

story

Reframe

video

Correct

captions

Refine the

edit

Export and

check

A compelling sentence can lose its value when the question, explanation or payoff falls outside the cut.

Source notes: Problem framing is a product hypothesis based on the editing workflow. No user-interview study or quantified time saving is claimed.

## Slide 3

A focused first customer

INITIAL CUSTOMER HYPOTHESIS

Independent podcasters

and educators

People with recurring conversations, lessons or webinars and a need to review each published claim.

VALUE TO TEST

More usable clips

from existing recordings

Fewer manual repairs

Clear source context

Control before publishing

Source notes: Qualitative opportunity and proposed initial segment. Demand, addressable market, customer willingness to pay and time savings are not validated. No numeric market estimate is included.

## Slide 4

A working path from import to export

Framecraft finds candidate moments, explains its ranking and keeps the edit in the creator’s hands.

01

Import and analyze

Authorized files or supported connections feed a durable local pipeline.

Import  /  Analyze  /  Rank  /  Edit  /  Export

Source notes: Actual application screenshot from an isolated demo workspace captured29October2026. The displayed demonstration uses public-domain government speech with original graphics. No private production project is visible. Direct HTTPS imports and reusable authorized originals are implemented. YouTube references alone do not establish downloadable media. Google Drive live OAuth remains unverified.

## Slide 5

The creator can inspect and refine every cut

See the evidence

Edit the actual result

Source timestamps and heuristic score evidence.

Saved captions, framing and rendered preview.

Source notes: Both captures show real application states on29October2026, not mockups. Editorial scores are heuristic estimates. The live source canvas approximates typography; rendered MP4 preview is authoritative. Corrections persist and clear obsolete recognized word estimates.

## Slide 6

Local media processing, durable project state

01

Authorized intake

File, HTTPS, source registry

02

Bounded worker

Process gate and checkpoints

03

Speech recognition

Whisper word estimates

04

Highlight selection

Context and ranked evidence

05

Visual edit

Captions and scene framing

Render and verify

FFmpeg, libass, ffprobe

React and TypeScript

Creator controls

FastAPI and durable queues

Separate import and analysis stages

SQLite WAL and local files

Sources, edits and export history

Source notes: Verified against current source29October2026. SQLite schema2. Separate durable import and analysis consumers. Windows workers use a Job Object with2048MiBdefault. ASR reads30second cores with2second context per side. The editorial engine builds90second extractive units and uses lexical/acoustic heuristics. OpenCV geometric framing is not active-speaker identity. No hosted model inference, Redis or PostgreSQL is implemented.

## Slide 7

Control over each cut

LOCAL PROCESSING

No mandatory paid AI key

Speech recognition and media analysis run on the user’s machine.

EXPLAINABLE CHOICES

Evidence beside the score

Creators can inspect hooks, context, payoff and source timestamps.

REVERSIBLE EDITING

Saved work stays available

Versioned edits and previous selections survive reanalysis.

Direction to validate: a review-first workflow for creators who value source control.

Source notes: All three current behaviors are source-verified. The privacy/control positioning is a proposed customer value proposition, not evidence of market preference or exclusive competitive advantage. Metadata/OAuth/download network activity can still occur. Framecraft is a local single-user application, not a reviewed public SaaS.

## Slide 8

Different approaches to the same editing job

Product

Documented focus

Delivery / evidence

Vizard

Full-context clips and captions

Hosted product / advertised

OpusClip

Multimodal prompt discovery

Hosted product / advertised

Submagic

Captions, effects and editing

Hosted product / advertised

Framecraft

Heuristic ranking and editable captions

Local Windows app / verified prototype

Framecraft gaps: reliable active-speaker attribution, forced alignment and broad multimodal reasoning.

Sources: official product documentation, accessed 29 Oct 2026. Vendor claims are not independent tests.

Source notes: Official sources accessed29October2026: https://vizard.ai/blog/meet-vizard-model-v2-full-context-clips-that-the-algorithm-actually-rewards ; https://www.opus.pro/clipanything ; https://www.submagic.co/ . Third-party rows reflect advertised capabilities only. We did not run an independent same-source benchmark against these products. No superiority ranking or unverified feature-absence claim is made.

## Slide 9

Product development and technical validation

290

automated tests + 4 subtests

60 min

continuous speech processed

1080p

real H.264 / AAC exports

Source duration

Analysis runtime

Words

Peak private memory

13 min 29 s

101.20 s

2,750

1,341 MiB

15 min

104.08 s

2,131

1,309 MiB

60 min

311.62 s

9,224

1,397 MiB

Tests rerun 29 Oct. Long-video evidence: 23 Oct 2026, one CPU workstation. Engineering validation only.

Source notes: Publication rerun29October2026:290tests+4subtests passed78.73s, no skips. TypeScript/Vite build passed. Historical media evidence23October2026 was re-inspected, not rerun in this publication phase. Timings include import/upload plus validation, ASR, selection and framing; exclude later export. Intel i7-11800H, CPUint8,4threads, tiny.en. Process-tree private memory sampled100ms, not an exact Job Object high-water measurement. No customer count, revenue or commercial traction is implied. See docs/VERIFIED_RESULTS.md and historical-long-video-metrics.csv.

## Slide 10

A focused validation and business model

PROPOSED FIRST EXPERIMENT

5–10 creator trials

Use authorized recordings. Review the first batch together. Measure clips kept, editing effort and export reliability.

Test willingness to pay before choosing a price.

BUSINESS HYPOTHESES

Creator subscription

Usage tier for higher volume

Professional editing controls

Demand, pricing and unit economics remain unvalidated.

Source notes: Proposed plan only.5–10is a planned trial cohort, not an existing user count. Framecraft has no established paying-customer, revenue, funding or product-market-fit claim. Potential subscriptions and usage tiers are hypotheses; no actual pricing is announced.

## Slide 11

The next milestones have clear evidence gates

QUALITY EVIDENCE

Better selection and timing

Label reference moments. Measure recognition errors and word onsets. Review failure cases.

CREATOR TRIALS

A workflow people keep using

Observe editing effort, clip acceptance, usability and willingness to pay.

DEPLOYMENT READINESS

A responsible hosted pilot

Establish account security, operational controls and per-minute processing cost first.

Source notes: Future work, no completion dates committed. Reliable speaker-aware framing, forced alignment, stronger semantic selection and approved provider integrations remain planned/experimental. Public cloud operation requires security, tenancy and cost controls beyond the current single-user loopback app.

## Slide 12

A useful product, built through measured iteration

KevBrad

Project direction, research, requirements and testing,

with AI-assisted implementation.

SUPPORT THAT WOULD ACCELERATE THE NEXT STAGE

Product mentorship

Creator introductions

Independent technical review

github.com/KevBrad/

framecraft-ai

Working local prototype

Seeking validation and startup support

Project and contact profile

Source notes: Confirmed GitHub handle:KevBrad. No public display name, employer, degree, cofounder, personal email or funding round was confirmed. The project reflects user-led product requirements/research/iteration and materially AI-assisted code creation. Support request is qualitative and does not assert an active fundraising round. GitHub project URL:https://github.com/KevBrad/framecraft-ai .
