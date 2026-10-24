# QaldyQ — TODO

## Implemented and verified offline

- [x] Single-file Russian SPA, FastAPI + SQLAlchemy + SQLite, KZT prices, responsive eco UI and QaldyQ slogan in header, hero, footer and impact tab.
- [x] Ten-city filter and cafe selector; 26 explicitly marked demo offers with approximate city-centre pins; Leaflet map/list, pickup-time filter and opt-in 25 km geolocation.
- [x] Low-stock/nearest hot banner based on the live filtered selection, orange 1–2 quantity badge, buyer booking/profile local storage, and WhatsApp share with the actual server-calculated discount.
- [x] Cafe registration/login/logout, Argon2id password hashes, opaque hashed server sessions in HttpOnly/SameSite cookies, CSRF and same-origin checks, idle/absolute expiry, SQLite-backed rate limiting and cafe-owner isolation of dashboard/orders/phone PII.
- [x] Pickup expiry, atomic inventory decrement, opt-in 45-minute Flash Drop pricing, Mystery Rescue Box and explainable per-cafe history forecast with cold-start fallback.
- [x] 20% commission and weight-based impact stats, including `/api/stats` and `/stats`; migration retains existing offers/orders.
- [x] Regression suite passed: Python compilation; auth/session/logout/CSRF/owner/rate-limit/forecast/price/stats; category filtering and legacy category migration; ten-city seed/idempotency; atomic multi-item basket success/rollback/PII boundaries; concurrent last-unit race (one order, one 409); HTML/JS/source checks; Compose YAML parse.
- [x] README and deployment docs updated for loopback-only local HTTP, secure-cookie/proxy defaults and explicit pre-launch requirements; secret-free `.env.example` now matches the Compose variables and documents the host-specific proxy allow-list.
- [x] Premium visual direction, supplied QaldyQ logo, eco palette and responsive ad rails merged; English “AUA Sense · Hackathon” label, company/business/help/policy header, partner hub and support/policy dialogs added. Telegram CTA uses the user-provided `@QALDYQ_BOT` (availability must be confirmed); category/favorites/cart, photo catalog, Kazakhstan-time Today/Daily filters and manual courier requests are integrated, with courier address owner-only. Demo disclosures and corrected Cyrillic Atyrau key retained.
- [x] Delivery package rebuilt and CRC/manifest verified: `outputs/QaldyQ-MVP.zip`.

## Launch gates not yet complete

- [ ] Select a hosting provider; no public deployment or URL exists.
- [ ] Live Uvicorn/API-browser acceptance and Docker image build were not verified in this execution environment: local loopback preview is unavailable and Docker CLI is absent. An offline fixture was visually reviewed at phone-, tablet- and laptop-sized captures, but does not replace live end-to-end acceptance.
- [ ] Before real merchant/customer use, add partner identity/email verification, password recovery, payments/refunds, pickup confirmation, retention/deletion policy and off-host backups.
- [ ] Before scaling beyond one container, migrate SQLite session/rate-limit storage to a shared managed database/cache; choose a production map-tile provider.
