"""
Overrides manuais de metadados de ativos (setor, nome, etc).
Persistidos no SQLite e aplicados sobre os dados da API.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "market_snapshots.db"

_UNCLASSIFIED = {"não informado", "none", "", "nan"}


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_overrides (
            ticker  TEXT PRIMARY KEY,
            setor   TEXT NOT NULL,
            nome    TEXT,
            updated TEXT DEFAULT (date('now'))
        )
    """)
    conn.commit()
    return conn


def set_sector_override(ticker: str, setor: str, nome: str | None = None) -> None:
    """Salva ou atualiza o setor de um ticker."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO sector_overrides (ticker, setor, nome, updated)
           VALUES (?, ?, ?, date('now'))
           ON CONFLICT(ticker) DO UPDATE SET setor=excluded.setor, updated=date('now')""",
        (ticker, setor, nome),
    )
    conn.commit()
    conn.close()


def set_sector_bulk(records: list[dict]) -> int:
    """Salva vários overrides de uma vez. Cada dict deve ter 'ticker' e 'setor'."""
    if not records:
        return 0
    conn = _get_conn()
    conn.executemany(
        """INSERT INTO sector_overrides (ticker, setor, nome, updated)
           VALUES (:ticker, :setor, :nome, date('now'))
           ON CONFLICT(ticker) DO UPDATE SET setor=excluded.setor, updated=date('now')""",
        [{"ticker": r["ticker"], "setor": r["setor"], "nome": r.get("nome")} for r in records],
    )
    conn.commit()
    saved = conn.execute("SELECT changes()").fetchone()[0]
    conn.close()
    return len(records)


def get_all_overrides() -> pd.DataFrame:
    """Retorna todos os overrides salvos."""
    if not DB_PATH.exists():
        return pd.DataFrame(columns=["ticker", "setor", "nome", "updated"])
    conn = _get_conn()
    df = pd.read_sql("SELECT * FROM sector_overrides ORDER BY ticker", conn)
    conn.close()
    return df


def apply_sector_overrides(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica overrides de setor ao DataFrame.
    Normaliza também 'None' / 'Não informado' → None antes de aplicar.
    """
    if df.empty:
        return df
    df = df.copy()
    # Normaliza variantes de "sem setor"
    df["setor"] = df["setor"].apply(
        lambda x: None if (str(x).strip().lower() in _UNCLASSIFIED) else x
    )
    overrides = get_all_overrides()
    if overrides.empty:
        return df
    override_map = dict(zip(overrides["ticker"], overrides["setor"]))
    mask = df["ticker"].isin(override_map)
    df.loc[mask, "setor"] = df.loc[mask, "ticker"].map(override_map)
    return df


def is_unclassified(setor) -> bool:
    return setor is None or str(setor).strip().lower() in _UNCLASSIFIED
