from app.assets import ASSETS_BY_ID, ASSETS_BY_SYMBOL, CORE_ASSETS, get_asset
from app.advisor import UNIVERSE
from app.live_dashboard import COINS
from app.prices import KNOWN, PAPRIKA_IDS
from app.watchlists import SUPPORTED

def test_core_asset_registry_has_unique_canonical_ids_and_symbols():
    assert len(ASSETS_BY_ID) == len(CORE_ASSETS)
    assert len(ASSETS_BY_SYMBOL) == len(CORE_ASSETS)
    assert len({asset.coin_id for asset in CORE_ASSETS}) == len(CORE_ASSETS)
    assert len({asset.symbol for asset in CORE_ASSETS}) == len(CORE_ASSETS)

def test_provider_mappings_are_derived_from_the_canonical_registry():
    for asset in CORE_ASSETS:
        assert KNOWN[asset.symbol] == (asset.coin_id, asset.name)
        assert PAPRIKA_IDS[asset.coin_id] == asset.paprika_id

def test_watchlist_and_live_dashboard_use_the_same_core_assets():
    assert set(SUPPORTED) == {(asset.symbol, asset.name) for asset in CORE_ASSETS}
    assert {(row[0], row[1].lower(), row[2]) for row in COINS} == {
        (asset.coin_id, asset.symbol, asset.name) for asset in CORE_ASSETS
    }

def test_advisor_universe_is_a_curated_subset_of_supported_exchange_pairs():
    expected = {
        (asset.symbol.upper(), asset.binance_pair.upper())
        for asset in CORE_ASSETS
        if asset.advisor_enabled and asset.binance_pair
    }
    assert set(UNIVERSE) == expected
    assert len(UNIVERSE) >= 5

def test_asset_lookup_accepts_symbol_and_canonical_id():
    assert get_asset('BTC') == get_asset('bitcoin')
    assert get_asset('$eth').coin_id == 'ethereum'
    assert get_asset('not-a-supported-asset') is None