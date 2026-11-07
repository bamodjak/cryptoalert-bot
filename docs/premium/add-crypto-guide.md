# Adding a cryptocurrency to ORBSAT

Premium members can request a new asset for the curated market views. Use the steps below so the symbol resolves to the correct asset and market-data provider ID.

## 1. Find the canonical asset

1. Open [CoinGecko](https://www.coingecko.com/en/coins).
2. Search for the exact project name (not just its ticker).
3. Open the asset page and copy its CoinGecko ID from the URL. For example, the Bitcoin page URL ends in `/coins/bitcoin`, so its ID is `bitcoin`.
4. Check the ticker and project name against the project's official website. Tickers are not unique.

## 2. Check that ORBSAT can resolve it

1. In your private chat with ORBSAT, send `/price TICKER` (replace TICKER with the asset symbol).
2. Confirm the returned name matches the intended project.
3. If it resolves to a different asset, do not add it. Send the official CoinGecko asset link to support for review.

## 3. Add it to your Watchlist

1. Open **Watchlist** from the bot menu or send `/watchlist`.
2. Tap **Add asset** and select the matching asset if it appears.
3. Confirm that the name and price are correct.

Useful links:
- [CoinGecko asset directory](https://www.coingecko.com/en/coins)
- [CoinMarketCap cryptocurrency directory](https://coinmarketcap.com/all/views/all/)
- [ORBSAT bot help](https://t.me/orbsat16bot)

**Important:** A Watchlist entry does not automatically add an asset to the Premium Live Market or signal universe. Those curated feeds require a provider mapping and validation before release. Never submit seed phrases, private keys, exchange API secrets, or wallet credentials.
