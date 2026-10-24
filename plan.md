# QaldyQ — implementation plan

## Product and architecture

QaldyQ is a Kazakhstan surplus-food marketplace: cafes, bakeries and small grocers list discounted end-of-day bundles for customer pickup. The MVP supports the ten city hubs in `CITY_NAMES`; the initial go-to-market focus remains Almaty and Astana. The platform target commission is 20%, and all consumer/business prices are in ₸. The user selected plain-local development, preserving Python + FastAPI + SQLAlchemy + SQLite and a single `static/index.html` SPA. FastAPI serves the UI and same-origin JSON endpoints. Order placement keeps its conditional atomic SQLite inventory decrement. SQLite is persistent and can be redirected with `FOODSAVER_DB_PATH`.

Public routes include `GET /api/deals`, `POST /api/orders`, `GET /api/stats` plus the `/stats` alias, `GET /api/auth/me`, and `GET /api/health`. Cafe-private routes — `POST /api/deals`, `GET /api/cafe/dashboard`, `GET /api/orders`, and `GET /api/cafe/forecast` — require an authenticated owner and CSRF token; login/register are rate-limited and same-origin checked. Offer fields include `address`, `latitude` and `longitude`; an idempotent startup migration adds them to legacy `deals` tables without removing existing rows. Coordinate pairs are range-checked and an address is required when a map pin is supplied.

## Interface and product functions

The Russian, mobile-first, single-file frontend retains the storefront, cafe dashboard, impact/stats view, order modal, search, KZT prices, weight and 20% commission. It provides: (1) a dropdown for all ten Kazakhstan cities and an idempotent seed for the 26 requested demo merchant names; demo names are explicitly prefixed `ДЕМО ·`, and do not imply actual partnership. (2) Map/List switching with Leaflet and visible OpenStreetMap attribution; 26 new demo offers use approximate city-centre points only, not merchant-branch addresses. Cafe-provided coordinates remain available for real listings. (3) pickup-start-time filtering, while preserving the existing `pickup_time` API field. (4) explicit opt-in geolocation to find geocoded listings within 25 km. (5) buyer-local booking summaries and an opt-in local profile; neither synchronizes across devices, and booking history excludes phone numbers. (6) a soft orange urgency label for quantity at or below two, plus a confirmation-only WhatsApp share action using an encoded `wa.me` message and the current page URL. The header uses the QaldyQ text wordmark; the inherited leaf/pin remains a decorative UI mark because no official logo asset was supplied. FoodSave is a functional reference only.

## Metrics and GTM

Stats remain reservation-based: turnover is the discounted unit-price sum, platform commission is 20% of turnover, and saved kg is ordered quantity multiplied by recorded bundle weight. The MVP does not process payments or verify physical pickup, so reservations/weights are not completed-sales or waste-diversion proof. Pilot reports must separate demo data, reservations and manually confirmed pickups. Track active partners, listing-to-reservation conversion, sell-through before pickup cutoff, repeat buyers, reported pickup/no-show, geocoded-active-listing share, map/list choice and acquisition cost in ₸. See `growth_plan.md` for the first-50-cafes funnel and geodata onboarding hypothesis.

## Design and source references

Preserve the eco-editorial QaldyQ direction: forest `#183d32`, citrus lime `#c7e36a`, warm paper, editorial headings paired with system UI text, and thumb-friendly Russian controls. Keep text-only left/right ad rails on desktop and stack them after the app on narrow screens. AUA Sense is shown without an unprovided logo, link or slogan. Leaflet 1.9.4 remains pinned by CDN SRI. OSM tiles use visible attribution and browser-default request/cache behavior, with no tile prefetch/offline downloads; public OSM tiles have no SLA. Production deployments should select a contracted/appropriate tile provider. `CITY_COORDINATES.md` documents each approximate demo point and its sources; `brand-spec.md` records identity guardrails.

## Project structure

- `main.py` — FastAPI, SQLAlchemy models, Pydantic schemas, legacy migration, idempotent 10-city seed and JSON API.
- `static/index.html` — single-file Russian storefront, 10-city filter, dashboard, stats, map, booking/profile UI and responsive ad rails.
- `requirements.txt` — app dependencies.
- `README.md`, `DEPLOYMENT.md`, `CITY_COORDINATES.md` — local run, API/economics, cloud/map notes and coordinate disclosure.
- `scripts/monitor_uvicorn.py`, `Dockerfile`, `docker-compose.yml` — supervisor and container deployment.
- `growth_plan.md`, `brand-spec.md` — supply GTM and brand guardrails.
- `plan.md`, `TODO.md` — implementation decisions and status.

