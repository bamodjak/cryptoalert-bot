"""Canonical registry for the bot's curated/core crypto assets.

Provider identifiers and exchange pairs live here so market data, watchlists,
live dashboards and research features cannot silently drift apart. Dynamic
market-rank lists (for example the top-33 browser) remain provider-driven.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Asset:
    coin_id: str
    symbol: str
    name: str
    paprika_id: str
    binance_pair: str | None = None
    advisor_enabled: bool = False


CORE_ASSETS: tuple[Asset, ...] = (
    Asset("bitcoin", "btc", "Bitcoin", "btc-bitcoin", "btcusdt", True),
    Asset("ethereum", "eth", "Ethereum", "eth-ethereum", "ethusdt", True),
    Asset("solana", "sol", "Solana", "sol-solana", "solusdt", True),
    Asset("binancecoin", "bnb", "BNB", "bnb-binance-coin", "bnbusdt", True),
    Asset("ripple", "xrp", "XRP", "xrp-xrp", "xrpusdt", True),
    Asset("cardano", "ada", "Cardano", "ada-cardano", "adausdt"),
    Asset("dogecoin", "doge", "Dogecoin", "doge-dogecoin", "dogeusdt", True),
    Asset("the-open-network", "ton", "Toncoin", "ton-toncoin", "tonusdt"),
    Asset("tron", "trx", "TRON", "trx-tron", "trxusdt"),
    Asset("avalanche-2", "avax", "Avalanche", "avax-avalanche", "avaxusdt", True),
    Asset("polkadot", "dot", "Polkadot", "dot-polkadot", "dotusdt"),
    Asset("chainlink", "link", "Chainlink", "link-chainlink", "linkusdt", True),
    Asset("litecoin", "ltc", "Litecoin", "ltc-litecoin", "ltcusdt"),
    Asset("tether", "usdt", "Tether", "usdt-tether"),
    Asset("usd-coin", "usdc", "USDC", "usdc-usd-coin", "usdcusdt"),
)

ASSETS_BY_SYMBOL = {asset.symbol: asset for asset in CORE_ASSETS}
ASSETS_BY_ID = {asset.coin_id: asset for asset in CORE_ASSETS}


def get_asset(symbol_or_id: str) -> Asset | None:
    """Resolve a curated asset by ticker or canonical CoinGecko identifier."""
    value = symbol_or_id.strip().lower().lstrip("$")
    return ASSETS_BY_SYMBOL.get(value) or ASSETS_BY_ID.get(value)
