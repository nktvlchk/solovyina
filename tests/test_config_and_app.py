import logging
import socket

import pytest

from bot.app import build_application
from bot.config import ConfigError, Settings
from bot.logging_setup import RedactSecretsFilter
from tests.conftest import make_settings

SECRET = "1234567890:AAH" + "x" * 32


def test_missing_token():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({})
    assert "TELEGRAM_BOT_TOKEN" in str(exc.value)


def test_error_never_contains_token_value():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({"TELEGRAM_BOT_TOKEN": "notatoken-secretvalue"})
    assert "secretvalue" not in str(exc.value)


def test_settings_repr_hides_secrets():
    s = Settings.from_env({"TELEGRAM_BOT_TOKEN": SECRET, "ANTHROPIC_API_KEY": "sk-ant-secret"})
    assert SECRET not in repr(s) and "sk-ant-secret" not in repr(s)


def test_defaults():
    s = make_settings()
    assert s.anthropic_api_key is None and s.llm_enabled is False
    assert s.store_raw_text is False
    assert s.log_level == "INFO"
    assert s.admin_ids == frozenset()


def test_admin_ids_and_validation():
    assert make_settings(ADMIN_IDS="1, 2,3").admin_ids == {1, 2, 3}
    with pytest.raises(ConfigError):
        make_settings(ADMIN_IDS="alice")
    with pytest.raises(ConfigError):
        make_settings(LLM_DAILY_LIMIT="many")
    with pytest.raises(ConfigError):
        make_settings(STORE_RAW_TEXT="maybe")


def test_redaction_filter():
    record = logging.LogRecord("x", logging.INFO, "f", 1, "url https://api/bot%s/getMe", (SECRET,), None)
    RedactSecretsFilter().filter(record)
    assert SECRET not in record.getMessage() and "[REDACTED]" in record.getMessage()


def test_application_builds_offline():
    app = build_application(make_settings())
    assert app.bot_data["services"].settings.telegram_bot_token == "123456:TEST"


def test_network_is_blocked_in_unit_tests():
    with pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 443))


def test_repository_has_no_tracked_secrets():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_secrets.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
