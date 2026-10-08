"""
Research de crédito — análise narrativa via OpenAI ou Anthropic + dados macro do BCB.

Detecta automaticamente qual chave está disponível no .env:
  OPENAI_API_KEY  → usa OpenAI (gpt-4o por padrão, ou OPENAI_MODEL)
  ANTHROPIC_API_KEY → usa Anthropic Claude (fallback)
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Optional

import httpx
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Modelo do relatório gerado pela IA
# ---------------------------------------------------------------------------

class ResearchReport(BaseModel):
    executive_summary: str
    offer_highlights: str
    macro_context: str
    sector_analysis: str
    financial_highlights: str
    peer_comparison_narrative: str
    strengths: list[str]
    risks: list[str]
    recommendation: str
    recommendation_rationale: str
    credit_score: float = Field(..., ge=0, le=100)


# ---------------------------------------------------------------------------
# BCB — dados macroeconômicos (SGS API pública)
# ---------------------------------------------------------------------------

_BCB_SERIES = {
    "selic_meta": 432,    # Meta Selic % a.a.
    "ipca_12m":   13522,  # IPCA acumulado 12 meses
    "usd_brl":    1,      # Câmbio PTAX USD/BRL
    "pib_growth": 4380,   # PIB crescimento acumulado ano
}


def fetch_macro_bcb() -> dict:
    """Busca indicadores macroeconômicos do BCB via SGS (API pública, sem autenticação)."""
    result: dict = {}
    for name, series_id in _BCB_SERIES.items():
        try:
            url = (
                f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}"
                f"/dados/ultimos/1?formato=json"
            )
            resp = httpx.get(url, timeout=8)
            data = resp.json()
            if data:
                val = data[-1].get("valor", "")
                result[name] = float(str(val).replace(",", "."))
        except Exception:
            result[name] = None
    return result


# ---------------------------------------------------------------------------
# Geração do research — OpenAI ou Anthropic
# ---------------------------------------------------------------------------

def generate_research(
    *,
    opportunity_data: dict,
    financials_data: Optional[dict],
    annual_history: list[dict],
    peers_data: list[dict],
    macro_data: dict,
    analysis_result: dict,
) -> ResearchReport:
    """
    Gera análise de crédito completa via IA.

    Prioridade:
      1. OPENAI_API_KEY  → OpenAI (gpt-4o ou OPENAI_MODEL)
      2. ANTHROPIC_API_KEY → Anthropic Claude (ANTHROPIC_MODEL)
      3. Fallback sem IA
    """
    openai_key    = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if openai_key:
        return _generate_openai(opportunity_data, financials_data, annual_history,
                                peers_data, macro_data, analysis_result, openai_key)
    if anthropic_key:
        return _generate_anthropic(opportunity_data, financials_data, annual_history,
                                   peers_data, macro_data, analysis_result, anthropic_key)
    return _fallback_report(opportunity_data, analysis_result)


def _build_prompt(opportunity_data: dict, financials_data, annual_history, peers_data, macro_data, analysis_result) -> tuple[str, str]:
    """Retorna (system_prompt, user_prompt) compartilhados entre providers."""
    opp = opportunity_data
    res = analysis_result

    user_prompt = f"""Analise a seguinte oportunidade de investimento em renda fixa e gere um research completo em português do Brasil para comitê de investimento:

## OFERTA
- Emissor: {opp.get('issuer')}
- Tipo: {opp.get('asset_type')}
- Setor: {opp.get('sector')}
- Indexador: {opp.get('indexer')}
- Spread/Taxa: {opp.get('spread', 0):.2f}% a.a.
- Duration: {opp.get('duration_years', 0):.1f} anos
- Vencimento: {opp.get('maturity_date') or 'Não informado'}
- Rating: {opp.get('rating') or 'N/R'} ({opp.get('rating_agency') or 'agência não informada'})
- Garantia: {opp.get('collateral') or 'Clean/Sem garantia'} ({opp.get('collateral_coverage') or 'N/A'}% cobertura)
- Isento IR (Lei 12.431): {'Sim' if opp.get('is_incentivized') else 'Não'}
- Observações do analista: {opp.get('notes') or 'Nenhuma'}

## ANÁLISE QUANTITATIVA
- Score de crédito calculado: {res.get('credit_score', 50):.1f}/100
- Spread vs mediana dos peers: {res.get('spread_vs_median', 0):+.2f}%
- Spread vs P25: {res.get('spread_vs_p25', 0):+.2f}%
- Spread vs P75: {res.get('spread_vs_p75', 0):+.2f}%
- Recomendação quantitativa: {res.get('recommendation', 'Neutro')}

## FUNDAMENTOS FINANCEIROS DO EMISSOR
{_format_financials(financials_data, annual_history)}

## PEERS DE MERCADO ({len(peers_data)} ativos comparáveis)
{_format_peers(peers_data, opp)}

