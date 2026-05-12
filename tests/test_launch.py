import pytest

from agent.launch import CreativeSpec, LaunchPlan


class TestCreativeSpecValidation:
    def test_requires_exactly_one_image_source(self):
        with pytest.raises(ValueError, match="ESATTAMENTE uno"):
            CreativeSpec(
                ad_name="x", headline="y", body="z",
                image_bytes=None, image_url=None,
            )
        with pytest.raises(ValueError, match="ESATTAMENTE uno"):
            CreativeSpec(
                ad_name="x", headline="y", body="z",
                image_bytes=b"123", image_url="https://x.com/y.png",
            )

    def test_url_only_ok(self):
        c = CreativeSpec(
            ad_name="x", headline="y", body="z",
            image_url="https://x.com/y.png",
        )
        assert c.image_bytes is None

    def test_bytes_only_ok(self):
        c = CreativeSpec(
            ad_name="x", headline="y", body="z",
            image_bytes=b"123",
        )
        assert c.image_url is None


class TestLaunchPlanValidation:
    def _good_plan(self, **overrides):
        defaults = dict(
            campaign_id="123",
            landing_url="https://x.com/lp",
            page_id="abc",
            instagram_user_id="def",
            target_adset_id="456",
        )
        defaults.update(overrides)
        return LaunchPlan(**defaults)

    def test_good_plan_no_errors(self):
        plan = self._good_plan()
        assert plan.validate() == []

    def test_missing_campaign(self):
        plan = self._good_plan(campaign_id="")
        assert "campaign_id mancante" in plan.validate()

    def test_invalid_landing_url(self):
        plan = self._good_plan(landing_url="not-http")
        errors = plan.validate()
        assert any("landing_url" in e for e in errors)

    def test_missing_page_id(self):
        plan = self._good_plan(page_id="")
        assert "page_id mancante" in plan.validate()

    def test_invalid_status(self):
        plan = self._good_plan(start_status="WHATEVER")
        assert any("start_status" in e for e in plan.validate())

    def test_new_adset_requires_name_and_budget(self):
        plan = self._good_plan(create_new_adset=True, target_adset_id="")
        errors = plan.validate()
        assert any("new_adset_name" in e for e in errors)
        assert any("new_adset_daily_budget" in e for e in errors)

    def test_existing_adset_requires_id(self):
        plan = self._good_plan(target_adset_id="", create_new_adset=False)
        assert any("target_adset_id" in e for e in plan.validate())
