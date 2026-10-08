"""
Módulo de Fundamentos Financeiros.

Armazena e busca indicadores financeiros das empresas emissoras:
  - Receita Líquida, EBITDA, EBIT, Lucro Líquido
  - Dívida Bruta, Caixa, Dívida Líquida
  - Alavancagem (DL/EBITDA), Margem EBITDA, Margem Líquida

Fontes de dados:
  1. yfinance    → empresas listadas na B3 (ticker + ".SA")
  2. CVM DFP     → todas as cias abertas que arquivam com a CVM (por CNPJ)
  3. Manual      → entrada direta pelo usuário
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent.parent / "data" / "financials.db"
CVM_CACHE_DIR = Path(__file__).parent.parent / "data" / "cvm_cache"

# Contas padrão CVM (DFP/ITR — consolidado)
_DRE_ACCOUNTS = {
    "3.01":  "net_revenue",         # Receita Líquida
    "3.04":  "gross_profit",        # Resultado Bruto
    "3.05":  "ebit",                # EBIT (antes resultado financeiro)
    "3.11":  "net_income",          # Lucro Líquido
}
_BPA_ACCOUNTS = {
    "1.01.01": "cash_equiv",        # Caixa e Equivalentes
    "1.01.02": "short_term_invest", # Aplicações Financeiras CP
}
_BPP_ACCOUNTS = {
    "2.01.04": "st_debt",           # Empréstimos CP
    "2.02.01": "lt_debt",           # Empréstimos LP
}
_DFC_KEYWORDS = ["depreciação", "amortização", "depreciation", "amortization"]


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

class CompanyFinancials(BaseModel):
    """Indicadores financeiros de uma empresa para um período."""

    company_name: str = Field(..., description="Nome da empresa")
    ticker_b3: Optional[str] = Field(None, description="Ticker B3 sem .SA (ex: PETR4)")
    cnpj: Optional[str] = Field(None, description="CNPJ formatado (ex: 00.000.000/0001-00)")
    sector: Optional[str] = None

    report_date: str = Field(..., description="Data de referência YYYY-MM-DD")
    period_type: str = Field("annual", description="annual | quarterly | ltm")

    # Demonstração de Resultado
    net_revenue_mm: Optional[float] = Field(None, description="Receita Líquida (R$ mm)")
    ebitda_mm: Optional[float] = Field(None, description="EBITDA (R$ mm)")
    ebit_mm: Optional[float] = Field(None, description="EBIT (R$ mm)")
    net_income_mm: Optional[float] = Field(None, description="Lucro Líquido (R$ mm)")
    depreciation_mm: Optional[float] = Field(None, description="D&A (R$ mm)")
    capex_mm: Optional[float] = Field(None, description="Capex (R$ mm)")

    # Balanço
    gross_debt_mm: Optional[float] = Field(None, description="Dívida Bruta (R$ mm)")
    cash_mm: Optional[float] = Field(None, description="Caixa (R$ mm)")
    net_debt_mm: Optional[float] = Field(None, description="Dívida Líquida (R$ mm)")

    # Indicadores derivados
    ebitda_margin_pct: Optional[float] = Field(None, description="Margem EBITDA (%)")
    net_margin_pct: Optional[float] = Field(None, description="Margem Líquida (%)")
    nd_ebitda: Optional[float] = Field(None, description="DL / EBITDA (x)")

    data_source: str = Field("manual", description="yfinance | cvm | manual")
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_financials (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name     TEXT NOT NULL,
            ticker_b3        TEXT,
            cnpj             TEXT,
            sector           TEXT,
            report_date      TEXT NOT NULL,
            period_type      TEXT DEFAULT 'annual',
            net_revenue_mm   REAL,
            ebitda_mm        REAL,
            ebit_mm          REAL,
            net_income_mm    REAL,
            depreciation_mm  REAL,
            capex_mm         REAL,
            gross_debt_mm    REAL,
            cash_mm          REAL,
            net_debt_mm      REAL,
            ebitda_margin_pct REAL,
            net_margin_pct   REAL,
            nd_ebitda        REAL,
            data_source      TEXT DEFAULT 'manual',
            updated_at       TEXT,
            UNIQUE(company_name, report_date, period_type)
        )
    """)
    conn.commit()
    return conn


