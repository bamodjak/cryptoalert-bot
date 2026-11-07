from app.watchlists import MAX_WATCHLIST_ITEMS, SUPPORTED, asset_picker_keyboard, watchlist_keyboard


def test_watchlist_asset_catalog_is_unique_and_bounded():
    symbols = [symbol for symbol, _ in SUPPORTED]
    assert len(symbols) == len(set(symbols))
    assert len(symbols) >= MAX_WATCHLIST_ITEMS
    assert {"btc", "eth", "sol", "usdt", "usdc"} <= set(symbols)


def test_asset_picker_uses_callback_data_and_back_action():
    keyboard = asset_picker_keyboard()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "wl:add:btc" in callbacks
    assert "wl:add:eth" in callbacks
    assert "wl:open" in callbacks


def test_watchlist_keyboard_exposes_add_and_dashboard_actions():
    keyboard = watchlist_keyboard([])
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["wl:add", "wl:addcustom", "nav:home"]



def test_watchlist_add_reports_temporary_market_lookup_failure():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.prices import PriceError
    from app.watchlists import cb_watchlist_add_asset

    async def scenario():
        user = SimpleNamespace(
            id=123, language="en", username="tester", first_name="Tester",
            last_name=None,
        )
        session = SimpleNamespace(
            get=AsyncMock(return_value=user),
            flush=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123, username="tester", first_name="Tester", last_name=None),
            data="wl:add:btc",
            answer=AsyncMock(),
            message=None,
        )
        prices = SimpleNamespace(resolve=AsyncMock(side_effect=PriceError("provider unavailable")))

        await cb_watchlist_add_asset(callback, session, prices)

        callback.answer.assert_awaited_once()
        assert callback.answer.await_args.kwargs["show_alert"] is True
        assert "temporarily" in callback.answer.await_args.args[0].lower() or "busy" in callback.answer.await_args.args[0].lower()

    asyncio.run(scenario())
