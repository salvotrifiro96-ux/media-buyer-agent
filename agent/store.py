"""Cross-agent storage via Supabase.

Permette agli agenti del team Leone (copywriter, graphic-designer) di SALVARE
i loro output in una tabella condivisa, cosi` che il media-buyer possa
sfogliarli e usarli quando compone una nuova ad.

Pattern d'uso:
    store = SupabaseStore.from_env()
    if store:
        store.save_text_output(
            agent_type='copywriter',
            subtype='ads_meta',
            title='Meta Ads — 5 varianti per Liberi col Mattone',
            payload={...},
            preview='Hai gia` provato Meta Ads...',
            metadata={...},
        )

Se le env vars non sono settate, `from_env()` ritorna None e l'agente
continua a funzionare senza persistenza. Cosi` lo sviluppo locale senza
Supabase non rompe nulla.

Note di sicurezza:
- la chiave usata e` la `service_role` (chiamata `sb_secret_*` nella console
  Supabase nuova). Tutti gli agenti girano server-side (Streamlit Cloud),
  quindi NON espone nulla al browser dell'utente finale.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import requests


_BUCKET_NAME = "agent-visuals"


@dataclass(frozen=True)
class SavedOutput:
    """Riferimento a un output appena salvato."""

    id: str
    image_url: str | None = None


class SupabaseStore:
    """Wrapper minimale REST su Supabase. Niente sdk: troppi conflitti di
    versioni col `supabase` PyPI e mi basta REST puro."""

    def __init__(self, url: str, secret_key: str) -> None:
        if not url or not secret_key:
            raise ValueError("SUPABASE_URL e SUPABASE_SECRET_KEY obbligatori")
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self._rest = f"{self.url}/rest/v1"
        self._storage = f"{self.url}/storage/v1"
        self._headers_rest = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            # Prefer: ritorna la riga inserita
            "Prefer": "return=representation",
        }

    @classmethod
    def from_env(cls) -> "SupabaseStore | None":
        """Costruisce dal env. Ritorna None se mancano le variabili.

        Cerca SUPABASE_URL e SUPABASE_SECRET_KEY (preferred) o
        SUPABASE_SERVICE_KEY (legacy fallback).
        """
        try:
            import streamlit as st

            url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
            key = (
                os.getenv("SUPABASE_SECRET_KEY")
                or os.getenv("SUPABASE_SERVICE_KEY")
                or st.secrets.get("SUPABASE_SECRET_KEY", "")
                or st.secrets.get("SUPABASE_SERVICE_KEY", "")
            )
        except Exception:
            url = os.getenv("SUPABASE_URL", "")
            key = (
                os.getenv("SUPABASE_SECRET_KEY", "")
                or os.getenv("SUPABASE_SERVICE_KEY", "")
            )
        if not url or not key:
            return None
        return cls(url=url, secret_key=key)

    # ── Storage upload ────────────────────────────────────────────────
    def upload_image(self, *, image_bytes: bytes, ext: str = "png") -> str:
        """Carica un'immagine sul bucket pubblico e ritorna l'URL accessibile."""
        if not image_bytes:
            raise ValueError("image_bytes vuoto")
        filename = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
        path = f"{_BUCKET_NAME}/{filename}"
        url = f"{self._storage}/object/{path}"
        r = requests.post(
            url,
            data=image_bytes,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": f"image/{ext.lstrip('.')}",
                "x-upsert": "false",
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Upload Supabase Storage fallito {r.status_code}: {r.text}"
            )
        return f"{self.url}/storage/v1/object/public/{path}"

    # ── REST: insert / select ─────────────────────────────────────────
    def _insert_output(self, row: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(
            f"{self._rest}/agent_outputs",
            data=json.dumps(row),
            headers=self._headers_rest,
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Insert agent_outputs fallito {r.status_code}: {r.text}")
        data = r.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"Risposta inattesa da Supabase: {data!r}")
        return data[0]

    def save_text_output(
        self,
        *,
        agent_type: str,
        subtype: str,
        title: str,
        payload: dict[str, Any],
        preview: str = "",
        metadata: dict[str, Any] | None = None,
        source_session_id: str | None = None,
    ) -> SavedOutput:
        """Salva un output testuale (copy, mail, nurturing)."""
        row = {
            "agent_type": agent_type,
            "subtype": subtype,
            "title": title,
            "payload": payload,
            "preview": preview[:500] if preview else "",
            "metadata": metadata or {},
            "source_session_id": source_session_id,
        }
        data = self._insert_output(row)
        return SavedOutput(id=str(data["id"]))

    def save_image_output(
        self,
        *,
        agent_type: str,
        subtype: str,
        title: str,
        image_bytes: bytes,
        payload: dict[str, Any],
        preview: str = "",
        metadata: dict[str, Any] | None = None,
        source_session_id: str | None = None,
    ) -> SavedOutput:
        """Salva un output visivo: uploada il PNG su Storage poi inserisce
        la row con `image_url` populated."""
        image_url = self.upload_image(image_bytes=image_bytes, ext="png")
        row = {
            "agent_type": agent_type,
            "subtype": subtype,
            "title": title,
            "payload": payload,
            "image_url": image_url,
            "preview": preview[:500] if preview else "",
            "metadata": metadata or {},
            "source_session_id": source_session_id,
        }
        data = self._insert_output(row)
        return SavedOutput(id=str(data["id"]), image_url=image_url)

    # ── REST: list per il media-buyer ─────────────────────────────────
    def list_recent_outputs(
        self,
        *,
        agent_type: str | None = None,
        subtype: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if agent_type:
            params["agent_type"] = f"eq.{agent_type}"
        if subtype:
            params["subtype"] = f"eq.{subtype}"
        r = requests.get(
            f"{self._rest}/agent_outputs",
            params=params,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
            },
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"List agent_outputs fallito {r.status_code}: {r.text}")
        return r.json() or []

    def mark_used(self, output_id: str) -> None:
        """Aggiorna used_at quando il media-buyer consuma un output in una ad."""
        r = requests.patch(
            f"{self._rest}/agent_outputs",
            params={"id": f"eq.{output_id}"},
            data=json.dumps({"used_at": "now()"}),
            headers=self._headers_rest,
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"mark_used fallito {r.status_code}: {r.text}")