## Verification and environment findings

Verified offline: Python compilation; in-memory API tests for deal validation, city/search, one-unit order, quantity decrement, oversell rejection, cafe dashboard and 20%/₸/kg metrics; legacy SQLite migration and serialization tests; an isolated regression test for all 26 exact merchant labels, ten supported cities, seed idempotency, preservation of user inventory, demo coordinate bounds/labels and the `/stats` alias; inline JavaScript `node --check`; HTML ID/selector and CSS-brace checks; buyer/cafe city options and map-centre keys; urgency and WhatsApp-share code. The project database was backed up and expanded from 4 to 30 offers (26 appended), with its existing offers unchanged and order count remaining 0.

Not verified: a live Uvicorn HTTP listener or real-browser visual/interaction acceptance. The available Python 3.12 embeddable runtime stalls during asyncio's loopback socket-pair setup, so Uvicorn never binds. The connected browser could not reach the isolated local preview on device loopback. No public URL is configured in the selected plain-local mode. These are environment limits, not a claim of live-server acceptance.

## Official references

- Python 3.12.10: https://www.python.org/downloads/release/python-31210/
- Python embeddable-package limitations: https://docs.python.org/3.12/using/windows.html#the-embeddable-package
- Leaflet quick start/download: https://leafletjs.com/examples/quick-start/ and https://leafletjs.com/download.html
- OpenStreetMap tile policy: https://operations.osmfoundation.org/policies/tiles/
- WhatsApp pre-filled share links: https://faq.whatsapp.com/425247423114725

## Approved security and feature extension

The follow-up request adds cafe-account registration/login/logout with Argon2id password hashes, opaque server-side sessions in HttpOnly cookies, CSRF and same-origin checks, session expiry, rate limiting, and strict cafe-owner scoping for listings and order PII. Keep buyer reservation available without a buyer account. Public deployment is preparation only; no hosting provider or public URL is selected. Partner identity verification, password reset, payment/refunds, and multi-replica session storage remain out of scope and must be called out before launch.

The storefront extension adds the slogan “Save food. Save money. Every bite counts.” in header, hero, footer and impact tab; a banner derived from the visible filter set and low-stock/nearest eligible offer; mystery rescue box type; and an opt-in Flash Drop price rule 45 minutes before pickup ends. Flash pricing is disclosed to the partner and buyer and becomes the captured order price, so stats and commission use the actual reserved price. Pickup start/end timestamps are stored in UTC while presentation uses Kazakhstan time. The partner forecast is a transparent history-based baseline with a disclosed time-based cold-start fallback, not an external/LLM model; it fills the quantity while retaining merchant-entered price and pickup terms.

Implementation gate: migrations must preserve all 30 current offers and existing orders; order stock decrement remains a single conditional atomic update; public GET endpoints must not expose customer PII; protected endpoints require the account owner; and tests must cover expiry/flash boundaries, concurrency-safe reservations, account isolation, CSRF rejection, session expiry, pricing/stat formulas, and predictable cold-start behavior. Source-level tests are distinct from live HTTP/browser acceptance. A public launch stays blocked until a provider and HTTPS/reverse-proxy settings are chosen and verified.

Security references: OWASP Session Management https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html ; Password Storage https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html ; CSRF Prevention https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html ; FastAPI password hashing https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/.


## Implementation status — 2026-10-24

Completed in this pass: cafe registration/login/logout, Argon2id password hashes, hashed opaque server sessions, HttpOnly/SameSite cookies, CSRF + same-origin enforcement, idle/absolute expiry, database-backed rate limits, and owner-scoped dashboard/order PII. The storefront now has its requested slogan placements, current-filter low-stock/nearest hot banner, mystery-box listing, transparent per-cafe history forecast with cold-start fallback, and an opt-in server-priced 45-minute Flash Drop. Local development docs now bind only to loopback; Docker production defaults require secure cookies and keep the app port private.

Verification passed offline: compilation; auth/CSRF/owner-isolation/logout/rate-limit/forecast/Flash Drop/expiry/stats route tests; legacy database migration; 10-city seed idempotency; and a two-thread last-unit reservation race yielding one success and one 409. The project DB preserves all 30 demos and existing order count is still zero. Compose syntax and UI source/JavaScript checks passed. No provider or public URL has been chosen; live Uvicorn/browser acceptance and a Docker image build remain unverified in this environment. The provider-selection, TLS/proxy trust, merchant verification and data-retention steps remain launch gates.