def save_financials(fin: CompanyFinancials) -> None:
    """Insere ou atualiza um registro de fundamentos."""
    conn = _get_conn()
    d = fin.model_dump()
    d.pop("id", None)
    d["updated_at"] = str(date.today())
    conn.execute("""
        INSERT INTO company_financials
            (company_name, ticker_b3, cnpj, sector, report_date, period_type,
             net_revenue_mm, ebitda_mm, ebit_mm, net_income_mm, depreciation_mm, capex_mm,
             gross_debt_mm, cash_mm, net_debt_mm,
             ebitda_margin_pct, net_margin_pct, nd_ebitda, data_source, updated_at)
        VALUES
            (:company_name, :ticker_b3, :cnpj, :sector, :report_date, :period_type,
             :net_revenue_mm, :ebitda_mm, :ebit_mm, :net_income_mm, :depreciation_mm, :capex_mm,
             :gross_debt_mm, :cash_mm, :net_debt_mm,
             :ebitda_margin_pct, :net_margin_pct, :nd_ebitda, :data_source, :updated_at)
        ON CONFLICT(company_name, report_date, period_type) DO UPDATE SET
            ticker_b3=excluded.ticker_b3,
            cnpj=excluded.cnpj,
            sector=excluded.sector,
            net_revenue_mm=excluded.net_revenue_mm,
            ebitda_mm=excluded.ebitda_mm,
            ebit_mm=excluded.ebit_mm,
            net_income_mm=excluded.net_income_mm,
            depreciation_mm=excluded.depreciation_mm,
            capex_mm=excluded.capex_mm,
            gross_debt_mm=excluded.gross_debt_mm,
            cash_mm=excluded.cash_mm,
            net_debt_mm=excluded.net_debt_mm,
            ebitda_margin_pct=excluded.ebitda_margin_pct,
            net_margin_pct=excluded.net_margin_pct,
            nd_ebitda=excluded.nd_ebitda,
            data_source=excluded.data_source,
            updated_at=excluded.updated_at
    """, d)
    conn.commit()
    conn.close()


def list_companies(latest_only: bool = True) -> pd.DataFrame:
    """Retorna tabela com todos os fundamentos (por padrão, apenas o registro mais recente por empresa).

    Quando latest_only=True, também desdublica por ticker_b3: se dois nomes distintos
    apontam para o mesmo ticker (ex: 'Marfrig' e 'Marfrig Global Foods S.A.'), mantém
    apenas o registro com report_date mais recente.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _get_conn()
    if latest_only:
        df = pd.read_sql_query("""
            SELECT cf.*
            FROM company_financials cf
            INNER JOIN (
                SELECT company_name, MAX(report_date) AS max_date
                FROM company_financials
                GROUP BY company_name
            ) latest ON cf.company_name = latest.company_name
                     AND cf.report_date = latest.max_date
            ORDER BY cf.company_name
        """, conn)
        # Desduplicar por ticker_b3: mesmo ativo com nomes ligeiramente diferentes
        if not df.empty and "ticker_b3" in df.columns:
            with_ticker = df[df["ticker_b3"].notna()].copy()
            without_ticker = df[df["ticker_b3"].isna()].copy()
            if not with_ticker.empty:
                with_ticker = with_ticker.sort_values("report_date", ascending=False)
                with_ticker = with_ticker.drop_duplicates(subset=["ticker_b3"], keep="first")
            df = pd.concat([with_ticker, without_ticker], ignore_index=True).sort_values("company_name")
    else:
        df = pd.read_sql_query(
            "SELECT * FROM company_financials ORDER BY company_name, report_date DESC", conn
        )
    conn.close()
    return df


def get_company_history(company_name: str) -> pd.DataFrame:
    """Histórico de todos os períodos de uma empresa."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _get_conn()
    df = pd.read_sql_query(
        "SELECT * FROM company_financials WHERE company_name = ? ORDER BY report_date DESC",
        conn, params=(company_name,)
    )
    conn.close()
    return df


def get_latest_for_company(company_name: str) -> Optional[CompanyFinancials]:
    """Retorna o registro mais recente de uma empresa, se existir."""
    hist = get_company_history(company_name)
    if hist.empty:
        return None
    row = hist.iloc[0].to_dict()
    row.pop("id", None)
    return CompanyFinancials(**{k: v for k, v in row.items() if v is not None or k in CompanyFinancials.model_fields})


