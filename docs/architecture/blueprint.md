# Orbsat architecture blueprint

## Decision

Keep Orbsat as a modular monolith. Do not split the Telegram bot, market data, alert processing, or live dashboard into separately deployed microservices yet. The current workload and Render deployment do not justify the operational cost of distributed services. Use micro-modules with strict dependency direction and small interfaces so individual components can be extracted later if scale requires it.

## Dependency direction

Telegram routers and web endpoints call application services. Application services coordinate domain rules, repositories and provider-neutral contracts. Repositories own persistence. Provider adapters normalize external APIs. Workers invoke the same application services as interactive handlers.

Rules:
1. Routers translate Telegram input into application-service calls. They do not own business rules or provider fallback logic.
2. Domain modules must not import Telegram, SQLAlchemy sessions, Redis clients, or provider SDKs.
3. Services coordinate use cases and may depend on domain contracts, repositories, and provider interfaces.
4. Repositories own persistence queries. A feature module must not import another feature's router or handler to reuse a database helper.
5. Provider adapters normalize external payloads before application services consume them.
6. Workers invoke the same application services as interactive handlers.
7. User-facing copy and keyboard construction belong to presentation and i18n modules, not market or persistence modules.

## Canonical shared concepts

### Asset registry

app/assets.py is the canonical registry for curated/core assets. It owns CoinGecko IDs, tickers, display names, CoinPaprika IDs, Binance pairs, and research-universe eligibility. Dynamic market-ranked lists such as the top-33 browser remain dynamic provider results; they must resolve to canonical IDs where possible and must not be confused with the curated core registry.

### Market data

One provider-neutral market service should own quote lookup and batch quote lookup; global market overview and movers; provider priority, fallback and circuit-breaker behavior; freshness timestamps and stale-data metadata; cache key naming, TTLs and validation. Consumers must not call CoinGecko, CoinPaprika or Binance directly except through provider adapters. All prices must carry source and fetched-at metadata.

### User and preferences

User creation/profile synchronization is in app/users.py, not in Telegram handlers. Preferences should grow through a dedicated service/repository: language, display currency, timezone, quiet hours, notification categories, and beginner/advanced presentation mode.

### Alerts and notifications

Alert creation, validation, update, pause, snooze, expiry, trigger history, and entitlement enforcement belong to an alert application service. Delivery belongs to a notification service with retry and delivery status. The worker and bot must use the same rules.

### Entitlements

Premium checks must use one entitlement service. UI visibility is not an authorization boundary; every use case must enforce entitlement and account suspension server-side.

## Proposed package layout

app/core/       configuration, dependency wiring, errors, logging
app/domain/     assets, market, alert and subscription contracts
app/providers/  CoinGecko, CoinPaprika, Binance, Ethereum adapters
app/repositories/ persistence queries
app/services/   market, alerts, watchlists, preferences, entitlements
app/bot/routers/ thin Telegram adapters
app/bot/keyboards/ reusable navigation and action keyboards
app/bot/presenters/ localized screen rendering
app/bot/middleware/ transport-level concerns
app/workers/    alert, stream, dashboard and scheduled jobs
app/web/        health and metrics endpoints
app/i18n/       English and Arabic translations

Do not move every existing file in one commit. Introduce boundaries incrementally, keep compatibility imports while callers migrate, and remove shims only after tests prove no references remain.

## User experience contract

Every asset detail view must offer consistent contextual actions: open price/history; add/remove from a watchlist; create an alert; open analysis where supported; return to the originating screen.

Top-33 prices, movers, watchlists, alert creation and signals must share the same asset identity. Navigation must be localized, paginated where needed, and preserve a reliable Back/Home route. Every visible button must perform an action or be removed.

Beginner mode is the default. Advanced mode exposes detailed indicators, custom alert rules and diagnostic market context without deleting or hiding the underlying capabilities.

## Delivery phases

1. Shared foundation: canonical asset registry, user lifecycle service, application dependency wiring, common error and localization conventions.
2. Feature interconnection: asset detail use case, shared market quote contract, contextual actions from all asset entry points.
3. Human UX: unified dashboard, search/pagination, full English/Arabic coverage, user preferences, clear stale-data and recovery states.
4. Alert and intelligence depth: edit/snooze/history, notification preferences, signal explanation and historical evaluation.
5. Reliability: distributed coordination for live feeds, integration tests, migration/rollback checks, operational dashboards and production smoke tests.

## Production safety

- Never reset or recreate the production database.
- Use additive, reversible Alembic migrations; destructive changes require a separate data migration and explicit review.
- Preserve Telegram commands during UI transitions.
- Do not change payment/refund or Premium entitlement semantics as part of unrelated refactors.
- Require unit, integration, migration and smoke tests before production rollout.
- Keep provider secrets out of logs and test fixtures.