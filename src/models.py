from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    CRI = "CRI"
    CRA = "CRA"
    DEBENTURE = "Debênture"


class IndexerType(str, Enum):
    CDI = "CDI"
    IPCA = "IPCA"
    IGPM = "IGP-M"
    PRE = "Pré-fixado"
    TR = "TR"


class Rating(str, Enum):
    AAA = "AAA"
    AA_PLUS = "AA+"
    AA = "AA"
    AA_MINUS = "AA-"
    A_PLUS = "A+"
    A = "A"
    A_MINUS = "A-"
    BBB_PLUS = "BBB+"
    BBB = "BBB"
    BBB_MINUS = "BBB-"
    BB_PLUS = "BB+"
    BB = "BB"
    BB_MINUS = "BB-"
    B = "B"
    NR = "NR"


class Opportunity(BaseModel):
    """Representa uma oportunidade de investimento em renda fixa."""

    ticker: Optional[str] = Field(None, description="Código do ativo (ex: CRIE11, XPCI11)")
    asset_type: AssetType
    issuer: str = Field(..., description="Nome do emissor / devedor")
    sector: str = Field(..., description="Setor econômico do emissor")

    indexer: IndexerType
    spread: float = Field(..., description="Spread sobre o indexador em % a.a.")
    duration_years: float = Field(..., description="Duration / prazo médio em anos")
    maturity_date: Optional[date] = None

    rating_agency: Optional[str] = None
    rating: Optional[Rating] = None
    is_incentivized: bool = Field(False, description="Isento de IR (Lei 12.431)")

    # Garantias
    collateral: Optional[str] = Field(None, description="Tipo de garantia (real, FGI, fidejussória, sem)")
    collateral_coverage: Optional[float] = Field(None, description="Cobertura da garantia em %")

    # Fundamentals opcionais (preenchidos manualmente ou via extração)
    leverage_net_debt_ebitda: Optional[float] = Field(None, description="Dívida Líq / EBITDA")
    ebitda_margin: Optional[float] = Field(None, description="Margem EBITDA em %")
    net_revenue_mm: Optional[float] = Field(None, description="Receita líquida em R$ milhões")

    notes: Optional[str] = Field(None, description="Observações livres")


class PeerAsset(BaseModel):
    """Ativo comparável (peer) buscado no mercado."""

    ticker: str
    issuer: str
    sector: str
    asset_type: AssetType
    indexer: IndexerType
    spread: float
    duration_years: float
    rating: Optional[Rating] = None
    is_incentivized: bool = False
    data_source: str = "ANBIMA"


class AnalysisResult(BaseModel):
    """Resultado consolidado da análise de crédito."""

    opportunity: Opportunity
    peers: list[PeerAsset]

    spread_vs_median: float
    spread_vs_p25: float
    spread_vs_p75: float

    credit_score: float = Field(..., ge=0, le=100)
    recommendation: str  # "Atrativo", "Neutro", "Não Atrativo"
    risk_factors: list[str]
    positive_factors: list[str]
