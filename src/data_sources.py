"""
Integração com a API de debêntures/CRI/CRA.

Endpoint:
  GET /get_debentures_table

Autenticação: Bearer token estático configurado em DATA_B3_TOKEN no .env

Estrutura da resposta:
  {
    "cri_cra_cdi":          [ {class_type, cnpj, duration, emissor, lei, nome,
                               setor, taxa_media, taxa_media_str, ticker,
                               type, vencimento, vol_total}, ... ],
    "cri_cra_ipca":         [ ... ],
    "cri_cra_pre_fixed":    [ ... ],
    "debentures_cdi":       [ ... ],
    "debentures_ipca":      [ ... ],
    "debentures_pre_fixed": [ ... ],
    "success": true
  }

Campos:
  - nome          → devedor / empresa
  - emissor       → securitizadora (para CRI/CRA)
  - setor         → setor econômico
  - taxa_media    → taxa negociada (spread % ou % CDI conforme group key)
  - duration      → em dias corridos → convertido para anos (/365)
  - lei           → isento IR (Lei 12.431)
  - ticker        → código do ativo
  - class_type    → "CRI", "CRA", "Debênture"
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from src.models import AssetType, IndexerType, PeerAsset, Rating

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_B3_TOKEN = os.getenv("DATA_B3_TOKEN", "")

DATA_B3_BASE_URL = os.getenv(
    "DATA_B3_BASE_URL",
    "https://data-b3-qtzd-debentures-apis-293570283792.us-east1.run.app",
)

# Anos de vencimento a incluir na busca (2026–2056)
_DUE_YEARS = ",".join(str(y) for y in range(2026, 2057))

# Mapeamento: chave da resposta → (asset classes permitidas, IndexerType)
_GROUP_MAP: dict[str, tuple[list[str], IndexerType]] = {
    "cri_cra_cdi":          (["CRI", "CRA"],  IndexerType.CDI),
    "cri_cra_ipca":         (["CRI", "CRA"],  IndexerType.IPCA),
    "cri_cra_pre_fixed":    (["CRI", "CRA"],  IndexerType.PRE),
    "debentures_cdi":       (["Debênture"],   IndexerType.CDI),
    "debentures_ipca":      (["Debênture"],   IndexerType.IPCA),
    "debentures_pre_fixed": (["Debênture"],   IndexerType.PRE),
}

_ASSET_TYPE_MAP = {
    "CRI": AssetType.CRI,
    "CRA": AssetType.CRA,
    "Debênture": AssetType.DEBENTURE,
}


# ---------------------------------------------------------------------------
# Fetch principal
# ---------------------------------------------------------------------------

def fetch_peers(
    sector: str,
    asset_type: AssetType,
    indexer: IndexerType,
    max_results: int = 50,
    period: int = 1,
    gross_up: bool = False,
) -> list[PeerAsset]:
    """
    Busca todos os ativos compatíveis (asset_type + indexer).
    Faz fallback para dados sintéticos se o token não estiver configurado.
    """
    if not DATA_B3_TOKEN:
        return _generate_synthetic_peers(sector, asset_type, indexer)

    try:
        raw = _fetch_all(period=period, gross_up=gross_up)
        return _parse_peers(raw, asset_type, indexer, sector, max_results)
    except Exception:
        return _generate_synthetic_peers(sector, asset_type, indexer)


def _fetch_all(period: int = 1, gross_up: bool = False) -> dict:
    """Chama o endpoint e retorna o JSON bruto."""
    resp = httpx.get(
        f"{DATA_B3_BASE_URL}/get_debentures_table",
        headers={"Authorization": f"Bearer {DATA_B3_TOKEN}"},
        params={
            "period": period,
            "class_type": "Debênture,CRI,CRA",
            "due_date": _DUE_YEARS,
            "gross_up": str(gross_up).lower(),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_peers(
    data: dict,
    asset_type: AssetType,
    indexer: IndexerType,
    sector: str,
    max_results: int,
) -> list[PeerAsset]:
    peers: list[PeerAsset] = []

    for group_key, (allowed_classes, group_indexer) in _GROUP_MAP.items():
        if group_indexer != indexer:
            continue

        records = data.get(group_key, [])
        for r in records:
            raw_class = r.get("class_type", "")
            if _ASSET_TYPE_MAP.get(raw_class) != asset_type:
                continue

            try:
                duration_days = float(r.get("duration") or 0)
                duration_years = round(duration_days / 365, 2)

                peers.append(PeerAsset(
                    ticker=str(r.get("ticker", "N/D")),
                    issuer=str(r.get("nome") or r.get("emissor", "")).strip(),
                    sector=str(r.get("setor") or "").strip(),
                    asset_type=asset_type,
                    indexer=indexer,
                    spread=float(r.get("taxa_media") or 0),
                    duration_years=duration_years,
                    rating=None,
                    is_incentivized=bool(r.get("lei", False)),
                    data_source="Data B3",
                ))
            except Exception:
                continue

            if len(peers) >= max_results:
                return peers

    return peers


# ---------------------------------------------------------------------------
# Fallback sintético
# ---------------------------------------------------------------------------

def _generate_synthetic_peers(
    sector: str, asset_type: AssetType, indexer: IndexerType
) -> list[PeerAsset]:
    """Peers sintéticos para demonstração sem token. NÃO usar em análises reais."""
    import random
    random.seed(hash(sector + asset_type.value + indexer.value) % 2**31)

    base = {"CDI": 2.5, "IPCA": 6.5, "Pré-fixado": 13.0}.get(indexer.value, 3.0)
    peers = []
    for i in range(10):
        peers.append(PeerAsset(
            ticker=f"DEMO{i+1:02d}",
            issuer=f"Empresa Exemplo {i+1}",
            sector=sector,
            asset_type=asset_type,
            indexer=indexer,
            spread=round(base + random.uniform(-1.5, 2.0), 2),
            duration_years=round(random.uniform(1.0, 7.0), 1),
            rating=None,
            is_incentivized=asset_type in (AssetType.CRI, AssetType.CRA),
            data_source="Sintético (demo)",
        ))
    return peers


# ---------------------------------------------------------------------------
# DataFrame completo do mercado
# ---------------------------------------------------------------------------

def load_market_df(period: int = 1, gross_up: bool = False) -> "pd.DataFrame":
    """
    Retorna todos os ativos da API como um DataFrame plano.
    Colunas: indexer, class_type, ticker, nome, emissor, setor,
             taxa_media, taxa_media_str, duration_years, vencimento, lei, vol_total
    """
    import pandas as pd

    if not DATA_B3_TOKEN:
        return pd.DataFrame()

    data = _fetch_all(period=period, gross_up=gross_up)
    rows = []

    _INDEXER_SUFFIX = {
        IndexerType.CDI:  "% do DI",
        IndexerType.IPCA: "% a.a.",
        IndexerType.PRE:  "% a.a.",
    }

    for group_key, (_, group_indexer) in _GROUP_MAP.items():
        for r in data.get(group_key, []):
            duration_days = float(r.get("duration") or 0)
            taxa_val = float(r.get("taxa_media") or 0)
            taxa_str = r.get("taxa_media_str") or ""
            if not taxa_str or taxa_str.strip().lower() == "none":
                suffix = _INDEXER_SUFFIX.get(group_indexer, "% a.a.")
                taxa_str = f"{taxa_val:.2f} {suffix}"
            rows.append({
                "indexer": group_indexer.value,
                "class_type": r.get("class_type", ""),
                "ticker": r.get("ticker", ""),
                "nome": str(r.get("nome") or r.get("emissor", "")).strip(),
                "emissor": str(r.get("emissor", "")).strip(),
                "setor": r.get("setor") or "Não informado",
                "taxa_media": taxa_val,
                "taxa_media_str": taxa_str,
                "duration_years": round(duration_days / 365, 2),
                "vencimento": str(r.get("vencimento", ""))[:10],
                "lei": bool(r.get("lei", False)),
                "vol_total": float(r.get("vol_total") or 0),
            })

    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not df.empty:
        from src.overrides import apply_sector_overrides
        df = apply_sector_overrides(df)
    return df


# ---------------------------------------------------------------------------
# Utilitário: testa conectividade
# ---------------------------------------------------------------------------

def test_connection() -> dict:
    result: dict = {
        "token_configured": bool(DATA_B3_TOKEN),
        "endpoint_ok": False,
        "record_counts": {},
        "sample_tickers": [],
        "error": None,
    }

    if not DATA_B3_TOKEN:
        result["error"] = "DATA_B3_TOKEN não configurado no .env"
        return result

    try:
        data = _fetch_all()
        result["endpoint_ok"] = bool(data.get("success", True))
        result["record_counts"] = {
            k: len(v) for k, v in data.items() if isinstance(v, list)
        }
        for records in data.values():
            if isinstance(records, list):
                result["sample_tickers"] += [r.get("ticker", "?") for r in records[:3]]
                if len(result["sample_tickers"]) >= 6:
                    break
    except Exception as e:
        result["error"] = str(e)

    return result
