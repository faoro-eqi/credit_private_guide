"""
Benchmarks de renda fixa soberana para cálculo de spread.

Fontes:
  - NTN-B : API interna qtzd-ntnb (JWT — mesmo token DATA_B3_TOKEN)
  - LTN/NTN-F : arquivo público diário ANBIMA (sem auth)
             URL: https://www.anbima.com.br/informacoes/merc-sec/arqs/msYYMMDD.txt

Interpolação: linear por dias_até_vencimento para obter a taxa do
benchmark na duration exata de cada ativo.
"""

from __future__ import annotations

import io
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

DATA_B3_TOKEN = os.getenv("DATA_B3_TOKEN", "")
NTNB_BASE_URL = "https://qtzd-ntnb-api-293570283792.us-east1.run.app"
ANBIMA_MS_URL = "https://www.anbima.com.br/informacoes/merc-sec/arqs/ms{date}.txt"

DB_PATH = Path(__file__).parent.parent / "data" / "market_snapshots.db"

# ---------------------------------------------------------------------------
# Fetch NTN-B (API interna)
# ---------------------------------------------------------------------------

def fetch_ntnb() -> pd.DataFrame:
    """
    Retorna DataFrame com vértices NTN-B.
    Tenta primeiro a API interna (JWT). Se falhar ou retornar vazio,
    usa o arquivo público da ANBIMA (mesmo ms*.txt que fetch_pre_fixed).
    """
    # Tenta API interna
    try:
        token = DATA_B3_TOKEN or os.getenv("DATA_B3_TOKEN", "")
        if token:
            resp = httpx.get(
                f"{NTNB_BASE_URL}/dashboard_table",
                headers={"Authorization": f"Bearer {token}"},
                params={"skip": 0, "take": 50},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(items, list) and len(items) > 0:
                    df = pd.DataFrame(items)[["code", "days_to_due", "due_date", "indicative_rate", "buy_rate", "sell_rate"]]
                    df = df.sort_values("days_to_due").reset_index(drop=True)
                    return df
    except Exception:
        pass

    # Fallback: ANBIMA pública
    ref = date.today()
    for _ in range(8):
        url = ANBIMA_MS_URL.format(date=_anbima_ms_date_str(ref))
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                df = _parse_anbima_ms(resp.text, ["NTN-B"])
                if not df.empty:
                    # adapta colunas para o formato esperado
                    if "buy_rate" not in df.columns:
                        df["buy_rate"] = df["indicative_rate"]
                        df["sell_rate"] = df["indicative_rate"]
                    return df[["code", "days_to_due", "due_date", "indicative_rate", "buy_rate", "sell_rate"]]
        except Exception:
            pass
        ref = ref - timedelta(days=1)

    return pd.DataFrame(columns=["code", "days_to_due", "due_date", "indicative_rate", "buy_rate", "sell_rate"])


# ---------------------------------------------------------------------------
# Fetch LTN + NTN-F (ANBIMA público)
# ---------------------------------------------------------------------------

def _anbima_ms_date_str(ref: date) -> str:
    return ref.strftime("%y%m%d")


def _parse_anbima_ms(text: str, tipos: list[str]) -> pd.DataFrame:
    """Parseia o arquivo ms*.txt da ANBIMA e retorna linhas dos tipos pedidos."""
    rows = []
    for line in text.splitlines():
        parts = line.split("@")
        if len(parts) < 9 or parts[0] not in tipos:
            continue
        try:
            titulo = parts[0]
            ref_date_str = parts[1]          # YYYYMMDD
            due_date_str = parts[4]          # YYYYMMDD
            indicative = float(parts[7].replace(",", "."))
            ref_dt = date(int(ref_date_str[:4]), int(ref_date_str[4:6]), int(ref_date_str[6:]))
            due_dt = date(int(due_date_str[:4]), int(due_date_str[4:6]), int(due_date_str[6:]))
            days = (due_dt - ref_dt).days
            rows.append({
                "code": f"{titulo}-{due_date_str}",
                "titulo": titulo,
                "days_to_due": days,
                "due_date": due_dt.isoformat(),
                "indicative_rate": indicative,
            })
        except (ValueError, IndexError):
            continue
    df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["code", "titulo", "days_to_due", "due_date", "indicative_rate"]
    )
    return df.sort_values("days_to_due").reset_index(drop=True)


