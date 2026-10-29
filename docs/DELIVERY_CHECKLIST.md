# Publication delivery checklist

Verified 29 October 2026.

## Published repositories

- [Framecraft source and portfolio](https://github.com/KevBrad/framecraft-ai): public, default branch `main`.
- [KevBrad profile](https://github.com/KevBrad): professional profile README is visible and features Framecraft.
- The initial 114-file remote source tree exactly matched its audited Git index. Public downloads of the README, seven visual assets, all four portfolio files and profile README matched their committed bytes. Text line endings were compared in canonical Git form.
- The rendered public README was inspected in a browser: seven embedded product/brand images loaded, including the architecture SVG. The public profile and its project links were inspected separately.
- Private vulnerability reporting is enabled. No project-wide permissive license was invented; source availability and dependency/asset attribution are documented.

## Actual execution results

| Check | Executed result | Evidence |
| --- | --- | --- |
| Existing local full suite | 290 passed + four subtests in 78.73 s, no skips | [Verified results](VERIFIED_RESULTS.md) |
| Existing frontend | TypeScript/Vite production build passed in isolated output | [Test guide](TESTING.md) |
| Published source CI | 177 passed, two deliberate media-test exclusions, 11.10 s; frontend build passed | [Source run](https://github.com/KevBrad/framecraft-ai/actions/runs/40841728996) |
| Clean Windows media CI | 290 passed + four subtests in 64.01 s, using prepared public fixtures and actual ASR/API/render tests | [Media run](https://github.com/KevBrad/framecraft-ai/actions/runs/40841863684) |
| Data preservation | Seven production table row hashes, current project original and four library originals unchanged | [Verified results](VERIFIED_RESULTS.md) |
| Application preservation | All 36 backend/frontend application files retain matching SHA-256 hashes | [Security audit](PUBLICATION_SECURITY_AUDIT.md) |
| Publication safety | File payload and both intentional new histories passed Gitleaks with zero findings before first push | [Security audit](PUBLICATION_SECURITY_AUDIT.md) |

The hosted runs above tested application commit `bedf509a8f7e4d8c5f6a6731e734fcf16a31f468`. Subsequent publication-checklist changes are documentation only. The complete continuous 15-/60-minute runs remain dated 23 October evidence, re-inspected rather than rerun during publication. Hosted media tests do not turn that historical evidence into new long-video benchmarks.

## Portfolio files

- [12-slide editable PowerPoint](../portfolio/pitch-deck/Framecraft_Startup_Pitch_Deck_2026.pptx): native text, shapes and tables, notes/source citations, 16:9. Reimported successfully with the presentation SDK. Package and geometry validators found zero issues. Microsoft PowerPoint itself was not installed for application-level inspection.
- [Matching 12-page pitch PDF](../portfolio/pitch-deck/Framecraft_Startup_Pitch_Deck_2026.pdf): reviewed image-based visual edition from the same slide model; the PowerPoint supplies editable/searchable text.
- [Six-page engineering case study](../portfolio/case-study/Framecraft_Engineering_Case_Study_2026.pdf): architecture, memory repair, checkpoints, testing, preservation and AI-assisted development tradeoffs.
- [One-page overview](../portfolio/one-pager/Framecraft_One_Page_Overview.pdf): concise recruiter/startup introduction.
- Every slide and PDF page was rendered and visually inspected; changed layouts were re-rendered. The PDFs have the expected page counts and sizes, with no unsafe active content or private metadata found.
- [Editable case-study/overview source](../portfolio/source/build_documents.py), [slide notes](../portfolio/pitch-deck/SLIDE_NOTES.md), [research and claim notes](RESEARCH_SOURCES.md), and [asset provenance](../assets/product/PROVENANCE.md) are included.
- Portfolio documents and public presentation assets were also installed into the existing local Framecraft project after backing up replaced documentation. Production data and application algorithms were preserved.

## Remaining product work

Live Google Drive OAuth still requires an actual configured client and consent. Reliable speaker attribution, forced alignment, broad multimodal reasoning, customer validation and hosted security/operations remain incomplete. These are documented product limits, not publication blockers. No customers, revenue, fundraising or competitive-superiority results were invented.

GitHub's authenticated API exposes no repository-pinning mutation, and the inspection browser is signed out. To pin manually: sign in, open your profile, choose **Customize your pins**, select **framecraft-ai**, and save. Framecraft is already prominently featured in the profile README.
