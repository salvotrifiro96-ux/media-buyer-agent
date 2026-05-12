import pytest

from agent.accounts import MetaAccount, load_account


def test_is_configured_true_when_all_present():
    a = MetaAccount(
        slug="X", name="X",
        access_token="t", ad_account_id="act_1",
        page_id="p", instagram_user_id="ig",
    )
    assert a.is_configured is True


@pytest.mark.parametrize("missing", ["access_token", "ad_account_id", "page_id", "instagram_user_id"])
def test_is_configured_false_when_missing(missing):
    kwargs = dict(
        slug="X", name="X",
        access_token="t", ad_account_id="act_1",
        page_id="p", instagram_user_id="ig",
    )
    kwargs[missing] = ""
    a = MetaAccount(**kwargs)
    assert a.is_configured is False


def test_load_account_from_env(monkeypatch):
    monkeypatch.setenv("META_TEST_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_TEST_AD_ACCOUNT_ID", "act_99")
    monkeypatch.setenv("META_TEST_PAGE_ID", "pp")
    monkeypatch.setenv("META_TEST_INSTAGRAM_USER_ID", "ii")
    a = load_account("test")
    assert a.is_configured
    assert a.access_token == "tok"
    assert a.ad_account_id == "act_99"


def test_load_account_missing_returns_empty():
    a = load_account("nonexistent_slug_zzz")
    assert a.is_configured is False
    assert a.access_token == ""
