"""
Credit Memo — Análise estruturada de emissões de CRI, CRA e Debêntures.
Baseado no framework IC Memo (IC: Investment Committee).

Estrutura:
  I.   Sumário Executivo
  II.  Visão do Emissor
  III. Contexto de Mercado / Setor
  IV.  Estrutura da Emissão (multi-série)
  V.   Posicionamento de Spread (vs NTN-B / peers)
  VI.  Fatores de Risco e Mitigantes
  VII. Recomendação por Série
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.benchmarks import add_spread_column, interpolate_rate
from src.snapshots import DB_PATH
import sqlite3

# ---------------------------------------------------------------------------
# Helpers de dados
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_peers(class_type: str, indexer: str, sector: str | None = None) -> pd.DataFrame:
    """Carrega peers do banco de dados."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    base_q = """
        SELECT ticker, nome, setor, class_type, indexer, taxa_media, duration_years, vencimento
        FROM snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots)
        AND class_type = ? AND indexer = ?
        AND duration_years > 0
        ORDER BY duration_years
    """
    df = pd.read_sql(base_q, conn, params=(class_type, indexer))
    conn.close()
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def _load_ntnb() -> pd.DataFrame:
    """Carrega curva NTN-B do banco."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            """SELECT code, days_to_due, indicative_rate
               FROM benchmark_snapshots
               WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM benchmark_snapshots)
               AND benchmark_type = 'ntnb'
               ORDER BY days_to_due""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def _interp_ntnb(ntnb_df: pd.DataFrame, duration_years: float) -> float | None:
    if ntnb_df.empty:
        return None
    return interpolate_rate(ntnb_df.rename(columns={"indicative_rate": "indicative_rate"}), duration_years * 365)


def _spread_over_ntnb(taxa: float, duration_years: float, ntnb_df: pd.DataFrame) -> float | None:
    bm = _interp_ntnb(ntnb_df, duration_years)
    return round(taxa - bm, 4) if bm is not None else None


def _color_spread(spread: float | None) -> str:
    if spread is None:
        return "—"
    if spread < 0.20:
        return f"🔴 {spread:+.2f}%"
    if spread < 0.50:
        return f"🟡 {spread:+.2f}%"
    return f"🟢 {spread:+.2f}%"


INDEXER_LABELS = {
    "CDI": "% do DI",
    "IPCA": "IPCA + % a.a.",
    "Pré-fixado": "% a.a.",
}

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("📋 Credit Memo")
st.caption("Análise estruturada de emissões de dívida — CRI, CRA e Debêntures.")

# ─── Cabeçalho da Emissão ────────────────────────────────────────────────────

