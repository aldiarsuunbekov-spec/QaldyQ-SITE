# QaldyQ — local run and secure deployment preparation

QaldyQ now has server-side cafe accounts and owner-scoped dashboard/order data, but **no cloud host or public URL has been selected or created**. Use this guide to run locally and prepare a later TLS deployment.

## Local Python run

Install Python 3.11+ and dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:QALDYQ_ENV = "development"
$env:QALDYQ_COOKIE_SECURE = "false" # HTTP localhost only
$env:QALDYQ_FORWARDED_ALLOW_IPS = "127.0.0.1"
python scripts/monitor_uvicorn.py --host 127.0.0.1
```

The supervisor checks port 8000 and `/api/health`, starts/restarts Uvicorn, and logs under `.work/logs/`. This local command listens only on loopback; do not use the HTTP cookie override on LAN or a public interface. It passes `--proxy-headers` with a narrow forwarded-IP allow-list. Use Ctrl+C to stop. For direct HTTPS reverse-proxy use, set `QALDYQ_FORWARDED_ALLOW_IPS` to the specific proxy IP/CIDR; never use `*` on an internet-facing app.

Local development sets the cookie without `Secure` only so HTTP `localhost` can authenticate. Do not carry `QALDYQ_COOKIE_SECURE=false` into staging or production. `QALDYQ_ENV=production` refuses to start unless secure cookies are enabled and disables FastAPI docs/OpenAPI routes.

## Docker Compose

Requires Docker Engine/Desktop and Compose plugin:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f app
```

Compose binds port 8000 to `127.0.0.1` by default, uses a persistent `foodsaver-data` volume, runs as non-root, and sets production mode with `Secure` cookies. To try the container over local HTTP only, override the defaults for that terminal/session:

```powershell
$env:QALDYQ_ENV = "development"
$env:QALDYQ_COOKIE_SECURE = "false"
docker compose up --build -d
```

```sh
QALDYQ_ENV=development QALDYQ_COOKIE_SECURE=false docker compose up --build -d
```

For a cloud deployment, keep these secure defaults. `QALDYQ_FORWARDED_ALLOW_IPS` defaults to `127.0.0.1`; set it to the actual TLS proxy IP or narrow Docker network CIDR when that proxy is separate. Point the TLS reverse proxy at the internal app port, set the original `Host` and trusted `X-Forwarded-Proto`/`X-Forwarded-For` headers, and expose only the proxy's HTTPS port. Do not publish port 8000 directly.

## Compose environment template

Copy `.env.example` to `.env` in the project root before running Compose; Compose reads this file automatically. It contains no secrets, keeps the app bound to loopback, enables production mode and `Secure` cookies, and is safe to include in the source package. `.env` is ignored by Git and must never be committed.

Before deploying, replace `QALDYQ_FORWARDED_ALLOW_IPS=127.0.0.1` with the exact TLS proxy IP/CIDR as seen by the app container. The correct value depends on the selected host and network; do not use `*`. Keep `FOODSAVER_BIND=127.0.0.1` when the reverse proxy runs on the same host. If the proxy is in a separate container/network, expose the app only on the private network and scope both the bind and forwarded-IP allow-list to that network.

`FOODSAVER_PORT` controls the host-side port mapping; `QALDYQ_ENV` must remain `production` and `QALDYQ_COOKIE_SECURE` must remain `true` for public HTTPS. The app does not need a static session-signing secret: it generates random session/CSRF tokens with the operating system's secure random source and stores only the session-token hash. Compose fixes the SQLite file at `/data/foodsaver.db` on its named volume.

`docker compose down` preserves the named database volume; `docker compose down -v` deletes it. Back up `/data/foodsaver.db` off-host before upgrades. Do not run multiple replicas against the same SQLite file.

## Public-launch checklist — not yet performed

1. Select a hosting provider/region, DNS name, data-retention period and backup target; none has been selected yet.
2. Keep `QALDYQ_ENV=production` and `QALDYQ_COOKIE_SECURE=true`. Terminate TLS at a maintained proxy, redirect HTTP to HTTPS, enable HSTS there, and keep the app port private.
3. Set `QALDYQ_FORWARDED_ALLOW_IPS` only to the proxy IP/CIDR. The supervisor then makes Uvicorn honor proxy headers from that allow-list. The app's same-origin guard compares browser `Origin` with the externally visible scheme/host; incorrect proxy trust will reject HTTPS writes, while overly broad trust permits header spoofing.
4. Test signup/login/logout, password minimums, missing/wrong CSRF token rejection, idle and absolute session expiry, rate limits, cookie flags, and two-cafe isolation. Verify cafe A cannot read cafe B's offers/order phone numbers by changing IDs or query parameters.
5. Decide whether registrations require manual partner verification; implement email verification and account recovery before depending on the service for real merchant access. Create a privacy notice and customer-data deletion process.
6. Add database snapshots and a restore drill, TLS/uptime monitoring, log-retention limits, incident response and a rollback plan. Never commit `.env` credentials.
7. Configure a production tile provider/usage policy for Leaflet and verify attribution. The OSM Standard tiles are best-effort, not an SLA-backed commercial map service.

## Security and scaling boundaries

Cafe passwords use Argon2id; only a hash is stored. Session tokens are random opaque values, stored server-side as hashes, transported in `HttpOnly; SameSite=Strict` cookies, and expire after 30 minutes idle or 8 hours absolute. Writes and private cafe APIs require a CSRF token and same-origin request. Signup, login, listing writes and reservations are rate-limited. Login attempts are throttled by client IP and email; rate-limit buckets are stored in SQLite.

Café order/customer-phone queries are filtered by authenticated `owner_id`; buyers can reserve a deal without a cafe account, but the public order route does not expose the resulting PII. Password reset/email verification, merchant due diligence, payment, refunds, pickup confirmation and a formal data-retention job are not implemented.

Sessions and rate limits are shared only inside this single SQLite instance. For multiple app replicas or higher traffic, migrate data/session/rate-limit storage to a managed server database/cache with reviewed migrations and backup policy. This checklist prepares deployment; it does not publish the app or create a URL.

## Map and browser location

Leaflet 1.9.4 is loaded from a pinned CDN; OpenStreetMap Standard tiles are requested directly by visitors' browsers. Map display needs outbound HTTPS access. If the CDN/tiles are unavailable, List view still works. Do not bulk-prefetch or mirror the public OSM tile endpoint; choose a provider with terms/capacity appropriate for production. Browser location is requested only after the nearby button is clicked and works in a secure browser context (HTTPS or localhost).