def delete_company(company_name: str) -> int:
    """Remove todos os registros de uma empresa. Retorna quantidade removida."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM company_financials WHERE company_name = ?", (company_name,))
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def find_b3_ticker(company_name: str) -> Optional[str]:
    """Wrapper público para descoberta automática de ticker B3 a partir do nome da empresa."""
    return _search_b3_ticker(company_name)


# ---------------------------------------------------------------------------
# Busca via yfinance
# ---------------------------------------------------------------------------

def fetch_from_yfinance(ticker_b3: str, use_ltm: bool = True) -> dict:
    """
    Busca dados financeiros da empresa via yfinance.

    Args:
        ticker_b3:  Ticker da B3 com ou sem .SA (ex: 'PETR4' ou 'PETR4.SA')
        use_ltm:    Se True, calcula LTM (últimos 12 meses) somando 4 trimestres.
                    Se False, usa o último demonstrativo anual.

    Returns:
        dict com os campos de CompanyFinancials, ou {'error': str} em caso de falha.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance não instalado. Execute: pip install yfinance"}

    try:
        ticker_str = ticker_b3.upper().replace(" ", "")
        if not ticker_str.endswith(".SA"):
            ticker_str = ticker_str + ".SA"

        t = yf.Ticker(ticker_str)
        info = t.info or {}

        def _val(df: pd.DataFrame, col, *keys):
            """Extrai o valor de uma das chaves possíveis em um DataFrame de financials."""
            if df is None or df.empty or col not in df.columns:
                return None
            for k in keys:
                if k in df.index:
                    v = df.loc[k, col]
                    if pd.notna(v):
                        return float(v)
            return None

        # --- Decide fonte: LTM (4 trimestres) ou anual ---
        period_type = "annual"
        report_date = None

        q_income = t.quarterly_income_stmt
        q_bs = t.quarterly_balance_sheet
        q_cf = t.quarterly_cashflow

        # Verifica se os 4 trimestres LTM têm dados de receita (evita LTM incompleto)
        _ltm_quarters_ok = False
        if use_ltm and not q_income.empty and len(q_income.columns) >= 4:
            _ltm_cand = q_income.columns[:4]
            _rev_key = next((k for k in ["Total Revenue", "Revenue"] if k in q_income.index), None)
            if _rev_key:
                _rev_vals = q_income.loc[_rev_key, _ltm_cand]
                _ltm_quarters_ok = int(_rev_vals.notna().sum()) >= 4
            else:
                _ltm_quarters_ok = True  # sem Revenue para checar, tenta LTM mesmo assim

        if _ltm_quarters_ok:
            # LTM: soma os últimos 4 trimestres de DRE + CF; balanço = último trimestre
            ltm_cols = q_income.columns[:4]
            report_date = ltm_cols[0].date().isoformat()
            period_type = "ltm"

            def _sum_ltm(df):
                if df is None or df.empty:
                    return None, None
                available = [c for c in ltm_cols if c in df.columns]
                return df[available], available

            inc_ltm, _ = _sum_ltm(q_income)
            cf_ltm, _ = _sum_ltm(q_cf)

            def _ltm(df, *keys):
                if df is None or df.empty:
                    return None
                for k in keys:
                    if k in df.index:
                        vals = df.loc[k].dropna()
                        if not vals.empty:
                            return float(vals.sum())
                return None

            revenue = _ltm(inc_ltm, "Total Revenue", "Revenue")
            ebit = _ltm(inc_ltm, "EBIT", "Operating Income")
            net_income = _ltm(inc_ltm, "Net Income", "Net Income Common Stockholders")
            da = _ltm(cf_ltm, "Depreciation And Amortization", "Depreciation",
                      "Depreciation Depletion And Amortization")
            capex_raw = _ltm(cf_ltm, "Capital Expenditure", "Capital Expenditures")

            # Balanço: último trimestre disponível
            bs_col = q_bs.columns[0] if not q_bs.empty else None
            cash_eq = _val(q_bs, bs_col, "Cash And Cash Equivalents",
                           "Cash Cash Equivalents And Short Term Investments")
            st_invest = _val(q_bs, bs_col, "Other Short Term Investments")
            st_debt = _val(q_bs, bs_col, "Current Debt", "Short Long Term Debt")
            lt_debt = _val(q_bs, bs_col, "Long Term Debt")

        else:
            # Anual
            a_income = t.income_stmt
            a_bs = t.balance_sheet
            a_cf = t.cashflow

            if a_income.empty:
                return {"error": f"Sem dados financeiros para {ticker_b3}"}

            col = a_income.columns[0]
            report_date = col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
            period_type = "annual"

            revenue = _val(a_income, col, "Total Revenue", "Revenue")
            ebit = _val(a_income, col, "EBIT", "Operating Income")
            net_income = _val(a_income, col, "Net Income", "Net Income Common Stockholders")
            da = _val(a_cf, col, "Depreciation And Amortization", "Depreciation",
                      "Depreciation Depletion And Amortization")
            capex_raw = _val(a_cf, col, "Capital Expenditure", "Capital Expenditures")

            bs_col = col if col in a_bs.columns else (a_bs.columns[0] if not a_bs.empty else None)
            cash_eq = _val(a_bs, bs_col, "Cash And Cash Equivalents",
                           "Cash Cash Equivalents And Short Term Investments")
            st_invest = _val(a_bs, bs_col, "Other Short Term Investments")
            st_debt = _val(a_bs, bs_col, "Current Debt", "Short Long Term Debt")
            lt_debt = _val(a_bs, bs_col, "Long Term Debt")

        # --- Cálculos derivados ---
        ebitda = None
        if ebit is not None and da is not None:
            ebitda = ebit + da
        elif ebit is not None:
            # EBITDA ≈ EBIT quando D&A não disponível (indicado abaixo)
            ebitda = ebit

        cash = (cash_eq or 0) + (st_invest or 0) if cash_eq is not None else None
        gross_debt = (st_debt or 0) + (lt_debt or 0) if (st_debt is not None or lt_debt is not None) else None
        net_debt = gross_debt - cash if (gross_debt is not None and cash is not None) else None
        capex = abs(capex_raw) if capex_raw is not None else None

        ebitda_margin = (ebitda / revenue * 100) if (ebitda and revenue) else None
        net_margin = (net_income / revenue * 100) if (net_income and revenue) else None
        nd_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda and ebitda > 0) else None

        company_name = info.get("longName") or info.get("shortName") or ticker_b3
        sector = info.get("sector") or info.get("industryKey") or None

        def _mm(v):
            return round(v / 1e6, 1) if v is not None else None

        return {
            "company_name": company_name,
            "ticker_b3": ticker_b3.upper().replace(".SA", ""),
            "sector": sector,
            "report_date": report_date,
            "period_type": period_type,
            "net_revenue_mm": _mm(revenue),
            "ebitda_mm": _mm(ebitda),
            "ebit_mm": _mm(ebit),
            "net_income_mm": _mm(net_income),
            "depreciation_mm": _mm(da),
            "capex_mm": _mm(capex),
            "gross_debt_mm": _mm(gross_debt),
            "cash_mm": _mm(cash),
            "net_debt_mm": _mm(net_debt),
            "ebitda_margin_pct": round(ebitda_margin, 1) if ebitda_margin is not None else None,
            "net_margin_pct": round(net_margin, 1) if net_margin is not None else None,
            "nd_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None,
            "data_source": "yfinance",
            "_da_estimated": da is None,  # sinaliza que EBITDA = EBIT se True
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Busca via CVM Open Data (DFP)
# ---------------------------------------------------------------------------

def fetch_from_cvm(cnpj: str, year: int | None = None) -> dict:
    """
    Busca dados do DFP da CVM para uma empresa (cia aberta).

    Args:
        cnpj:  CNPJ no formato XX.XXX.XXX/XXXX-XX ou apenas dígitos.
        year:  Ano de referência (padrão: ano atual - 1, i.e. último DFP completo).

    Returns:
        dict com campos de CompanyFinancials, ou {'error': str}.
    """
    if year is None:
        year = date.today().year - 1

    # Normaliza CNPJ
    cnpj_digits = "".join(c for c in cnpj if c.isdigit())
    if len(cnpj_digits) != 14:
        return {"error": f"CNPJ inválido: {cnpj}"}

    try:
        dre = _load_cvm_csv("DRE", "con", year)
        bpa = _load_cvm_csv("BPA", "con", year)
        bpp = _load_cvm_csv("BPP", "con", year)
        dfc = _load_cvm_csv("DFC_MD", "con", year)
    except Exception as exc:
        return {"error": f"Erro ao baixar dados CVM: {exc}"}

    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        col = "CNPJ_CIA" if "CNPJ_CIA" in df.columns else None
        if col is None:
            return pd.DataFrame()
        df_c = df[df[col].str.replace(r"\D", "", regex=True) == cnpj_digits].copy()
        # Pega o último exercício (ORDEM_EXERC == "ÚLTIMO" ou max DT_FIM_EXERC)
        if "ORDEM_EXERC" in df_c.columns:
            df_c = df_c[df_c["ORDEM_EXERC"].str.upper().isin(["ÚLTIMO", "PENÚLTIMO"])].copy()
            df_c = df_c[df_c["ORDEM_EXERC"].str.upper() == "ÚLTIMO"]
        return df_c

    dre_f = _filter(dre)
    bpa_f = _filter(bpa)
    bpp_f = _filter(bpp)
    dfc_f = _filter(dfc)

    if dre_f.empty:
        return {"error": f"Empresa com CNPJ {cnpj} não encontrada no DFP {year}"}

    company_name = dre_f["DENOM_CIA"].iloc[0] if "DENOM_CIA" in dre_f.columns else cnpj
    report_date = dre_f["DT_FIM_EXERC"].iloc[0] if "DT_FIM_EXERC" in dre_f.columns else f"{year}-12-31"

    def _account(df: pd.DataFrame, code: str) -> Optional[float]:
        if df.empty or "CD_CONTA" not in df.columns or "VL_CONTA" not in df.columns:
            return None
        row = df[df["CD_CONTA"] == code]
        if row.empty:
            return None
        val = pd.to_numeric(row["VL_CONTA"].iloc[0], errors="coerce")
        scale = row["ESCALA_MOEDA"].iloc[0] if "ESCALA_MOEDA" in row.columns else "MIL"
        if pd.isna(val):
            return None
        return float(val) / 1000.0 if scale == "MIL" else float(val) / 1e6

    revenue = _account(dre_f, "3.01")
    ebit = _account(dre_f, "3.05")
    net_income = _account(dre_f, "3.11")
    cash_eq = _account(bpa_f, "1.01.01")
    st_invest = _account(bpa_f, "1.01.02")
    st_debt = _account(bpp_f, "2.01.04")
    lt_debt = _account(bpp_f, "2.02.01")

    # D&A: busca na DFC por linhas com keywords
    da = None
    if not dfc_f.empty and "DS_CONTA" in dfc_f.columns and "VL_CONTA" in dfc_f.columns:
        mask = dfc_f["DS_CONTA"].str.lower().apply(
            lambda s: any(k in s for k in _DFC_KEYWORDS)
        )
        da_rows = dfc_f[mask]
        if not da_rows.empty:
            vals = pd.to_numeric(da_rows["VL_CONTA"], errors="coerce").dropna()
            if not vals.empty:
                scale = da_rows["ESCALA_MOEDA"].iloc[0] if "ESCALA_MOEDA" in da_rows.columns else "MIL"
                da = float(vals.abs().max()) / (1000.0 if scale == "MIL" else 1e6)

    ebitda = (ebit or 0) + (da or 0) if ebit is not None else None
    cash = (cash_eq or 0) + (st_invest or 0) if cash_eq is not None else None
    gross_debt = (st_debt or 0) + (lt_debt or 0) if (st_debt is not None or lt_debt is not None) else None
    net_debt = gross_debt - cash if (gross_debt is not None and cash is not None) else None
    ebitda_margin = (ebitda / revenue * 100) if (ebitda and revenue) else None
    net_margin = (net_income / revenue * 100) if (net_income and revenue) else None
    nd_ebitda = (net_debt / ebitda) if (net_debt is not None and ebitda and ebitda > 0) else None

    return {
        "company_name": str(company_name).strip().title(),
        "cnpj": cnpj,
        "report_date": str(report_date)[:10],
        "period_type": "annual",
        "net_revenue_mm": round(revenue, 1) if revenue is not None else None,
        "ebitda_mm": round(ebitda, 1) if ebitda is not None else None,
        "ebit_mm": round(ebit, 1) if ebit is not None else None,
        "net_income_mm": round(net_income, 1) if net_income is not None else None,
        "depreciation_mm": round(da, 1) if da is not None else None,
        "gross_debt_mm": round(gross_debt, 1) if gross_debt is not None else None,
        "cash_mm": round(cash, 1) if cash is not None else None,
        "net_debt_mm": round(net_debt, 1) if net_debt is not None else None,
        "ebitda_margin_pct": round(ebitda_margin, 1) if ebitda_margin is not None else None,
        "net_margin_pct": round(net_margin, 1) if net_margin is not None else None,
        "nd_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None,
        "data_source": "cvm",
    }


def _load_cvm_csv(report: str, scope: str, year: int) -> pd.DataFrame:
    """
    Baixa e faz cache do CSV do DFP da CVM.

    report: DRE | BPA | BPP | DFC_MD
    scope:  con (consolidado) | ind (individual)
    year:   ano de referência
    """
    CVM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CVM_CACHE_DIR / f"dfp_{report}_{scope}_{year}.csv"

    if not cache_file.exists():
        zip_url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip"
        resp = httpx.get(zip_url, timeout=60, follow_redirects=True)
        resp.raise_for_status()

        target_name = f"dfp_cia_aberta_{report}_{scope}_{year}.csv"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            match = next((n for n in names if target_name in n), None)
            if match is None:
                # fallback: tenta sem prefixo
                match = next((n for n in names if f"_{report}_{scope}_" in n), None)
            if match is None:
                return pd.DataFrame()
            with zf.open(match) as f:
                df = pd.read_csv(f, sep=";", encoding="latin-1", dtype=str)
        df.to_csv(cache_file, index=False, encoding="utf-8")
    else:
        df = pd.read_csv(cache_file, dtype=str, encoding="utf-8")

    return df


def clear_cvm_cache() -> int:
    """Remove arquivos de cache da CVM. Retorna quantidade removida."""
    if not CVM_CACHE_DIR.exists():
        return 0
    files = list(CVM_CACHE_DIR.glob("*.csv"))
    for f in files:
        f.unlink()
    return len(files)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def derive_indicators(data: dict) -> dict:
    """
    Recalcula indicadores derivados a partir dos campos brutos.
    Útil após entrada manual.
    """
    rev = data.get("net_revenue_mm")
    ebit = data.get("ebit_mm")
    da = data.get("depreciation_mm")
    net_inc = data.get("net_income_mm")
    gross_d = data.get("gross_debt_mm")
    cash = data.get("cash_mm")

    ebitda = data.get("ebitda_mm")
    if ebitda is None and ebit is not None and da is not None:
        ebitda = ebit + da
        data["ebitda_mm"] = round(ebitda, 1)

    net_debt = data.get("net_debt_mm")
    if net_debt is None and gross_d is not None and cash is not None:
        net_debt = gross_d - cash
        data["net_debt_mm"] = round(net_debt, 1)

    if rev and rev > 0:
        if ebitda is not None:
            data["ebitda_margin_pct"] = round(ebitda / rev * 100, 1)
        if net_inc is not None:
            data["net_margin_pct"] = round(net_inc / rev * 100, 1)

    if net_debt is not None and ebitda and ebitda > 0:
        data["nd_ebitda"] = round(net_debt / ebitda, 2)

    return data


# ---------------------------------------------------------------------------
# Histórico anual multi-período (para análise de crescimento)
# ---------------------------------------------------------------------------

def fetch_annual_history(ticker_b3: str) -> list[dict]:
    """
    Busca todos os períodos anuais disponíveis no yfinance (geralmente até 4 anos).
    Retorna lista de dicts compatíveis com CompanyFinancials, um por ano.
    Em caso de erro, retorna [{'error': str}].
    """
    try:
        import yfinance as yf
    except ImportError:
        return [{"error": "yfinance não instalado."}]

    try:
        ticker_str = ticker_b3.upper().replace(" ", "")
        if not ticker_str.endswith(".SA"):
            ticker_str += ".SA"

        t = yf.Ticker(ticker_str)
        info = t.info or {}
        a_income = t.income_stmt
        a_bs = t.balance_sheet
        a_cf = t.cashflow

        if a_income is None or a_income.empty:
            return [{"error": f"Sem demonstrativos anuais para {ticker_b3}"}]

        company_name = info.get("longName") or info.get("shortName") or ticker_b3
        sector = info.get("sector") or info.get("industryKey") or None

        def _val(df, col, *keys):
            if df is None or df.empty or col not in df.columns:
                return None
            for k in keys:
                if k in df.index:
                    v = df.loc[k, col]
                    if pd.notna(v):
                        return float(v)
            return None

        def _mm(v):
            return round(v / 1e6, 1) if v is not None else None

        results = []
        for inc_col in a_income.columns:
            report_date = (
                inc_col.date().isoformat() if hasattr(inc_col, "date") else str(inc_col)[:10]
            )

            # Cashflow: alinhar pela mesma data, senão usar primeira coluna
            cf_col = None
            if a_cf is not None and not a_cf.empty:
                cf_col = inc_col if inc_col in a_cf.columns else a_cf.columns[0]

            # Balanço: alinhar pela mesma data, senão usar primeira coluna
            bs_col = None
            if a_bs is not None and not a_bs.empty:
                bs_col = inc_col if inc_col in a_bs.columns else a_bs.columns[0]

            revenue = _val(a_income, inc_col, "Total Revenue", "Revenue")
            ebit = _val(a_income, inc_col, "EBIT", "Operating Income")
            net_income = _val(a_income, inc_col, "Net Income", "Net Income Common Stockholders")

            da = _val(a_cf, cf_col, "Depreciation And Amortization", "Depreciation",
                      "Depreciation Depletion And Amortization") if cf_col else None
            capex_raw = _val(a_cf, cf_col, "Capital Expenditure", "Capital Expenditures") if cf_col else None

            cash_eq = _val(a_bs, bs_col, "Cash And Cash Equivalents",
                           "Cash Cash Equivalents And Short Term Investments") if bs_col else None
            st_invest = _val(a_bs, bs_col, "Other Short Term Investments") if bs_col else None
            st_debt = _val(a_bs, bs_col, "Current Debt", "Short Long Term Debt") if bs_col else None
            lt_debt = _val(a_bs, bs_col, "Long Term Debt") if bs_col else None

            ebitda = (ebit + da) if (ebit is not None and da is not None) else ebit
            cash = (cash_eq or 0) + (st_invest or 0) if cash_eq is not None else None
            gross_debt = (
                (st_debt or 0) + (lt_debt or 0)
                if (st_debt is not None or lt_debt is not None)
                else None
            )
            net_debt = gross_debt - cash if (gross_debt is not None and cash is not None) else None
            capex = abs(capex_raw) if capex_raw is not None else None

            ebitda_margin = (ebitda / revenue * 100) if (ebitda and revenue) else None
            net_margin = (net_income / revenue * 100) if (net_income and revenue) else None
            nd_ebitda = (
                round(net_debt / ebitda, 2)
                if (net_debt is not None and ebitda and ebitda > 0)
                else None
            )

            results.append({
                "company_name": company_name,
                "ticker_b3": ticker_b3.upper().replace(".SA", ""),
                "sector": sector,
                "report_date": report_date,
                "period_type": "annual",
                "net_revenue_mm": _mm(revenue),
                "ebitda_mm": _mm(ebitda),
                "ebit_mm": _mm(ebit),
                "net_income_mm": _mm(net_income),
                "depreciation_mm": _mm(da),
                "capex_mm": _mm(capex),
                "gross_debt_mm": _mm(gross_debt),
                "cash_mm": _mm(cash),
                "net_debt_mm": _mm(net_debt),
                "ebitda_margin_pct": round(ebitda_margin, 1) if ebitda_margin is not None else None,
                "net_margin_pct": round(net_margin, 1) if net_margin is not None else None,
                "nd_ebitda": nd_ebitda,
                "data_source": "yfinance",
            })

        # Ordena mais antigo → mais recente para facilitar cálculos de crescimento
        results.sort(key=lambda x: x["report_date"])
        return results

    except Exception as exc:
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Export de demonstrativos brutos para conferência
# ---------------------------------------------------------------------------

def fetch_raw_statements_excel(ticker_b3: str) -> bytes | dict:
    """
    Baixa os demonstrativos brutos (DRE, Balanço, DFC) do yfinance e retorna
    bytes de um arquivo Excel com múltiplas abas para conferência dos dados.

    Abas geradas:
      • DRE_Trimestral  — income_stmt trimestral (últimos 4 trimestres)
      • DRE_Anual       — income_stmt anual (últimos 4 anos)
      • Balanco_Trim    — balance_sheet trimestral
      • Balanco_Anual   — balance_sheet anual
      • DFC_Trimestral  — cashflow trimestral
      • DFC_Anual       — cashflow anual
      • Indicadores     — resumo dos indicadores calculados (como salvos no banco)
      • Fonte           — metadados: ticker, URL Yahoo Finance, data do download

    Returns:
        bytes do .xlsx, ou {'error': str}.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance não instalado."}

    try:
        ticker_str = ticker_b3.upper().replace(" ", "")
        if not ticker_str.endswith(".SA"):
            ticker_str = ticker_str + ".SA"

        t = yf.Ticker(ticker_str)
        info = t.info or {}
        company_name = info.get("longName") or info.get("shortName") or ticker_b3

        # Coleta dos demonstrativos
        sheets: dict[str, pd.DataFrame] = {}

        def _prep(df: pd.DataFrame, label: str) -> pd.DataFrame:
            """Formata índice e colunas para leitura humana."""
            if df is None or df.empty:
                return pd.DataFrame({"Mensagem": [f"Sem dados para {label}"]})
            out = df.copy()
            # Converte colunas Timestamp para string legível
            out.columns = [str(c)[:10] if hasattr(c, "date") else str(c) for c in out.columns]
            out.index.name = "Linha"
            # Converte valores em bilhões para R$ mm (yfinance retorna em moeda local)
            # Nota: yfinance retorna em BRL absoluto; dividimos por 1e6 para R$ mm
            try:
                out = out.apply(pd.to_numeric, errors="ignore")
                numeric_cols = out.select_dtypes(include="number").columns
                out[numeric_cols] = out[numeric_cols].map(
                    lambda x: round(x / 1e6, 1) if pd.notna(x) else x
                )
            except Exception:
                pass
            return out.reset_index()

        sheets["DRE_Trimestral"] = _prep(t.quarterly_income_stmt, "DRE Trimestral")
        sheets["DRE_Anual"] = _prep(t.income_stmt, "DRE Anual")
        sheets["Balanco_Trim"] = _prep(t.quarterly_balance_sheet, "Balanço Trimestral")
        sheets["Balanco_Anual"] = _prep(t.balance_sheet, "Balanço Anual")
        sheets["DFC_Trimestral"] = _prep(t.quarterly_cashflow, "DFC Trimestral")
        sheets["DFC_Anual"] = _prep(t.cashflow, "DFC Anual")

        # Aba de indicadores calculados (resumo)
        ind_data = fetch_from_yfinance(ticker_b3, use_ltm=True)
        ind_data.pop("_da_estimated", None)
        ind_rows = [
            {"Indicador": k, "Valor": v}
            for k, v in ind_data.items()
            if k not in ("data_source",) and v is not None
        ]
        sheets["Indicadores"] = pd.DataFrame(ind_rows)

        # Aba de metadados / fonte
        from datetime import datetime
        sheets["Fonte"] = pd.DataFrame([
            {"Campo": "Empresa", "Valor": company_name},
            {"Campo": "Ticker B3", "Valor": ticker_b3.upper().replace(".SA", "")},
            {"Campo": "Ticker Yahoo Finance", "Valor": ticker_str},
            {"Campo": "URL Yahoo Finance", "Valor": f"https://finance.yahoo.com/quote/{ticker_str}/financials/"},
            {"Campo": "Setor (Yahoo)", "Valor": info.get("sector", "—")},
            {"Campo": "Indústria (Yahoo)", "Valor": info.get("industry", "—")},
            {"Campo": "Moeda reportada", "Valor": info.get("financialCurrency", "BRL")},
            {"Campo": "Nota sobre valores", "Valor": "Todos os valores monetários convertidos para R$ milhões (÷ 1.000.000)"},
            {"Campo": "Data do download", "Valor": datetime.now().strftime("%Y-%m-%d %H:%M")},
            {"Campo": "Fonte primária", "Valor": "Yahoo Finance via yfinance (https://pypi.org/project/yfinance/)"},
        ])

        # Gera o Excel em memória
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        buf.seek(0)
        return buf.read()

    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Sync automático: empresas do mercado → fundamentos
# ---------------------------------------------------------------------------

def _search_b3_ticker(company_name: str) -> Optional[str]:
    """
    Tenta descobrir o ticker B3 de uma empresa pelo nome usando yfinance Search.
    Retorna o ticker sem '.SA', ou None se não encontrar.

    Tenta progressivamente nomes mais curtos até achar resultado na B3 (exchange SAO).
    """
    try:
        import unicodedata
        import yfinance as yf

        def _pick_from(results: list) -> Optional[str]:
            """Filtra pela B3 e prioriza ON(3) > PN(4) > unit(11) > outros. Ignora fracionários (F)."""
            b3 = [
                r for r in results
                if r.get("exchange") == "SAO" or str(r.get("symbol", "")).endswith(".SA")
            ]
            # Remove fracionários (símbolo termina em F antes de .SA, ex: VALE3F.SA)
            b3 = [r for r in b3 if not str(r.get("symbol", "")).upper().replace(".SA", "").endswith("F")]
            if not b3:
                return None
            order = {"3": 0, "4": 1, "11": 2, "5": 3, "6": 4}
            def _rank(r):
                sym = r.get("symbol", "").upper().replace(".SA", "")
                for sfx, rank in order.items():
                    if sym.endswith(sfx):
                        return rank
                return 99
            b3_sorted = sorted(b3, key=_rank)
            sym = b3_sorted[0].get("symbol", "").upper().replace(".SA", "").strip()
            return sym if sym else None

        def _normalize(s: str) -> str:
            """Remove acentos para facilitar a busca."""
            return "".join(
                c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn"
            )

        # Palavras relevantes (sem stop words e sufixos jurídicos)
        _STOP = {"s.a.", "sa", "s/a", "ltda", "eireli", "de", "do", "da", "dos",
                 "das", "e", "the", "cia", "grupo", "holding"}
        words_orig = [w for w in company_name.split() if w.lower().rstrip(".") not in _STOP]

        # Sequência de tentativas de query (sem duplicatas)
        attempts: list[str] = []
        for n_words in (len(words_orig), 3, 2, 1):
            if n_words < 1:
                continue
            chunk = words_orig[:n_words]
            for use_norm in (False, True):
                query = " ".join(_normalize(w) if use_norm else w for w in chunk)
                if query and query not in attempts:
                    attempts.append(query)

        seen_queries: set[str] = set()
        for query in attempts:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            results = yf.Search(query, max_results=10, news_count=0).quotes
            ticker = _pick_from(results)
            if ticker:
                return ticker

        return None
    except Exception:
        return None


def sync_companies_from_market(
    skip_recent_days: int = 7,
    on_progress=None,
) -> list[dict]:
    """
    Varre todas as empresas da base de mercado (load_market_df), busca o ticker B3
    automaticamente e salva os fundamentos no banco.

    Args:
        skip_recent_days: Pula empresas atualizadas nos últimos N dias.
        on_progress: Callback opcional (i, total, company_name) para barra de progresso.

    Returns:
        Lista de dicts com: company_name, sector, ticker_found, status, message.
    """
    from src.data_sources import load_market_df  # import local para evitar circular

    df_market = load_market_df()
    if df_market.empty:
        return [{"company_name": "—", "status": "erro", "message": "Sem dados de mercado (verifique token)."}]

    # Empresas únicas com setor
    unique = (
        df_market[["nome", "setor"]]
        .drop_duplicates(subset=["nome"])
        .dropna(subset=["nome"])
    )
    unique = unique[unique["nome"].str.strip() != ""]

    # Quais já foram atualizadas recentemente?
    recently_updated: set[str] = set()
    if DB_PATH.exists():
        conn = _get_conn()
        cutoff = str(date.today().replace(day=max(1, date.today().day - skip_recent_days)))
        rows = conn.execute(
            "SELECT company_name FROM company_financials WHERE updated_at >= ?", (cutoff,)
        ).fetchall()
        conn.close()
        recently_updated = {r[0] for r in rows}

    total = len(unique)
    results = []

    for i, (_, row) in enumerate(unique.iterrows()):
        nome: str = str(row["nome"]).strip()
        setor: str = str(row.get("setor", "")).strip() or None

        if on_progress:
            on_progress(i, total, nome)

        if nome in recently_updated:
            results.append({
                "company_name": nome,
                "sector": setor,
                "ticker_found": "—",
                "status": "pulado",
                "message": f"Atualizado nos últimos {skip_recent_days} dias.",
            })
            continue

        # 1. Descobre ticker B3
        ticker = _search_b3_ticker(nome)

        if ticker:
            # 2a. Busca via yfinance
            data = fetch_from_yfinance(ticker, use_ltm=True)
            if "error" in data:
                results.append({
                    "company_name": nome,
                    "sector": setor,
                    "ticker_found": ticker,
                    "status": "erro",
                    "message": data["error"],
                })
                continue

            data.pop("_da_estimated", None)
            # Preserva o nome como está no mercado, não o retornado pelo yfinance
            data["company_name"] = nome
            data["sector"] = setor or data.get("sector")
            try:
                fin = CompanyFinancials(**{k: v for k, v in data.items() if k in CompanyFinancials.model_fields})
                save_financials(fin)
                results.append({
                    "company_name": nome,
                    "sector": setor,
                    "ticker_found": ticker,
                    "status": "ok",
                    "message": "LTM {} — EBITDA R${}mm".format(
                        str(data.get("report_date", ""))[:7],
                        data.get("ebitda_mm") or "?",
                    ),
                })
            except Exception as exc:
                results.append({
                    "company_name": nome,
                    "sector": setor,
                    "ticker_found": ticker,
                    "status": "erro",
                    "message": str(exc),
                })
        else:
            # 2b. Não encontrado na B3 — registra como "não listada"
            results.append({
                "company_name": nome,
                "sector": setor,
                "ticker_found": None,
                "status": "nao_listada",
                "message": "Ticker B3 não encontrado (empresa privada ou nome ambíguo).",
            })

    if on_progress:
        on_progress(total, total, "Concluído")

    return results
