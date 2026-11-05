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
## Implemented experience additions

- Asset details are shared by the Top-33 browser, market movers and watchlist entries. Contextual actions lead to alert creation, watchlist addition and research.
- The curated core asset registry is shared by pricing, watchlists, the live dashboard and the advisor. Dynamic provider-ranked lists remain dynamic.
- Persistent quick navigation and watchlist copy are localized in English and Arabic.
- Personal settings persist timezone and advanced presentation mode. Advanced mode adds market capitalization and 24-hour volume when the provider supplies those metrics.
- Alerts can be edited, snoozed for one hour and resumed. The worker excludes currently snoozed alerts. Triggered alerts remain available through recent history.
- User timezone is used to render triggered-alert history. The timezone database is installed as a Python dependency so ZoneInfo works in minimal containers.

- The live trade stream and Telegram dashboard updater share one Redis-elected runtime lease. This keeps the in-memory quote cache and message updater on the same instance during overlapping deployments and prevents duplicate stream consumers or dashboard edits.

## Notification preferences and advisor explainability

- User settings persist independent price-alert and gas-alert notification toggles. Disabled categories are excluded from alert evaluation; their active rules remain stored and can resume when re-enabled.
- The premium advisor exposes stable, human-readable reasons for trend, momentum, RSI, volume and volatility contributions.
- The advisor includes a closed-candle historical directional evaluation with sample count, win rate and average directional return. It is explicitly in-sample, excludes fees/slippage and is not presented as a forecast.

## Live-market operational telemetry

- Prometheus metrics expose runtime lease ownership, WebSocket connection state, reconnects, received trades, dashboard edits, edit failures and lease-loss events.
- The public `/health` response keeps dependency and alert-worker readiness checks, and adds non-sensitive `live_runtime_active` and `live_stream_connected` fields. A disconnected exchange stream remains observable without falsely marking the entire bot unavailable.

## Current module map

- `main.py` is the composition root: it creates infrastructure, registers routers, starts workers and exposes health/metrics endpoints.
- `assets.py` is the dependency-light canonical asset registry; `prices.py` owns provider access and normalization.
- `market_handlers.py` owns market and advisor Telegram routes; `market_views.py` owns provider-neutral market presentation.
- `watchlists.py`, `preferences.py` and `live_dashboard.py` own their feature routers and workflows.
- `keyboards.py` owns shared Telegram navigation and alert keyboards; `users.py` owns Telegram-user lifecycle synchronization.
- `advisor.py` owns quantitative scoring, explanations and historical evaluation; `worker.py` owns scheduled alert evaluation.
- `handlers.py` remains the compatibility/root router for alert creation and management, billing, referrals and legacy admin commands. New features should be added as dedicated feature modules and included from the composition root rather than expanding this file.
