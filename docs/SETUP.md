# Windows setup and troubleshooting

## Requirements

- Python 3.12 or newer with the Python launcher or interpreter on PATH.
- Node.js 22.12 or newer and npm.
- An x64 Windows workstation with enough free RAM and storage for bounded workers and cached models.
- FFmpeg and ffprobe. Setup downloads an external GPL-enabled essentials build if neither is configured.
- Network access for dependency/model downloads and any explicitly selected remote media source.

```powershell
git clone https://github.com/KevBrad/framecraft-ai.git
Set-Location framecraft-ai
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Open `http://127.0.0.1:8000`. The startup script changes to the project directory. Keep its terminal running. Local work remains in `data/`, which is ignored and is never shipped in the public repository.

## Local models

Balanced uses English `tiny.en`, CPU int8, beam 3. Weights download on first recognition. Quality selects cached multilingual `base`, beam 5. To cache that optional model deliberately:

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper.utils import download_model; download_model('base', cache_dir='data/models')"
```

A missing quality cache falls back explicitly. Operator model paths can be configured using `WHISPER_MODEL_PATH`. No private transcript is sent to an external inference service. `WHISPER_MODEL` and supported local configuration are environment variables; `.env.example` documents examples, but the app does not automatically load a `.env` file.

## Optional Google Drive

No credential ships with Framecraft. Create a real Google-issued Desktop OAuth client and place its JSON in `data/connections/google-oauth-client.json`, or set `GOOGLE_OAUTH_CLIENT_FILE`. The implementation uses official OAuth/Drive endpoints, PKCE and a read-only file picker. Choose Google Drive in the app, check configuration and sign in. Account-backed live authorization was not verified in the published project evidence. Restricted-scope verification obligations depend on the intended Google application distribution.

## Troubleshooting

| Symptom | Check |
|---|---|
| Port 8000 already in use | Reuse the running instance or stop the other instance before startup |
| FFmpeg unavailable | Put tools on PATH or set `FFMPEG_PATH` and `FFPROBE_PATH` explicitly |
| Model download unavailable | Cache a supported CTranslate2 model and configure its path |
| Audio too quiet or silent | Use audible source speech; no dialogue is fabricated |
| Worker out of memory | Close heavy applications or reduce `TRANSCRIBE_CHUNK_SECONDS` to 10, then retry saved work |
| Caption spelling/timing wrong | Correct the saved line/timing; inspect the real rendered preview |
| YouTube reference needs a source | Supply an authorized original or an approved connection; metadata alone is insufficient |
| Project storage full | Back up local data and manage projects deliberately; nothing expires automatically |

Docker configuration is supplied, but Docker/Linux were not verified in the recorded release. Non-Windows secret storage needs optional Fernet configuration. The local service is not a supported public multi-user deployment.