def fetch_pre_fixed(tries: int = 3) -> pd.DataFrame:
    """
    Retorna DataFrame com LTN e NTN-F da ANBIMA (arquivo público diário).
    Tenta os últimos `tries` dias úteis se o arquivo do dia não existir.
    """
    ref = date.today()
    for _ in range(tries + 5):  # tenta até 8 dias para cobrir fins de semana
        url = ANBIMA_MS_URL.format(date=_anbima_ms_date_str(ref))
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                text = resp.text
                df = _parse_anbima_ms(text, ["LTN", "NTN-F"])
                if not df.empty:
                    return df
        except Exception:
            pass
        ref = ref - timedelta(days=1)
    return pd.DataFrame(columns=["code", "titulo", "days_to_due", "due_date", "indicative_rate"])


# ---------------------------------------------------------------------------
# Interpolação de spread
# ---------------------------------------------------------------------------

def interpolate_rate(benchmark_df: pd.DataFrame, duration_days: float) -> float | None:
    """
    Interpola linearmente a taxa do benchmark para uma duration específica (dias).
    Retorna None se não houver dados suficientes.
    """
    df = benchmark_df.dropna(subset=["indicative_rate", "days_to_due"]).sort_values("days_to_due")
    if df.empty:
        return None

    days_arr = df["days_to_due"].values.astype(float)
    rate_arr = df["indicative_rate"].values.astype(float)

    # Fora dos extremos: extrapola com o valor mais próximo
    if duration_days <= days_arr[0]:
        return float(rate_arr[0])
    if duration_days >= days_arr[-1]:
        return float(rate_arr[-1])

    # Interpolação linear
    return float(pd.Series(rate_arr).iloc[
        pd.Series(days_arr).searchsorted(duration_days) - 1
    ] + (duration_days - days_arr[pd.Series(days_arr).searchsorted(duration_days) - 1]) /
        (days_arr[pd.Series(days_arr).searchsorted(duration_days)] -
         days_arr[pd.Series(days_arr).searchsorted(duration_days) - 1]) *
        (rate_arr[pd.Series(days_arr).searchsorted(duration_days)] -
         rate_arr[pd.Series(days_arr).searchsorted(duration_days) - 1]))


def add_spread_column(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    spread_col: str = "spread",
    benchmark_rate_col: str = "benchmark_rate",
) -> pd.DataFrame:
    """
    Adiciona colunas `spread` e `benchmark_rate` ao DataFrame de ativos.
    duration_days = duration_years * 365
    """
    df = df.copy()
    df[benchmark_rate_col] = df["duration_years"].apply(
        lambda y: interpolate_rate(benchmark_df, y * 365)
    )
    df[spread_col] = df["taxa_media"] - df[benchmark_rate_col]
    return df


# ---------------------------------------------------------------------------
# Persistência de snapshots de benchmarks
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_snapshots (
            snapshot_date  TEXT NOT NULL,
            benchmark_type TEXT NOT NULL,
            code           TEXT NOT NULL,
            days_to_due    INTEGER,
            due_date       TEXT,
            indicative_rate REAL,
            PRIMARY KEY (snapshot_date, benchmark_type, code)
        )
    """)
    conn.commit()
    return conn


def fetch_anbima_for_date(date_str: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Busca dados ANBIMA (NTN-B e LTN/NTN-F) para uma data específica (YYYY-MM-DD).
    Tenta até 5 dias antes para cobrir fins de semana e feriados.
    Retorna (ntnb_df, pre_df) com a mesma estrutura de fetch_ntnb / fetch_pre_fixed.
    """
    ref = date.fromisoformat(date_str)
    for _ in range(6):
        url = ANBIMA_MS_URL.format(date=_anbima_ms_date_str(ref))
        try:
            resp = httpx.get(url, timeout=12, follow_redirects=True)
            if resp.status_code == 200:
                text = resp.text
                ntnb_df = _parse_anbima_ms(text, ["NTN-B"])
                pre_df  = _parse_anbima_ms(text, ["LTN", "NTN-F"])
                if not ntnb_df.empty or not pre_df.empty:
                    return ntnb_df, pre_df
        except Exception:
            pass
        ref = ref - timedelta(days=1)
    empty = pd.DataFrame(columns=["code", "days_to_due", "due_date", "indicative_rate"])
    return empty.copy(), empty.copy()