with st.expander("📝 I. Dados da Emissão", expanded=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        issuer = st.text_input("Emissor / Devedor", value="BRF S.A.")
        asset_type = st.selectbox("Tipo de Ativo", ["CRA", "CRI", "Debênture"], index=0)
        sector = st.text_input("Setor", value="Proteínas")
    with c2:
        rating = st.text_input("Rating (preliminar)", value="AAA(EXP)sf(bra)")
        rating_agency = st.text_input("Agência", value="Fitch Ratings")
        guarantee = st.text_input("Garantia", value="Clean")
    with c3:
        volume_total = st.text_input("Volume máximo", value="R$ 1 bilhão")
        negotiation = st.text_input("Negociação", value="B3 S.A.")
        analyst = st.text_input("Analista responsável", value="")

    st.divider()
    st.markdown("**Descrição do Emissor**")
    c4, c5 = st.columns(2)
    with c4:
        business_desc = st.text_area(
            "Visão do negócio",
            value=(
                "BRF S.A. é uma das maiores companhias de alimentos do mundo, com operações "
                "em mais de 40 países. Atua nos segmentos de aves, suínos e processados, "
                "com marcas como Sadia, Perdigão e Qualy. Receita líquida anual de ~R$ 60 bi."
            ),
            height=100,
        )
    with c5:
        market_context = st.text_area(
            "Contexto de mercado / setor",
            value=(
                "Setor de proteínas animal brasileiro: exportações recordes em 2024-25, "
                "demanda asiática aquecida (China), câmbio favorável às exportadoras. "
                "Principais riscos: custo de grão (milho/soja), câmbio e ambiente sanitário."
            ),
            height=100,
        )

# ─── Estrutura da Emissão ────────────────────────────────────────────────────

with st.expander("📊 II. Estrutura da Emissão — Séries", expanded=True):
    st.markdown("Defina cada série da emissão. Use os dados do termo de oferta.")
    st.markdown("*Para emissões multi-série, preencha todas as séries relevantes.*")

    n_series = st.number_input("Número de séries", min_value=1, max_value=8, value=5, step=1)

    SERIES_DEFAULTS = [
        {"label": "1ª Série", "prazo": 4.5, "duration": 4.5, "indexer": "CDI", "taxa": 101.5, "amort": "Bullet", "pagamento_juros": "Bullet", "call": "1 ano (0,45% aa)"},
        {"label": "2ª Série", "prazo": 7.0, "duration": 4.77, "indexer": "Pré-fixado", "taxa": 13.40, "amort": "Bullet", "pagamento_juros": "Semestral", "call": "2 anos"},
        {"label": "3ª Série", "prazo": 10.0, "duration": 7.17, "indexer": "IPCA", "taxa": 7.70, "amort": "Bullet", "pagamento_juros": "Semestral", "call": "2 anos"},
        {"label": "4ª Série", "prazo": 20.0, "duration": 10.22, "indexer": "IPCA", "taxa": 7.90, "amort": "Anual (18-20°)", "pagamento_juros": "Semestral", "call": "2 anos"},
        {"label": "5ª Série", "prazo": 30.0, "duration": 12.0, "indexer": "IPCA", "taxa": 8.25, "amort": "Anual (28-30°)", "pagamento_juros": "Semestral", "call": "2 anos"},
    ]

    series_data = []
    for i in range(int(n_series)):
        d = SERIES_DEFAULTS[i] if i < len(SERIES_DEFAULTS) else {"label": f"{i+1}ª Série", "prazo": 5.0, "duration": 4.0, "indexer": "IPCA", "taxa": 7.50, "amort": "Bullet", "pagamento_juros": "Semestral", "call": "—"}
        with st.container():
            st.markdown(f"**{d['label']}**")
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            with sc1:
                prazo = st.number_input("Prazo (anos)", value=float(d["prazo"]), key=f"prazo_{i}", min_value=0.5, step=0.5)
                dur = st.number_input("Duration (anos)", value=float(d["duration"]), key=f"dur_{i}", min_value=0.5, step=0.01)
            with sc2:
                indexer = st.selectbox("Indexador", ["CDI", "IPCA", "Pré-fixado"], key=f"idx_{i}",
                                       index=["CDI", "IPCA", "Pré-fixado"].index(d["indexer"]))
                taxa = st.number_input(
                    "Taxa teto (CDI% ou spread%)",
                    value=float(d["taxa"]), key=f"taxa_{i}",
                    min_value=0.0, max_value=200.0, step=0.05,
                    help="Para CDI: % do DI (ex: 101.5). Para IPCA/Pré: spread % a.a.",
                )
            with sc3:
                amort = st.text_input("Amortização", value=d["amort"], key=f"amort_{i}")
                juros = st.text_input("Pagamento de Juros", value=d["pagamento_juros"], key=f"juros_{i}")
            with sc4:
                call = st.text_input("Resgate Antecipado", value=d["call"], key=f"call_{i}")
            with sc5:
                obs = st.text_input("Obs.", value="", key=f"obs_{i}")

            series_data.append({
                "serie": d["label"],
                "prazo_anos": prazo,
                "duration_anos": dur,
                "indexer": indexer,
                "taxa_teto": taxa,
                "amortizacao": amort,
                "pagamento_juros": juros,
                "call": call,
                "obs": obs,
            })
            if i < int(n_series) - 1:
                st.divider()

# ─── Análise de Spread ───────────────────────────────────────────────────────

st.divider()
if st.button("🔍 Gerar Credit Memo", type="primary", use_container_width=True):
    st.session_state["memo_generated"] = True
    st.session_state["memo_series"] = series_data
    st.session_state["memo_issuer"] = issuer
    st.session_state["memo_asset_type"] = asset_type
    st.session_state["memo_sector"] = sector
    st.session_state["memo_rating"] = rating
    st.session_state["memo_rating_agency"] = rating_agency
    st.session_state["memo_guarantee"] = guarantee
    st.session_state["memo_volume"] = volume_total
    st.session_state["memo_business"] = business_desc
    st.session_state["memo_market"] = market_context
    st.session_state["memo_analyst"] = analyst

if not st.session_state.get("memo_generated"):
    st.info("Preencha os dados acima e clique em **Gerar Credit Memo**.")
    st.stop()

# ── Recupera do session state ──────────────────────────────────────────────
series_data    = st.session_state["memo_series"]
issuer         = st.session_state["memo_issuer"]
asset_type     = st.session_state["memo_asset_type"]
sector         = st.session_state["memo_sector"]
rating         = st.session_state["memo_rating"]
rating_agency  = st.session_state["memo_rating_agency"]
guarantee      = st.session_state["memo_guarantee"]
volume_total   = st.session_state["memo_volume"]
business_desc  = st.session_state["memo_business"]
market_context = st.session_state["memo_market"]
analyst        = st.session_state["memo_analyst"]

ntnb_df = _load_ntnb()

# ── Calcula spread sobre NTN-B para séries IPCA ────────────────────────────
for s in series_data:
    if s["indexer"] == "IPCA" and not ntnb_df.empty:
        bm = _interp_ntnb(ntnb_df, s["duration_anos"])
        s["bm_ntnb"] = round(bm, 4) if bm else None
        s["spread_bm"] = round(s["taxa_teto"] - bm, 4) if bm else None
    elif s["indexer"] == "Pré-fixado":
        s["bm_ntnb"] = None
        s["spread_bm"] = None
    else:
        s["bm_ntnb"] = None
        s["spread_bm"] = None

# ── Carrega peers por indexador ────────────────────────────────────────────
ipca_peers_df  = _load_peers(asset_type, "IPCA", sector)
cdi_peers_df   = _load_peers(asset_type, "CDI", sector)
pre_peers_df   = _load_peers(asset_type, "Pré-fixado", sector)

# Adiciona spread sobre NTN-B para peers IPCA
if not ipca_peers_df.empty and not ntnb_df.empty:
    ipca_peers_df = add_spread_column(ipca_peers_df, ntnb_df.rename(columns={"indicative_rate": "indicative_rate"}), spread_col="spread_ntnb", benchmark_rate_col="bm_rate")

st.divider()

# ===========================================================================
# I. SUMÁRIO EXECUTIVO
# ===========================================================================

st.header(f"📋 Credit Memo — {asset_type} {issuer}")
st.caption(f"Gerado em {date.today().strftime('%d/%m/%Y')} {f'| Analista: {analyst}' if analyst else ''}")

col_h1, col_h2, col_h3, col_h4 = st.columns(4)
col_h1.metric("Emissor", issuer)
col_h2.metric("Rating", rating)
col_h3.metric("Garantia", guarantee)
col_h4.metric("Volume Máximo", volume_total)

st.divider()

# ===========================================================================
# II. VISÃO DO EMISSOR E MERCADO
# ===========================================================================

st.subheader("🏢 II. Emissor & Contexto de Mercado")
c_biz, c_mkt = st.columns(2)
with c_biz:
    st.markdown("**Visão do Negócio**")
    st.info(business_desc)
with c_mkt:
    st.markdown("**Contexto Setorial**")
    st.info(market_context)

st.divider()

# ===========================================================================
# III. ESTRUTURA DA EMISSÃO
# ===========================================================================

st.subheader("📊 III. Estrutura da Emissão")

df_series = pd.DataFrame(series_data)

# Formata tabela de exibição
def _fmt_taxa(row):
    if row["indexer"] == "CDI":
        return f"{row['taxa_teto']:.2f}% do DI"
    elif row["indexer"] == "IPCA":
        return f"IPCA + {row['taxa_teto']:.2f}% a.a."
    else:
        return f"{row['taxa_teto']:.2f}% a.a."

def _fmt_spread(row):
    s = row.get("spread_bm")
    bm = row.get("bm_ntnb")
    if s is None or bm is None:
        return "—"
    color = "🟢" if s >= 0.50 else ("🟡" if s >= 0.20 else "🔴")
    return f"{color} {s:+.2f}% ({bm:.2f}% NTN-B)"

display_cols = []
for s in series_data:
    row = {
        "Série": s["serie"],
        "Prazo": f"{s['prazo_anos']:.1f}a",
        "Duration": f"{s['duration_anos']:.2f}a",
        "Indexador": s["indexer"],
        "Taxa Teto": _fmt_taxa(s),
        "Spread/NTN-B": _fmt_spread(s),
        "Amortização": s["amortizacao"],
        "Juros": s["pagamento_juros"],
        "Call": s["call"],
    }
    display_cols.append(row)

st.dataframe(pd.DataFrame(display_cols), hide_index=True, use_container_width=True)

st.divider()

# ===========================================================================
# IV. POSICIONAMENTO DE SPREAD — GRÁFICO VISUAL
# ===========================================================================

st.subheader("📐 IV. Posicionamento de Spread sobre NTN-B")

ipca_series = [s for s in series_data if s["indexer"] == "IPCA" and s.get("spread_bm") is not None]

if ipca_series and not ntnb_df.empty:
    # Curva NTN-B
    ntnb_plot = ntnb_df.copy()
    ntnb_plot["duration_years"] = ntnb_plot["days_to_due"] / 365

    fig = go.Figure()

    # Curva NTN-B
    fig.add_trace(go.Scatter(
        x=ntnb_plot["duration_years"],
        y=ntnb_plot["indicative_rate"],
        mode="lines+markers",
        name="NTN-B (curva soberana)",
        line=dict(color="#2c7bb6", width=2, dash="dot"),
        marker=dict(size=6),
    ))

    # Peers IPCA do mesmo setor
    if not ipca_peers_df.empty:
        # Filtra peers do setor
        peers_setor = ipca_peers_df[ipca_peers_df["setor"].str.contains(sector.split()[0], case=False, na=False)]
        peers_outros = ipca_peers_df[~ipca_peers_df["setor"].str.contains(sector.split()[0], case=False, na=False)]

        if not peers_outros.empty:
            fig.add_trace(go.Scatter(
                x=peers_outros["duration_years"],
                y=peers_outros["taxa_media"],
                mode="markers",
                name=f"Peers {asset_type} IPCA (outros setores)",
                marker=dict(size=6, color="#aaa", opacity=0.5),
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Taxa: %{y:.2f}%<br>Duration: %{x:.2f}a<extra></extra>",
                customdata=list(zip(peers_outros["ticker"], peers_outros["nome"].str[:35])),
            ))

        if not peers_setor.empty:
            fig.add_trace(go.Scatter(
                x=peers_setor["duration_years"],
                y=peers_setor["taxa_media"],
                mode="markers",
                name=f"Peers {asset_type} IPCA — {sector}",
                marker=dict(size=9, color="#ff7f0e", symbol="circle-open", line=dict(width=2)),
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Taxa: %{y:.2f}%<br>Duration: %{x:.2f}a<extra></extra>",
                customdata=list(zip(peers_setor["ticker"], peers_setor["nome"].str[:35])),
            ))

    # Novas séries (oferta)
    xs_new = [s["duration_anos"] for s in ipca_series]
    ys_new = [s["taxa_teto"] for s in ipca_series]
    labels_new = [f"{s['serie']}<br>IPCA+{s['taxa_teto']:.2f}%<br>Spread: {s['spread_bm']:+.2f}%" for s in ipca_series]

    fig.add_trace(go.Scatter(
        x=xs_new,
        y=ys_new,
        mode="markers+text",
        name=f"Nova oferta — {issuer}",
        marker=dict(size=14, color="#d62728", symbol="star"),
        text=[s["serie"] for s in ipca_series],
        textposition="top center",
        hovertemplate="<b>%{text}</b><br>Taxa: %{y:.2f}%<br>Duration: %{x:.2f}a<br>%{customdata}<extra></extra>",
        customdata=labels_new,
    ))

    fig.update_layout(
        title=f"Curva de Spread — {asset_type} IPCA vs NTN-B | Setor: {sector}",
        xaxis_title="Duration (anos)",
        yaxis_title="Taxa Real (IPCA + % a.a.)",
        height=480,
        plot_bgcolor="#f8f9fa",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="left", x=0),
        hovermode="closest",
    )
    fig.add_annotation(
        text="★ = Nova oferta  |  Pontos laranja = peers do mesmo setor  |  Linha pontilhada = NTN-B (risco zero)",
        xref="paper", yref="paper", x=0.5, y=-0.35,
        showarrow=False, font=dict(size=10, color="#666"),
    )
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Adicione séries IPCA para visualizar o posicionamento de spread sobre a curva NTN-B.")

