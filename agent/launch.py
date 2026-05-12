"""Launch step — il media-buyer crea N ads partendo da una combinazione
di copy + visual scelti dall'operatore.

Niente "pause losers": questo agente e` per *creazione*, non refresh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.meta_api import MetaClient


@dataclass(frozen=True)
class CreativeSpec:
    """Una singola creativita` da lanciare come ad.

    `image_bytes` OPPURE `image_url` deve essere fornito (l'altro None).
    """

    ad_name: str
    headline: str          # Meta "name" del link_data
    body: str              # Meta "message" del link_data (primary text)
    description: str = ""
    cta_type: str = "LEARN_MORE"
    image_bytes: bytes | None = field(default=None, repr=False)
    image_url: str | None = None  # se non None, viene scaricata e ricaricata su Meta
    creative_label: str | None = None

    def __post_init__(self) -> None:
        if (self.image_bytes is None) == (self.image_url is None):
            raise ValueError(
                "Fornisci ESATTAMENTE uno fra image_bytes e image_url"
            )


@dataclass(frozen=True)
class LaunchPlan:
    campaign_id: str
    landing_url: str
    page_id: str
    instagram_user_id: str
    # Se True crea un nuovo adset clonando un adset attivo. Se False, usa
    # `target_adset_id`.
    create_new_adset: bool = False
    target_adset_id: str = ""
    new_adset_name: str = ""
    new_adset_daily_budget_eur: float = 0.0
    new_adset_start_time_iso: str = ""
    # Status delle ads create: ACTIVE per partire subito, PAUSED per safety.
    start_status: str = "PAUSED"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.campaign_id:
            errors.append("campaign_id mancante")
        if not self.landing_url or not self.landing_url.startswith("http"):
            errors.append("landing_url deve iniziare con http(s)")
        if not self.page_id:
            errors.append("page_id mancante")
        if not self.instagram_user_id:
            errors.append("instagram_user_id mancante")
        if self.start_status not in ("ACTIVE", "PAUSED"):
            errors.append("start_status deve essere ACTIVE o PAUSED")
        if self.create_new_adset:
            if not self.new_adset_name.strip():
                errors.append("new_adset_name richiesto quando create_new_adset=True")
            if self.new_adset_daily_budget_eur <= 0:
                errors.append("new_adset_daily_budget_eur deve essere > 0")
        else:
            if not self.target_adset_id:
                errors.append("target_adset_id richiesto quando create_new_adset=False")
        return errors


@dataclass(frozen=True)
class LaunchResult:
    created: list[dict[str, str]]
    new_adset_id: str | None = None


def _clone_adset(
    meta: MetaClient,
    *,
    source_adset_id: str,
    plan: LaunchPlan,
) -> str:
    import json as _json

    src = meta.get_adset_full(source_adset_id)
    targeting = src.get("targeting") or {}
    promoted_object = src.get("promoted_object") or {}
    if isinstance(targeting, str):
        targeting = _json.loads(targeting)
    if isinstance(promoted_object, str):
        promoted_object = _json.loads(promoted_object)
    return meta.create_adset(
        campaign_id=plan.campaign_id,
        name=plan.new_adset_name,
        daily_budget_cents=int(round(plan.new_adset_daily_budget_eur * 100)),
        billing_event=src.get("billing_event") or "IMPRESSIONS",
        optimization_goal=src.get("optimization_goal") or "OFFSITE_CONVERSIONS",
        bid_strategy=src.get("bid_strategy") or "LOWEST_COST_WITHOUT_CAP",
        promoted_object=promoted_object,
        targeting=targeting,
        start_time=plan.new_adset_start_time_iso or None,
        status="ACTIVE",
        destination_type=src.get("destination_type"),
    )


def launch_ads(
    *,
    meta: MetaClient,
    plan: LaunchPlan,
    creatives: list[CreativeSpec],
) -> LaunchResult:
    """Lancia le creative come ads in Meta.

    Steps:
      1. Valida il piano
      2. Determina l'adset target (esistente vs cloned-new)
      3. Per ogni creative: upload immagine -> create creative + ad
    """
    errors = plan.validate()
    if errors:
        raise ValueError("LaunchPlan non valido: " + "; ".join(errors))
    if not creatives:
        raise ValueError("Nessuna creative da lanciare")

    new_adset_id: str | None = None
    if plan.create_new_adset:
        # clona il primo adset attivo della campagna come template
        src = meta.find_active_adset(plan.campaign_id)
        new_adset_id = _clone_adset(meta, source_adset_id=src, plan=plan)
        target_adset_id = new_adset_id
    else:
        target_adset_id = plan.target_adset_id

    created: list[dict[str, str]] = []
    for spec in creatives:
        if spec.image_url:
            image_hash = meta.upload_image_from_url(
                spec.image_url, filename=f"{spec.ad_name}.png"
            )
        else:
            assert spec.image_bytes is not None  # garantito da __post_init__
            image_hash = meta.upload_image_bytes(
                spec.image_bytes, filename=f"{spec.ad_name}.png"
            )

        result = meta.create_ad(
            adset_id=target_adset_id,
            ad_name=spec.ad_name,
            page_id=plan.page_id,
            instagram_user_id=plan.instagram_user_id,
            landing_url=plan.landing_url,
            image_hash=image_hash,
            headline=spec.headline,
            body=spec.body,
            description=spec.description,
            cta_type=spec.cta_type,
            creative_label=spec.creative_label,
            status=plan.start_status,
        )
        created.append(
            {
                "ad_name": spec.ad_name,
                "ad_id": result["ad_id"],
                "creative_id": result["creative_id"],
            }
        )
    return LaunchResult(created=created, new_adset_id=new_adset_id)