## INDICADORES MACROECONÔMICOS (BCB — dados mais recentes)
{_format_macro(macro_data)}

Retorne EXCLUSIVAMENTE um JSON válido, sem markdown nem texto adicional, com esta estrutura exata:
{{
  "executive_summary": "3-4 frases resumindo a oportunidade, o emissor e o principal atrativo ou ponto de atenção",
  "offer_highlights": "2-3 frases descrevendo os termos da emissão, estrutura de garantias e benefício fiscal",
  "macro_context": "2-3 frases sobre o cenário macro atual (juros, inflação, câmbio) e seu impacto neste tipo de ativo e setor",
  "sector_analysis": "3-4 frases sobre dinâmica do setor, posição competitiva do emissor, tendências e riscos setoriais",
  "financial_highlights": "3-4 frases sobre os principais números financeiros, alavancagem, margens e tendência de crédito",
  "peer_comparison_narrative": "2-3 frases comparando spread e duration desta emissão com os peers de mercado",
  "strengths": ["ponto positivo 1", "ponto positivo 2", "ponto positivo 3", "ponto positivo 4", "ponto positivo 5"],
  "risks": ["ponto de atenção 1", "ponto de atenção 2", "ponto de atenção 3", "ponto de atenção 4", "ponto de atenção 5"],
  "recommendation": "Atrativo",
  "recommendation_rationale": "2-3 frases justificando a recomendação final considerando risco/retorno",
  "credit_score": 72.0
}}"""

    system = (
        "Você é um analista sênior de crédito de uma gestora de investimentos brasileira de primeira linha. "
        "Sua especialidade é análise fundamentalista de crédito corporativo — CRI, CRA e debêntures. "
        "Escreva em português do Brasil, de forma técnica e objetiva, para relatórios de comitê de investimento. "
        "Responda APENAS com o JSON solicitado, sem markdown, sem ```json``` e sem qualquer texto adicional."
    )
    return system, user_prompt


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences do JSON retornado pela IA."""
    if "```" not in raw:
        return raw
    for part in raw.split("```"):
        part = part.strip()
        if part.startswith("json"):
            part = part[4:].strip()
        try:
            json.loads(part)
            return part
        except Exception:
            continue
    return raw


def _generate_openai(
    opportunity_data, financials_data, annual_history,
    peers_data, macro_data, analysis_result, api_key: str
) -> ResearchReport:
    try:
        from openai import OpenAI
    except ImportError:
        return _fallback_report(opportunity_data, analysis_result)

    system, user_prompt = _build_prompt(
        opportunity_data, financials_data, annual_history,
        peers_data, macro_data, analysis_result
    )
    try:
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(_strip_fences(raw))
        return ResearchReport(**data)
    except Exception as exc:
        report = _fallback_report(opportunity_data, analysis_result)
        report.risks.insert(0, f"⚠ Erro OpenAI: {str(exc)[:120]}")
        return report