# ===========================================================================
# V. TABELA DE PEERS POR SÉRIE — COMPARAÇÃO DETALHADA
# ===========================================================================

st.divider()
st.subheader("🔎 V. Comparação com Peers por Faixa de Duration")

for s in series_data:
    if s["indexer"] != "IPCA":
        continue

    dur = s["duration_anos"]
    band_low  = max(0, dur - 1.5)
    band_high = dur + 1.5

    peers_band = ipca_peers_df[
        ipca_peers_df["duration_years"].between(band_low, band_high)
    ].sort_values("taxa_media").copy() if not ipca_peers_df.empty else pd.DataFrame()

    with st.expander(f"**{s['serie']}** — Duration {dur:.2f}a | IPCA + {s['taxa_teto']:.2f}% | Spread: {_color_spread(s.get('spread_bm'))}", expanded=True):
        if peers_band.empty:
            st.info("Nenhum peer encontrado nessa faixa de duration.")
        else:
            peers_band["Spread/NTN-B"] = peers_band["spread_ntnb"].apply(
                lambda x: f"{x:+.2f}%" if pd.notna(x) else "—"
            )
            peers_band["vencimento_fmt"] = pd.to_datetime(peers_band["vencimento"], errors="coerce").dt.strftime("%m/%Y")

            # Destaca emissor da oferta
            peers_band["_flag"] = peers_band["nome"].str.contains(issuer.split()[0], case=False, na=False)

            # Linha da oferta
            bm_val = s.get("bm_ntnb")
            nova_linha = pd.DataFrame([{
                "ticker": "★ OFERTA",
                "nome": f"{issuer} (nova oferta)",
                "setor": sector,
                "taxa_media": s["taxa_teto"],
                "duration_years": dur,
                "vencimento_fmt": "—",
                "Spread/NTN-B": f"{s['spread_bm']:+.2f}%" if s.get("spread_bm") is not None else "—",
                "_flag": True,
            }])

            show = pd.concat([nova_linha, peers_band], ignore_index=True)

            # Cálculos de posicionamento
            peer_spreads = peers_band["spread_ntnb"].dropna()
            if not peer_spreads.empty and s.get("spread_bm") is not None:
                mediana_peers = peer_spreads.median()
                p25 = peer_spreads.quantile(0.25)
                p75 = peer_spreads.quantile(0.75)
                delta_mediana = s["spread_bm"] - mediana_peers
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                col_m1.metric("Spread oferta (NTN-B)", f"{s['spread_bm']:+.2f}%")
                col_m2.metric("Mediana peers", f"{mediana_peers:.2f}%", f"{delta_mediana:+.2f}% vs mediana")
                col_m3.metric("P25 peers", f"{p25:.2f}%")
                col_m4.metric("P75 peers", f"{p75:.2f}%")

            st.dataframe(
                show[["ticker", "nome", "setor", "duration_years", "taxa_media", "Spread/NTN-B", "vencimento_fmt"]].rename(columns={
                    "ticker": "Ticker",
                    "nome": "Emissor",
                    "setor": "Setor",
                    "duration_years": "Duration (a)",
                    "taxa_media": "Taxa (IPCA+%)",
                    "vencimento_fmt": "Vencimento",
                }),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Duration (a)": st.column_config.NumberColumn(format="%.2f"),
                    "Taxa (IPCA+%)": st.column_config.NumberColumn(format="%.2f"),
                }
            )

