# Media Buyer Agent

Agente Streamlit del team marketing Leone. Crea **nuove ads su Meta** componendo:

- 🖼 **Visual** — uploadati manualmente o pescati dagli output recenti del **graphic-designer-agent**
- ✍️ **Copy** — scritto a mano o pescato dagli output recenti del **copywriter-agent**
- 🛒 **Destinazione** — campagna esistente + adset (esistente o nuovo, clonato dal targeting di uno attivo)

Multi-account: Swat / Patatino / LRES (selettore in sidebar).

## Architettura cross-agent

```
                         ┌──────────────────────────────────────┐
                         │  Supabase (fmzunwsrpgdexlwmkruy)     │
                         │  ┌────────────────────────────────┐  │
copywriter-agent ───────►│  │ agent_outputs (jsonb payload)  │  │
                         │  └────────────────────────────────┘  │
graphic-designer-agent ─►│  ┌────────────────────────────────┐  │
                         │  │ agent-visuals (Storage bucket) │  │
                         │  └────────────────────────────────┘  │
                         └──────────────────────────────────────┘
                                       ▲
                                       │ list / mark_used
                                       │
                                  media-buyer-agent
                                       │
                                       ▼
                                  Meta Graph API v21
```

I 3 agenti sono tutti su Streamlit Cloud, indipendenti. Lo storage Supabase è il punto di sincronizzazione.

## Setup locale

```bash
cd media-buyer-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# poi compila i token Meta in .env (almeno 1 account)
streamlit run app.py
```

Apre su http://localhost:8501.

## Setup secrets multi-account (Streamlit Cloud)

Vai su Settings → Secrets della tua app deployata e incolla questo TOML, valorizzando i token Meta:

```toml
APP_PASSWORD = "faraone.92"
SUPABASE_URL = "https://fmzunwsrpgdexlwmkruy.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."

# Per ogni Meta business account che vuoi gestire:
META_SWAT_ACCESS_TOKEN = "EAA..."
META_SWAT_AD_ACCOUNT_ID = "act_191279579779492"
META_SWAT_PAGE_ID = "..."
META_SWAT_INSTAGRAM_USER_ID = "..."
META_SWAT_PIXEL_ID = "..."

META_PATATINO_ACCESS_TOKEN = "..."
META_PATATINO_AD_ACCOUNT_ID = "act_900331255794779"
META_PATATINO_PAGE_ID = "..."
META_PATATINO_INSTAGRAM_USER_ID = "..."
META_PATATINO_PIXEL_ID = "..."

META_LRES_ACCESS_TOKEN = "..."
META_LRES_AD_ACCOUNT_ID = "act_2176405965804688"
META_LRES_PAGE_ID = "..."
META_LRES_INSTAGRAM_USER_ID = "..."
META_LRES_PIXEL_ID = "..."
```

L'app filtra automaticamente gli account NON configurati. Aggiungere un nuovo account = aggiungere un nuovo slug in `agent/accounts.py:DEFAULT_ACCOUNT_SLUGS` e le 5 var nei secrets.

Per ottenere i token Meta serve un **System User token** con permessi `ads_management` + `pages_read_engagement` + `pages_manage_ads`. Generabile da Business Settings → System Users → Generate New Token.

## Test

```bash
pytest
```

23 unit test su validazione plan + parsing accounts + CTA whitelist. Niente API calls.

## Struttura

```
app.py                 → Streamlit UI (3 tab: Sfoglia / Crea ad / Storico)
agent/
  accounts.py          → MetaAccount + loader multi-account
  meta_api.py          → MetaClient (list campagne, list adsets, create_ad, upload_image)
  launch.py            → CreativeSpec + LaunchPlan + launch_ads()
  store.py             → SupabaseStore (riusato da copywriter e graphic-designer)
tests/                 → pytest, niente API
```

## Flow tipico

1. (Una volta) `copywriter-agent` genera 5 Meta Ads varianti — autosave su Supabase
2. (Una volta) `graphic-designer-agent` genera 3 visual square — autosave su Storage + Supabase
3. Apri `media-buyer-agent`
4. Tab "Sfoglia lavori": seleziona 1 visual + 1 copy (chip "Selezionato" appare)
5. Tab "Crea ad": scegli account → campagna → adset (esistente o nuovo) → landing URL + nome
6. Lancia con status `PAUSED` (default). Verifica in Ads Manager. Attiva.

## Pattern condivisi col team

- Stessa password gate (`APP_PASSWORD`)
- Stesso schema Supabase via `agent/store.py` (duplicato in copywriter, graphic-designer, media-buyer — niente codice condiviso fra repo, intenzionalmente)
- Stessa cifra di scaffolding: `app.py` + `agent/` + `tests/` + `requirements.txt` + `.env.example`
- Niente Claude/OpenAI in questo agente (è puramente operativo)
