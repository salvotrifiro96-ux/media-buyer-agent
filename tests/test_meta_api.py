import pytest

from agent.meta_api import CTA_OPTIONS, MetaClient


class TestMetaClientInit:
    def test_requires_token(self):
        with pytest.raises(ValueError, match="access_token"):
            MetaClient(access_token="", ad_account_id="act_1")

    def test_ad_account_prefix(self):
        with pytest.raises(ValueError, match="act_"):
            MetaClient(access_token="t", ad_account_id="123")

    def test_valid(self):
        c = MetaClient(access_token="t", ad_account_id="act_1")
        assert c.account == "act_1"


class TestCreateAdValidation:
    def setup_method(self):
        self.client = MetaClient(access_token="t", ad_account_id="act_1")

    def test_invalid_status(self):
        with pytest.raises(ValueError, match="status"):
            self.client.create_ad(
                adset_id="a", ad_name="n", page_id="p",
                instagram_user_id="i", landing_url="https://x",
                image_hash="h", headline="hl", body="b",
                status="UNKNOWN",
            )

    def test_invalid_cta(self):
        with pytest.raises(ValueError, match="cta_type"):
            self.client.create_ad(
                adset_id="a", ad_name="n", page_id="p",
                instagram_user_id="i", landing_url="https://x",
                image_hash="h", headline="hl", body="b",
                cta_type="FAKE_CTA",
            )


def test_cta_options_sorted_and_nonempty():
    assert len(CTA_OPTIONS) > 0
    assert CTA_OPTIONS == sorted(CTA_OPTIONS)
    assert "LEARN_MORE" in CTA_OPTIONS
