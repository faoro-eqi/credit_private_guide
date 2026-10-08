"""
Motor de análise de crédito.

Calcula:
  - Posicionamento do spread vs peers (percentis)
  - Score de crédito composto (0-100)
  - Fatores de risco e positivos
  - Recomendação final
"""

from __future__ import annotations

import statistics
from typing import Optional

from src.models import AnalysisResult, Opportunity, PeerAsset, Rating


# ---------------------------------------------------------------------------
# Mapeamento de rating → score numérico
# ---------------------------------------------------------------------------

RATING_SCORE: dict[str, float] = {
    "AAA": 100, "AA+": 95, "AA": 90, "AA-": 85,
    "A+": 80,  "A": 75,   "A-": 70,
    "BBB+": 60, "BBB": 50, "BBB-": 40,
    "BB+": 30,  "BB": 20,  "BB-": 10,
    "B": 5,    "NR": 0,
}


# ---------------------------------------------------------------------------
# Análise principal
# ---------------------------------------------------------------------------

def run_analysis(opportunity: Opportunity, peers: list[PeerAsset]) -> AnalysisResult:
    spreads = [p.spread for p in peers if p.indexer == opportunity.indexer]

    if spreads:
        spreads_sorted = sorted(spreads)
        n = len(spreads_sorted)
        median = statistics.median(spreads_sorted)
        p25 = spreads_sorted[max(0, int(n * 0.25) - 1)]
        p75 = spreads_sorted[min(n - 1, int(n * 0.75))]
    else:
        median = opportunity.spread
        p25 = opportunity.spread
        p75 = opportunity.spread

    credit_score = _compute_score(opportunity, peers, median, p25, p75)
    risk_factors, positive_factors = _identify_factors(opportunity, peers, median)
    recommendation = _recommendation(credit_score)

    return AnalysisResult(
        opportunity=opportunity,
        peers=peers,
        spread_vs_median=round(opportunity.spread - median, 2),
        spread_vs_p25=round(opportunity.spread - p25, 2),
        spread_vs_p75=round(opportunity.spread - p75, 2),
        credit_score=round(credit_score, 1),
        recommendation=recommendation,
        risk_factors=risk_factors,
        positive_factors=positive_factors,
    )


# ---------------------------------------------------------------------------
# Score composto
# ---------------------------------------------------------------------------

def _compute_score(
    opp: Opportunity,
    peers: list[PeerAsset],
    median: float,
    p25: float,
    p75: float,
) -> float:
    """
    Score de 0 a 100 baseado em:
      - Spread relativo aos peers    (40 pts)
      - Rating                       (25 pts)
      - Alavancagem                  (15 pts)
      - Garantias                    (10 pts)
      - Incentivo fiscal             (10 pts)
    """
    score = 0.0

    # 1. Spread vs peers (40 pts)
    if peers:
        relative = opportunity_percentile(opp.spread, [p.spread for p in peers])
        score += relative * 40
    else:
        score += 20  # neutro

    # 2. Rating (25 pts)
    if opp.rating:
        raw = RATING_SCORE.get(opp.rating.value, 0)
        score += (raw / 100) * 25
    else:
        score += 8  # penaliza levemente ausência de rating

    # 3. Alavancagem (15 pts) — quanto menor, melhor
    if opp.leverage_net_debt_ebitda is not None:
        lev = opp.leverage_net_debt_ebitda
        if lev <= 1.0:
            score += 15
        elif lev <= 2.0:
            score += 12
        elif lev <= 3.0:
            score += 8
        elif lev <= 4.5:
            score += 4
        else:
            score += 0
    else:
        score += 7  # neutro

    # 4. Garantias (10 pts)
    collateral_score = {
        "alienação fiduciária": 10,
        "cessão fiduciária": 9,
        "hipoteca": 7,
        "fgi": 8,
        "aval": 5,
        "fiança": 5,
        "sem garantia": 0,
    }
    if opp.collateral:
        key = opp.collateral.lower()
        score += next(
            (v for k, v in collateral_score.items() if k in key),
            5,
        )
    else:
        score += 5  # neutro

    # 5. Incentivo fiscal (10 pts)
    if opp.is_incentivized:
        score += 10

    return min(max(score, 0), 100)


def opportunity_percentile(value: float, reference: list[float]) -> float:
    """Retorna fração (0–1) de peers com spread menor que o da oportunidade."""
    if not reference:
        return 0.5
    below = sum(1 for v in reference if v < value)
    return below / len(reference)


# ---------------------------------------------------------------------------
# Fatores de risco e positivos
# ---------------------------------------------------------------------------

def _identify_factors(
    opp: Opportunity,
    peers: list[PeerAsset],
    peer_median: float,
) -> tuple[list[str], list[str]]:
    risks: list[str] = []
    positives: list[str] = []

    # Spread
    delta = opp.spread - peer_median
    if delta >= 0.5:
        positives.append(f"Spread {delta:+.2f}% acima da mediana dos peers — prêmio adicional")
    elif delta <= -0.5:
        risks.append(f"Spread {delta:+.2f}% abaixo da mediana dos peers — pricing apertado")

    # Rating
    if opp.rating is None:
        risks.append("Sem rating formal — risco de crédito não avaliado por agência")
    elif opp.rating.value in ("BB+", "BB", "BB-", "B"):
        risks.append(f"Rating {opp.rating.value} — grau especulativo (high yield)")
    elif opp.rating.value in ("AAA", "AA+", "AA", "AA-"):
        positives.append(f"Rating {opp.rating.value} — alta qualidade de crédito")

    # Alavancagem
    if opp.leverage_net_debt_ebitda is not None:
        lev = opp.leverage_net_debt_ebitda
        if lev > 4.5:
            risks.append(f"Alavancagem elevada: Dív. Líq./EBITDA = {lev:.1f}x")
        elif lev < 2.0:
            positives.append(f"Alavancagem saudável: Dív. Líq./EBITDA = {lev:.1f}x")

    # Garantias
    if opp.collateral:
        col = opp.collateral.lower()
        if "sem garantia" in col:
            risks.append("Operação sem garantia real — risco sênior não colateralizado")
        elif "fiduciária" in col or "hipoteca" in col:
            positives.append(f"Garantia real: {opp.collateral}")

    # Incentivo fiscal
    if opp.is_incentivized:
        positives.append("Isento de IR para pessoa física (Lei 12.431)")

    # Duration / Liquidez
    if opp.duration_years > 7:
        risks.append(f"Duration longa ({opp.duration_years:.1f} anos) — maior risco de marcação a mercado")
    elif opp.duration_years <= 3:
        positives.append(f"Duration curta ({opp.duration_years:.1f} anos) — menor risco de duração")

    return risks, positives


# ---------------------------------------------------------------------------
# Recomendação
# ---------------------------------------------------------------------------

def _recommendation(score: float) -> str:
    if score >= 70:
        return "Atrativo"
    elif score >= 45:
        return "Neutro"
    else:
        return "Não Atrativo"
