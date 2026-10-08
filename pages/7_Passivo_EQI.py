"""
Análise de Passivo EQI — oscilações das posições de crédito da carteira EQI.

Carrega uma planilha CSV (ticker;sum;issuer) com as posições da carteira,
filtra posições > R$ 1M, cruza com a base de taxas e exibe as oscilações
ordenadas por exposição (maior → menor).
"""

from __future__ import annotations

import sys
from datetime import date
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.data_sources import DATA_B3_TOKEN, load_market_df
from src.snapshots import (
    available_benchmark_dates_internal,
    available_dates,
    get_oscillations,
    get_spread_oscillations,
    load_snapshot,
    save_snapshot,
)
from src.benchmarks import (
    auto_backfill_benchmarks,
    fetch_ntnb,
    fetch_pre_fixed,
    save_benchmark_snapshot,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "passivo_eqi.csv"
MIN_EXPOSURE = 1_000_000  # R$ 1M
_BENCHMARK_MAP = {"IPCA": "ntnb", "Pré-fixado": "pre"}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600, show_spinner="Carregando dados de mercado...")
def _load_market(period: int) -> pd.DataFrame:
    return load_market_df(period=period)


@st.cache_data(ttl=3600, show_spinner="Carregando benchmarks...")
def _load_benchmarks() -> tuple[pd.DataFrame, pd.DataFrame]:
    return fetch_ntnb(), fetch_pre_fixed()


@st.cache_data(ttl=86400, show_spinner=False)
def _auto_backfill() -> list[str]:
    return auto_backfill_benchmarks(days_back=7)


# ---------------------------------------------------------------------------
# Helpers de portfólio
# ---------------------------------------------------------------------------


def _parse_br_number(s: str) -> float:
    """Converte '550.673.635,26' → 550673635.26 (formato BR)."""
    try:
        cleaned = str(s).strip().replace(".", "").replace(",", ".")
        return float(cleaned)
    except Exception:
        return 0.0


