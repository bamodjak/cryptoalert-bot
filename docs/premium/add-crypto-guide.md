# Adding a custom cryptocurrency to ORBSAT

Premium members can add a custom asset to their personal Watchlist without leaving Telegram. ORBSAT searches CoinGecko, lets the user choose the exact project, verifies the asset metadata and current USD quote, and then adds it to the Watchlist.

## Steps in Telegram

1. Open **Watchlist** from the main menu or send `/watchlist`.
2. Tap **Add custom asset (Premium)**.
3. Type the project or coin name (for example, `Render`, `Arbitrum` or `Solana`). A ticker also works when it returns useful matches.
4. Review the result buttons carefully. Coin tickers are not unique; choose the matching project name and ticker, not just the first familiar symbol.
5. ORBSAT checks the selected CoinGecko asset metadata and current USD quote before adding it. If verification fails, choose another result or try again later.

## Limits and recovery

- The Watchlist holds up to 10 assets total. Custom assets use one of those slots; Premium does not increase the cap.
- Duplicate assets are not added twice.
- If the Watchlist is full, remove an existing item before adding another.
- Use `/cancel` or the **Cancel** button to leave the search flow. You can reopen Watchlist at any time.
- Search and quote providers can be rate-limited or temporarily unavailable. A failed verification does not add an unverified asset.

## Scope

A personal Watchlist entry does not automatically add an asset to the Premium Live Market or quantitative research universe. Those curated feeds require separate provider mapping, data-quality checks and validation before release.

Never submit seed phrases, private keys, exchange API secrets or wallet credentials to the bot.
