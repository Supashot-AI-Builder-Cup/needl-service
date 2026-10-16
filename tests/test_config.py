"""Settings parsing and validation (no Firestore needed)."""

import pytest

from app.config import MIB, load_settings


def test_defaults(monkeypatch):
    for name in ("MAX_UPLOAD_TOTAL_MB", "TRUSTED_PROXY_HOPS", "JOB_TIMEOUT_S", "JOB_STALE_AFTER_S", "ENABLE_LEGACY_ROUTES"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert settings.max_upload_total_bytes == 30 * MIB
    assert settings.trusted_proxy_hops == 1
    assert settings.job_stale_after_s == 240
    assert settings.legacy_routes is True
    assert settings.firestore_database == "(default)"


@pytest.mark.parametrize("value", ["0", "31"])
def test_upload_limit_must_fit_cloud_run(monkeypatch, value):
    monkeypatch.setenv("MAX_UPLOAD_TOTAL_MB", value)
    with pytest.raises(ValueError, match="MAX_UPLOAD_TOTAL_MB"):
        load_settings()


def test_upload_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_TOTAL_MB", "12")
    assert load_settings().max_upload_total_bytes == 12 * MIB


def test_proxy_hops_must_be_positive(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    with pytest.raises(ValueError, match="TRUSTED_PROXY_HOPS"):
        load_settings()


def test_stale_after_follows_job_timeout(monkeypatch):
    monkeypatch.delenv("JOB_STALE_AFTER_S", raising=False)
    monkeypatch.setenv("JOB_TIMEOUT_S", "100")
    assert load_settings().job_stale_after_s == 160


def test_legacy_routes_toggle(monkeypatch):
    monkeypatch.setenv("ENABLE_LEGACY_ROUTES", "false")
    assert load_settings().legacy_routes is False


def test_cors_origins_drop_trailing_slash(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173/, https://needl.web.app")
    assert load_settings().cors_origins == ["http://localhost:5173", "https://needl.web.app"]
