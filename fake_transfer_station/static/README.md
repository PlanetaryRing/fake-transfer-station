# Vendored CPA management frontend

`management.html` is the **unmodified official release asset**, not a recreation.

- Project: https://github.com/router-for-me/Cli-Proxy-API-Management-Center
- Release: `v1.26.0`
- Commit: `c9e2f7cb0b065c13211285d9525bcc8761f17837`
- Asset: https://github.com/router-for-me/Cli-Proxy-API-Management-Center/releases/download/v1.26.0/management.html
- Expected SHA-256: `aae0653e65dc81668ded759c8e93d474ce295e0b159a1ca205bf0b6316655df1`
- License: MIT, reproduced in `management.LICENSE`.

The application serves this file at `/management.html`. All local v8/v0
management API requests fail with HTTP 403 (management disabled), regardless of
credentials. No management operations or credentials are persisted by this backend.

The original UI remains unchanged, including its server-address selection and
browser-storage behavior. Selecting a different server connects to that server;
this project's rejection behavior applies only to this project's endpoints.

To restore the pinned assets, run `python scripts/fetch_management.py` from the
project root. Downloads and HTML checksum verification occur only when explicitly
running that script; application startup does not contact GitHub.