# ===========================================================================
# VI. ANÁLISE DE CRÉDITO — PONTOS POSITIVOS, RISCOS E RECOMENDAÇÃO
# ===========================================================================

st.divider()
st.subheader("⚖️ VI. Análise de Crédito")

# ── Geração automática de argumentos baseada nos dados calculados ──────────

def _gerar_analise_automatica(series_data, ipca_peers_df, issuer):
    """
    Gera argumentos de comitê baseados nos dados reais:
    - Compara spread da oferta vs mediana de peers
    - Identifica se o próprio emissor tem emissões mais baratas em duration menor
    - Aponta séries caras/baratas com argumentos concretos
    """
    pontos_positivos = []
    pontos_negativos = []
    alertas_por_serie = {}  # serie_label -> lista de strings

    for s in series_data:
        if s["indexer"] != "IPCA" or ipca_peers_df.empty:
            continue

        dur = s["duration_anos"]
        spread_oferta = s.get("spread_bm")
        taxa_oferta = s["taxa_teto"]
        label = s["serie"]
        alertas_por_serie[label] = []

        if spread_oferta is None:
            continue

        # Peers na banda de duration ±1.5a
        band_peers = ipca_peers_df[ipca_peers_df["duration_years"].between(max(0, dur - 1.5), dur + 1.5)].copy()

        # Peers do MESMO emissor (todas as durations)
        own_peers = ipca_peers_df[
            ipca_peers_df["nome"].str.contains(issuer.split()[0], case=False, na=False)
        ].copy()

        # Peers do mesmo emissor com MENOR duration
        own_shorter = own_peers[own_peers["duration_years"] < dur - 0.3].sort_values("duration_years")

        # Alerta: emissor paga mais em duration MENOR (precificação invertida)
        for _, row in own_shorter.iterrows():
            peer_spread = row.get("spread_ntnb")
            if peer_spread is not None and pd.notna(peer_spread) and peer_spread > spread_oferta + 0.05:
                alertas_por_serie[label].append(
                    f"⚠️ **Inversão de spread:** {row['ticker']} ({row['nome'][:30]}) paga "
                    f"**IPCA+{row['taxa_media']:.2f}%** (spread {peer_spread:+.2f}% NTN-B) com duration "
                    f"**{row['duration_years']:.2f}a — menor** que esta série ({dur:.2f}a). "
                    f"Não faz sentido tomar mais duration pelo mesmo risco de crédito pagando menos."
                )
                pontos_negativos.append(
                    f"- **{label}:** emissão existente do próprio emissor ({row['ticker']}, {row['duration_years']:.2f}a) "
                    f"paga {peer_spread:+.2f}% NTN-B — **{peer_spread - spread_oferta:+.2f}% acima** desta série com menor duration."
                )

        if not band_peers.empty and "spread_ntnb" in band_peers.columns:
            peer_spreads_val = band_peers["spread_ntnb"].dropna()
            if not peer_spreads_val.empty:
                mediana = peer_spreads_val.median()
                p25 = peer_spreads_val.quantile(0.25)
                p75 = peer_spreads_val.quantile(0.75)
                delta = spread_oferta - mediana

                if delta < -0.10:
                    alertas_por_serie[label].append(
                        f"❌ **Spread abaixo do mercado:** oferta paga {spread_oferta:+.2f}% NTN-B vs mediana de peers "
                        f"**{mediana:.2f}%** — desconto de **{abs(delta):.2f}%** sem compensação visível de crédito."
                    )
                    pontos_negativos.append(
                        f"- **{label}:** spread {delta:+.2f}% abaixo da mediana de peers (n={len(peer_spreads_val)}). "
                        f"Oferta está na cauda inferior (abaixo do P25={p25:.2f}%)."
                    )
                elif delta < 0.05:
                    alertas_por_serie[label].append(
                        f"🟡 **Spread na mediana:** {spread_oferta:+.2f}% NTN-B ≈ mediana de peers ({mediana:.2f}%). "
                        f"Oferta justa, sem prêmio para novo papel."
                    )
                else:
                    alertas_por_serie[label].append(
                        f"✅ **Spread acima do mercado:** {spread_oferta:+.2f}% NTN-B vs mediana {mediana:.2f}% "
                        f"(+{delta:.2f}%). Há prêmio de emissão relevante."
                    )
                    pontos_positivos.append(
                        f"- **{label}:** spread {delta:+.2f}% acima da mediana de peers ({len(peer_spreads_val)} papéis) — "
                        f"prêmio concreto vs mercado secundário."
                    )

    return pontos_positivos, pontos_negativos, alertas_por_serie


