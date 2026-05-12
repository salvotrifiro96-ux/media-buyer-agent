"""Meta Graph API wrapper per il media-buyer.

Sviluppato dall'estratto del `funnel-refresher-agent` ma con focus su:
  - list campagne / list adset (per popolare i dropdown)
  - create_ad partendo da un image_hash (upload bytes oppure download URL Supabase)
  - clone targeting di un adset esistente quando l'operatore vuole un nuovo adset

Niente logica di "pause losers" qui — quella sta nel refresher.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

GRAPH = "https://graph.facebook.com/v21.0"


@dataclass(frozen=True)
class CampaignInfo:
    id: str
    name: str
    status: str
    objective: str


@dataclass(frozen=True)
class AdsetInfo:
    id: str
    name: str
    status: str
    effective_status: str
    campaign_id: str
    daily_budget: int | None  # in cents


class MetaError(RuntimeError):
    """Raised when the Graph API returns an error response."""


class MetaClient:
    def __init__(self, access_token: str, ad_account_id: str) -> None:
        if not access_token:
            raise ValueError("Meta access_token is required")
        if not ad_account_id.startswith("act_"):
            raise ValueError("ad_account_id must start with 'act_'")
        self.token = access_token
        self.account = ad_account_id

    # ── low-level helpers ─────────────────────────────────────────────
    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {"access_token": self.token, **(params or {})}
        r = requests.get(f"{GRAPH}/{endpoint}", params=p, timeout=30)
        body = r.json()
        if "error" in body:
            raise MetaError(f"GET {endpoint}: {body['error']}")
        return body

    def _post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        body = {"access_token": self.token, **data}
        r = requests.post(f"{GRAPH}/{endpoint}", data=body, timeout=30)
        resp = r.json()
        if "error" in resp:
            raise MetaError(f"POST {endpoint}: {resp['error']}")
        return resp

    # ── reads ─────────────────────────────────────────────────────────
    def list_campaigns(self, limit: int = 50) -> list[CampaignInfo]:
        data = self._get(
            f"{self.account}/campaigns",
            {
                "fields": "id,name,status,effective_status,objective",
                "limit": limit,
            },
        ).get("data", [])
        return [
            CampaignInfo(
                id=c["id"],
                name=c.get("name", ""),
                status=c.get("status", ""),
                objective=c.get("objective", ""),
            )
            for c in data
        ]

    def list_adsets(self, campaign_id: str) -> list[AdsetInfo]:
        data = self._get(
            f"{campaign_id}/adsets",
            {
                "fields": "id,name,status,effective_status,campaign_id,daily_budget",
                "limit": 50,
            },
        ).get("data", [])
        out: list[AdsetInfo] = []
        for a in data:
            try:
                budget = int(a.get("daily_budget")) if a.get("daily_budget") else None
            except (TypeError, ValueError):
                budget = None
            out.append(
                AdsetInfo(
                    id=a["id"],
                    name=a.get("name", ""),
                    status=a.get("status", ""),
                    effective_status=a.get("effective_status", ""),
                    campaign_id=a.get("campaign_id", campaign_id),
                    daily_budget=budget,
                )
            )
        return out

    def find_active_adset(self, campaign_id: str) -> str:
        adsets = self.list_adsets(campaign_id)
        actives = [a for a in adsets if a.effective_status == "ACTIVE"]
        if not actives:
            raise MetaError(
                f"No active adset found in campaign {campaign_id}. "
                f"Available: {[(a.name, a.effective_status) for a in adsets]}"
            )
        return actives[0].id

    def get_adset_full(self, adset_id: str) -> dict[str, Any]:
        """Tutti i campi utili per clonare un adset."""
        fields = [
            "name",
            "campaign_id",
            "daily_budget",
            "billing_event",
            "optimization_goal",
            "bid_strategy",
            "promoted_object",
            "targeting",
            "destination_type",
            "status",
            "effective_status",
        ]
        return self._get(adset_id, {"fields": ",".join(fields)})

    # ── writes ────────────────────────────────────────────────────────
    def create_adset(
        self,
        *,
        campaign_id: str,
        name: str,
        daily_budget_cents: int,
        billing_event: str,
        optimization_goal: str,
        bid_strategy: str,
        promoted_object: dict[str, Any],
        targeting: dict[str, Any],
        start_time: str | None = None,
        status: str = "ACTIVE",
        destination_type: str | None = None,
    ) -> str:
        data: dict[str, Any] = {
            "campaign_id": campaign_id,
            "name": name,
            "daily_budget": str(daily_budget_cents),
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            "bid_strategy": bid_strategy,
            "promoted_object": json.dumps(promoted_object),
            "targeting": json.dumps(targeting),
            "status": status,
        }
        if start_time:
            data["start_time"] = start_time
        if destination_type:
            data["destination_type"] = destination_type
        r = self._post(f"{self.account}/adsets", data)
        return r["id"]

    def upload_image_bytes(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Upload PNG bytes a /adimages, ritorna l'image_hash."""
        r = requests.post(
            f"{GRAPH}/{self.account}/adimages",
            files={"file": (filename, image_bytes, "image/png")},
            data={"access_token": self.token},
            timeout=60,
        )
        body = r.json()
        if "images" not in body:
            raise MetaError(f"Upload failed: {body}")
        return list(body["images"].values())[0]["hash"]

    def upload_image_from_url(self, url: str, filename: str = "image.png") -> str:
        """Convenience: scarica l'immagine da `url` e la rilancia su Meta.

        Utile per i visual generati dal graphic-designer (sono su Supabase
        Storage), evitando di chiedere all'operatore di download/upload manuale.
        """
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            raise MetaError(f"Download immagine {url} fallito: HTTP {r.status_code}")
        return self.upload_image_bytes(r.content, filename=filename)

    def create_ad(
        self,
        *,
        adset_id: str,
        ad_name: str,
        page_id: str,
        instagram_user_id: str,
        landing_url: str,
        image_hash: str,
        headline: str,
        body: str,
        description: str = "",
        cta_type: str = "LEARN_MORE",
        creative_label: str | None = None,
        status: str = "PAUSED",
    ) -> dict[str, str]:
        """Crea creative + ad. Ritorna {'ad_id', 'creative_id'}.

        Default `status=PAUSED` perche` il media-buyer e` per *creazione*: meglio
        rivedere in Ads Manager prima di farle partire. L'operatore puo`
        switchare a ACTIVE nell'UI.
        """
        if status not in ("ACTIVE", "PAUSED"):
            raise ValueError(f"status deve essere ACTIVE o PAUSED, ricevuto {status!r}")
        if cta_type not in _ALLOWED_CTAS:
            raise ValueError(f"cta_type non supportata: {cta_type}")
        object_story_spec = {
            "page_id": page_id,
            "instagram_user_id": instagram_user_id,
            "link_data": {
                "link": landing_url,
                "image_hash": image_hash,
                "name": headline,
                "message": body,
                "description": description,
                "call_to_action": {
                    "type": cta_type,
                    "value": {"link": landing_url},
                },
            },
        }
        creative = self._post(
            f"{self.account}/adcreatives",
            {
                "name": creative_label or f"MB — {ad_name}",
                "object_story_spec": json.dumps(object_story_spec),
            },
        )
        ad = self._post(
            f"{self.account}/ads",
            {
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative["id"]}),
                "name": ad_name,
                "status": status,
            },
        )
        return {"ad_id": ad["id"], "creative_id": creative["id"]}


# CTA accettate da Meta. Aggiungerne altre se servono — vedi
# https://developers.facebook.com/docs/marketing-api/reference/ad-creative-link-data-call-to-action
_ALLOWED_CTAS = frozenset({
    "LEARN_MORE",
    "SIGN_UP",
    "APPLY_NOW",
    "DOWNLOAD",
    "GET_OFFER",
    "SUBSCRIBE",
    "CONTACT_US",
    "GET_QUOTE",
    "BOOK_TRAVEL",
    "ORDER_NOW",
    "SHOP_NOW",
    "WATCH_MORE",
    "MESSAGE_PAGE",
    "INSTALL_APP",
})

CTA_OPTIONS = sorted(_ALLOWED_CTAS)