def save_benchmark_for_date(
    date_str: str,
    ntnb_df: pd.DataFrame,
    pre_df: pd.DataFrame,
    overwrite: bool = False,
) -> tuple[bool, str]:
    """
    Salva benchmark para uma data específica. Retorna (ok, mensagem).
    Se overwrite=True, remove entradas existentes antes de salvar.
    """
    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(*) FROM benchmark_snapshots WHERE snapshot_date = ?", (date_str,)
    ).fetchone()[0]
    if existing > 0 and not overwrite:
        conn.close()
        return False, f"Já existe snapshot para {date_str} ({existing} linhas). Use overwrite=True para substituir."
    if existing > 0 and overwrite:
        conn.execute("DELETE FROM benchmark_snapshots WHERE snapshot_date = ?", (date_str,))
        conn.commit()

    saved = 0
    for btype, bdf in [("ntnb", ntnb_df), ("pre", pre_df)]:
        if bdf.empty:
            continue
        rows = bdf[["code", "days_to_due", "due_date", "indicative_rate"]].copy()
        rows["snapshot_date"] = date_str
        rows["benchmark_type"] = btype
        rows.to_sql("benchmark_snapshots", conn, if_exists="append", index=False)
        saved += len(rows)

    conn.commit()
    conn.close()
    return True, f"Salvo: {saved} vértices para {date_str}."


def save_benchmark_snapshot(ntnb_df: pd.DataFrame, pre_df: pd.DataFrame) -> bool:
    """Salva snapshots NTN-B e Pré para hoje. Idempotente — ignora se já salvo."""
    ok, _ = save_benchmark_for_date(str(date.today()), ntnb_df, pre_df, overwrite=False)
    return ok


def load_benchmark_snapshot(target_date: str, benchmark_type: str) -> pd.DataFrame:
    """Carrega snapshot de benchmark de uma data específica."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _get_conn()
    df = pd.read_sql(
        "SELECT * FROM benchmark_snapshots WHERE snapshot_date = ? AND benchmark_type = ?",
        conn,
        params=(target_date, benchmark_type),
    )
    conn.close()
    return df


def load_benchmark_snapshots_batch(dates: list[str], benchmark_type: str) -> dict[str, pd.DataFrame]:
    """
    Carrega snapshots de benchmark para múltiplas datas em uma única query.
    Retorna dict {snapshot_date: DataFrame}.
    """
    if not DB_PATH.exists() or not dates:
        return {}
    conn = _get_conn()
    placeholders = ",".join("?" * len(dates))
    df = pd.read_sql(
        f"SELECT * FROM benchmark_snapshots WHERE snapshot_date IN ({placeholders}) AND benchmark_type = ?",
        conn,
        params=(*dates, benchmark_type),
    )
    conn.close()
    if df.empty:
        return {}
    return {d: grp.reset_index(drop=True) for d, grp in df.groupby("snapshot_date")}


def available_benchmark_dates() -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM benchmark_snapshots ORDER BY snapshot_date DESC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def auto_backfill_benchmarks(days_back: int = 7) -> list[str]:
    """
    Verifica os últimos `days_back` dias e preenche lacunas via ANBIMA ms*.txt.
    Inclui NTN-B, LTN e NTN-F — todos presentes no mesmo arquivo público.
    Retorna lista de mensagens de resultado (silencioso se tudo já existir).
    """
    existing = set(available_benchmark_dates())
    results = []
    for i in range(1, days_back + 1):
        d_str = str(date.today() - timedelta(days=i))
        if d_str in existing:
            continue  # já temos este dia, pula
        ntnb_df, pre_df = fetch_anbima_for_date(d_str)
        if ntnb_df.empty and pre_df.empty:
            results.append(f"sem dados ANBIMA para {d_str} (feriado/indisponível)")
        else:
            ok, msg = save_benchmark_for_date(d_str, ntnb_df, pre_df, overwrite=False)
            results.append(msg)
    return results