def _load_portfolio() -> pd.DataFrame:
    """Carrega CSV de posições persistido no servidor."""
    if not PORTFOLIO_PATH.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(PORTFOLIO_PATH, sep=";", dtype=str, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(PORTFOLIO_PATH, sep=";", dtype=str, encoding="latin-1")
    df.columns = [c.strip().lower() for c in df.columns]
    if "sum" not in df.columns or "ticker" not in df.columns:
        return pd.DataFrame()
    df["volume"] = df["sum"].apply(_parse_br_number)
    return df[df["volume"] >= MIN_EXPOSURE].copy()


def _save_portfolio(content: str) -> pd.DataFrame:
    """Persiste conteúdo do CSV e retorna df parseado."""
    PORTFOLIO_PATH.parent.mkdir(exist_ok=True)
    PORTFOLIO_PATH.write_text(content, encoding="utf-8")
    try:
        df = pd.read_csv(StringIO(content), sep=";", dtype=str)
    except Exception:
        return pd.DataFrame()
    df.columns = [c.strip().lower() for c in df.columns]
    if "sum" not in df.columns or "ticker" not in df.columns:
        return pd.DataFrame()
    df["volume"] = df["sum"].apply(_parse_br_number)
    return df[df["volume"] >= MIN_EXPOSURE].copy()


# ---------------------------------------------------------------------------
# Helpers de formatação
# ---------------------------------------------------------------------------


def _fmt_rate(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.3f}"


def _fmt_delta(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.3f}"


def _fmt_brl(v: float | None) -> str:
    """Formata número como moeda brasileira: R$ 550.673.635,26"""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == 0:
        return "—"
    s = f"{v:,.2f}"                          # "550,673,635.26"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _fmt_volume(v: float | None) -> str:
    """Formato abreviado para KPIs."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == 0:
        return "—"
    if v >= 1e9:
        return f"R$ {v/1e9:.3f}B"
    return f"R$ {v/1e6:.2f}M"


def _fmt_pct(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.2f}%"


# ---------------------------------------------------------------------------
# Setup da página
# ---------------------------------------------------------------------------

st.title("📊 Análise de Passivo EQI")
st.caption(
    "Oscilações de taxa e spread dos ativos de crédito na carteira EQI, "
    "ordenados por exposição (maior → menor). Apenas posições > R$ 1M."
)

if not DATA_B3_TOKEN:
    st.error("Token não configurado. Adicione **DATA_B3_TOKEN** ao `.env` e reinicie.")
    st.stop()

# ---------------------------------------------------------------------------
# Upload de planilha de posições
# ---------------------------------------------------------------------------

_has_portfolio = PORTFOLIO_PATH.exists()

with st.expander(
    "📁 Planilha de Posições" + (" ✅" if _has_portfolio else " — nenhuma planilha carregada"),
    expanded=not _has_portfolio,
):
    st.markdown(
        "Carregue um **CSV com separador `;`** e colunas: `ticker`, `sum`, `issuer`.  \n"
        "A coluna `sum` deve estar no formato numérico brasileiro (ex: `550.673.635,26`).  \n"
        "O arquivo será salvo no servidor para uso futuro."
    )
    uploaded = st.file_uploader(
        "Selecionar CSV de posições",
        type=["csv"],
        key="passivo_upload",
        label_visibility="collapsed",
    )
    if uploaded is not None:
        raw = uploaded.read()
        for enc in ("utf-8", "latin-1", "utf-8-sig"):
            try:
                content_str = raw.decode(enc)
                break
            except UnicodeDecodeError:
                content_str = None
        if content_str:
            df_uploaded = _save_portfolio(content_str)
            if df_uploaded.empty:
                st.error("Não foi possível processar o arquivo. Verifique o formato (ticker;sum;issuer).")
            else:
                st.success(
                    f"✅ Planilha carregada: **{len(df_uploaded)}** posições acima de R$ 1M "
                    f"(total: {len(pd.read_csv(StringIO(content_str), sep=';'))} linhas no arquivo)"
                )
                st.cache_data.clear()
                st.rerun()
        else:
            st.error("Não foi possível decodificar o arquivo.")

    elif _has_portfolio:
        _mtime = PORTFOLIO_PATH.stat().st_mtime
        from datetime import datetime
        _dt = datetime.fromtimestamp(_mtime).strftime("%d/%m/%Y %H:%M")
        st.caption(f"📄 Usando planilha salva em `data/passivo_eqi.csv` — última atualização: {_dt}")

# ---------------------------------------------------------------------------
# Carrega portfólio
# ---------------------------------------------------------------------------

df_port = _load_portfolio()

if df_port.empty:
    st.warning(
        "Nenhuma planilha de posições carregada ou sem posições acima de R$ 1M. "
        "Use o painel acima para fazer upload."
    )
    st.stop()

df_port["ticker"] = df_port["ticker"].str.strip().str.upper()
portfolio_map: dict[str, float] = dict(zip(df_port["ticker"], df_port["volume"]))
total_portfolio = sum(portfolio_map.values())

_issuer_raw = df_port["issuer"].str.strip() if "issuer" in df_port.columns else pd.Series(["" ] * len(df_port), index=df_port.index)
issuer_map: dict[str, str] = dict(zip(df_port["ticker"], _issuer_raw.fillna("")))

# ---------------------------------------------------------------------------
# Sidebar — controles
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Filtros")

    _snap_dates = available_dates()
    _snap_date_objs = [date.fromisoformat(d) for d in _snap_dates]
    _today = date.today()
    _min_date = _snap_date_objs[-1] if _snap_date_objs else _today

    _picked = st.date_input(
        "📅 Data de análise",
        value=_today,
        min_value=_min_date,
        max_value=_today,
        help="Data base para calcular D-1, D-7 e D-30. Snapshots: " + ", ".join(_snap_dates[:5]) + ("…" if len(_snap_dates) > 5 else ""),
        key="passivo_date_pick",
    )
    _picked_str = str(_picked)
    _use_snapshot = _picked_str != str(_today)
    _reference_date: str | None = _picked_str if _use_snapshot else None

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

    st.divider()

    direction = st.radio(
        "Direção",
        options=["Todos", "Só altas", "Só quedas"],
        index=0,
        key="passivo_direction",
    )

    sort_by = st.radio(
        "Ordenar por",
        options=["Exposição (volume)", "Variação (|Δ|)"],
        index=0,
        key="passivo_sortby",
    )

    st.divider()

    view_mode = st.radio(
        "Visualização",
        options=["Por Ticker", "Por Emissor"],
        index=0,
        key="passivo_viewmode",
        help=(
            "**Por Ticker**: uma linha por ativo.\n\n"
            "**Por Emissor**: agrupa todos os tickers do mesmo emissor + indexador "
            "e calcula a média ponderada por volume da oscilação."
        ),
    )

# ---------------------------------------------------------------------------
# Carrega dados de mercado
# ---------------------------------------------------------------------------

if _use_snapshot:
    _closest = next((d for d in _snap_dates if d <= _picked_str), None)
    if not _closest:
        st.warning(f"Nenhum snapshot disponível para {_picked_str}.")
        st.stop()
    if _closest != _picked_str:
        st.info(f"📅 Sem snapshot em {_picked_str} — usando **{_closest}** (mais próximo disponível)")
    _reference_date = _closest
    df_market = load_snapshot(_closest)
    if df_market.empty:
        st.warning(f"Snapshot de {_closest} não encontrado.")
        st.stop()
    st.info(f"📅 Calculando oscilações a partir de **{_closest}**")
else:
    df_market = _load_market(period)
    if df_market.empty:
        st.warning("Nenhum dado retornado pela API.")
        st.stop()
    save_snapshot(df_market)
    ntnb_df, pre_df = _load_benchmarks()
    save_benchmark_snapshot(ntnb_df, pre_df)
    _auto_backfill()

# Deriva indexer_label (igual às outras páginas)
def _indexer_label(row: pd.Series) -> str:
    if row.get("indexer") != "CDI":
        return row.get("indexer", "")
    s = str(row.get("taxa_media_str", ""))
    return "CDI+" if s.startswith("DI +") else "% CDI"

if "indexer" in df_market.columns:
    df_market["indexer_label"] = df_market.apply(_indexer_label, axis=1)

# ---------------------------------------------------------------------------
# Filtra pelos tickers do portfólio
# ---------------------------------------------------------------------------

_snap_dates_check = available_dates()
if len(_snap_dates_check) < 2:
    st.info(
        "Ainda não há histórico suficiente para calcular oscilações. "
        "Volte após ao menos dois snapshots diários terem sido salvos."
    )
    st.stop()

df_market_port = df_market[
    df_market["ticker"].str.upper().isin(portfolio_map)
].copy()

if df_market_port.empty:
    st.warning(
        "Nenhum ticker da planilha encontrado na base de dados de mercado. "
        "Verifique se os códigos estão corretos."
    )
    st.stop()

matched = set(df_market_port["ticker"].str.upper().tolist())
not_found = sorted(set(portfolio_map.keys()) - matched)

# ---------------------------------------------------------------------------
# Toggle de spread
# ---------------------------------------------------------------------------

_has_bm_history = len(available_benchmark_dates_internal()) >= 2

spread_mode = False
if _has_bm_history:
    spread_mode = st.toggle(
        "📐 Ver oscilação de spread (over benchmark)",
        value=False,
        key="passivo_spread_mode",
        help=(
            "Para ativos IPCA: spread sobre NTN-B interpolada.  \n"
            "Para ativos Pré-fixado: spread sobre a curva pré.  \n"
            "Demais indexadores mantêm a taxa nominal."
        ),
    )

# ---------------------------------------------------------------------------
# Calcula oscilações
# ---------------------------------------------------------------------------

df_osc = get_oscillations(df_market_port, days_back=[1, 7, 30], reference_date=_reference_date)

# Sobrepõe colunas de spread para IPCA e Pré-fixado, se modo spread ativo
if spread_mode:
    for idx_lbl, bm_type in _BENCHMARK_MAP.items():
        df_spread = get_spread_oscillations(
            df_market_port,
            days_back=[1, 7, 30],
            indexer_val=idx_lbl,
            benchmark_type=bm_type,
            reference_date=_reference_date,
        )
        if df_spread.empty:
            continue

        # Colunas de spread disponíveis
        spread_cols = [c for c in df_spread.columns if c.startswith("spread") or c.startswith("delta_spread")]
        df_spread_sub = df_spread[["ticker"] + spread_cols].copy()
        df_osc = df_osc.merge(df_spread_sub, on="ticker", how="left", suffixes=("", f"_{bm_type}"))

        mask = df_osc["indexer"] == idx_lbl
        # Substitui taxa_hoje e taxa_Dn pelo spread para este indexador
        if "spread_hoje" in df_osc.columns:
            df_osc.loc[mask, "taxa_hoje"] = df_osc.loc[mask, "spread_hoje"]
        for n in [1, 7, 30]:
            if f"spread_D{n}" in df_osc.columns:
                df_osc.loc[mask, f"taxa_D{n}"] = df_osc.loc[mask, f"spread_D{n}"]
            if f"delta_spread_D{n}" in df_osc.columns:
                df_osc.loc[mask, f"delta_D{n}"] = df_osc.loc[mask, f"delta_spread_D{n}"]

# Adiciona colunas do portfólio
df_osc["_ticker_up"] = df_osc["ticker"].str.upper()
df_osc["volume"] = df_osc["_ticker_up"].map(portfolio_map)
df_osc["pct_portfolio"] = df_osc["volume"] / total_portfolio * 100
df_osc["issuer"] = df_osc["_ticker_up"].map(issuer_map).fillna("—")
df_osc = df_osc[df_osc["volume"].notna()].drop(columns=["_ticker_up"])

has_d1  = "delta_D1"  in df_osc.columns and df_osc["delta_D1"].notna().any()
has_d7  = "delta_D7"  in df_osc.columns and df_osc["delta_D7"].notna().any()
has_d30 = "delta_D30" in df_osc.columns and df_osc["delta_D30"].notna().any()

if not has_d1 and not has_d7 and not has_d30:
    st.info("Sem comparações disponíveis. Verifique se há snapshots históricos suficientes.")
    st.stop()

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

kc1, kc2, kc3, kc4 = st.columns(4)
kc1.metric("Posições encontradas", f"{len(df_osc)} / {len(portfolio_map)}")
if total_portfolio >= 1e9:
    vol_label = f"R$ {total_portfolio/1e9:.2f}B"
else:
    vol_label = f"R$ {total_portfolio/1e6:.1f}M"
kc2.metric("Volume total (filtrado)", vol_label)
_top = df_osc.nlargest(1, "volume")
kc3.metric("Maior exposição", _top["ticker"].iloc[0] if len(_top) else "—")
kc4.metric("Não encontrados", len(not_found))

if not_found:
    with st.expander(f"⚠️ {len(not_found)} tickers da planilha não encontrados na base", expanded=False):
        st.write(", ".join(not_found))

st.divider()

# ---------------------------------------------------------------------------
# Renderização de cada janela de período
# ---------------------------------------------------------------------------

_taxa_label     = "Spread atual" if spread_mode else "Taxa atual"
_taxa_past_base = "Spread"        if spread_mode else "Taxa"


def _apply_gradient(col: pd.Series) -> list[str]:
    """Gradiente branco→verde (alta) / branco→vermelho (queda) proporcional ao max absoluto."""
    non_null = col.dropna()
    max_abs = non_null.abs().max() if not non_null.empty else 0
    if max_abs == 0:
        return ["" for _ in col]
    styles = []
    for v in col:
        if pd.isna(v):
            styles.append("")
            continue
        t = min(abs(v) / max_abs, 1.0)
        if v > 0:
            # taxa subiu → risco aumentou → vermelho
            r_c = int(255 - t * 75)
            g_c = int(255 - t * 225)
            b_c = int(255 - t * 225)
        else:
            # taxa caiu → risco diminuiu → verde
            r_c = int(255 - t * 235)
            g_c = int(255 - t * 145)
            b_c = int(255 - t * 200)
        styles.append(
            f"background-color: rgb({r_c},{g_c},{b_c}); "
            f"color: {'white' if t > 0.55 else 'black'}"
        )
    return styles


def _render_period(delta_col: str, taxa_past_col: str, data_col: str | None, label: str) -> None:
    df_w = df_osc[df_osc[delta_col].notna()].copy()
    if df_w.empty:
        st.info(f"Sem dados comparativos para {label}.")
        return

    # Data de referência do snapshot passado
    if data_col and data_col in df_w.columns:
        ref_raw = df_w[data_col].iloc[0]
        try:
            from datetime import datetime as _dt
            ref_fmt = _dt.strptime(str(ref_raw), "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            ref_fmt = str(ref_raw)
        st.caption(f"Comparando com snapshot de **{ref_fmt}**")

    taxa_past_label = f"{_taxa_past_base} {label}"

    # ----------------------------------------------------------------
    # Modo: Por Ticker
    # ----------------------------------------------------------------
    if view_mode == "Por Ticker":
        if direction == "Só altas":
            df_w = df_w[df_w[delta_col] > 0].copy()
        elif direction == "Só quedas":
            df_w = df_w[df_w[delta_col] < 0].copy()

        if df_w.empty:
            st.info("Nenhum ativo nesta direção com os filtros aplicados.")
            return

        if sort_by == "Variação (|Δ|)":
            df_w = df_w.reindex(df_w[delta_col].abs().sort_values(ascending=False).index)
        else:
            df_w = df_w.sort_values("volume", ascending=False)

        rows = []
        for _, r in df_w.iterrows():
            dv = r[delta_col]
            rows.append({
                "Ticker":        r["ticker"],
                "Emissor":       str(r.get("issuer", "") or "—"),
                "Indexador":     str(r.get("indexer_label", r.get("indexer", "")) or "—"),
                "Vencimento":    str(r.get("vencimento", "") or "—"),
                _taxa_label:     r.get("taxa_hoje"),
                taxa_past_label: r.get(taxa_past_col),
                "Δ p.p.":        dv,
                "↑↓":            "▲" if dv > 0 else ("▼" if dv < 0 else "─"),
                "Volume (R$)":   _fmt_brl(r.get("volume")),
                "% Carteira":    r.get("pct_portfolio"),
            })

        df_tbl = pd.DataFrame(rows)
        styled = (
            df_tbl.style
            .apply(_apply_gradient, subset=["Δ p.p."])
            .format({
                "Δ p.p.": lambda v: f"{v:+.3f}" if pd.notna(v) else "—",
                _taxa_label:     lambda v: f"{v:.3f}" if pd.notna(v) else "—",
                taxa_past_label: lambda v: f"{v:.3f}" if pd.notna(v) else "—",
                "% Carteira":    lambda v: f"{v:.2f}%" if pd.notna(v) else "—",
            })
        )
        col_cfg: dict = {
            "Ticker":      st.column_config.TextColumn("Ticker",      width="small"),
            "Emissor":     st.column_config.TextColumn("Emissor"),
            "Indexador":   st.column_config.TextColumn("Indexador",   width="small"),
            "Vencimento":  st.column_config.TextColumn("Vencimento",  width="small"),
            "↑↓":          st.column_config.TextColumn("↑↓",          width="small"),
            "Volume (R$)": st.column_config.TextColumn("Volume (R$)", width="medium"),
        }
        st.dataframe(styled, hide_index=True, use_container_width=True, column_config=col_cfg)

        n_altas  = int((df_w[delta_col] > 0).sum())
        n_quedas = int((df_w[delta_col] < 0).sum())
        rs1, rs2, rs3, rs4 = st.columns(4)
        rs1.metric("Ativos em alta",  n_altas)
        rs2.metric("Volume em alta",  _fmt_volume(df_w.loc[df_w[delta_col] > 0, "volume"].sum()))
        rs3.metric("Ativos em queda", n_quedas)
        rs4.metric("Volume em queda", _fmt_volume(df_w.loc[df_w[delta_col] < 0, "volume"].sum()))

    # ----------------------------------------------------------------
    # Modo: Por Emissor
    # ----------------------------------------------------------------
    else:
        agg_rows: list[dict] = []
        for (iss, idx_lbl), grp in df_w.groupby(["issuer", "indexer_label"], sort=False):
            total_vol = float(grp["volume"].sum())
            w = grp["volume"] / total_vol if total_vol > 0 else pd.Series(
                [1.0 / len(grp)] * len(grp), index=grp.index
            )

            def _wavg(series: pd.Series) -> float | None:
                valid = series.notna()
                if not valid.any():
                    return None
                return float((series.fillna(0) * w).sum())

            dv = _wavg(grp[delta_col])
            agg_rows.append({
                "Emissor":       str(iss) or "—",
                "Indexador":     str(idx_lbl) or "—",
                "Nº Ativos":     len(grp),
                "Tickers":       ", ".join(sorted(grp["ticker"].tolist())),
                _taxa_label:     _wavg(grp["taxa_hoje"]),
                taxa_past_label: _wavg(grp.get(taxa_past_col, pd.Series(dtype=float))),
                "Δ p.p. (méd.)": dv,
                "↑↓":            "▲" if (dv or 0) > 0 else ("▼" if (dv or 0) < 0 else "─"),
                "_vol_num":      total_vol,
                "Volume (R$)":   _fmt_brl(total_vol),
                "% Carteira":    float(grp["pct_portfolio"].sum()),
            })

        if not agg_rows:
            st.info("Sem emissores com dados neste período.")
            return

        df_grp = pd.DataFrame(agg_rows)

        # Filtro de direção (sobre a média ponderada)
        if direction == "Só altas":
            df_grp = df_grp[df_grp["Δ p.p. (méd.)"] > 0].copy()
        elif direction == "Só quedas":
            df_grp = df_grp[df_grp["Δ p.p. (méd.)"] < 0].copy()

        if df_grp.empty:
            st.info("Nenhum emissor nesta direção com os filtros aplicados.")
            return

        if sort_by == "Variação (|Δ|)":
            df_grp = df_grp.reindex(df_grp["Δ p.p. (méd.)"].abs().sort_values(ascending=False).index)
        else:
            df_grp = df_grp.sort_values("_vol_num", ascending=False)

        df_grp = df_grp.drop(columns=["_vol_num"])

        styled = (
            df_grp.style
            .apply(_apply_gradient, subset=["Δ p.p. (méd.)"])
            .format({
                "Δ p.p. (méd.)": lambda v: f"{v:+.3f}" if pd.notna(v) else "—",
                _taxa_label:      lambda v: f"{v:.3f}" if pd.notna(v) else "—",
                taxa_past_label:  lambda v: f"{v:.3f}" if pd.notna(v) else "—",
                "% Carteira":     lambda v: f"{v:.2f}%" if pd.notna(v) else "—",
            })
        )
        col_cfg_grp: dict = {
            "Emissor":          st.column_config.TextColumn("Emissor"),
            "Indexador":        st.column_config.TextColumn("Indexador",   width="small"),
            "Nº Ativos":        st.column_config.NumberColumn("Nº Ativos",  width="small"),
            "Tickers":          st.column_config.TextColumn("Tickers"),
            "↑↓":               st.column_config.TextColumn("↑↓",           width="small"),
            "Volume (R$)":      st.column_config.TextColumn("Volume (R$)",  width="medium"),
        }
        st.dataframe(styled, hide_index=True, use_container_width=True, column_config=col_cfg_grp)
        st.caption(
            "⚠️ Oscilação média ponderada por volume. "
            "Taxa/Spread são médias ponderadas de todos os tickers do emissor com mesmo indexador."
        )

        n_altas  = int((df_grp["Δ p.p. (méd.)"] > 0).sum())
        n_quedas = int((df_grp["Δ p.p. (méd.)"] < 0).sum())
        rs1, rs2, rs3, rs4 = st.columns(4)
        rs1.metric("Emissores em alta",  n_altas)
        rs2.metric("Volume em alta",     _fmt_volume(df_grp.loc[df_grp["Δ p.p. (méd.)"] > 0, "% Carteira"].sum() / 100 * total_portfolio))
        rs3.metric("Emissores em queda", n_quedas)
        rs4.metric("Volume em queda",    _fmt_volume(df_grp.loc[df_grp["Δ p.p. (méd.)"] < 0, "% Carteira"].sum() / 100 * total_portfolio))


# ---------------------------------------------------------------------------
# Tabs por período
# ---------------------------------------------------------------------------

_tab_labels: list[str] = []
if has_d1:
    _tab_labels.append("🗓️ D-1 (dia anterior)")
if has_d7:
    _tab_labels.append("📆 D-7 (semana anterior)")
if has_d30:
    _tab_labels.append("📅 D-30 (mês anterior)")

_tabs = st.tabs(_tab_labels)
_ti = 0

if has_d1:
    with _tabs[_ti]:
        _render_period(
            "delta_D1", "taxa_D1",
            "data_D1" if "data_D1" in df_osc.columns else None,
            "D-1",
        )
    _ti += 1

if has_d7:
    with _tabs[_ti]:
        _render_period(
            "delta_D7", "taxa_D7",
            "data_D7" if "data_D7" in df_osc.columns else None,
            "D-7",
        )
    _ti += 1

if has_d30:
    with _tabs[_ti]:
        _render_period(
            "delta_D30", "taxa_D30",
            "data_D30" if "data_D30" in df_osc.columns else None,
            "D-30",
        )

st.divider()
st.caption(
    "Baseado em snapshots diários salvos automaticamente. "
    "Tickers não encontrados na base de dados são ignorados. "
    "Não constitui recomendação de investimento."
)
