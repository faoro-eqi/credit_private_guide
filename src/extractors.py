"""
Extração de dados de oportunidades a partir de PDF ou Excel.
Tenta extrair campos estruturados; o que não encontrar retorna None
para o usuário preencher manualmente no formulário.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_from_pdf(file_bytes: bytes) -> dict[str, Any]:
    """
    Extrai metadados básicos de um PDF de prospecto / termo de securitização.
    Requer: pdfplumber
    """
    try:
        import pdfplumber
    except ImportError:
        return {"error": "pdfplumber não instalado. Execute: pip install pdfplumber"}

    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages[:20]:  # primeiras 20 páginas são suficientes
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    return _parse_pdf_text(text)


def _parse_pdf_text(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    # Emissor / Devedor
    for pattern in [
        r"(?:emissora|emitente|devedora?)[:\s]+([A-ZÀ-Ú][^\n]{3,80})",
        r"(?:raz[aã]o social)[:\s]+([A-ZÀ-Ú][^\n]{3,80})",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["issuer"] = m.group(1).strip()
            break

    # Tipo de ativo
    if re.search(r"\bCRI\b", text):
        result["asset_type"] = "CRI"
    elif re.search(r"\bCRA\b", text):
        result["asset_type"] = "CRA"
    elif re.search(r"deb[eê]nture", text, re.IGNORECASE):
        result["asset_type"] = "Debênture"

    # Indexador e spread
    cdi_spread = re.search(
        r"CDI\s*\+\s*([\d,\.]+)\s*%", text, re.IGNORECASE
    )
    ipca_spread = re.search(
        r"IPCA\s*\+\s*([\d,\.]+)\s*%", text, re.IGNORECASE
    )
    if cdi_spread:
        result["indexer"] = "CDI"
        result["spread"] = float(cdi_spread.group(1).replace(",", "."))
    elif ipca_spread:
        result["indexer"] = "IPCA"
        result["spread"] = float(ipca_spread.group(1).replace(",", "."))

    # Prazo / vencimento
    m = re.search(
        r"(?:vencimento|maturidade)[:\s]+(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})",
        text, re.IGNORECASE
    )
    if m:
        result["maturity_date_raw"] = m.group(1)

    # Rating
    rating_match = re.search(
        r"\b(AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?)\b", text
    )
    if rating_match:
        result["rating"] = rating_match.group(1)

    # Incentivado (Lei 12.431)
    if re.search(r"12\.431|isento|incentivad", text, re.IGNORECASE):
        result["is_incentivized"] = True

    # Garantia
    for gtype in ["alienação fiduciária", "cessão fiduciária", "hipoteca",
                   "aval", "fiança", "fgi", "sem garantia"]:
        if re.search(gtype, text, re.IGNORECASE):
            result["collateral"] = gtype.title()
            break

    return result


# ---------------------------------------------------------------------------
# Excel / CSV
# ---------------------------------------------------------------------------

def extract_from_excel(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """
    Lê a primeira planilha de um Excel/CSV e tenta mapear colunas conhecidas
    para os campos do modelo Opportunity.
    """
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0)
    except Exception as e:
        return {"error": str(e)}

    # Normaliza cabeçalhos
    df.columns = [str(c).strip().lower() for c in df.columns]
    row = df.iloc[0].to_dict() if not df.empty else {}

    COLUMN_MAP = {
        "issuer": ["emissor", "emissora", "devedor", "issuer"],
        "sector": ["setor", "sector", "segmento"],
        "asset_type": ["tipo", "ativo", "tipo_ativo", "asset_type"],
        "indexer": ["indexador", "indexer", "índice"],
        "spread": ["spread", "taxa", "taxa_spread", "juros"],
        "duration_years": ["duration", "prazo", "duration_years"],
        "rating": ["rating", "nota", "classificação"],
        "is_incentivized": ["incentivado", "isento", "lei_12431"],
        "collateral": ["garantia", "collateral"],
    }

    result: dict[str, Any] = {}
    for field, aliases in COLUMN_MAP.items():
        for alias in aliases:
            if alias in row and pd.notna(row[alias]):
                result[field] = row[alias]
                break

    return result
