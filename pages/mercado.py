"""Dashboard de Visão de Mercado — CRI, CRA e Debêntures."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.data_sources import DATA_B3_TOKEN, load_market_df
from src.snapshots import save_snapshot as _save_snapshot, available_dates, load_snapshot
from src.benchmarks import (
    add_spread_column,
    auto_backfill_benchmarks,
    fetch_ntnb,
    fetch_pre_fixed,
    save_benchmark_snapshot,
)

# ---------------------------------------------------------------------------
# Constantes visuais
# ---------------------------------------------------------------------------

COLOR_MAP = {
    "CRI": "#1f77b4",
    "CRA": "#2ca02c",
    "Debênture": "#ff7f0e",
}

Y_LABELS = {
    "CDI": "% do DI",
    "CDI+": "CDI + % a.a.",
    "IPCA": "IPCA + % a.a.",
    "Pré-fixado": "% a.a.",
}

SPREAD_LABELS = {
    "IPCA": "Spread over NTN-B (p.p.)",
    "Pré-fixado": "Spread over LTN/NTN-F (p.p.)",
}

# ---------------------------------------------------------------------------
# Carregamento com cache (1h)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Carregando dados de mercado...")
def _cached_load(period: int) -> pd.DataFrame:
    return load_market_df(period=period)


@st.cache_data(ttl=3600, show_spinner="Carregando benchmarks...")
def _cached_benchmarks() -> tuple[pd.DataFrame, pd.DataFrame]:
    ntnb = fetch_ntnb()
    pre = fetch_pre_fixed()
    return ntnb, pre


@st.cache_data(ttl=86400, show_spinner=False)
def _auto_backfill() -> list[str]:
    """Preenche benchmarks dos últimos 7 dias — roda 1x/dia silenciosamente."""
    return auto_backfill_benchmarks(days_back=7)


# ---------------------------------------------------------------------------
# Título
# ---------------------------------------------------------------------------

st.title("📊 Visão de Mercado — CRI · CRA · Debêntures")
st.caption("Dados de negociação da B3. Utilize os filtros para explorar o mercado antes de analisar um ativo.")

if not DATA_B3_TOKEN:
    st.error("Token não configurado. Adicione **DATA_B3_TOKEN** ao `.env` e reinicie.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar — controles fixos
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Filtros")

    # Seletor de data
    _snap_dates = available_dates()  # ordenado DESC
    _snap_date_objs = [date.fromisoformat(d) for d in _snap_dates]
    _today = date.today()
    _min_date = _snap_date_objs[-1] if _snap_date_objs else _today
    _picked = st.date_input(
        "📅 Data de análise",
        value=_today,
        min_value=_min_date,
        max_value=_today,
        help="Selecione uma data para analisar. Datas com snapshot salvo: " + ", ".join(_snap_dates[:5]) + ("…" if len(_snap_dates) > 5 else ""),
        key="mercado_date_pick",
    )
    _picked_str = str(_picked)
    _use_snapshot = _picked_str != str(_today)
    selected_date_label = _picked_str

    period = 1  # sempre usa o dado mais recente

    if not _use_snapshot:
        if st.button("🔄 Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

# ---------------------------------------------------------------------------
# Carrega dados
# ---------------------------------------------------------------------------

if _use_snapshot:
    # fallback para o snapshot mais próximo disponível
    _closest = next((d for d in _snap_dates if d <= selected_date_label), None)
    if not _closest:
        st.warning(f"Nenhum snapshot disponível para {selected_date_label}.")
        st.stop()
    if _closest != selected_date_label:
        st.info(f"📅 Sem snapshot em {selected_date_label} — exibindo **{_closest}** (mais próximo disponível)")
        selected_date_label = _closest
    df = load_snapshot(selected_date_label)
    if df.empty:
        st.warning(f"Snapshot de {selected_date_label} não encontrado.")
        st.stop()
    st.info(f"📅 Exibindo dados históricos de **{selected_date_label}**")
else:
    df = _cached_load(period)
    if df.empty:
        st.warning("Nenhum dado retornado pela API. Verifique o token no `.env`.")
        st.stop()

ntnb_df, pre_df = _cached_benchmarks()

if not _use_snapshot and not df.empty:
    _save_snapshot(df)
    save_benchmark_snapshot(ntnb_df, pre_df)
    _auto_backfill()

# ---------------------------------------------------------------------------
# Sidebar — filtros dinâmicos
# ---------------------------------------------------------------------------

with st.sidebar:
    st.divider()

    tipos = st.multiselect(
        "Tipo de ativo",
        options=sorted(df["class_type"].unique()),
        default=sorted(df["class_type"].unique()),
    )

    setores_disponiveis = sorted(df["setor"].dropna().unique())
    setores = st.multiselect(
        "Setor",
        options=setores_disponiveis,
        default=[],
        placeholder="Todos os setores",
    )

    emissor_busca = st.text_input(
        "Buscar emissor / devedor / ticker",
        placeholder="Ex: Petrobras, MRV, XPCA11...",
    )

    isento_ir = st.radio(
        "Isento de IR",
        options=["Todos", "Sim", "Não"],
        horizontal=True,
    )

# ---------------------------------------------------------------------------
# Aplica filtros globais
# ---------------------------------------------------------------------------

mask = df["class_type"].isin(tipos) if tipos else pd.Series([True] * len(df), index=df.index)

if setores:
    mask &= df["setor"].isin(setores)

if emissor_busca.strip():
    q = emissor_busca.strip()
    mask &= (
        df["nome"].str.contains(q, case=False, na=False)
        | df["emissor"].str.contains(q, case=False, na=False)
        | df["ticker"].str.contains(q, case=False, na=False)
    )

if isento_ir == "Sim":
    mask &= df["lei"] == True
elif isento_ir == "Não":
    mask &= df["lei"] == False

df_f = df[mask].copy()

# ---------------------------------------------------------------------------
# Métricas resumo
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("Ativos filtrados", f"{len(df_f):,}", f"de {len(df):,} totais")

for col, idx_val, unit in zip(
    [col2, col3, col4],
    ["CDI", "IPCA", "Pré-fixado"],
    ["% DI", "% a.a.", "% a.a."],
):
    sub = df_f[df_f["indexer"] == idx_val]["taxa_media"]
    n = len(sub)
    if n > 0:
        col.metric(
            f"Mediana {idx_val}",
            f"{sub.median():.2f} {unit}",
            f"{n} ativos",
        )
    else:
        col.metric(f"Mediana {idx_val}", "—")

st.divider()

# ---------------------------------------------------------------------------
# Abas por indexador
# ---------------------------------------------------------------------------

tab_cdi, tab_ipca, tab_pre = st.tabs(["📈 CDI", "📈 IPCA", "📈 Pré-fixado"])


def _render_tab(tab, indexer_val: str, df_override=None, key_suffix: str = "") -> None:
    with tab:
        df_idx = df_override.copy() if df_override is not None else df_f[df_f["indexer"] == indexer_val].copy()
        _key = f"{indexer_val}{key_suffix}"

        if df_idx.empty:
            st.info(f"Nenhum ativo **{indexer_val}** com os filtros selecionados.")
            return

        # Toggle spread apenas para IPCA e Pré-fixado
        has_benchmark = indexer_val in SPREAD_LABELS
        spread_mode = False
        if has_benchmark:
            spread_mode = st.toggle(
                "📐 Ver spread over benchmark",
                value=False,
                key=f"spread_{_key}",
                help=SPREAD_LABELS[indexer_val],
            )

        # Seleciona benchmark correto e calcula spread se necessário
        if has_benchmark and spread_mode:
            bdf = ntnb_df if indexer_val == "IPCA" else pre_df
            df_idx = add_spread_column(df_idx, bdf, spread_col="spread", benchmark_rate_col="benchmark_rate")
            y_col = "spread"
            y_label = SPREAD_LABELS[indexer_val]
        else:
            y_col = "taxa_media"
            y_label = Y_LABELS[indexer_val]

        c1, c2 = st.columns(2)
        with c1:
            t_min = float(df_idx[y_col].min())
            t_max = float(df_idx[y_col].max())
            if t_min >= t_max:
                t_max = t_min + 0.01
            t_range = st.slider(
                f"{'Spread' if spread_mode else 'Taxa'} ({y_label})",
                min_value=t_min,
                max_value=t_max,
                value=(t_min, t_max),
                key=f"taxa_{_key}",
                format="%.2f",
            )
        with c2:
            d_min = float(df_idx["duration_years"].min())
            d_max = float(df_idx["duration_years"].max())
            if d_min >= d_max:
                d_max = d_min + 0.01
            d_range = st.slider(
                "Duration (anos)",
                min_value=d_min,
                max_value=d_max,
                value=(d_min, d_max),
                key=f"dur_{_key}",
                format="%.1f",
            )

        df_tab = df_idx[
            df_idx[y_col].between(*t_range)
            & df_idx["duration_years"].between(*d_range)
        ].copy()
        df_tab["vencimento_fmt"] = pd.to_datetime(
            df_tab["vencimento"], errors="coerce"
        ).dt.strftime("%d/%m/%Y")

        hover_extra: dict = {}
        if has_benchmark and spread_mode and "benchmark_rate" in df_tab.columns:
            df_tab["benchmark_rate"] = df_tab["benchmark_rate"].round(4)
            hover_extra = {"benchmark_rate": ":.4f"}

        fig = px.scatter(
            df_tab,
            x="duration_years",
            y=y_col,
            color="class_type",
            color_discrete_map=COLOR_MAP,
            hover_name="ticker",
            hover_data={
                "taxa_media_str": True,
                "nome": True,
                "setor": True,
                "duration_years": ":.2f",
                **({"lei": True} if "lei" in df_tab.columns else {}),
                "vencimento_fmt": True,
                "taxa_media": False,
                "class_type": False,
                "indexer": False,
                **hover_extra,
            },
            labels={
                "duration_years": "Duration (anos)",
                y_col: y_label,
                "class_type": "Tipo",
                "nome": "Emissor/Devedor",
                "setor": "Setor",
                "taxa_media_str": "Taxa",
                "lei": "Isento IR",
                "vencimento_fmt": "Vencimento",
                "benchmark_rate": "Benchmark",
                "spread": y_label,
            },
            title=f"{indexer_val} — {'Spread' if spread_mode else 'Taxa'} × Duration  ({len(df_tab)} ativos)",
            height=540,
        )
        fig.update_traces(
            marker=dict(size=9, opacity=0.75, line=dict(width=0.5, color="white"))
        )

        # Curva do benchmark no gráfico de taxa bruta
        if has_benchmark and not spread_mode:
            bdf = ntnb_df if indexer_val == "IPCA" else pre_df
            if not bdf.empty:
                import numpy as np
                dur_range_days = [d_range[0] * 365, d_range[1] * 365]
                bench_days = bdf["days_to_due"].values
                bench_rates = bdf["indicative_rate"].values
                x_line = np.linspace(dur_range_days[0], dur_range_days[1], 100)
                y_line = [float(pd.Series(bench_rates)[
                    max(0, int(pd.Series(bench_days).searchsorted(d)) - 1)
                ] + (
                    (d - bench_days[max(0, int(pd.Series(bench_days).searchsorted(d)) - 1)]) /
                    max(1, bench_days[min(len(bench_days)-1, int(pd.Series(bench_days).searchsorted(d)))] -
                        bench_days[max(0, int(pd.Series(bench_days).searchsorted(d)) - 1)])
                ) * (
                    bench_rates[min(len(bench_rates)-1, int(pd.Series(bench_days).searchsorted(d)))] -
                    bench_rates[max(0, int(pd.Series(bench_days).searchsorted(d)) - 1)]
                )) for d in x_line]
                fig.add_scatter(
                    x=x_line / 365,
                    y=y_line,
                    mode="lines",
                    name="Benchmark" + (" NTN-B" if indexer_val == "IPCA" else " LTN/NTN-F"),
                    line=dict(color="#888888", width=1.5, dash="dot"),
                    hovertemplate="Duration: %{x:.2f} anos<br>Benchmark: %{y:.4f}%<extra></extra>",
                )

        # Linha zero para spread
        if has_benchmark and spread_mode:
            fig.add_hline(y=0, line_dash="dot", line_color="#888888", opacity=0.6,
                          annotation_text="Benchmark", annotation_position="bottom right")

        fig.update_layout(
            plot_bgcolor="#f8f9fa",
            paper_bgcolor="white",
            legend=dict(
                title="Tipo",
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            xaxis=dict(title="Duration (anos)", gridcolor="#e0e0e0", zeroline=False),
            yaxis=dict(title=y_label, gridcolor="#e0e0e0", zeroline=False),
            hoverlabel=dict(bgcolor="white", font_size=12),
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander(f"📋 Tabela — {len(df_tab)} ativos {indexer_val}", expanded=False):
            col_map = {
                "ticker": "Ticker",
                "nome": "Emissor / Devedor",
                "setor": "Setor",
                "taxa_media_str": "Taxa",
                "duration_years": "Duration (anos)",
                "vencimento_fmt": "Vencimento",
                "lei": "Isento IR",
                "vol_total": "Volume (R$)",
            }
            if has_benchmark and spread_mode and "benchmark_rate" in df_tab.columns:
                col_map["benchmark_rate"] = "Benchmark"
                col_map["spread"] = "Spread"
            col_map = {k: v for k, v in col_map.items() if k in df_tab.columns}
            df_show = df_tab[list(col_map.keys())].rename(columns=col_map).copy()
            if "Isento IR" in df_show.columns:
                df_show["Isento IR"] = df_show["Isento IR"].map({True: "✅ Sim", False: "❌ Não"})
            if "Volume (R$)" in df_show.columns:
                df_show["Volume (R$)"] = df_show["Volume (R$)"].apply(
                    lambda x: f"R$ {x:,.0f}" if x > 0 else "—"
                )
            if "Duration (anos)" in df_show.columns:
                df_show["Duration (anos)"] = df_show["Duration (anos)"].apply(lambda x: f"{x:.2f}")
            if "Benchmark" in df_show.columns:
                df_show["Benchmark"] = df_show["Benchmark"].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "—")
            if "Spread" in df_show.columns:
                df_show["Spread"] = df_show["Spread"].apply(lambda x: f"{x:+.4f}" if pd.notna(x) else "—")
            st.dataframe(
                df_show,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Ticker": st.column_config.TextColumn(width="small"),
                    "Emissor / Devedor": st.column_config.TextColumn(width="medium"),
                    "Taxa": st.column_config.TextColumn(width="medium"),
                },
            )


# CDI: sub-tabs %CDI e CDI+
with tab_cdi:
    _df_cdi = df_f[df_f["indexer"] == "CDI"]
    _df_pct  = _df_cdi[_df_cdi["taxa_media_str"].str.contains("% do DI", na=False)]
    _df_plus = _df_cdi[_df_cdi["taxa_media_str"].str.startswith("DI +", na=False)]
    sub_pct, sub_plus = st.tabs([
        f"% do CDI  ({len(_df_pct)})",
        f"CDI+  ({len(_df_plus)})",
    ])
    _render_tab(sub_pct,  "CDI",  df_override=_df_pct,  key_suffix="_pct")
    _render_tab(sub_plus, "CDI+", df_override=_df_plus, key_suffix="_plus")

_render_tab(tab_ipca, "IPCA")
_render_tab(tab_pre, "Pré-fixado")

st.divider()
st.caption("Fonte: Data B3. Não constitui recomendação de investimento.")
