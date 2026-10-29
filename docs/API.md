# API guide

The loopback FastAPI service exposes `/docs` for the generated, current OpenAPI contract. Refer to `backend/schemas.py` and `backend/import_routes.py` for exact validation limits.

| Endpoint | Purpose |
|---|---|
| `POST /api/projects` | Multipart authorized file upload and analysis settings |
| `GET /api/projects`, `GET /api/projects/{id}` | Persistent project state, candidates, jobs and exports |
| `POST /api/projects/{id}/analyze` | Reanalyze with validated discovery/duration/model preferences |
| `POST /api/sources/resolve` | Classify source readiness without claiming metadata is media |
| `GET /api/sources`, `POST /api/sources/link` | Reusable originals and explicit YouTube reference binding |
| `POST /api/imports`, `GET /api/imports` | Durable import creation and progress |
| `POST /api/imports/{id}/cancel`, `/retry` | Pause/resume or renew an expired direct link |
| `PUT /api/clips/{id}` | Save the canonical edit with optimistic version checking |
| `POST /api/clips/{id}/render` | Queue a genuine encoded export |
| `GET /api/projects/{id}/exports.zip` | Stream existing MP4 exports as ZIP |
| `GET /api/connections/google-drive` | Accurate connection/configuration state |

`AnalysisSettings` includes discovery mode, requested clip count, duration bounds, local model mode and presentation defaults. API defaults retain 720p compatibility; the creation UI explicitly requests 1080p. Editorial output retains a legacy score and adds a 0–10 score with dimensions, source evidence, rank, topic, tags and review indicators. Confidence means ASR word confidence, not probability of audience success.

Edits retain legacy styles and add newer caption presets, pictograms, keywords, animation, resolution, opening title and audio-normalization controls. User caption changes clear stale per-word estimates. Past selection generations remain available. No public endpoint accepts arbitrary executable provider code or cloud inference instructions.