pontos_pos_auto, pontos_neg_auto, alertas_series = _gerar_analise_automatica(
    series_data, ipca_peers_df, issuer
)

# ── Exibe alertas por série de forma destacada ─────────────────────────────
for s in series_data:
    if s["indexer"] != "IPCA":
        continue
    alertas = alertas_series.get(s["serie"], [])
    if alertas:
        with st.container():
            st.markdown(f"**{s['serie']} — {s['indexer']}+ {s['taxa_teto']:.2f}% | Duration {s['duration_anos']:.2f}a**")
            for a in alertas:
                st.markdown(a)

st.divider()

# ── Caixas editáveis: positivos e riscos ──────────────────────────────────
with st.expander("Editar pontos positivos e fatores de risco", expanded=True):
    c_pos, c_risk = st.columns(2)
    with c_pos:
        st.markdown("### ✅ Pontos Positivos")
        positivos_auto_text = "\n".join(pontos_pos_auto) if pontos_pos_auto else ""
        positivos_base = (
            "- Rating AAA(EXP)sf(bra) pela Fitch — maior grau de investimento em renda fixa estruturada\n"
            "- BRF: recuperação financeira sólida com margens em expansão nos últimos 3 anos\n"
            "- Diversificação geográfica (40+ países) mitiga concentração de risco\n"
            "- CRA isento de IR para PF (Lei 12.431) — melhora retorno líquido\n"
            "- Exportações de proteínas em nível recorde (2024-25), câmbio favorável"
        )
        positivos_default = (positivos_auto_text + "\n" + positivos_base).strip()
        positivos = st.text_area("", value=positivos_default, height=200, key="pos_factors")

    with c_risk:
        st.markdown("### ⚠️ Fatores de Risco")
        negativos_auto_text = "\n".join(pontos_neg_auto) if pontos_neg_auto else ""
        riscos_base = (
            "- Garantia clean: sem colateral real — exposição corporativa integral à BRF\n"
            "- Custo de grão (milho/soja): ~60% do CPV, altamente volátil e sujeito a clima\n"
            "- Risco sanitário: embargos por surtos (gripe aviária) podem paralisar exportações\n"
            "- Séries longas (20-30a): alta sensibilidade à duration em cenário de deterioração fiscal"
        )
        riscos_default = (negativos_auto_text + "\n" + riscos_base).strip()
        riscos = st.text_area("", value=riscos_default, height=200, key="risk_factors")

