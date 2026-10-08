"""
Parser de Releases de Resultados em PDF.

Extrai indicadores financeiros de PDFs de earnings releases brasileiros.
Suporta tabelas com múltiplos períodos (ex: 1T26, 1T25, 2025, 2024).
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber

# ---------------------------------------------------------------------------
# Normalização e parse de números
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Remove acentos, lowercase, strip."""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def _parse_num(s: str) -> Optional[float]:
    """
    Converte string numérica no formato brasileiro para float.
    '3.135,3' → 3135.3 | '(350)' → -350.0 | '—' → None
    """
    s = re.sub(r"[R$%\s]", "", str(s)).strip()
    if not s or s in ("-", "—", "–", "n/a", "nd", "nm", "n.m.", "n.d."):
        return None
    # Negativos entre parênteses
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    # Formato BR: 1.234,56 → separador milhares = '.', decimal = ','
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Mapeamento keyword → campo financeiro
# (ordem importa: mais específico primeiro)
# ---------------------------------------------------------------------------

FIELD_KEYWORDS: list[tuple[str, list[str]]] = [
    ("net_revenue_mm", [
        "receita liquida de vendas e servicos",
        "receita liquida de vendas",
        "receita operacional liquida",
        "receita liquida",
        "receita de vendas e servicos",
        "receita de vendas",
        "receita total liquida",
        "net revenue",
        "receita total",
    ]),
    ("ebitda_mm", [
        "ebitda ajustado recorrente",
        "ebitda ajustado",
        "ebitda recorrente",
        "ebitda ex-",
        "ebitda proforma",
        "ebitda (ajustado)",
        "lajida ajustado",
        "ebitda",
        "lajida",
    ]),
    ("ebit_mm", [
        "ebit ajustado",
        "ebit recorrente",
        "resultado antes do resultado financeiro",
        "resultado operacional",
        "lucro operacional",
        "lajir ajustado",
        "ebit",
        "lajir",
    ]),
    ("net_income_mm", [
        "lucro liquido atribuivel aos acionistas",
        "lucro (prejuizo) liquido",
        "lucro/prejuizo liquido",
        "resultado liquido do periodo",
        "resultado liquido",
        "lucro liquido",
        "net income",
        "net profit",
    ]),
    ("net_debt_mm", [
        "divida liquida ajustada",
        "divida liquida",
        "endividamento liquido",
        "net debt",
        "divida financeira liquida",
    ]),
    ("gross_debt_mm", [
        "divida bruta",
        "endividamento bruto",
        "divida financeira bruta",
        "gross debt",
        "divida total",
    ]),
    ("cash_mm", [
        "caixa e equivalentes de caixa e titulos",
        "caixa e equivalentes de caixa",
        "caixa e aplicacoes financeiras",
        "caixa e equivalentes",
        "disponibilidades e aplicacoes",
        "disponibilidades",
        "caixa e titulos",
        "cash and cash equivalents",
    ]),
    ("depreciation_mm", [
        "depreciacao, amortizacao e exaustao",
        "depreciacao e amortizacao",
        "depreciacao, amortizacao",
        "depreciation and amortization",
        "depreciation",
        "d&a",
    ]),
    ("capex_mm", [
        "investimentos em ativos imobilizados",
        "investimentos em capex",
        "capital expenditure",
        "capex",
    ]),
]


def _match_field(cell: str) -> Optional[str]:
    """Retorna o nome do campo financeiro se a célula corresponde a alguma keyword."""
    norm = _norm(cell)
    for field_name, keywords in FIELD_KEYWORDS:
        for kw in keywords:
            if kw in norm:
                return field_name
    return None


# ---------------------------------------------------------------------------
# Detecção de período nos cabeçalhos
# ---------------------------------------------------------------------------

_QTR_ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

def _parse_period_label(header: str) -> Optional[dict]:
    """
    Converte string de cabeçalho em info de período.
    Exemplos: '1T26' → quarterly 2026-03-31 | '2025' → annual 2025-12-31
    """
    h = str(header).strip()
    # Trimestral: 1T26, 1T2026, Q1 2026, Q1/26, 1°T26
    m = re.search(r"([1-4])\s*[Tt°º]\s*(\d{2,4})", h)
    if not m:
        m = re.search(r"[Qq]\s*([1-4])[\s/\-]*(\d{2,4})", h)
        if m:
            # swap groups for Q1 2026 format
            q, yr = int(m.group(1)), int(m.group(2))
        else:
            m = None
    if m:
        try:
            q = int(m.group(1))
            yr = int(m.group(2))
            if yr < 100:
                yr += 2000
            return {"label": f"{q}T{str(yr)[2:]}", "date": f"{yr}-{_QTR_ENDS[q]}", "type": "quarterly"}
        except (ValueError, IndexError):
            pass

    # Anual: "2025", "FY2025", "Ano 2025", "12M 2025", "Jan-Dez 2025"
    yr_m = re.search(r"\b(20\d{2})\b", h)
    if yr_m:
        norm_h = _norm(h)
        if any(x in norm_h for x in ["fy", "ano", "12m", "jan", "dez", "dec", "full", "year", "exercicio"]):
            yr = int(yr_m.group(1))
            return {"label": str(yr), "date": f"{yr}-12-31", "type": "annual"}
        # Plain 4-digit year with nothing else significant
        if re.fullmatch(r"20\d{2}", h.strip()):
            yr = int(h.strip())
            return {"label": str(yr), "date": f"{yr}-12-31", "type": "annual"}

    return None


