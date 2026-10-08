"""
Visualizações Plotly para o dashboard de análise de crédito.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from src.models import AnalysisResult, PeerAsset


COLORS = {
    "primary": "#1f77b4",
    "opportunity": "#d62728",  # vermelho para destacar a oportunidade
    "positive": "#2ca02c",
    "negative": "#d62728",
    "neutral": "#ff7f0e",
    "bg": "#f8f9fa",
}

RECOMMENDATION_COLORS = {
    "Atrativo": "#2ca02c",
    "Neutro": "#ff7f0e",
    "Não Atrativo": "#d62728",
}


# ---------------------------------------------------------------------------
# Gauge de score
# ---------------------------------------------------------------------------

def score_gauge(score: float, recommendation: str) -> go.Figure:
    color = RECOMMENDATION_COLORS.get(recommendation, COLORS["neutral"])
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=score,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": f"Score de Crédito<br><span style='font-size:0.9em;color:{color}'>{recommendation}</span>"},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 45], "color": "#ffcccc"},
                {"range": [45, 70], "color": "#fff3cd"},
                {"range": [70, 100], "color": "#d4edda"},
            ],
            "threshold": {
                "line": {"color": "black", "width": 3},
                "thickness": 0.75,
                "value": score,
            },
        },
    ))
    fig.update_layout(height=280, margin=dict(t=60, b=20, l=20, r=20))
    return fig


# ---------------------------------------------------------------------------
# Spread vs peers (box + scatter)
# ---------------------------------------------------------------------------

def spread_comparison_chart(result: AnalysisResult) -> go.Figure:
    peer_spreads = [p.spread for p in result.peers if p.indexer == result.opportunity.indexer]
    opp_spread = result.opportunity.spread
    opp_label = result.opportunity.ticker or result.opportunity.issuer

    fig = go.Figure()

    # Box dos peers
    fig.add_trace(go.Box(
        y=peer_spreads,
        name="Peers",
        boxpoints="all",
        jitter=0.4,
        pointpos=0,
        marker=dict(color=COLORS["primary"], size=6, opacity=0.5),
        line=dict(color=COLORS["primary"]),
    ))

    # Oportunidade em destaque
    fig.add_trace(go.Scatter(
        x=["Peers"],
        y=[opp_spread],
        mode="markers+text",
        name=opp_label,
        text=[f"  {opp_label}: {opp_spread:.2f}%"],
        textposition="middle right",
        marker=dict(color=COLORS["opportunity"], size=14, symbol="diamond"),
    ))

    indexer = result.opportunity.indexer.value
    fig.update_layout(
        title=f"Spread vs Peers ({indexer} +  %, a.a.)",
        yaxis_title=f"Spread {indexer} + (% a.a.)",
        showlegend=True,
        height=400,
        plot_bgcolor=COLORS["bg"],
    )
    return fig


# ---------------------------------------------------------------------------
# Tabela de peers
# ---------------------------------------------------------------------------

def peers_table(peers: list[PeerAsset]) -> go.Figure:
    if not peers:
        fig = go.Figure()
        fig.add_annotation(text="Nenhum peer encontrado", showarrow=False)
        return fig

    df = pd.DataFrame([p.model_dump() for p in peers])
    display_cols = ["ticker", "issuer", "sector", "indexer", "spread",
                    "duration_years", "rating", "is_incentivized", "data_source"]
    df = df[[c for c in display_cols if c in df.columns]]

    col_labels = {
        "ticker": "Ticker", "issuer": "Emissor", "sector": "Setor",
        "indexer": "Indexador", "spread": "Spread (%)", "duration_years": "Duration",
        "rating": "Rating", "is_incentivized": "Isento IR", "data_source": "Fonte",
    }
    df.columns = [col_labels.get(c, c) for c in df.columns]
    if "Isento IR" in df.columns:
        df["Isento IR"] = df["Isento IR"].map({True: "Sim", False: "Não"})
    if "Spread (%)" in df.columns:
        df["Spread (%)"] = df["Spread (%)"].apply(lambda x: f"{x:.2f}%")

    fig = go.Figure(go.Table(
        header=dict(
            values=list(df.columns),
            fill_color=COLORS["primary"],
            font=dict(color="white", size=12),
            align="left",
        ),
        cells=dict(
            values=[df[c].tolist() for c in df.columns],
            fill_color=[["white", COLORS["bg"]] * (len(df) // 2 + 1)],
            align="left",
            font=dict(size=11),
        ),
    ))
    fig.update_layout(height=max(250, 40 + len(df) * 28), margin=dict(t=10, b=10))
    return fig


# ---------------------------------------------------------------------------
# Radar de fatores
# ---------------------------------------------------------------------------

def radar_chart(result: AnalysisResult) -> go.Figure:
    from src.analysis import RATING_SCORE, opportunity_percentile

    opp = result.opportunity
    peers = result.peers

    spread_pct = opportunity_percentile(opp.spread, [p.spread for p in peers]) * 100
    rating_score = RATING_SCORE.get(opp.rating.value if opp.rating else "NR", 0)

    lev = opp.leverage_net_debt_ebitda
    if lev is None:
        leverage_score = 50
    elif lev <= 1:
        leverage_score = 100
    elif lev <= 3:
        leverage_score = 75 - (lev - 1) * 12.5
    elif lev <= 5:
        leverage_score = 50 - (lev - 3) * 15
    else:
        leverage_score = max(0, 20 - (lev - 5) * 5)

    collateral_map = {
        "alienação fiduciária": 100, "cessão fiduciária": 90, "hipoteca": 70,
        "fgi": 80, "aval": 50, "fiança": 50, "sem garantia": 0,
    }
    col = (opp.collateral or "").lower()
    collateral_score = next((v for k, v in collateral_map.items() if k in col), 50)

    duration_score = max(0, 100 - opp.duration_years * 10)
    fiscal_score = 100 if opp.is_incentivized else 40

    categories = ["Spread Relativo", "Rating", "Alavancagem",
                  "Garantias", "Duration", "Fiscal"]
    values = [spread_pct, rating_score, leverage_score,
              collateral_score, duration_score, fiscal_score]
    values_closed = values + [values[0]]
    categories_closed = categories + [categories[0]]

    fig = go.Figure(go.Scatterpolar(
        r=values_closed,
        theta=categories_closed,
        fill="toself",
        name="Oportunidade",
        line_color=COLORS["opportunity"],
        fillcolor="rgba(214, 39, 40, 0.2)",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False,
        title="Perfil de Risco/Retorno",
        height=350,
    )
    return fig


# ---------------------------------------------------------------------------
# Spread histórico (placeholder para quando houver série temporal)
# ---------------------------------------------------------------------------

def spread_history_placeholder() -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text="📊 Série histórica de spreads<br>disponível após integração com ANBIMA API",
        showarrow=False,
        font=dict(size=14),
        xref="paper", yref="paper",
        x=0.5, y=0.5,
    )
    fig.update_layout(height=200, plot_bgcolor=COLORS["bg"])
    return fig
