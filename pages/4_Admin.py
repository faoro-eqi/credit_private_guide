"""
Página administrativa — gerenciamento de snapshots.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.benchmarks import (
    fetch_anbima_for_date,
    save_benchmark_for_date,
    available_benchmark_dates,
)
from src.snapshots import available_dates, save_snapshot, DB_PATH
from src.overrides import (
    get_all_overrides,
    set_sector_bulk,
    is_unclassified,
    apply_sector_overrides,
)
import sqlite3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot_status() -> pd.DataFrame:
    """Retorna tabela de status de todos os snapshots."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    asset_rows = conn.execute(
        "SELECT snapshot_date, COUNT(*) as ativos FROM snapshots GROUP BY snapshot_date ORDER BY snapshot_date DESC"
    ).fetchall()
    bm_rows = conn.execute(
        """SELECT snapshot_date,
            SUM(CASE WHEN benchmark_type='ntnb' THEN 1 ELSE 0 END) as ntnb,
            SUM(CASE WHEN benchmark_type='pre' THEN 1 ELSE 0 END) as pre
           FROM benchmark_snapshots GROUP BY snapshot_date ORDER BY snapshot_date DESC"""
    ).fetchall()
    conn.close()

    asset_df = pd.DataFrame(asset_rows, columns=["data", "ativos"])
    bm_df = pd.DataFrame(bm_rows, columns=["data", "ntnb_vertices", "pre_vertices"])

    all_dates = sorted(
        set(asset_df["data"].tolist() + bm_df["data"].tolist()), reverse=True
    )
    rows = []
    for d in all_dates:
        a = asset_df[asset_df["data"] == d]["ativos"].values
        n = bm_df[bm_df["data"] == d]["ntnb_vertices"].values
        p = bm_df[bm_df["data"] == d]["pre_vertices"].values
        rows.append({
            "Data": d,
            "Ativos": int(a[0]) if len(a) else "—",
            "NTN-B (vértices)": int(n[0]) if len(n) else "—",
            "Pré/LTN (vértices)": int(p[0]) if len(p) else "—",
        })
    return pd.DataFrame(rows)


