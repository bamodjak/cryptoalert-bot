import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"


def imported_modules(path: str) -> set[str]:
    tree = ast.parse((APP / path).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_core_asset_registry_has_no_runtime_framework_dependencies():
    modules = imported_modules("assets.py")
    assert modules <= {"dataclasses"}


def test_shared_keyboards_do_not_depend_on_feature_handlers_or_providers():
    modules = imported_modules("keyboards.py")
    assert not any(name.endswith(("handlers", "prices", "advisor", "watchlists")) for name in modules)


def test_market_views_are_provider_and_transport_neutral():
    modules = imported_modules("market_views.py")
    assert not any(name.endswith(("handlers", "prices", "advisor")) for name in modules)
    assert not any(name.startswith(("redis", "sqlalchemy", "aiohttp")) for name in modules)


def test_feature_routers_do_not_import_each_other_for_shared_helpers():
    assert "app.handlers" not in imported_modules("watchlists.py")


def test_user_lifecycle_service_does_not_depend_on_telegram_transport():
    modules = imported_modules("users.py")
    assert not any(name == "aiogram" or name.startswith("aiogram.") for name in modules)
