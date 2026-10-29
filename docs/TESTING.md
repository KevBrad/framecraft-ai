# Test scope and commands

## Full local media suite

Run in the configured environment. Test workspaces are isolated from user projects. The two preparation steps fetch documented public fixtures; they do not need private recordings.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_fixture.py
.\.venv\Scripts\python.exe scripts\prepare_face_fixture.py
.\.venv\Scripts\python.exe -m pytest backend tests -q
npm.cmd --prefix frontend run build
```

The full suite includes genuine ASR/API/FFmpeg acceptance, caption/pictogram pixels, media probing, resource containment and persistent edits. Unit cases also use synthetic decoders or mocked HTTP/OAuth deliberately; those are not evidence of live Google sign-in. Browser scripts under `frontend/tests` use real APIs and require a running instance plus an expendable authorized fixture/project.

## Public CI

`ci.yml` builds the frontend and runs Windows logic, transport, Drive-secret-storage and security regressions without model downloads or private media. This command passed 177 tests locally, with two media-only tests explicitly excluded. The separate `media-integration.yml` workflow is manually dispatched and installs media tools, prepares public speech/photo fixtures and runs the full suite. Jobs use immutable revisions of official GitHub Actions.

Hosted checks may differ from the configured workstation. Check the live Actions result rather than inferring a remote pass from local results. The verified-results document records which tests ran today and which media/browser records are historical.