# ── Recomendação por série ─────────────────────────────────────────────────
st.divider()
st.markdown("### 🎯 Recomendação por Série")
st.caption("A sugestão automática leva em conta: (1) spread sobre NTN-B vs peers, (2) inversão de spread com emissões mais curtas do mesmo emissor.")

rec_options = ["✅ Atrativo — Investir", "⚖️ Neutro — Monitorar", "❌ Não Atrativo — Passar"]

def _rec_default_inteligente(s, alertas_series, ipca_peers_df):
    """
    Critério para sugestão de recomendação:
    - Se há inversão de spread (emissor paga mais em duration menor): Não Atrativo
    - Se spread < mediana de peers: Não Atrativo
    - Se spread dentro ±0.10% da mediana: Neutro
    - Se spread > mediana + 0.10%: Atrativo
    """
    if s["indexer"] != "IPCA" or s.get("spread_bm") is None:
        return 1  # Neutro default para CDI/Pré

    alertas = alertas_series.get(s["serie"], [])
    # Se há inversão de spread → não atrativo
    if any("Inversão" in a for a in alertas):
        return 2

    dur = s["duration_anos"]
    spread_oferta = s["spread_bm"]
    band_peers = ipca_peers_df[ipca_peers_df["duration_years"].between(max(0, dur - 1.5), dur + 1.5)].copy() if not ipca_peers_df.empty else pd.DataFrame()

    if not band_peers.empty and "spread_ntnb" in band_peers.columns:
        peer_spreads_val = band_peers["spread_ntnb"].dropna()
        if not peer_spreads_val.empty:
            mediana = peer_spreads_val.median()
            delta = spread_oferta - mediana
            if delta < -0.05:
                return 2  # Não atrativo
            if delta < 0.10:
                return 1  # Neutro
            return 0  # Atrativo

    # Sem peers: critério absoluto de spread
    if spread_oferta >= 0.50:
        return 0
    if spread_oferta >= 0.25:
        return 1
    return 2


