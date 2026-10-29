# Third-party notices and demonstration materials

The original application has no selected project-wide source license. Every dependency retains its own terms. Direct package versions are pinned in requirements.txt and frontend/package-lock.json; inspect upstream distributions for their full notices.

| Component | Upstream license/status |
|---|---|
| React/ReactDOM, Vite, FastAPI, Pydantic, SQLAlchemy, faster-whisper | MIT |
| TypeScript, python-multipart, OpenCV 4, Hugging Face Hub/tokenizers, Playwright | Apache-2.0 |
| Uvicorn, HTTPX, NumPy, psutil | BSD-3-Clause |
| pytest, CTranslate2 | MIT |
| Pillow | MIT-CMU |
| DejaVu Sans | Retained bundled font terms in assets/fonts/LICENSE-DejaVu.txt |
| Liberation Sans | SIL OFL 1.1 in assets/fonts/LICENSE-Liberation.txt |
| Whisper tiny.en / base weights | OpenAI Whisper MIT, converted by Systran; downloaded locally, not distributed here |
| FFmpeg/ffprobe | Build-dependent LGPL/GPL terms. The setup helper obtains an external GPL-enabled essentials build; binaries are not bundled |

References: [Whisper license](https://github.com/openai/whisper/blob/main/LICENSE), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FFmpeg legal](https://ffmpeg.org/legal.html), [Gyan builds](https://www.gyan.dev/ffmpeg/builds/).

Tests use the public-domain federal JFK speech excerpt supplied by the official Whisper tests and, for a clearly synthetic motion fixture, a pinned NASA photo distributed by scikit-image. Both are fetched into ignored test artifacts. The NASA usage guidance applies and no endorsement is implied.

The eight colored libass vector pictograms are original Framecraft drawings. Product screenshots show the real app using original illustrative graphics. The public rendered preview is deliberately muted. USGS speech attribution, public-domain source and manual editorial refinements are documented in assets/product/PROVENANCE.md. No third-party video images, personnel faces, agency logos, proprietary competitor assets or private recordings ship in these public materials.