def _generate_anthropic(
    opportunity_data, financials_data, annual_history,
    peers_data, macro_data, analysis_result, api_key: str
) -> ResearchReport:
    try:
        import anthropic
    except ImportError:
        return _fallback_report(opportunity_data, analysis_result)

    system, user_prompt = _build_prompt(
        opportunity_data, financials_data, annual_history,
        peers_data, macro_data, analysis_result
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")
        msg = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = msg.content[0].text.strip()
        data = json.loads(_strip_fences(raw))
        return ResearchReport(**data)
    except Exception as exc:
        report = _fallback_report(opportunity_data, analysis_result)
        report.risks.insert(0, f"⚠ Erro Anthropic: {str(exc)[:120]}")
        return report


# ---------------------------------------------------------------------------
# Fallback — quando API key não está configurada
# ---------------------------------------------------------------------------

def _fallback_report(opp: dict, res: dict) -> ResearchReport:
    rec = res.get("recommendation", "Neutro")
    score = float(res.get("credit_score", 50))
    diff = float(res.get("spread_vs_median", 0))
    return ResearchReport(
        executive_summary=(
            f"{opp.get('issuer')} emite {opp.get('asset_type')} indexado a {opp.get('indexer')} "
            f"com spread de {opp.get('spread', 0):.2f}% a.a. e duration de "
            f"{opp.get('duration_years', 0):.1f} anos. "
            "Configure OPENAI_API_KEY (ou ANTHROPIC_API_KEY) no .env para análise narrativa completa via IA."
        ),
        offer_highlights=(
            f"Ativo {('isento de IR (Lei 12.431)' if opp.get('is_incentivized') else 'tributado')}. "
            f"Garantia: {opp.get('collateral') or 'não informada'}. "
            f"Rating: {opp.get('rating') or 'N/R'}."
        ),
        macro_context="Dados macroeconômicos do BCB carregados. Configure ANTHROPIC_API_KEY para análise de contexto macro.",
        sector_analysis=f"Emissor do setor {opp.get('sector')}. Configure ANTHROPIC_API_KEY para análise setorial completa.",
        financial_highlights="Fundamentos financeiros carregados. Configure ANTHROPIC_API_KEY para síntese narrativa dos resultados.",
        peer_comparison_narrative=(
            f"Spread {diff:+.2f}% vs mediana dos peers. "
            f"{'Acima da mediana — prêmio positivo.' if diff > 0 else 'Abaixo da mediana — spread comprimido.'}"
        ),
        strengths=(
            ["Spread acima da mediana dos peers — prêmio adicional ao investidor"] if diff > 0
            else ["Ativo de emissor com estrutura organizada"]
        ),
        risks=["Configure ANTHROPIC_API_KEY para análise de riscos e pontos de atenção via IA"],
        recommendation=rec,
        recommendation_rationale=f"Score quantitativo de {score:.0f}/100 baseado em spread relativo, rating e estrutura.",
        credit_score=score,
    )


# ---------------------------------------------------------------------------
# Formatadores de contexto para o prompt
# ---------------------------------------------------------------------------

def _format_financials(fin: Optional[dict], history: list[dict]) -> str:
    if not fin and not history:
        return "Dados financeiros não disponíveis para este emissor."
    lines = []
    if fin:
        period = f"{fin.get('report_date', '')[:7]} ({fin.get('period_type', '').upper()})"
        lines.append(f"Dados mais recentes [{period}]:")
        for label, key, fmt in [
            ("Receita Líquida", "net_revenue_mm", "mm"),
            ("EBITDA",          "ebitda_mm",      "mm"),
            ("Margem EBITDA",   "ebitda_margin_pct", "pct"),
            ("Lucro Líquido",   "net_income_mm",  "mm"),
            ("Dívida Bruta",    "gross_debt_mm",  "mm"),
            ("Dívida Líquida",  "net_debt_mm",    "mm"),
            ("DL/EBITDA",       "nd_ebitda",      "x"),
        ]:
            v = fin.get(key)
            if v is None or (isinstance(v, float) and v != v):
                continue
            if fmt == "mm":
                lines.append(f"  {label}: R$ {v:,.0f}mm")
            elif fmt == "pct":
                lines.append(f"  {label}: {v:.1f}%")
            elif fmt == "x":
                qual = "(baixa)" if v < 2 else "(moderada)" if v < 3.5 else "(alta — atenção)"
                lines.append(f"  {label}: {v:.1f}x {qual}")

    valid_hist = [h for h in history if not h.get("error") and h.get("net_revenue_mm")]
    if len(valid_hist) >= 2:
        lines.append(f"\nHistórico anual ({len(valid_hist)} períodos):")
        for h in valid_hist[-4:]:
            rev = h.get("net_revenue_mm") or 0
            ebi = h.get("ebitda_mm") or 0
            nd = h.get("nd_ebitda")
            year = h.get("report_date", "")[:4]
            nd_str = f" | DL/EBITDA {nd:.1f}x" if nd is not None else ""
            lines.append(f"  {year}: Receita R${rev:,.0f}mm | EBITDA R${ebi:,.0f}mm{nd_str}")

    return "\n".join(lines)


def _format_peers(peers: list[dict], opp: dict) -> str:
    if not peers:
        return "Sem peers disponíveis para comparação."
    lines = []
    for p in peers[:12]:
        issuer = (p.get("issuer") or "")[:22]
        ticker = (p.get("ticker") or "")[:10]
        spread = p.get("spread", 0)
        dur = p.get("duration_years", 0)
        rating = p.get("rating") or "NR"
        lines.append(f"  {ticker:<10} {issuer:<24} {spread:.2f}% | {dur:.1f}a | {rating}")

    peer_spreads = [p.get("spread", 0) for p in peers if p.get("spread") is not None]
    if peer_spreads:
        med = statistics.median(peer_spreads)
        opp_spread = opp.get("spread", 0)
        lines.append(
            f"\nMediana peers: {med:.2f}% | Este ativo: {opp_spread:.2f}% ({opp_spread - med:+.2f}%)"
        )
    return "\n".join(lines)


def _format_macro(macro: dict) -> str:
    parts = []
    if macro.get("selic_meta"): parts.append(f"Meta Selic {macro['selic_meta']:.2f}% a.a.")
    if macro.get("ipca_12m"):   parts.append(f"IPCA 12m {macro['ipca_12m']:.2f}%")
    if macro.get("usd_brl"):    parts.append(f"USD/BRL R${macro['usd_brl']:.2f}")
    if macro.get("pib_growth"): parts.append(f"PIB {macro['pib_growth']:.2f}%")
    return " | ".join(parts) if parts else "Dados macro não disponíveis."