# ---------------------------------------------------------------------------
# Detecção de unidade (R$ milhões / R$ mil / R$ bilhões)
# ---------------------------------------------------------------------------

def _detect_unit_factor(text: str) -> float:
    """
    Retorna fator multiplicativo para converter os valores da tabela para R$ milhões.
    - 'R$ mil' ou 'em milhares'  → os valores estão em R$mil   → fator = 0.001
    - 'R$ milhões'               → já em R$mm                  → fator = 1.0
    - 'R$ bilhões'               → valores em R$bi             → fator = 1000.0
    """
    n = _norm(text[:5000])  # analisa apenas o início do documento
    if any(x in n for x in ["r$ bilhoes", "em bilhoes", "bilhoes de reais", "r$ bi "]):
        return 1000.0
    if any(x in n for x in ["em r$ mil\n", "em r$ mil ", "(r$ mil)", "em milhares", "r$ mil)"]):
        return 0.001
    return 1.0  # default: R$ milhões


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class ParsedPeriod:
    label: str                          # ex: "1T26", "2025"
    date: str                           # ex: "2026-03-31"
    period_type: str                    # "quarterly" | "annual" | "unknown"
    fields: dict = field(default_factory=dict)  # campo → valor em R$mm


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

def parse_release_pdf(pdf_bytes: bytes) -> list[ParsedPeriod]:
    """
    Analisa um PDF de release de resultados e extrai indicadores financeiros.

    Estratégia:
    1. Extrai todas as tabelas via pdfplumber
    2. Detecta período nos cabeçalhos (row 0 ou row 1)
    3. Faz match de keywords nas células da primeira coluna
    4. Extrai valores numéricos das colunas de período detectadas
    5. Aplica fator de unidade (R$mm / R$mil / R$bi)

    Retorna lista de ParsedPeriod ordenada do mais recente ao mais antigo.
    """
    accumulated: dict[str, ParsedPeriod] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "".join(page.extract_text() or "" for page in pdf.pages[:8])
        unit_factor = _detect_unit_factor(full_text)

        for page in pdf.pages[:30]:
            for table in (page.extract_tables() or []):
                if not table or len(table) < 2:
                    continue

                # --- Detectar colunas de período ---
                period_cols: dict[int, dict] = {}

                for header_row_idx in (0, 1):
                    if header_row_idx >= len(table):
                        break
                    for col_idx, cell in enumerate(table[header_row_idx]):
                        if col_idx == 0 or cell is None:
                            continue
                        pinfo = _parse_period_label(str(cell))
                        if pinfo:
                            period_cols[col_idx] = pinfo
                    if period_cols:
                        break

                # Fallback: sem cabeçalho de período — usa a 1ª coluna numérica como "atual"
                if not period_cols:
                    for row in table[1:3]:
                        if not row:
                            continue
                        for col_idx in range(1, len(row)):
                            if row[col_idx] and _parse_num(str(row[col_idx])) is not None:
                                period_cols[col_idx] = {
                                    "label": "atual", "date": "", "type": "unknown"
                                }
                                break
                        if period_cols:
                            break

                if not period_cols:
                    continue

                # --- Extrair dados por linha ---
                for row in table[1:]:
                    if not row or row[0] is None:
                        continue
                    field_name = _match_field(str(row[0]))
                    if not field_name:
                        continue

                    for col_idx, pinfo in period_cols.items():
                        if col_idx >= len(row):
                            continue
                        val = _parse_num(str(row[col_idx] or ""))
                        if val is None:
                            continue
                        pkey = pinfo["label"]
                        if pkey not in accumulated:
                            accumulated[pkey] = ParsedPeriod(
                                label=pinfo["label"],
                                date=pinfo["date"],
                                period_type=pinfo["type"],
                            )
                        # Não sobrescreve se já foi preenchido por tabela anterior
                        if field_name not in accumulated[pkey].fields:
                            accumulated[pkey].fields[field_name] = round(val * unit_factor, 1)

    # Ordenar: mais recente primeiro (por data; sem data vai para o fim)
    return sorted(
        accumulated.values(),
        key=lambda p: p.date if p.date else "0000",
        reverse=True,
    )