def _import_asset_csv(df_csv: pd.DataFrame, date_str: str, overwrite: bool) -> tuple[int, str]:
    """Importa DataFrame de ativos para o banco."""
    REQUIRED = {"ticker", "indexer", "taxa_media"}
    missing = REQUIRED - set(df_csv.columns)
    if missing:
        return 0, f"Colunas obrigatórias ausentes: {missing}"

    if not DB_PATH.exists():
        return 0, "Banco de dados não encontrado."

    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE snapshot_date = ?", (date_str,)
    ).fetchone()[0]
    if existing > 0 and not overwrite:
        conn.close()
        return 0, f"Já existem {existing} ativos para {date_str}. Marque 'Substituir' para sobrescrever."
    if existing > 0 and overwrite:
        conn.execute("DELETE FROM snapshots WHERE snapshot_date = ?", (date_str,))
        conn.commit()

    OPTIONAL = ["class_type", "nome", "setor", "taxa_media_str", "duration_years", "vencimento"]
    cols_to_save = ["ticker", "indexer", "taxa_media"] + [c for c in OPTIONAL if c in df_csv.columns]
    rows = df_csv[cols_to_save].copy()
    rows["snapshot_date"] = date_str
    rows.to_sql("snapshots", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    return len(rows), f"✅ {len(rows)} ativos importados para {date_str}."


def _csv_template() -> bytes:
    template = pd.DataFrame([
        {
            "ticker": "CRI11X0001",
            "indexer": "IPCA",
            "class_type": "CRI",
            "nome": "Exemplo Emissora S.A.",
            "setor": "Imobiliário",
            "taxa_media": 7.25,
            "taxa_media_str": "IPCA + 7.25% a.a.",
            "duration_years": 4.5,
            "vencimento": "2030-06-15",
        },
        {
            "ticker": "DEB22Y0001",
            "indexer": "CDI+",
            "class_type": "Debênture",
            "nome": "Exemplo CDI Emissora",
            "setor": "Energia",
            "taxa_media": 1.80,
            "taxa_media_str": "CDI + 1.80% a.a.",
            "duration_years": 2.1,
            "vencimento": "2028-03-01",
        },
    ])
    return template.to_csv(index=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("⚙️ Admin — Snapshots")
st.caption("Gerencie o histórico de taxas para cálculo de oscilações e spread.")

# ── Atualizar Token ──────────────────────────────────────────────────────────
st.header("🔑 Atualizar token DATA_B3")

_ENV_PATH = Path(__file__).parent.parent / ".env"
_current_token = os.environ.get("DATA_B3_TOKEN", "")
if _current_token:
    st.caption(f"Token atual: `{_current_token[:20]}…{_current_token[-10:]}`")
else:
    st.warning("Nenhum token configurado.")

new_token = st.text_area(
    "Cole o novo token JWT aqui",
    height=100,
    placeholder="eyJ0eXAiOiJKV1Qi…",
    key="new_token_input",
)

col_t1, col_t2 = st.columns(2)

with col_t1:
    if st.button("💾 Salvar token", use_container_width=True, key="save_token_btn"):
        token = (new_token or "").strip()
        if not token:
            st.error("Cole o token antes de salvar.")
        else:
            # Atualiza variável de ambiente em memória
            os.environ["DATA_B3_TOKEN"] = token
            # Persiste no .env
            env_text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
            import re as _re
            if _re.search(r"^DATA_B3_TOKEN=", env_text, _re.MULTILINE):
                env_text = _re.sub(r"^DATA_B3_TOKEN=.*$", f"DATA_B3_TOKEN={token}", env_text, flags=_re.MULTILINE)
            else:
                env_text = env_text.rstrip("\n") + f"\nDATA_B3_TOKEN={token}\n"
            _ENV_PATH.write_text(env_text)
            st.success("Token salvo! Agora clique em **Buscar dados e salvar snapshot**.")
            st.rerun()

with col_t2:
    if st.button("🔄 Buscar dados e salvar snapshot", use_container_width=True, key="fetch_snapshot_btn"):
        if not os.environ.get("DATA_B3_TOKEN"):
            st.error("Salve o token primeiro.")
        else:
            import src.data_sources as _ds
            _ds.DATA_B3_TOKEN = os.environ["DATA_B3_TOKEN"]
            st.cache_data.clear()
            with st.spinner("Buscando dados da API…"):
                try:
                    from src.data_sources import load_market_df
                    df_fresh = load_market_df()
                except Exception as e:
                    st.error(f"Erro ao buscar dados: {e}")
                    df_fresh = pd.DataFrame()
            if df_fresh.empty:
                st.error("Retornou vazio. Verifique o token.")
            else:
                from src.snapshots import save_snapshot
                saved = save_snapshot(df_fresh)
                if saved:
                    st.success(f"✅ Snapshot salvo: {len(df_fresh)} ativos para hoje ({date.today()}).")
                else:
                    st.info(f"Snapshot de hoje já existia ({len(df_fresh)} ativos). Dados atualizados em cache.")
                st.rerun()

st.divider()

st.header("📊 Status dos snapshots")

status_df = _snapshot_status()
if status_df.empty:
    st.info("Nenhum snapshot encontrado.")
else:
    def _color(val):
        if val == "—":
            return "color: #cc6600"
        return ""
    st.dataframe(
        status_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Data": st.column_config.TextColumn(width="small"),
            "Ativos": st.column_config.NumberColumn(format="%d"),
            "NTN-B (vértices)": st.column_config.TextColumn(width="medium"),
            "Pré/LTN (vértices)": st.column_config.TextColumn(width="medium"),
        },
    )

st.divider()

# ── Backfill Benchmarks ──────────────────────────────────────────────────────
st.header("📥 Backfill de benchmarks (ANBIMA histórico)")
st.markdown(
    "Busca os dados de NTN-B e LTN/NTN-F no arquivo público da ANBIMA para uma data específica. "
    "Útil para preencher lacunas e habilitar o spread D-1 / D-7."
)

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    bm_dates_existing = set(available_benchmark_dates())
    # sugestão: preenche os últimos 7 dias que faltam
    suggestions = []
    for i in range(1, 8):
        d = str(date.today() - timedelta(days=i))
        if d not in bm_dates_existing:
            suggestions.append(d)

    bm_date = st.date_input(
        "Data alvo",
        value=date.today() - timedelta(days=1),
        max_value=date.today(),
        key="bm_date",
    )
with col2:
    overwrite_bm = st.checkbox("Substituir se já existir", key="overwrite_bm")
with col3:
    st.write("")  # espaçamento
    fetch_btn = st.button("⬇️ Buscar e salvar", use_container_width=True, key="fetch_bm")

if suggestions:
    st.caption(f"Datas sem benchmark ainda: {', '.join(suggestions[:5])}")

if fetch_btn:
    with st.spinner(f"Buscando ANBIMA para {bm_date}…"):
        ntnb_df, pre_df = fetch_anbima_for_date(str(bm_date))
    if ntnb_df.empty and pre_df.empty:
        st.error(f"Não foi possível obter dados da ANBIMA para {bm_date} (tentou 5 dias anteriores também).")
    else:
        ok, msg = save_benchmark_for_date(str(bm_date), ntnb_df, pre_df, overwrite=overwrite_bm)
        if ok:
            st.success(msg)
            st.markdown(
                f"- **NTN-B**: {len(ntnb_df)} vértices  \n"
                f"- **LTN/NTN-F**: {len(pre_df)} vértices"
            )
        else:
            st.warning(msg)
        st.rerun()

# Botão de backfill em lote para os últimos N dias
with st.expander("🔄 Backfill em lote (últimos dias)"):
    n_days = st.number_input("Quantos dias atrás?", min_value=1, max_value=30, value=5, step=1)
    overwrite_bulk = st.checkbox("Substituir existentes", key="overwrite_bulk")
    if st.button("⬇️ Buscar todos", key="bulk_backfill"):
        progress = st.progress(0, text="Iniciando…")
        results = []
        for i, offset in enumerate(range(1, int(n_days) + 1)):
            d_str = str(date.today() - timedelta(days=offset))
            progress.progress((i + 1) / n_days, text=f"Buscando {d_str}…")
            ntnb_df, pre_df = fetch_anbima_for_date(d_str)
            if ntnb_df.empty and pre_df.empty:
                results.append(f"❌ {d_str} — sem dados na ANBIMA")
            else:
                ok, msg = save_benchmark_for_date(d_str, ntnb_df, pre_df, overwrite=overwrite_bulk)
                results.append(f"{'✅' if ok else '⚠️'} {d_str} — {msg}")
        progress.empty()
        for r in results:
            st.markdown(r)
        st.rerun()

st.divider()

# ── Importar Ativos via CSV ──────────────────────────────────────────────────
st.header("📤 Importar ativos via CSV")
st.markdown(
    "Faça o upload de um CSV com as taxas de mercado. "
    "Use o template abaixo para garantir o formato correto."
)

st.download_button(
    "📋 Baixar template CSV",
    data=_csv_template(),
    file_name="template_snapshot.csv",
    mime="text/csv",
)

col_up1, col_up2, col_up3 = st.columns([2, 1, 1])
with col_up1:
    csv_date = st.date_input(
        "Data dos dados",
        value=date.today() - timedelta(days=1),
        max_value=date.today(),
        key="csv_date",
    )
with col_up2:
    overwrite_csv = st.checkbox("Substituir se já existir", key="overwrite_csv")
with col_up3:
    st.write("")
    uploaded = st.file_uploader("", type=["csv"], key="csv_upload", label_visibility="collapsed")

if uploaded is not None:
    try:
        df_csv = pd.read_csv(uploaded)
        st.subheader("Pré-visualização")
        st.dataframe(df_csv.head(10), use_container_width=True, hide_index=True)
        st.caption(f"{len(df_csv)} linhas · colunas: {list(df_csv.columns)}")

        if st.button("💾 Importar para o banco", key="import_csv"):
            n, msg = _import_asset_csv(df_csv, str(csv_date), overwrite=overwrite_csv)
            if n > 0:
                st.success(msg)
            else:
                st.error(msg)
            st.rerun()
    except Exception as e:
        st.error(f"Erro ao ler CSV: {e}")

st.divider()

# ── Classificar Setores ──────────────────────────────────────────────────────
st.header("🏷️ Classificar Setores")
st.markdown(
    "Ativos com setor **Não informado** ou **None** aparecem aqui para classificação. "
    "O setor salvo é aplicado automaticamente toda vez que o dashboard carrega."
)

# Lista de setores conhecidos (banco + overrides)
@st.cache_data(ttl=60)
def _known_sectors() -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT DISTINCT setor FROM (
            SELECT setor FROM snapshots
            UNION ALL
            SELECT setor FROM sector_overrides
        ) WHERE setor IS NOT NULL AND LOWER(setor) NOT IN ('não informado','none','nan','')
        ORDER BY setor"""
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

# Ativos sem setor — pega ticker+nome mais recentes
@st.cache_data(ttl=60)
def _unclassified_assets() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    # Pega o snapshot mais recente por ticker
    df = pd.read_sql("""
        SELECT s.ticker, s.nome, s.setor, s.indexer, s.class_type
        FROM snapshots s
        INNER JOIN (
            SELECT ticker, MAX(snapshot_date) as last_date
            FROM snapshots GROUP BY ticker
        ) m ON s.ticker = m.ticker AND s.snapshot_date = m.last_date
        WHERE s.setor IS NULL OR LOWER(s.setor) IN ('não informado','none','nan','')
        ORDER BY s.nome
    """, conn)
    conn.close()
    # Exclui os que já foram classificados via override
    overrides = get_all_overrides()
    if not overrides.empty:
        df = df[~df["ticker"].isin(overrides["ticker"])]
    return df

sectors = _known_sectors()
df_unclass = _unclassified_assets()

col_info1, col_info2 = st.columns(2)
col_info1.metric("Sem setor (pendentes)", len(df_unclass))
col_info2.metric("Setores cadastrados", len(sectors))

if df_unclass.empty:
    st.success("✅ Todos os ativos estão classificados!")
else:
    # Filtro de busca
    search = st.text_input("🔍 Filtrar por ticker ou nome", key="setor_search", placeholder="ex: SMTO, Energia...")
    df_filtered = df_unclass.copy()
    if search.strip():
        q = search.strip().lower()
        df_filtered = df_filtered[
            df_filtered["ticker"].str.lower().str.contains(q) |
            df_filtered["nome"].str.lower().str.contains(q)
        ]

    st.caption(f"Exibindo {len(df_filtered)} de {len(df_unclass)} ativos pendentes")

    # Formulário de edição em batch
    with st.form("setor_form"):
        sector_options = ["— selecionar —"] + sorted(sectors) + ["➕ Novo setor..."]
        changes: list[dict] = []

        # Renderiza N ativos por vez
        PAGE_SIZE = 20
        page = st.session_state.get("setor_page", 0)
        total_pages = max(1, (len(df_filtered) - 1) // PAGE_SIZE + 1)
        df_page = df_filtered.iloc[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

        cols_header = st.columns([2, 4, 2, 3, 3])
        for c, h in zip(cols_header, ["Ticker", "Nome", "Tipo", "Indexador", "Setor"]):
            c.markdown(f"**{h}**")
        st.markdown("---")

        row_data = []
        for _, row in df_page.iterrows():
            c1, c2, c3, c4, c5 = st.columns([2, 4, 2, 3, 3])
            c1.write(row["ticker"])
            c2.write(row["nome"] or "—")
            c3.write(row.get("class_type", "—"))
            c4.write(row.get("indexer", "—"))
            sel = c5.selectbox(
                "",
                options=sector_options,
                key=f"sel_{row['ticker']}",
                label_visibility="collapsed",
            )
            row_data.append({"ticker": row["ticker"], "nome": row["nome"], "sel": sel})

        # Campo para novo setor customizado
        new_sector_name = st.text_input(
            "Novo setor (se selecionou '➕ Novo setor...' acima):",
            key="new_sector_input",
            placeholder="ex: Agronegócio",
        )

        submitted = st.form_submit_button("💾 Salvar classificações", use_container_width=True, type="primary")
        if submitted:
            for rd in row_data:
                setor_val = rd["sel"]
                if setor_val == "➕ Novo setor..." and new_sector_name.strip():
                    setor_val = new_sector_name.strip()
                elif setor_val == "— selecionar —" or setor_val == "➕ Novo setor...":
                    continue  # não alterado
                changes.append({"ticker": rd["ticker"], "setor": setor_val, "nome": rd["nome"]})

            if changes:
                n = set_sector_bulk(changes)
                st.success(f"✅ {n} ativos classificados com sucesso!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.info("Nenhuma alteração — selecione um setor para ao menos um ativo.")

    # Paginação
    p1, p2, p3 = st.columns([1, 2, 1])
    if p1.button("◀ Anterior", disabled=(page == 0), key="prev_page"):
        st.session_state["setor_page"] = max(0, page - 1)
        st.rerun()
    p2.caption(f"Página {page + 1} de {total_pages}")
    if p3.button("Próxima ▶", disabled=(page >= total_pages - 1), key="next_page"):
        st.session_state["setor_page"] = min(total_pages - 1, page + 1)
        st.rerun()

st.divider()

# ── Overrides existentes ─────────────────────────────────────────────────────
with st.expander("✏️ Editar classificações já salvas", expanded=False):
    df_ov = get_all_overrides()
    if df_ov.empty:
        st.info("Nenhum override salvo ainda.")
    else:
        search_ov = st.text_input("🔍 Filtrar por ticker ou setor", key="ov_search", placeholder="ticker ou setor...")
        df_ov_filtered = df_ov.copy()
        if search_ov.strip():
            q = search_ov.strip().lower()
            df_ov_filtered = df_ov_filtered[
                df_ov_filtered["ticker"].str.lower().str.contains(q) |
                df_ov_filtered["setor"].str.lower().str.contains(q)
            ]

        st.caption(f"{len(df_ov_filtered)} de {len(df_ov)} classificações")

        OV_PAGE_SIZE = 20
        ov_page = st.session_state.get("ov_page", 0)
        ov_total_pages = max(1, (len(df_ov_filtered) - 1) // OV_PAGE_SIZE + 1)
        df_ov_page = df_ov_filtered.iloc[ov_page * OV_PAGE_SIZE : (ov_page + 1) * OV_PAGE_SIZE]

        with st.form("edit_overrides_form"):
            sector_options_ov = sorted(sectors) + ["➕ Novo setor..."]

            cols_h = st.columns([2, 4, 3, 3])
            for c, h in zip(cols_h, ["Ticker", "Nome", "Setor atual", "Novo setor"]):
                c.markdown(f"**{h}**")
            st.markdown("---")

            edit_rows = []
            for _, row in df_ov_page.iterrows():
                c1, c2, c3, c4 = st.columns([2, 4, 3, 3])
                c1.write(row["ticker"])
                c2.write(row.get("nome") or "—")
                c3.write(row["setor"])
                # Selectbox pré-selecionado com o setor atual
                current_idx = sector_options_ov.index(row["setor"]) if row["setor"] in sector_options_ov else 0
                sel = c4.selectbox(
                    "",
                    options=sector_options_ov,
                    index=current_idx,
                    key=f"ov_sel_{row['ticker']}",
                    label_visibility="collapsed",
                )
                edit_rows.append({"ticker": row["ticker"], "nome": row.get("nome"), "setor_atual": row["setor"], "sel": sel})

            new_ov_sector = st.text_input(
                "Novo setor (se selecionou '➕ Novo setor...' acima):",
                key="new_ov_sector_input",
                placeholder="ex: Agronegócio",
            )

            col_save, col_del = st.columns(2)
            save_edits = col_save.form_submit_button("💾 Salvar alterações", use_container_width=True, type="primary")

            if save_edits:
                changes_ov = []
                for rd in edit_rows:
                    setor_val = rd["sel"]
                    if setor_val == "➕ Novo setor..." and new_ov_sector.strip():
                        setor_val = new_ov_sector.strip()
                    elif setor_val == "➕ Novo setor...":
                        continue
                    if setor_val != rd["setor_atual"]:
                        changes_ov.append({"ticker": rd["ticker"], "setor": setor_val, "nome": rd["nome"]})
                if changes_ov:
                    set_sector_bulk(changes_ov)
                    st.success(f"✅ {len(changes_ov)} classificação(ões) atualizada(s).")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.info("Nenhuma alteração detectada.")

        # Paginação
        op1, op2, op3 = st.columns([1, 2, 1])
        if op1.button("◀ Anterior", disabled=(ov_page == 0), key="ov_prev"):
            st.session_state["ov_page"] = max(0, ov_page - 1)
            st.rerun()
        op2.caption(f"Página {ov_page + 1} de {ov_total_pages}")
        if op3.button("Próxima ▶", disabled=(ov_page >= ov_total_pages - 1), key="ov_next"):
            st.session_state["ov_page"] = min(ov_total_pages - 1, ov_page + 1)
            st.rerun()

