"""
Persistência de snapshots diários de taxas para análise de oscilações.
Armazena em SQLite: data/market_snapshots.db
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "market_snapshots.db"

_COLS = ["ticker", "indexer", "class_type", "nome", "setor", "taxa_media", "taxa_media_str", "duration_years", "vencimento"]


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_date  TEXT NOT NULL,
            ticker         TEXT NOT NULL,
            indexer        TEXT,
            class_type     TEXT,
            nome           TEXT,
            setor          TEXT,
            taxa_media     REAL,            taxa_media_str TEXT,            duration_years REAL,
            vencimento     TEXT,
            PRIMARY KEY (snapshot_date, ticker)
        )
    """)
    conn.commit()
    # migration: add taxa_media_str if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()]
    if "taxa_media_str" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN taxa_media_str TEXT")
        conn.commit()
    return conn


def save_snapshot(df: pd.DataFrame, today: str | None = None) -> bool:
    """Salva snapshot do dia. Retorna True se salvou, False se já existia."""
    if df.empty:
        return False
    today = today or str(date.today())
    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE snapshot_date = ?", (today,)
    ).fetchone()[0]
    if existing > 0:
        conn.close()
        return False  # já salvo hoje
    rows = df[[c for c in _COLS if c in df.columns]].copy()
    rows["snapshot_date"] = today
    rows.to_sql("snapshots", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    return True


def available_dates() -> list[str]:
    """Lista todas as datas com snapshots disponíveis (mais recente primeiro)."""
    if not DB_PATH.exists():
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM snapshots ORDER BY snapshot_date DESC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def load_snapshot(target_date: str) -> pd.DataFrame:
    """Carrega snapshot de uma data específica (YYYY-MM-DD)."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _get_conn()
    df = pd.read_sql(
        "SELECT * FROM snapshots WHERE snapshot_date = ?",
        conn,
        params=(target_date,),
    )
    conn.close()
    return df


def _closest_date_before(target: str, dates: list[str]) -> str | None:
    """Retorna a data disponível mais próxima <= target."""
    past = [d for d in dates if d <= target]
    return past[0] if past else None  # já ordenado DESC


def get_oscillations(df_today: pd.DataFrame, days_back: list[int], reference_date: str | None = None) -> pd.DataFrame:
    """
    Junta df_today com snapshots de D-n para cada n em days_back.
    reference_date: data base para calcular D-n (default: hoje).
    Retorna DataFrame com taxa_hoje, taxa_Dn, delta_Dn para cada n.
    """
    _cols = ["ticker", "indexer", "indexer_label", "class_type", "nome", "setor", "taxa_media", "duration_years"]
    if "vencimento" in df_today.columns:
        _cols.append("vencimento")
    result = df_today[_cols].copy()
    result = result.rename(columns={"taxa_media": "taxa_hoje"})

    ref = date.fromisoformat(reference_date) if reference_date else date.today()
    dates = available_dates()
    for n in days_back:
        target = str(ref - timedelta(days=n))
        closest = _closest_date_before(target, dates)
        if not closest:
            continue
        df_past = load_snapshot(closest)
        if df_past.empty:
            continue
        df_past = df_past[["ticker", "taxa_media", "snapshot_date"]].rename(
            columns={"taxa_media": f"taxa_D{n}", "snapshot_date": f"data_D{n}"}
        )
        result = result.merge(df_past, on="ticker", how="left")
        result[f"delta_D{n}"] = result["taxa_hoje"] - result[f"taxa_D{n}"]

    return result


def get_spread_oscillations(
    df_today: pd.DataFrame,
    days_back: list[int],
    indexer_val: str,
    benchmark_type: str,
    reference_date: str | None = None,
) -> pd.DataFrame:
    """
    Calcula oscilações de spread (taxa - benchmark_interpolado) para D-n.
    reference_date: data base para calcular D-n (default: hoje).
    Requer que benchmarks tenham sido salvos em benchmark_snapshots.
    """
    from src.benchmarks import add_spread_column, load_benchmark_snapshot

    ref = date.fromisoformat(reference_date) if reference_date else date.today()
    # Spread na data de referência
    bdf_today = load_benchmark_snapshot(reference_date or str(date.today()), benchmark_type)
    if bdf_today.empty:
        return pd.DataFrame()

    df_idx = df_today[df_today["indexer"] == indexer_val].copy()
    if df_idx.empty:
        return pd.DataFrame()

    df_idx = add_spread_column(df_idx, bdf_today, spread_col="spread_hoje", benchmark_rate_col="bm_hoje")

    _scols = ["ticker", "indexer", "indexer_label", "class_type", "nome", "setor",
              "taxa_media", "duration_years", "spread_hoje", "bm_hoje"]
    if "vencimento" in df_idx.columns:
        _scols.append("vencimento")
    result = df_idx[_scols].copy()

    dates = available_benchmark_dates_internal()
    for n in days_back:
        target = str(ref - timedelta(days=n))
        past_dates = [d for d in dates if d <= target]
        if not past_dates:
            continue
        closest = past_dates[0]
        bdf_past = load_benchmark_snapshot(closest, benchmark_type)
        if bdf_past.empty:
            continue

        # carrega taxas passadas dos ativos
        from src.snapshots import load_snapshot as _ls, available_dates, _closest_date_before
        asset_dates = available_dates()
        asset_closest = _closest_date_before(target, asset_dates)
        if not asset_closest:
            continue
        df_asset_past = _ls(asset_closest)
        if df_asset_past.empty:
            continue

        df_asset_past = df_asset_past[df_asset_past["indexer"] == indexer_val][
            ["ticker", "taxa_media", "duration_years"]
        ].copy()
        df_asset_past = add_spread_column(df_asset_past, bdf_past, spread_col=f"spread_D{n}", benchmark_rate_col=f"bm_D{n}")
        df_asset_past = df_asset_past[["ticker", f"spread_D{n}"]].rename(columns={"ticker": "ticker"})

        result = result.merge(df_asset_past, on="ticker", how="left")
        result[f"delta_spread_D{n}"] = result["spread_hoje"] - result[f"spread_D{n}"]

    return result


def list_all_tickers() -> list[str]:
    """Retorna lista de tickers distintos presentes em qualquer snapshot, ordenada."""
    if not DB_PATH.exists():
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM snapshots WHERE ticker IS NOT NULL ORDER BY ticker"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def list_tickers_with_names() -> dict[str, str]:
    """
    Retorna dict {ticker: nome} usando o valor mais recente de `nome` por ticker.
    Tickers sem nome ficam com string vazia.
    """
    if not DB_PATH.exists():
        return {}
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT ticker, nome
        FROM snapshots
        WHERE ticker IS NOT NULL
          AND snapshot_date = (
              SELECT MAX(s2.snapshot_date)
              FROM snapshots s2
              WHERE s2.ticker = snapshots.ticker
          )
        ORDER BY ticker
        """
    ).fetchall()
    conn.close()
    return {r[0]: (r[1] or "") for r in rows}


def get_ticker_history(ticker: str) -> pd.DataFrame:
    """
    Retorna todas as entradas de um ticker ao longo dos snapshots, ordenadas por data.
    Colunas: snapshot_date, taxa_media, taxa_media_str, duration_years, nome, setor,
             indexer, class_type, vencimento
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _get_conn()
    df = pd.read_sql(
        "SELECT * FROM snapshots WHERE ticker = ? ORDER BY snapshot_date ASC",
        conn,
        params=(ticker,),
    )
    conn.close()
    return df


def available_benchmark_dates_internal() -> list[str]:
    """Lista datas disponíveis em benchmark_snapshots."""
    if not DB_PATH.exists():
        return []
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT snapshot_date FROM benchmark_snapshots ORDER BY snapshot_date DESC"
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [r[0] for r in rows]
