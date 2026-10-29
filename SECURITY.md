# Security

Framecraft is a local single-user application bound to loopback. It is not a reviewed public multi-user SaaS. Keep dependencies/media tools patched and treat imported media as untrusted.

Use GitHub private vulnerability reporting when enabled on this repository. Do not post credentials, transcripts, private videos, database files or unredacted local paths in public issues. If a private report option is unavailable, open a minimal issue requesting a private contact route without disclosing exploit/credential details.

Direct imports validate public HTTPS destinations, DNS addressing and redirects. Media validation/analysis runs with resource and time limits. Windows secrets use per-user DPAPI; no client JSON, token, signed link or model weight belongs in Git. Source-library permission confirmation does not independently establish copyright ownership. A successful publication secret scan does not certify codec safety or deployment hardening.

The public package omits user data, local configuration, logs, model caches and downloaded tools. Configuration examples contain placeholders only. See docs/PUBLICATION_SECURITY_AUDIT.md for scope and final publication evidence.