rec_data = []
for s in series_data:
    with st.container():
        col_s, col_r, col_j = st.columns([1, 2, 3])
        with col_s:
            st.markdown(f"**{s['serie']}**")
            st.caption(f"{s['indexer']} | Dur. {s['duration_anos']:.2f}a")
        with col_r:
            rec_default = _rec_default_inteligente(s, alertas_series, ipca_peers_df)
            rec = st.selectbox("Recomendação", rec_options, index=rec_default, key=f"rec_{s['serie']}")
        with col_j:
            # Justificativa automática baseada nos dados reais
            alertas = alertas_series.get(s["serie"], [])
            if alertas:
                just_auto = alertas[0].replace("⚠️ ", "").replace("❌ ", "").replace("🟡 ", "").replace("✅ ", "")
                # Remove markdown bold para campo de texto
                just_auto = just_auto.replace("**", "")
            elif s.get("spread_bm") is not None:
                just_auto = (
                    f"Spread de {s['spread_bm']:+.2f}% sobre NTN-B ({s['bm_ntnb']:.2f}%). "
                    f"Sem emissões comparáveis na faixa de duration para benchmarking."
                )
            else:
                just_auto = f"Indexado a {s['indexer']}; avaliar vs mercado de referência."
            just = st.text_input("Justificativa", key=f"just_{s['serie']}", value=just_auto)
        rec_data.append({"serie": s["serie"], "recomendacao": rec, "justificativa": just})

