"""Media Buyer Agent — Streamlit UI.

Tre tab:
  - Sfoglia lavori (output recenti del copywriter + graphic-designer)
  - Crea ad: form per assemblare copy + visual, scegliere account/campagna/adset,
    e lanciare la creative su Meta
  - Storico lanci: ads lanciate in questa sessione
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv

from agent.accounts import DEFAULT_ACCOUNT_SLUGS, MetaAccount, load_accounts
from agent.launch import CreativeSpec, LaunchPlan, launch_ads
from agent.meta_api import CTA_OPTIONS, CampaignInfo, MetaClient
from agent.orch_link import linked_project_id, save_to_project_button, sidebar_project_picker
from agent.store import SupabaseStore


load_dotenv()


def _secret(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val:
        return val
    try:
        return st.secrets.get(key, default)
    except (FileNotFoundError, AttributeError):
        return default


APP_PASSWORD = _secret("APP_PASSWORD")
SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_KEY = _secret("SUPABASE_SECRET_KEY") or _secret("SUPABASE_SERVICE_KEY")

st.set_page_config(page_title="Media Buyer Agent", layout="wide", page_icon="🛒")


# ── Password gate ──────────────────────────────────────────────────
def _password_gate() -> None:
    if not APP_PASSWORD:
        return
    if st.session_state.get("authed"):
        return
    st.title("🛒 Media Buyer Agent")
    pw = st.text_input("Password", type="password", key="pw_input")
    if st.button("Entra"):
        if pw == APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Password errata")
    st.stop()


_password_gate()


# ── Session state ──────────────────────────────────────────────────
DEFAULT_STATE: dict[str, Any] = {
    "account_slug": "",
    "selected_visual": None,        # dict da agent_outputs (agent_type='designer')
    "selected_copy": None,          # dict da agent_outputs (agent_type='copywriter')
    "selected_copy_variant_idx": 0, # quale variante del copy usare
    "campaigns": None,              # lista CampaignInfo, fetched on demand
    "campaign_id": "",
    "adsets": None,                 # lista AdsetInfo
    "launch_history": [],           # storico locale (in-session)
    "filter_agent": "all",
    "filter_subtype": "",
}
for k, v in DEFAULT_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _store() -> SupabaseStore | None:
    if "_supabase_store" not in st.session_state:
        try:
            st.session_state._supabase_store = SupabaseStore.from_env()
        except Exception:
            st.session_state._supabase_store = None
    return st.session_state._supabase_store


def _meta_client(account: MetaAccount) -> MetaClient:
    """Cache un MetaClient per account_slug."""
    cache_key = f"_meta_{account.slug}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = MetaClient(
            access_token=account.access_token,
            ad_account_id=account.ad_account_id,
        )
    return st.session_state[cache_key]


# ── Sidebar ────────────────────────────────────────────────────────


def _sidebar() -> MetaAccount | None:
    st.sidebar.header("🛒 Setup")

    if not SUPABASE_URL or not SUPABASE_KEY:
        st.sidebar.warning("Supabase non configurato: niente browse output.")

    st.sidebar.subheader("Account Meta")
    accounts = load_accounts()
    configured = [a for a in accounts if a.is_configured]

    if not configured:
        st.sidebar.error(
            "Nessun account Meta configurato. Imposta i token nei secrets "
            "(vedi README sezione 'Setup secrets multi-account')."
        )
        return None

    options = [a.slug for a in configured]
    default_idx = 0
    if st.session_state.account_slug in options:
        default_idx = options.index(st.session_state.account_slug)
    slug = st.sidebar.selectbox(
        "Profilo",
        options=options,
        index=default_idx,
        format_func=lambda s: next(
            (f"{a.name} ({a.ad_account_id})" for a in configured if a.slug == s), s
        ),
        key="_sb_account",
    )
    st.session_state.account_slug = slug
    account = next(a for a in configured if a.slug == slug)

    st.sidebar.caption(
        f"Page: `{account.page_id}` · IG: `{account.instagram_user_id}`"
    )
    if account.pixel_id:
        st.sidebar.caption(f"Pixel: `{account.pixel_id}`")

    # Avvisa per account ancora da configurare
    not_yet = [a.slug for a in accounts if not a.is_configured]
    if not_yet:
        st.sidebar.caption(
            "Account ancora da configurare: " + ", ".join(not_yet)
        )

    st.sidebar.divider()
    if st.sidebar.button("🔄 Reset selezione", use_container_width=True):
        for k in list(DEFAULT_STATE):
            st.session_state[k] = DEFAULT_STATE[k]
        st.rerun()

    return account


# ── Tab 1: Sfoglia lavori ──────────────────────────────────────────


def _render_browse_tab() -> None:
    st.subheader("📂 Lavori recenti del team")
    st.caption(
        "Output salvati automaticamente da **copywriter** e **graphic-designer**. "
        "Selezionali per usarli nella tab 'Crea ad'."
    )

    store = _store()
    if store is None:
        st.warning("Supabase non configurato.")
        return

    # Filtri
    fc1, fc2, fc3 = st.columns([1, 1, 3])
    agent_filter = fc1.selectbox(
        "Agente",
        options=["all", "designer", "copywriter"],
        format_func=lambda x: {"all": "Tutti", "designer": "Designer", "copywriter": "Copywriter"}[x],
        index=["all", "designer", "copywriter"].index(st.session_state.filter_agent),
        key="filter_agent",
    )
    subtype_filter = fc2.text_input(
        "Subtype contiene",
        value=st.session_state.filter_subtype,
        placeholder="es. visual_ad, ads_meta",
        key="filter_subtype",
    )
    if fc3.button("🔄 Ricarica"):
        st.rerun()

    # Fetch outputs
    try:
        outputs = store.list_recent_outputs(
            agent_type=None if agent_filter == "all" else agent_filter,
            limit=80,
        )
    except Exception as e:
        st.error(f"Lettura Supabase fallita: {e}")
        return

    if subtype_filter:
        outputs = [o for o in outputs if subtype_filter.lower() in (o.get("subtype") or "").lower()]

    if not outputs:
        st.info("Nessun output trovato con questi filtri.")
        return

    # Separa visual da copy
    visuals = [o for o in outputs if o.get("agent_type") == "designer"]
    copies = [o for o in outputs if o.get("agent_type") == "copywriter"]

    col_v, col_c = st.columns(2)

    with col_v:
        st.markdown(f"### 🖼 Visual ({len(visuals)})")
        for o in visuals:
            _render_visual_card(o)

    with col_c:
        st.markdown(f"### ✍️ Copy ({len(copies)})")
        for o in copies:
            _render_copy_card(o)


def _format_created(iso_string: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_string


def _render_visual_card(output: dict) -> None:
    with st.container(border=True):
        if output.get("image_url"):
            st.image(output["image_url"], use_container_width=True)
        st.markdown(f"**{output.get('title') or '(senza titolo)'}**")
        meta = output.get("metadata") or {}
        st.caption(
            f"`{output.get('subtype', '?')}` · "
            f"fmt={meta.get('fmt', '?')} · "
            f"{_format_created(output.get('created_at', ''))}"
        )
        is_selected = bool(
            st.session_state.selected_visual
            and st.session_state.selected_visual.get("id") == output.get("id")
        )
        btn_label = "✅ Selezionato" if is_selected else "Seleziona"
        if st.button(btn_label, key=f"sel_v_{output['id']}", disabled=is_selected):
            st.session_state.selected_visual = output
            st.rerun()


def _render_copy_card(output: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**{output.get('title') or '(senza titolo)'}**")
        meta = output.get("metadata") or {}
        st.caption(
            f"`{output.get('subtype', '?')}` · "
            f"{_format_created(output.get('created_at', ''))}"
        )
        preview = output.get("preview") or ""
        if preview:
            st.markdown(
                f"<div style='color:#555; font-size:0.9rem; "
                f"max-height:80px; overflow:hidden;'>{preview[:200]}…</div>",
                unsafe_allow_html=True,
            )
        # Conta varianti se presenti
        payload = output.get("payload") or {}
        variants = payload.get("variants") or payload.get("mails") or []
        if variants:
            st.caption(f"Varianti: {len(variants)}")

        is_selected = bool(
            st.session_state.selected_copy
            and st.session_state.selected_copy.get("id") == output.get("id")
        )
        btn_label = "✅ Selezionato" if is_selected else "Seleziona"
        if st.button(btn_label, key=f"sel_c_{output['id']}", disabled=is_selected):
            st.session_state.selected_copy = output
            st.session_state.selected_copy_variant_idx = 0
            st.rerun()


# ── Tab 2: Crea ad ─────────────────────────────────────────────────


def _render_create_tab(account: MetaAccount) -> None:
    st.subheader(f"🚀 Crea ad — {account.name}")
    st.caption(
        f"Assembla copy + visual e lancia su {account.ad_account_id}. "
        f"Le ads partono in `PAUSED` per default."
    )

    # ── Step 1: Visual ─────────────────────────────────────────────
    st.markdown("### 1) Visual")
    visual_choice = st.radio(
        "Da dove arriva il visual?",
        options=["from_designer", "upload"],
        format_func=lambda x: {
            "from_designer": "🖼 Usa visual del graphic-designer (selezionato in 'Sfoglia lavori')",
            "upload": "📤 Upload manuale",
        }[x],
        horizontal=False,
    )

    image_bytes: bytes | None = None
    image_url: str | None = None
    image_source_id: str | None = None  # output_id Supabase, per mark_used

    if visual_choice == "from_designer":
        v = st.session_state.selected_visual
        if not v or not v.get("image_url"):
            st.info(
                "Seleziona un visual dalla tab 'Sfoglia lavori' (o cambia in upload manuale)."
            )
        else:
            st.image(v["image_url"], width=300)
            st.caption(f"**{v.get('title', '')}**")
            image_url = v["image_url"]
            image_source_id = v.get("id")
            if st.button("✖️ Deseleziona visual"):
                st.session_state.selected_visual = None
                st.rerun()
    else:
        uploaded = st.file_uploader(
            "PNG / JPG (formato consigliato 1080x1080 per Meta feed)",
            type=["png", "jpg", "jpeg"],
        )
        if uploaded:
            image_bytes = uploaded.getvalue()
            st.image(image_bytes, width=300)

    st.divider()

    # ── Step 2: Copy ───────────────────────────────────────────────
    st.markdown("### 2) Copy")
    copy_choice = st.radio(
        "Da dove arriva il copy?",
        options=["from_copywriter", "manual"],
        format_func=lambda x: {
            "from_copywriter": "✍️ Usa copy del copywriter (selezionato in 'Sfoglia lavori')",
            "manual": "✏️ Scrivi manualmente",
        }[x],
        horizontal=False,
    )

    headline = ""
    body = ""
    description = ""
    copy_source_id: str | None = None
    suggested_cta = "LEARN_MORE"

    if copy_choice == "from_copywriter":
        c = st.session_state.selected_copy
        if not c:
            st.info("Seleziona un copy dalla tab 'Sfoglia lavori'.")
        else:
            copy_source_id = c.get("id")
            payload = c.get("payload") or {}
            variants = payload.get("variants") or payload.get("mails") or []
            if not variants:
                st.warning("Il copy selezionato non ha varianti utilizzabili.")
            else:
                idx = st.selectbox(
                    "Quale variante?",
                    options=list(range(len(variants))),
                    format_func=lambda i: (
                        f"#{i + 1}: "
                        + (variants[i].get("headline")
                           or variants[i].get("primary_text")
                           or variants[i].get("subject")
                           or "(senza headline)")[:80]
                    ),
                    index=min(st.session_state.selected_copy_variant_idx, len(variants) - 1),
                    key="copy_variant_idx",
                )
                st.session_state.selected_copy_variant_idx = idx
                variant = variants[idx]
                # Map dei campi: dipende dal subtype del copywriter
                if c.get("subtype", "").startswith("ads_meta"):
                    headline = variant.get("headline", "")
                    body = variant.get("primary_text", "")
                    description = variant.get("description", "")
                    suggested_cta = variant.get("cta", "LEARN_MORE")
                elif c.get("subtype", "") == "ads_linkedin":
                    headline = variant.get("headline", "")
                    body = variant.get("body", "")
                    suggested_cta = "LEARN_MORE"
                elif c.get("subtype", "").startswith("nurturing") or c.get("subtype") == "confirmation_mail":
                    # le mail non sono pensate per Meta ad — l'operatore
                    # le converte manualmente. Pre-compiliamo come hint.
                    headline = variant.get("subject", "")
                    body = variant.get("body", "")
                else:
                    headline = variant.get("headline", "") or variant.get("subject", "")
                    body = variant.get("body", "") or variant.get("primary_text", "")
            if st.button("✖️ Deseleziona copy"):
                st.session_state.selected_copy = None
                st.rerun()

    # Comunque mostra i campi editabili (manuale o pre-compilati dal copywriter)
    c1, c2 = st.columns([2, 1])
    headline = c1.text_input(
        "Headline (Meta `name`)",
        value=headline,
        max_chars=40,
        help="Mostrata in alto sotto al primary text. Max 40 char visibili.",
    )
    cta_type = c2.selectbox(
        "Call to action",
        options=CTA_OPTIONS,
        index=CTA_OPTIONS.index(suggested_cta) if suggested_cta in CTA_OPTIONS else 0,
    )
    body = st.text_area(
        "Primary text (Meta `message`)",
        value=body,
        height=140,
        max_chars=2000,
    )
    description = st.text_input(
        "Description (mostrata solo in alcuni placement, opzionale)",
        value=description,
        max_chars=125,
    )

    st.divider()

    # ── Step 3: Destinazione ───────────────────────────────────────
    st.markdown("### 3) Destinazione: campagna + adset")

    if st.session_state.campaigns is None or st.button("🔄 Ricarica campagne"):
        try:
            with st.spinner("Carico campagne Meta…"):
                meta = _meta_client(account)
                st.session_state.campaigns = meta.list_campaigns()
                st.session_state.adsets = None
        except Exception as e:
            st.error(f"Errore Meta API: {e}")
            return

    campaigns: list[CampaignInfo] = st.session_state.campaigns or []
    if not campaigns:
        st.warning("Nessuna campagna trovata su questo account.")
        return

    camp_idx_default = 0
    if st.session_state.campaign_id:
        for i, c in enumerate(campaigns):
            if c.id == st.session_state.campaign_id:
                camp_idx_default = i
                break
    camp = st.selectbox(
        "Campagna",
        options=list(range(len(campaigns))),
        index=camp_idx_default,
        format_func=lambda i: f"{campaigns[i].name} · {campaigns[i].objective} · {campaigns[i].status}",
    )
    selected_campaign = campaigns[camp]
    if selected_campaign.id != st.session_state.campaign_id:
        st.session_state.campaign_id = selected_campaign.id
        st.session_state.adsets = None

    # Adsets
    if st.session_state.adsets is None:
        try:
            meta = _meta_client(account)
            st.session_state.adsets = meta.list_adsets(selected_campaign.id)
        except Exception as e:
            st.error(f"Errore caricamento adsets: {e}")
            return

    adsets = st.session_state.adsets or []
    adset_choice = st.radio(
        "Adset",
        options=["existing", "new"],
        format_func=lambda x: {"existing": "Usa adset esistente", "new": "Crea nuovo adset (clona targeting)"}[x],
        horizontal=True,
    )

    target_adset_id = ""
    new_adset_name = ""
    new_adset_budget = 0.0
    new_adset_start = ""

    if adset_choice == "existing":
        if not adsets:
            st.warning("Nessun adset in questa campagna.")
            return
        idx = st.selectbox(
            "Quale adset?",
            options=list(range(len(adsets))),
            format_func=lambda i: (
                f"{adsets[i].name} · {adsets[i].effective_status}"
                + (f" · €{adsets[i].daily_budget // 100}/d" if adsets[i].daily_budget else "")
            ),
        )
        target_adset_id = adsets[idx].id
    else:
        cols = st.columns(3)
        new_adset_name = cols[0].text_input("Nome nuovo adset")
        new_adset_budget = cols[1].number_input(
            "Budget giornaliero (€)", min_value=1.0, value=20.0, step=1.0
        )
        new_adset_start = cols[2].text_input(
            "Start time ISO (opzionale)",
            placeholder="2026-05-15T08:00:00+02:00",
        )

    st.divider()

    # ── Step 4: Lancio ─────────────────────────────────────────────
    st.markdown("### 4) Lancio")

    cols = st.columns([2, 1, 1])
    landing_url = cols[0].text_input(
        "Landing URL",
        placeholder="https://leonemasterschool.com/lp/...",
    )
    start_status = cols[1].radio(
        "Status iniziale",
        options=["PAUSED", "ACTIVE"],
        horizontal=True,
        help="Default PAUSED: rivedi in Ads Manager e attiva quando pronto.",
    )
    ad_name = cols[2].text_input(
        "Nome ad",
        placeholder="mb_test_1",
        help="Identificatore per la tracciabilita`. Niente spazi.",
    )

    # Pre-flight checks
    missing: list[str] = []
    if not (image_bytes or image_url):
        missing.append("visual")
    if not headline.strip():
        missing.append("headline")
    if not body.strip():
        missing.append("primary text")
    if not landing_url.strip():
        missing.append("landing URL")
    if not ad_name.strip() or " " in ad_name:
        missing.append("nome ad (no spazi)")
    if adset_choice == "new" and not new_adset_name.strip():
        missing.append("nome nuovo adset")

    if missing:
        st.warning(f"Manca: {', '.join(missing)}")

    if st.button(
        f"🚀 Lancia ad ({start_status})",
        type="primary",
        disabled=bool(missing),
        use_container_width=True,
    ):
        _execute_launch(
            account=account,
            campaign_id=selected_campaign.id,
            create_new_adset=(adset_choice == "new"),
            target_adset_id=target_adset_id,
            new_adset_name=new_adset_name,
            new_adset_budget=new_adset_budget,
            new_adset_start=new_adset_start,
            landing_url=landing_url,
            ad_name=ad_name,
            headline=headline,
            body=body,
            description=description,
            cta_type=cta_type,
            start_status=start_status,
            image_bytes=image_bytes,
            image_url=image_url,
            image_source_id=image_source_id,
            copy_source_id=copy_source_id,
        )


def _execute_launch(
    *,
    account: MetaAccount,
    campaign_id: str,
    create_new_adset: bool,
    target_adset_id: str,
    new_adset_name: str,
    new_adset_budget: float,
    new_adset_start: str,
    landing_url: str,
    ad_name: str,
    headline: str,
    body: str,
    description: str,
    cta_type: str,
    start_status: str,
    image_bytes: bytes | None,
    image_url: str | None,
    image_source_id: str | None,
    copy_source_id: str | None,
) -> None:
    plan = LaunchPlan(
        campaign_id=campaign_id,
        landing_url=landing_url,
        page_id=account.page_id,
        instagram_user_id=account.instagram_user_id,
        create_new_adset=create_new_adset,
        target_adset_id=target_adset_id,
        new_adset_name=new_adset_name,
        new_adset_daily_budget_eur=new_adset_budget,
        new_adset_start_time_iso=new_adset_start,
        start_status=start_status,
    )
    creative = CreativeSpec(
        ad_name=ad_name,
        headline=headline,
        body=body,
        description=description,
        cta_type=cta_type,
        image_bytes=image_bytes,
        image_url=image_url,
        creative_label=f"MB — {ad_name}",
    )

    try:
        with st.spinner("Lancio su Meta…"):
            meta = _meta_client(account)
            result = launch_ads(meta=meta, plan=plan, creatives=[creative])
    except Exception as e:
        st.error(f"Lancio fallito: {e}")
        st.caption(traceback.format_exc())
        return

    st.success(f"Ad creata! ad_id={result.created[0]['ad_id']}")

    launched_record = {
        "ad_name": ad_name,
        "ad_id": result.created[0]["ad_id"],
        "creative_id": result.created[0]["creative_id"],
        "account": account.name,
        "campaign_id": campaign_id,
        "adset_id": result.new_adset_id or target_adset_id,
        "status": start_status,
        "landing_url": landing_url,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    # Storico locale
    st.session_state.launch_history.insert(0, launched_record)

    # Archivio persistente su Supabase
    store = _store()
    if store:
        try:
            store.save_text_output(
                agent_type="media-buyer",
                subtype="ad_launched",
                title=f"{ad_name} · {account.name}",
                payload={
                    "launched_ad": launched_record,
                    "creative": {
                        "ad_name": ad_name,
                        "headline": headline,
                        "body": body,
                        "description": description,
                        "cta_type": cta_type,
                        "image_url": image_url,
                    },
                    "plan": {
                        "create_new_adset": create_new_adset,
                        "target_adset_id": target_adset_id,
                        "new_adset_name": new_adset_name,
                        "new_adset_budget": new_adset_budget,
                        "new_adset_start": new_adset_start,
                        "start_status": start_status,
                    },
                    "sources": {
                        "image_source_id": image_source_id,
                        "copy_source_id": copy_source_id,
                    },
                },
                preview=f"ad_id={result.created[0]['ad_id']} · status={start_status}",
                metadata={
                    "account_slug": account.slug,
                    "campaign_id": campaign_id,
                    "start_status": start_status,
                },
            )
        except Exception as e:
            st.toast(f"Archivio non aggiornato: {e}", icon="⚠️")

    # Cross-app: salva ad lanciata nel progetto orchestrator collegato
    if linked_project_id():
        save_to_project_button(
            agent_slug="media",
            output={"launched_ad": launched_record},
            user_input={"campaign_id": campaign_id, "account": account.slug},
            label=f"🎯 Registra ad lanciata per progetto",
            key_suffix=f"media_{result.created[0]['ad_id']}",
        )

    # Mark used su Supabase
    store = _store()
    if store:
        for src_id in filter(None, (image_source_id, copy_source_id)):
            try:
                store.mark_used(src_id)
            except Exception:
                pass


# ── Tab 3: Storico ─────────────────────────────────────────────────


def _render_history_tab() -> None:
    st.subheader("📜 Ads lanciate")
    st.caption(
        "Storico persistente su Supabase. Include le ad lanciate da tutte le "
        "sessioni precedenti."
    )

    store = _store()
    rows: list[dict] = []
    if store is not None:
        try:
            rows = store.list_recent_outputs(agent_type="media-buyer", limit=80)
        except Exception as e:
            st.warning(f"Lettura archivio fallita: {e}. Mostro solo la sessione.")

    # Fallback / unione con sessione corrente
    if not rows:
        history = st.session_state.launch_history
        if not history:
            st.info("Nessuna ad ancora lanciata.")
            return
        for item in history:
            with st.container(border=True):
                cols = st.columns([3, 1])
                cols[0].markdown(
                    f"**{item['ad_name']}** · {item['status']} · {item['account']}"
                )
                cols[0].caption(
                    f"ad_id `{item['ad_id']}` · creative `{item['creative_id']}` · "
                    f"adset `{item['adset_id']}`"
                )
                cols[0].caption(f"landing: {item['landing_url']}")
                cols[1].caption(item["created_at"])
        return

    for o in rows:
        payload = o.get("payload") or {}
        launched = payload.get("launched_ad") or {}
        with st.container(border=True):
            cols = st.columns([3, 1])
            cols[0].markdown(
                f"**{launched.get('ad_name', o.get('title', '?'))}** · "
                f"{launched.get('status', '?')} · {launched.get('account', '?')}"
            )
            cols[0].caption(
                f"ad_id `{launched.get('ad_id', '—')}` · "
                f"creative `{launched.get('creative_id', '—')}` · "
                f"adset `{launched.get('adset_id', '—')}`"
            )
            if (url := launched.get("landing_url")):
                cols[0].caption(f"landing: {url}")
            cols[1].caption(o.get("created_at", "")[:16].replace("T", " "))


# ── Top-level rendering ────────────────────────────────────────────


def _main() -> None:
    account = _sidebar()
    sidebar_project_picker()

    st.title("🛒 Media Buyer Agent")
    st.caption(
        "Assembla copy + visual prodotti dal team agenti e lancia ads su Meta. "
        "Multi-account (Swat / Patatino / LRES)."
    )

    tab_browse, tab_create, tab_hist = st.tabs(
        ["📂 Sfoglia lavori", "🚀 Crea ad", "📜 Storico"]
    )
    with tab_browse:
        _render_browse_tab()
    with tab_create:
        if account is None:
            st.info(
                "Configura almeno un account Meta nei secrets per usare questa "
                "tab. Vedi README → 'Setup secrets multi-account'."
            )
        else:
            _render_create_tab(account)
    with tab_hist:
        _render_history_tab()


_main()
