from types import SimpleNamespace

import pytest

from backend.app.routers import telegram


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"text": "/stats"}, "/stats"),
        ({"text": "   /warn@MyBot   now"}, "/warn"),
        ({"caption": "/warn"}, "/warn"),
        (
            {
                "text": "/start something",
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
            "/start",
        ),
        ({"text": "no command"}, None),
    ],
)
def test_extract_command_variants(message, expected):
    assert telegram._extract_command(message) == expected


def test_is_authorized_user_defaults_to_true(monkeypatch):
    monkeypatch.setattr(telegram, "settings", SimpleNamespace(telegram_allowed_users=[]), raising=False)
    assert telegram._is_authorized_user({"from": {"id": 123}})


def test_is_authorized_user_checks_ids_and_usernames(monkeypatch):
    monkeypatch.setattr(
        telegram,
        "settings",
        SimpleNamespace(telegram_allowed_users=["12345", "FriendlyUser"]),
        raising=False,
    )

    assert telegram._is_authorized_user({"from": {"id": 12345}})
    assert telegram._is_authorized_user({"from": {"username": "friendlyuser"}})
    assert not telegram._is_authorized_user({"from": {"id": 1, "username": "nobody"}})