# ===========================================================================
# VIII. EXPORTAR MEMO (texto)
# ===========================================================================

st.divider()
if st.button("📥 Exportar memo em texto", use_container_width=True):
    lines = [
        f"# CREDIT MEMO — {asset_type} {issuer}",
        f"**Data:** {date.today().strftime('%d/%m/%Y')}  |  **Analista:** {analyst or '—'}",
        "",
        "---",
        "## I. SUMÁRIO EXECUTIVO",
        f"- **Emissor:** {issuer}",
        f"- **Tipo:** {asset_type} | **Setor:** {sector}",
        f"- **Rating:** {rating} ({rating_agency})",
        f"- **Garantia:** {guarantee}",
        f"- **Volume máximo:** {volume_total}",
        "",
        "## II. VISÃO DO EMISSOR",
        business_desc,
        "",
        "## III. CONTEXTO DE MERCADO",
        market_context,
        "",
        "## IV. ESTRUTURA DA EMISSÃO",
    ]
    for s in series_data:
        taxa_fmt = _fmt_taxa(s)
        spread_fmt = f"Spread NTN-B: {s['spread_bm']:+.2f}% ({s['bm_ntnb']:.2f}% NTN-B)" if s.get("spread_bm") is not None else ""
        lines.append(f"- **{s['serie']}**: Prazo {s['prazo_anos']:.1f}a | Duration {s['duration_anos']:.2f}a | {taxa_fmt} | {s['amortizacao']} | {spread_fmt}")

    lines += [
        "",
        "## V. ANÁLISE DE CRÉDITO",
        "### Pontos Positivos",
        positivos,
        "",
        "### Fatores de Risco",
        riscos,
        "",
        "## VI. RECOMENDAÇÃO POR SÉRIE",
    ]
    for r in rec_data:
        lines.append(f"- **{r['serie']}**: {r['recomendacao']} — {r['justificativa']}")

    memo_text = "\n".join(lines)
    st.download_button(
        "⬇️ Baixar memo (.md)",
        data=memo_text,
        file_name=f"credit_memo_{issuer.replace(' ', '_')}_{date.today()}.md",
        mime="text/markdown",
    )
    with st.expander("Pré-visualização do memo"):
        st.markdown(memo_text)

st.caption("Este relatório é gerado automaticamente com base nos dados de mercado disponíveis. Não constitui recomendação de investimento.")
