"""
Página de Oscilações de Taxa — ranking de maiores variações D-1 e D-7.
"""

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
import plotly.graph_objects as go

from src.benchmarks import load_benchmark_snapshots_batch, interpolate_rate
from src.snapshots import available_dates, get_oscillations, save_snapshot, get_spread_oscillations, available_benchmark_dates_internal, load_snapshot, get_ticker_history, list_tickers_with_names
from src.benchmarks import auto_backfill_benchmarks, fetch_ntnb, fetch_pre_fixed, save_benchmark_snapshot

# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Carregando dados de mercado...")
def _load(period: int) -> pd.DataFrame:
    return load_market_df(period=period)


@st.cache_data(ttl=3600, show_spinner="Carregando benchmarks...")
def _load_benchmarks() -> tuple[pd.DataFrame, pd.DataFrame]:
    return fetch_ntnb(), fetch_pre_fixed()


@st.cache_data(ttl=86400, show_spinner=False)
def _auto_backfill() -> list[str]:
    return auto_backfill_benchmarks(days_back=7)


st.title("📉 Oscilações de Taxa")
st.caption("Ranking de ativos com maior variação de taxa em relação ao dia anterior, à semana anterior e ao mês anterior.")

if not DATA_B3_TOKEN:
    st.error("Token não configurado. Adicione **DATA_B3_TOKEN** ao `.env` e reinicie.")
    st.stop()

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
        help="Selecione a data base para calcular D-1, D-7 e D-30. Snapshots disponíveis: " + ", ".join(_snap_dates[:5]) + ("…" if len(_snap_dates) > 5 else ""),
        key="osc_date_pick",
    )
    _picked_str = str(_picked)
    _use_snapshot = _picked_str != str(_today)
    selected_date_label = _picked_str
    _reference_date = _picked_str if _use_snapshot else None

    period = st.selectbox(
        "Período de apuração",
        options=[1, 5, 21],
        format_func=lambda x: {1: "1 dia", 5: "5 dias", 21: "21 dias"}[x],
        disabled=_use_snapshot,
    )
    if not _use_snapshot:
        if st.button("🔄 Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

if _use_snapshot:
    _closest = next((d for d in _snap_dates if d <= selected_date_label), None)
    if not _closest:
        st.warning(f"Nenhum snapshot disponível para {selected_date_label}.")
        st.stop()
    if _closest != selected_date_label:
        st.info(f"📅 Sem snapshot em {selected_date_label} — usando **{_closest}** (mais próximo disponível)")
        selected_date_label = _closest
        _reference_date = _closest
    df_today = load_snapshot(selected_date_label)
    if df_today.empty:
        st.warning(f"Snapshot de {selected_date_label} não encontrado.")
        st.stop()
    st.info(f"📅 Calculando oscilações a partir de **{selected_date_label}**")
else:
    df_today = _load(period)
    if df_today.empty:
        st.warning("Nenhum dado retornado pela API.")
        st.stop()

# Garante snapshot e benchmarks apenas no modo ao vivo
if not _use_snapshot:
    save_snapshot(df_today)
    ntnb_df, pre_df = _load_benchmarks()
    save_benchmark_snapshot(ntnb_df, pre_df)
    _auto_backfill()

# Deriva subcategoria CDI: % CDI ou CDI+
def _indexer_label(row: pd.Series) -> str:
    if row["indexer"] != "CDI":
        return row["indexer"]
    s = str(row.get("taxa_media_str", ""))
    if s.startswith("DI +"):
        return "CDI+"
    return "% CDI"

df_today["indexer_label"] = df_today.apply(_indexer_label, axis=1)

dates = available_dates()

if len(dates) < 2:
    st.info(
        "Ainda não há histórico suficiente para calcular oscilações. "
        "Volte amanhã após o dashboard ter salvo pelo menos dois snapshots diários."
    )
    st.divider()
    st.subheader("📅 Snapshots disponíveis")
    if dates:
        st.write(", ".join(dates))
    else:
        st.write("Nenhum snapshot salvo ainda.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar — filtros de ativos
# ---------------------------------------------------------------------------

with st.sidebar:
    st.divider()
    tipos = st.multiselect(
        "Tipo de ativo",
        options=sorted(df_today["class_type"].unique()),
        default=sorted(df_today["class_type"].unique()),
    )
    top_n = st.slider("Top N por direção", min_value=5, max_value=50, value=20, step=5)

    _all_emissores = sorted(df_today["nome"].dropna().unique().tolist()) if "nome" in df_today.columns else []
    filtro_emissor = st.multiselect(
        "Emissor",
        options=_all_emissores,
        default=[],
        placeholder="Todos os emissores",
    )

    _all_setores = sorted(df_today["setor"].dropna().unique().tolist()) if "setor" in df_today.columns else []
    filtro_setor = st.multiselect(
        "Setor",
        options=_all_setores,
        default=[],
        placeholder="Todos os setores",
    )

# ---------------------------------------------------------------------------
# Calcula oscilações
# ---------------------------------------------------------------------------

df_osc = get_oscillations(df_today, days_back=[1, 7, 30])

# Aplica filtro de tipo (indexador vira tab)
mask = df_osc["class_type"].isin(tipos)
df_osc = df_osc[mask].copy()

if filtro_emissor:
    df_osc = df_osc[df_osc["nome"].isin(filtro_emissor)].copy()
if filtro_setor:
    df_osc = df_osc[df_osc["setor"].isin(filtro_setor)].copy()

# Colunas disponíveis (depende do histórico)
has_d1  = "delta_D1"  in df_osc.columns and df_osc["delta_D1"].notna().any()
has_d7  = "delta_D7"  in df_osc.columns and df_osc["delta_D7"].notna().any()
has_d30 = "delta_D30" in df_osc.columns and df_osc["delta_D30"].notna().any()

if not has_d1 and not has_d7 and not has_d30:
    st.info("Ainda sem comparações disponíveis com os filtros selecionados.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabela de snapshots disponíveis
# ---------------------------------------------------------------------------

with st.expander("📅 Snapshots disponíveis no histórico", expanded=False):
    st.write(", ".join(dates))

st.divider()

# ---------------------------------------------------------------------------
# Função de renderização por janela
# ---------------------------------------------------------------------------

COLOR_UP   = "#e74c3c"   # taxa subiu = risco aumentou = vermelho
COLOR_DOWN = "#2ecc71"   # taxa caiu  = risco diminuiu = verde


def _render_window(delta_col: str, taxa_past_col: str, data_col: str | None, label: str, df: pd.DataFrame | None = None, taxa_hoje_col: str = "taxa_hoje", chart_scope: str = "") -> None:
    _base = df if df is not None else df_osc
    df_w = _base[_base[delta_col].notna()].copy()
    if df_w.empty:
        st.info(f"Sem dados comparativos para {label}.")
        return

    # Data de referência
    if data_col and data_col in df_w.columns:
        ref_date_raw = df_w[data_col].iloc[0]
        try:
            from datetime import datetime
            ref_date = datetime.strptime(str(ref_date_raw), "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            ref_date = str(ref_date_raw)
        st.caption(f"Comparando hoje com snapshot de **{ref_date}**")

    import re
    n_match = re.search(r"(\d+)$", delta_col)
    n = int(n_match.group(1)) if n_match else 1
    col_alta, col_queda = st.columns(2)

    is_spread = "spread" in delta_col
    unit = "p.p." if is_spread else "p.p."

    for col, title, ascending, color, icon in [
        (col_alta,  "📈 Maiores altas",  False, COLOR_UP,   "▲"),
        (col_queda, "📉 Maiores quedas", True,  COLOR_DOWN,  "▼"),
    ]:
        with col:
            st.subheader(title)
            df_sorted = (
                df_w[df_w[delta_col] > 0].nlargest(top_n, delta_col)
                if not ascending
                else df_w[df_w[delta_col] < 0].nsmallest(top_n, delta_col)
            ).copy()

            if df_sorted.empty:
                st.info("Nenhum ativo nesta direção.")
                continue

            df_plot = df_sorted.sort_values(delta_col, ascending=ascending).copy()

            # Formata colunas para hover legível
            def _fmt(val, sign=False):
                if pd.isna(val):
                    return "—"
                return f"{val:+.3f}" if sign else f"{val:.3f}"

            delta_fmt   = df_plot[delta_col].apply(lambda x: _fmt(x, sign=True) + f" {unit}")
            hoje_fmt    = df_plot[taxa_hoje_col].apply(_fmt) if taxa_hoje_col in df_plot.columns else ["—"] * len(df_plot)
            past_fmt    = df_plot[taxa_past_col].apply(_fmt) if taxa_past_col in df_plot.columns else ["—"] * len(df_plot)
            nome_col    = df_plot["nome"].fillna("—") if "nome" in df_plot.columns else ["—"] * len(df_plot)
            setor_col   = df_plot["setor"].fillna("—") if "setor" in df_plot.columns else ["—"] * len(df_plot)
            venc_col    = df_plot["vencimento"].fillna("—") if "vencimento" in df_plot.columns else ["—"] * len(df_plot)
            dur_col     = (
                df_plot["duration_years"].apply(lambda v: f"{v:.1f}a" if pd.notna(v) else "—")
                if "duration_years" in df_plot.columns
                else ["—"] * len(df_plot)
            )

            fig = px.bar(
                df_plot,
                x=delta_col,
                y="ticker",
                orientation="h",
                color_discrete_sequence=[color],
                height=max(280, len(df_plot) * 30),
            )
            fig.update_traces(
                customdata=list(zip(nome_col, setor_col, delta_fmt, hoje_fmt, past_fmt, venc_col, dur_col)),
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    f"<b>Δ {label}: %{{customdata[2]}}</b><br>"
                    "<span style='color:#888'>──────────────</span><br>"
                    "Emissor: %{customdata[0]}<br>"
                    "Setor: %{customdata[1]}<br>"
                    "Vencimento: %{customdata[5]} (%{customdata[6]})<br>"
                    f"Hoje: %{{customdata[3]}}<br>"
                    f"{label}: %{{customdata[4]}}"
                    "<extra></extra>"
                ),
                marker_line_width=0,
            )
            fig.update_layout(
                plot_bgcolor="#f8f9fa",
                paper_bgcolor="white",
                showlegend=False,
                xaxis=dict(
                    title=f"Δ {unit}",
                    gridcolor="#e8e8e8",
                    zeroline=True,
                    zerolinecolor="#aaa",
                    zerolinewidth=1.5,
                    tickformat="+.3f",
                ),
                yaxis=dict(title=None, autorange="reversed", tickfont=dict(size=11)),
                margin=dict(l=0, r=12, t=8, b=28),
                hoverlabel=dict(bgcolor="white", font_size=12, bordercolor="#ddd"),
            )
            chart_direction = "down" if ascending else "up"
            st.plotly_chart(
                fig,
                use_container_width=True,
                key=f"osc_{chart_scope}_{delta_col}_{chart_direction}",
            )

            # Tabela compacta
            show_cols = {
                "ticker": "Ticker",
                "nome": "Emissor",
                taxa_hoje_col: "Hoje",
                taxa_past_col: label,
                delta_col: f"Δ {icon}",
            }
            available = {k: v for k, v in show_cols.items() if k in df_sorted.columns}
            df_tbl = df_sorted[list(available.keys())].rename(columns=available).copy()
            for c_orig, c_new in available.items():
                if c_orig == delta_col:
                    df_tbl[c_new] = df_sorted[c_orig].apply(lambda x: _fmt(x, sign=True))
                elif c_orig in (taxa_hoje_col, taxa_past_col):
                    df_tbl[c_new] = df_sorted[c_orig].apply(_fmt)
            st.dataframe(df_tbl, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Tabs por indexador
# ---------------------------------------------------------------------------

_LABEL_ORDER = ["% CDI", "CDI+", "IPCA", "Pré-fixado"]
_available = [l for l in _LABEL_ORDER if l in df_osc["indexer_label"].unique()]
_tab_labels = ["🌐 Todos"] + _available
_tabs = st.tabs(_tab_labels)


_BENCHMARK_MAP = {"IPCA": "ntnb", "Pré-fixado": "pre"}
_has_bm_history = len(available_benchmark_dates_internal()) >= 2


def _render_indexer_tab(tab, df_tab: pd.DataFrame, indexer_label: str = "") -> None:
    with tab:
        if df_tab.empty:
            st.info("Nenhum ativo disponível com os filtros selecionados.")
            return

        _has_d1  = "delta_D1"  in df_tab.columns and df_tab["delta_D1"].notna().any()
        _has_d7  = "delta_D7"  in df_tab.columns and df_tab["delta_D7"].notna().any()
        _has_d30 = "delta_D30" in df_tab.columns and df_tab["delta_D30"].notna().any()

        if not _has_d1 and not _has_d7 and not _has_d30:
            st.info("Histórico insuficiente para este indexador.")
            return

        # Toggle spread para IPCA e Pré-fixado
        bm_type = _BENCHMARK_MAP.get(indexer_label)
        spread_mode = False
        df_spread = pd.DataFrame()
        if bm_type and _has_bm_history:
            spread_mode = st.toggle(
                "📐 Ver oscilação de spread (over benchmark)",
                value=False,
                key=f"spread_osc_{indexer_label}",
                help="Mostra variação do spread sobre o benchmark soberano, isolando o risco de crédito do movimento de juros.",
            )
            if spread_mode:
                df_spread = get_spread_oscillations(
                    df_today, days_back=[1, 7, 30],
                    indexer_val=indexer_label,
                    benchmark_type=bm_type,
                    reference_date=_reference_date,
                )
                df_spread = df_spread[df_spread["class_type"].isin(tipos)]
                if filtro_emissor:
                    df_spread = df_spread[df_spread["nome"].isin(filtro_emissor)]
                if filtro_setor:
                    df_spread = df_spread[df_spread["setor"].isin(filtro_setor)]

        # Determina qual df usar e quais colunas delta
        if spread_mode and not df_spread.empty:
            _df = df_spread
            d1_col,  d7_col,  d30_col  = "delta_spread_D1",  "delta_spread_D7",  "delta_spread_D30"
            past1_col, past7_col, past30_col = "spread_D1", "spread_D7", "spread_D30"
            taxa_hoje_label = "spread_hoje"
        else:
            _df = df_tab
            d1_col,  d7_col,  d30_col  = "delta_D1",  "delta_D7",  "delta_D30"
            past1_col, past7_col, past30_col = "taxa_D1", "taxa_D7", "taxa_D30"
            taxa_hoje_label = "taxa_hoje"

        _has_d1_eff  = d1_col  in _df.columns and _df[d1_col].notna().any()
        _has_d7_eff  = d7_col  in _df.columns and _df[d7_col].notna().any()
        _has_d30_eff = d30_col in _df.columns and _df[d30_col].notna().any()

        sub_labels = []
        if _has_d1_eff:
            sub_labels.append("🗓️ D-1 (dia anterior)")
        if _has_d7_eff:
            sub_labels.append("📆 D-7 (semana anterior)")
        if _has_d30_eff:
            sub_labels.append("📅 D-30 (mês anterior)")

        if not sub_labels:
            st.info("Sem dados comparativos para este modo.")
            return

        sub_tabs = st.tabs(sub_labels)
        idx = 0
        if _has_d1_eff:
            with sub_tabs[idx]:
                _render_window(
                    d1_col, past1_col,
                    "data_D1" if "data_D1" in _df.columns else None,
                    "D-1", df=_df,
                    taxa_hoje_col=taxa_hoje_label,
                    chart_scope=f"{indexer_label or 'todos'}_{'spread' if spread_mode else 'taxa'}",
                )
            idx += 1
        if _has_d7_eff:
            with sub_tabs[idx]:
                _render_window(
                    d7_col, past7_col,
                    "data_D7" if "data_D7" in _df.columns else None,
                    "D-7", df=_df,
                    taxa_hoje_col=taxa_hoje_label,
                    chart_scope=f"{indexer_label or 'todos'}_{'spread' if spread_mode else 'taxa'}",
                )
            idx += 1
        if _has_d30_eff:
            with sub_tabs[idx]:
                _render_window(
                    d30_col, past30_col,
                    "data_D30" if "data_D30" in _df.columns else None,
                    "D-30", df=_df,
                    taxa_hoje_col=taxa_hoje_label,
                    chart_scope=f"{indexer_label or 'todos'}_{'spread' if spread_mode else 'taxa'}",
                )


# Tab Todos (só taxa bruta)
_render_indexer_tab(_tabs[0], df_osc, indexer_label="")

# Tabs por indexador
for i, lbl in enumerate(_available):
    _df_lbl = df_osc[df_osc["indexer_label"] == lbl].copy()
    _render_indexer_tab(_tabs[i + 1], _df_lbl, indexer_label=lbl)

st.caption("Oscilações baseadas em snapshots diários salvos automaticamente. Não constitui recomendação de investimento.")

st.divider()

# ---------------------------------------------------------------------------
# Histórico de Ativo — time-series via snapshots
# ---------------------------------------------------------------------------

st.subheader("📈 Histórico de Taxa — Ativo Individual")

ticker_names = list_tickers_with_names()
if not ticker_names:
    st.info(
        "Nenhum snapshot disponível ainda. "
        "Salve snapshots diários usando o botão **Atualizar dados** acima."
    )
else:
    # Monta labels "TICKER (EMISSOR)" e mantém mapeamento de volta ao ticker
    _label_to_ticker = {
        f"{tk} ({nm})" if nm else tk: tk
        for tk, nm in sorted(ticker_names.items())
    }
    _ticker_labels = list(_label_to_ticker.keys())

    _col_sel, _col_mode, _col_info = st.columns([2, 1, 2])
    with _col_sel:
        selected_label = st.selectbox(
            "Pesquisar ativo",
            options=_ticker_labels,
            index=0,
            key="hist_ticker_select",
        )
    with _col_mode:
        modo_spread = st.toggle(
            "Spread vs NTN-B",
            value=False,
            key="hist_modo_spread",
            help="Exibe o spread do ativo sobre a NTN-B interpolada na duration correspondente a cada data.",
        )

    selected_ticker = _label_to_ticker[selected_label]
    hist_df = get_ticker_history(selected_ticker)

    if hist_df.empty:
        st.warning(f"Sem histórico para **{selected_ticker}**.")
    else:
        hist_df["snapshot_date"] = pd.to_datetime(hist_df["snapshot_date"])
        nome_ativo    = hist_df["nome"].dropna().iloc[-1]    if "nome"    in hist_df.columns and not hist_df["nome"].dropna().empty    else selected_ticker
        indexer_ativo = hist_df["indexer"].dropna().iloc[-1] if "indexer" in hist_df.columns and not hist_df["indexer"].dropna().empty else ""
        setor_ativo   = hist_df["setor"].dropna().iloc[-1]   if "setor"   in hist_df.columns and not hist_df["setor"].dropna().empty   else ""

        with _col_info:
            st.caption(
                f"**{nome_ativo}** &nbsp;·&nbsp; Indexador: `{indexer_ativo}` "
                f"&nbsp;·&nbsp; Setor: {setor_ativo} "
                f"&nbsp;·&nbsp; {len(hist_df)} snapshots"
            )

        # ---- Calcula spread vs NTN-B se solicitado -------------------------
        plot_y = hist_df["taxa_media"].copy()
        y_label = "Taxa média (% a.a.)"
        hover_label = "Taxa"
        line_color = "#1565c0"
        spread_warning = None

        if modo_spread:
            snap_dates_str = hist_df["snapshot_date"].dt.strftime("%Y-%m-%d").tolist()
            bm_by_date = load_benchmark_snapshots_batch(snap_dates_str, "ntnb")
            spreads = []
            for _, row in hist_df.iterrows():
                d_str = row["snapshot_date"].strftime("%Y-%m-%d")
                bm_df = bm_by_date.get(d_str)
                if bm_df is not None and not bm_df.empty and pd.notna(row.get("duration_years")):
                    ntnb_rate = interpolate_rate(bm_df, row["duration_years"] * 365)
                    spreads.append(row["taxa_media"] - ntnb_rate if ntnb_rate is not None else None)
                else:
                    spreads.append(None)
            hist_df["spread_ntnb"] = spreads
            n_missing = sum(1 for s in spreads if s is None)
            if n_missing == len(spreads):
                spread_warning = (
                    "Sem snapshots de NTN-B salvos para este período. "
                    "Exibindo a taxa bruta. Acesse a aba **Admin** para salvar benchmarks históricos."
                )
                modo_spread = False
            else:
                plot_y = hist_df["spread_ntnb"]
                y_label = "Spread vs NTN-B (p.p.)"
                hover_label = "Spread"
                line_color = "#6a1b9a"
                if n_missing > 0:
                    spread_warning = f"⚠️ {n_missing} datas sem benchmark NTN-B — aparecem como lacunas no gráfico."

        if spread_warning:
            st.warning(spread_warning)

        # ---- Gráfico -------------------------------------------------------
        fig_hist_ts = go.Figure()
        fig_hist_ts.add_trace(go.Scatter(
            x=hist_df["snapshot_date"],
            y=plot_y,
            mode="lines+markers",
            name=y_label,
            line=dict(color=line_color, width=2),
            marker=dict(size=6),
            hovertemplate=f"%{{x|%d/%m/%Y}}<br>{hover_label}: <b>%{{y:.2f}}%</b><extra></extra>",
            connectgaps=False,
        ))

        # Anotação no último ponto
        _valid = plot_y.dropna()
        if len(_valid) >= 1:
            _last_idx = _valid.index[-1]
            _first_idx = _valid.index[0]
            _last_val  = _valid.iloc[-1]
            _first_val = _valid.iloc[0]
            _delta_plot = _last_val - _first_val
            _cor = "#155724" if _delta_plot < 0 else "#721c24"
            fig_hist_ts.add_annotation(
                x=hist_df.loc[_last_idx, "snapshot_date"],
                y=_last_val,
                text=f"{_last_val:.2f}%",
                showarrow=True,
                arrowhead=2,
                font=dict(color=_cor, size=11, family="monospace"),
                bgcolor="white",
                bordercolor=_cor,
                borderwidth=1,
                ax=30, ay=-30,
            )

        fig_hist_ts.update_layout(
            height=380,
            title=dict(
                text=f"{selected_ticker} — {y_label.lower()} ao longo dos snapshots",
                font=dict(size=13),
                x=0,
            ),
            xaxis=dict(title=None, tickformat="%d/%m/%y"),
            yaxis=dict(title=y_label),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=50, b=30, l=0, r=20),
            plot_bgcolor="#f8f9fa",
        )
        st.plotly_chart(fig_hist_ts, use_container_width=True)

        # ---- Métricas resumo -----------------------------------------------
        if not _valid.empty:
            _mc1, _mc2, _mc3, _mc4, _mc5 = st.columns(5)
            _mc1.metric("Atual",   f"{_valid.iloc[-1]:.2f}%")
            _mc2.metric("Inicial", f"{_valid.iloc[0]:.2f}%")
            _dv = _valid.iloc[-1] - _valid.iloc[0]
            _mc3.metric(
                "Variação total",
                f"{_dv:+.2f}%",
                delta=("▲ abriu" if _dv > 0 else "▼ fechou"),
                delta_color="inverse" if _dv > 0 else "normal",
            )
            _mc4.metric("Mínima", f"{_valid.min():.2f}%")
            _mc5.metric("Máxima", f"{_valid.max():.2f}%")

        # ---- Tabela detalhada ----------------------------------------------
        with st.expander("📋 Ver dados completos"):
            tbl_cols = ["snapshot_date", "taxa_media", "taxa_media_str", "duration_years"]
            if modo_spread and "spread_ntnb" in hist_df.columns:
                tbl_cols.append("spread_ntnb")
            tbl_cols = [c for c in tbl_cols if c in hist_df.columns]
            tbl = hist_df[tbl_cols].copy()
            tbl["snapshot_date"] = tbl["snapshot_date"].dt.strftime("%d/%m/%Y")
            tbl = tbl.rename(columns={
                "snapshot_date": "Data",
                "taxa_media":    "Taxa (%)",
                "taxa_media_str": "Taxa (str)",
                "duration_years": "Duration (anos)",
                "spread_ntnb":   "Spread NTN-B (%)",
            }).sort_values("Data", ascending=False)
            st.dataframe(tbl, hide_index=True, use_container_width=True)
