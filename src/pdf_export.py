"""
Exportação da análise de crédito para PDF via WeasyPrint.

Gera HTML com layout profissional (1-2 páginas A4) e converte para PDF.
Charts são gerados com matplotlib (PNG base64 embedded).
"""

from __future__ import annotations

import base64
import io
import statistics
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Charts matplotlib → PNG bytes
# ---------------------------------------------------------------------------

def _build_peers_chart(
    peers: list[dict], opp_spread: float, opp_label: str
) -> Optional[bytes]:
    """Gráfico horizontal: spread dos peers com ativo analisado destacado."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if not peers:
            return None

        data = sorted(peers, key=lambda x: x.get("spread", 0))[:18]
        labels = [
            f"{p.get('ticker', '')[:8]} – {(p.get('issuer') or '')[:16]}"
            for p in data
        ]
        values = [p.get("spread", 0) for p in data]

        fig, ax = plt.subplots(figsize=(7, max(3.0, len(data) * 0.28 + 0.8)))
        ax.barh(labels, values, color="#5c85d6", alpha=0.8, height=0.6)

        ax.axvline(x=opp_spread, color="#c0392b", linewidth=2, linestyle="--")
        ax.text(
            opp_spread + max(values) * 0.015,
            len(labels) - 0.8,
            f"{opp_label}: {opp_spread:.2f}%",
            color="#c0392b", fontsize=7.5, fontweight="bold", va="top",
        )

        if values:
            med = statistics.median(values)
            ax.axvline(x=med, color="#e67e22", linewidth=1.2, linestyle=":")
            ax.text(
                med + max(values) * 0.01, 0.3,
                f"Med: {med:.2f}%",
                color="#e67e22", fontsize=7,
            )

        ax.set_xlabel("Spread (% a.a.)", fontsize=8)
        ax.set_title("Comparação de Spread vs Peers", fontsize=9, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="x", alpha=0.25, linewidth=0.5)
        ax.tick_params(axis="y", labelsize=7)
        fig.tight_layout(pad=0.6)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


def _build_history_chart(annual_history: list[dict]) -> Optional[bytes]:
    """Gráfico de evolução DRE com linha de DL/EBITDA."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        valid = [
            h for h in annual_history
            if not h.get("error") and h.get("net_revenue_mm")
        ]
        if len(valid) < 2:
            return None

        years  = [h["report_date"][:4] for h in valid]
        rev    = [h.get("net_revenue_mm") or 0 for h in valid]
        ebitda = [h.get("ebitda_mm") or 0 for h in valid]
        nd_v   = [h.get("nd_ebitda") for h in valid]

        x = np.arange(len(years))
        w = 0.35
        fig, ax1 = plt.subplots(figsize=(5.5, 3.2))
        ax1.bar(x - w / 2, rev,    w, label="Receita", color="#1565c0", alpha=0.85)
        ax1.bar(x + w / 2, ebitda, w, label="EBITDA",  color="#2e7d32", alpha=0.85)
        ax1.set_xticks(x)
        ax1.set_xticklabels(years, fontsize=8)
        ax1.set_ylabel("R$ mm", fontsize=8)
        ax1.tick_params(axis="y", labelsize=7.5)
        ax1.spines["top"].set_visible(False)
        ax1.legend(loc="upper left", fontsize=7.5, framealpha=0.7)

        if any(v is not None for v in nd_v):
            ax2 = ax1.twinx()
            nd_clean = [v if v is not None else float("nan") for v in nd_v]
            ax2.plot(x, nd_clean, "o--", color="#c0392b", lw=1.8, ms=5, label="DL/EBITDA")
            ax2.axhline(y=3.5, color="#c0392b", lw=0.8, ls=":", alpha=0.5)
            ax2.set_ylabel("DL/EBITDA (x)", color="#c0392b", fontsize=7.5)
            ax2.tick_params(axis="y", labelcolor="#c0392b", labelsize=7.5)
            ax2.spines["top"].set_visible(False)
            ax2.legend(loc="upper right", fontsize=7.5, framealpha=0.7)

        ax1.set_title("Evolução DRE & Alavancagem", fontsize=9, fontweight="bold")
        fig.tight_layout(pad=0.6)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


def _b64(img: bytes) -> str:
    return base64.b64encode(img).decode()


# ---------------------------------------------------------------------------
# HTML template (WeasyPrint-compatible: display:table, no flexbox/grid)
# ---------------------------------------------------------------------------

_REC_STYLE = {
    "Atrativo":      ("#155724", "#d4edda"),
    "Neutro":        ("#856404", "#fff3cd"),
    "Não Atrativo":  ("#721c24", "#f8d7da"),
}


def render_html(
    *,
    opp,
    result,
    report,
    financials,
    annual_history: list[dict],
    peers: list,
    macro: dict,
) -> str:
    """Gera o HTML completo do relatório de análise de crédito."""
    dt = datetime.now().strftime("%d/%m/%Y %H:%M")
    rec = report.recommendation
    rec_fg, rec_bg = _REC_STYLE.get(rec, ("#333", "#eee"))
    score_color = (
        "#155724" if report.credit_score >= 70
        else "#856404" if report.credit_score >= 45
        else "#721c24"
    )

    mat_str = opp.maturity_date.strftime("%d/%m/%Y") if opp.maturity_date else "—"
    rating_str = opp.rating.value if opp.rating else "N/R"

    # Charts
    peers_dicts = [p.model_dump() if hasattr(p, "model_dump") else p for p in peers]
    opp_label = opp.ticker or opp.issuer[:15]
    chart_peers = _build_peers_chart(peers_dicts, opp.spread, opp_label)
    chart_hist  = _build_history_chart(annual_history)

    # Macro pills
    macro_cells = ""
    for k, v in [
        ("Meta Selic", f"{macro['selic_meta']:.2f}% a.a." if macro.get("selic_meta") else None),
        ("IPCA 12m",   f"{macro['ipca_12m']:.2f}%"        if macro.get("ipca_12m")   else None),
        ("USD/BRL",    f"R${macro['usd_brl']:.2f}"         if macro.get("usd_brl")    else None),
        ("PIB",        f"{macro['pib_growth']:.2f}%"       if macro.get("pib_growth") else None),
    ]:
        if v is not None:
            macro_cells += (
                f'<td style="border:none;text-align:center;padding:3px 7px;'
                f'background:#e8eaf6;border-radius:4px">'
                f'<div style="font-size:10.5pt;font-weight:bold;color:#1a237e">{v}</div>'
                f'<div style="font-size:6.5pt;color:#666;margin-top:1px">{k}</div></td>'
                f'<td style="border:none;width:5px"></td>'
            )

    # Financial table
    fin_rows = ""
    if financials:
        fd = financials.model_dump() if hasattr(financials, "model_dump") else financials

        def _fv(v, fmt="mm"):
            if v is None or (isinstance(v, float) and v != v):
                return "—"
            if fmt == "mm":  return f"R$ {v:,.0f}mm"
            if fmt == "x":   return f"{v:.1f}x"
            if fmt == "pct": return f"{v:.1f}%"
            return str(v)

        period = f"{fd.get('report_date', '')[:7]} ({fd.get('period_type', '').upper()})"
        nd = fd.get("nd_ebitda")
        nd_color = (
            "#155724" if (nd is not None and nd < 2)
            else "#856404" if (nd is not None and nd < 3.5)
            else "#721c24"
        )
        fin_rows = f"""
<tr><th colspan="2" style="font-size:6.5pt;font-weight:normal;opacity:.75">
  {period} · {fd.get('data_source', '').capitalize()}
</th></tr>
<tr><td>Receita Líq.</td>   <td>{_fv(fd.get('net_revenue_mm'))}</td></tr>
<tr><td>EBITDA</td>          <td>{_fv(fd.get('ebitda_mm'))}</td></tr>
<tr><td>Mg. EBITDA</td>      <td>{_fv(fd.get('ebitda_margin_pct'), 'pct')}</td></tr>
<tr><td>Lucro Líq.</td>      <td>{_fv(fd.get('net_income_mm'))}</td></tr>
<tr><td>Dívida Bruta</td>    <td>{_fv(fd.get('gross_debt_mm'))}</td></tr>
<tr><td>Dívida Líq.</td>     <td>{_fv(fd.get('net_debt_mm'))}</td></tr>
<tr style="font-weight:bold">
  <td>DL / EBITDA</td>
  <td style="color:{nd_color}">{_fv(nd, 'x')}</td>
</tr>"""

    fin_section = (
        f'<table style="width:auto;min-width:170px;border-collapse:collapse;font-size:7.8pt">'
        f'{fin_rows}</table>'
        if fin_rows
        else '<p style="font-size:7.5pt;color:#999">Sem dados financeiros cadastrados.</p>'
    )

    img_hist  = (
        f'<img src="data:image/png;base64,{_b64(chart_hist)}" '
        f'style="width:100%;max-width:330px;margin-top:4px" />'
        if chart_hist else ""
    )
    img_peers = (
        f'<img src="data:image/png;base64,{_b64(chart_peers)}" style="width:100%" />'
        if chart_peers else ""
    )

    # Peers table (top 10 by spread desc, analyzed highlighted)
    sorted_peers = sorted(peers_dicts, key=lambda x: x.get("spread", 0), reverse=True)[:10]
    peers_rows = ""
    for p in sorted_peers:
        diff = p.get("spread", 0) - opp.spread
        dc = "#721c24" if diff > 0.2 else "#155724" if diff < -0.2 else "#856404"
        peers_rows += (
            f"<tr>"
            f"<td>{p.get('ticker', '')}</td>"
            f"<td>{(p.get('issuer') or '')[:22]}</td>"
            f"<td>{p.get('spread', 0):.2f}%</td>"
            f"<td style='color:{dc}'>{diff:+.2f}%</td>"
            f"<td>{p.get('duration_years', 0):.1f}a</td>"
            f"<td>{p.get('rating') or 'NR'}</td>"
            f"</tr>"
        )

    peer_spreads = [p.get("spread", 0) for p in peers_dicts if p.get("spread") is not None]
    med_str = f"{statistics.median(peer_spreads):.2f}%" if peer_spreads else "—"
    diff_vs_med = result.spread_vs_median
    diff_color_med = "#155724" if diff_vs_med > 0 else "#721c24"

    strengths_li = "".join(f"<li>{s}</li>" for s in report.strengths)
    risks_li = "".join(f"<li>{r}</li>" for r in report.risks)
    notes_block = (
        f'<p style="font-size:7.5pt;font-style:italic;margin-top:5px">Obs.: {opp.notes}</p>'
        if opp.notes else ""
    )

    # KPI pills
    pills = "".join(
        f'<td style="border:none;padding:2px 4px;background:#f0f4ff;border-radius:3px">'
        f'<span style="font-size:7.5pt;color:#1a237e"><strong>{l}:</strong> {v}</span></td>'
        f'<td style="border:none;width:4px"></td>'
        for l, v in [
            ("Tipo",       opp.asset_type.value),
            ("Indexador",  opp.indexer.value),
            ("Spread",     f"{opp.spread:.2f}% a.a."),
            ("Duration",   f"{opp.duration_years:.1f} anos"),
            ("Vencimento", mat_str),
            ("Rating",     rating_str),
            ("Garantia",   opp.collateral or "Clean"),
            ("Isento IR",  "Sim ✓" if opp.is_incentivized else "Não"),
        ]
    )

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><style>
@page {{
  size: A4;
  margin: 12mm 13mm 15mm 13mm;
  @bottom-left   {{ content: "Análise de Crédito · Uso Interno";
                   font-size: 6pt; color: #bbb; font-family: Arial; }}
  @bottom-center {{ content: counter(page) " de " counter(pages);
                   font-size: 6pt; color: #bbb; font-family: Arial; }}
  @bottom-right  {{ content: "Gerado em {dt}";
                   font-size: 6pt; color: #bbb; font-family: Arial; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, Helvetica, sans-serif; font-size: 8.5pt;
        color: #1a1a2e; line-height: 1.45; }}
h3 {{ font-size: 8pt; font-weight: bold; color: #1a237e;
      border-bottom: 1px solid #c5cae9; padding-bottom: 2px;
      margin: 8px 0 4px 0; text-transform: uppercase; letter-spacing: 0.4px; }}
p {{ margin-bottom: 3px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 7.8pt; }}
th {{ background: #1a237e; color: white; padding: 3px 6px;
      font-weight: normal; text-align: left; }}
td {{ padding: 2.5px 6px; border-bottom: 1px solid #e8eaf6; vertical-align: top; }}
tr:nth-child(even) td {{ background: #f8f9ff; }}
ul {{ list-style: none; padding: 0; margin: 0; }}
li {{ padding: 1.5px 0 1.5px 14px; position: relative; font-size: 8.3pt; }}
.strength-item {{ color: #1b5e20; }}
.strength-item::before {{ content: "✓"; position: absolute; left: 0;
                          color: #2e7d32; font-weight: bold; }}
.risk-item {{ color: #4e342e; }}
.risk-item::before {{ content: "▲"; position: absolute; left: 0;
                      color: #e65100; font-size: 7pt; top: 3px; }}
</style>
</head>
<body>

<!-- CABEÇALHO -->
<table style="width:100%;border:none;margin-bottom:5px">
  <tr>
    <td style="border:none;padding:0;vertical-align:bottom">
      <div style="font-size:15pt;font-weight:bold;color:#1a237e;line-height:1.2">
        {opp.issuer}
      </div>
      <div style="font-size:8pt;color:#555;margin-top:2px">
        {opp.asset_type.value} &middot; {opp.sector} &middot;
        {opp.indexer.value} {opp.spread:.2f}% a.a. &middot;
        Duration {opp.duration_years:.1f}a &middot; Vcto {mat_str} &middot; Rating {rating_str}
      </div>
    </td>
    <td style="border:none;padding:0;text-align:right;width:175px;vertical-align:bottom">
      <div style="display:inline-block;background:{rec_bg};color:{rec_fg};
                  padding:4px 12px;border-radius:4px;font-weight:bold;font-size:9pt">
        {rec}
      </div>
      <div style="font-size:22pt;font-weight:bold;color:{score_color};
                  margin-top:2px;line-height:1.1">
        {report.credit_score:.0f}
        <span style="font-size:8pt;color:#aaa;font-weight:normal">/100</span>
      </div>
    </td>
  </tr>
</table>
<hr style="border:none;border-top:2.5px solid #1a237e;margin-bottom:6px"/>

<!-- KPI PILLS -->
<table style="border:none;margin-bottom:6px"><tr>{pills}</tr></table>

<!-- RESUMO EXECUTIVO -->
<h3>📋 Resumo Executivo</h3>
<p>{report.executive_summary}</p>
<p style="margin-top:3px">{report.offer_highlights}</p>

<!-- MACRO & SETOR -->
<h3>🌐 Contexto Macroeconômico &amp; Setor</h3>
<table style="border:none;margin-bottom:5px">
  <tr>
    <td style="border:none;padding:0;vertical-align:top;width:230px">
      <table style="border:none;border-spacing:0"><tr>{macro_cells}</tr></table>
    </td>
    <td style="border:none;padding:0 0 0 10px;vertical-align:top">
      <p>{report.macro_context}</p>
      <p style="margin-top:3px">{report.sector_analysis}</p>
    </td>
  </tr>
</table>

<!-- FUNDAMENTOS -->
<h3>💼 Fundamentos do Emissor</h3>
<table style="border:none">
  <tr>
    <td style="border:none;padding:0;vertical-align:top;width:190px">
      {fin_section}
    </td>
    <td style="border:none;padding:0 0 0 10px;vertical-align:top">
      <p>{report.financial_highlights}</p>
      {img_hist}
    </td>
  </tr>
</table>

<!-- PEERS -->
<h3>📊 Comparação com Peers de Mercado</h3>
<table style="border:none">
  <tr>
    <td style="border:none;padding:0;vertical-align:top;width:46%">
      {img_peers}
      <p style="font-size:7.3pt;color:#555;margin-top:3px">
        {report.peer_comparison_narrative}
      </p>
      <p style="font-size:7.5pt;margin-top:2px">
        Spread vs mediana:
        <strong style="color:{diff_color_med}">{diff_vs_med:+.2f}%</strong>
        &nbsp;|&nbsp; Mediana: {med_str} &nbsp;|&nbsp; {len(peers)} peers
      </p>
    </td>
    <td style="border:none;padding:0 0 0 10px;vertical-align:top;width:54%">
      <table>
        <tr>
          <th>Ticker</th><th>Emissor</th><th>Spread</th>
          <th>vs Este</th><th>Dur.</th><th>Rating</th>
        </tr>
        <tr style="background:#fff3e0;font-weight:bold">
          <td>{opp.ticker or '—'}</td>
          <td>{opp.issuer[:22]}</td>
          <td style="color:{diff_color_med}">{opp.spread:.2f}%</td>
          <td>—</td>
          <td>{opp.duration_years:.1f}a</td>
          <td>{rating_str}</td>
        </tr>
        {peers_rows}
      </table>
    </td>
  </tr>
</table>

<!-- PONTOS FORTES / RISCOS -->
<h3>✅ Pontos Fortes &nbsp; · &nbsp; ⚠️ Pontos de Atenção</h3>
<table style="border:none">
  <tr>
    <td style="border:none;padding:0;vertical-align:top;width:50%">
      <ul>
        {"".join(f'<li class="strength-item">{s}</li>' for s in report.strengths)}
      </ul>
    </td>
    <td style="border:none;padding:0 0 0 14px;vertical-align:top;width:50%">
      <ul>
        {"".join(f'<li class="risk-item">{r}</li>' for r in report.risks)}
      </ul>
    </td>
  </tr>
</table>

<!-- CONCLUSÃO -->
<h3>🏁 Conclusão &amp; Recomendação</h3>
<div style="background:{rec_bg};border-left:3px solid {rec_fg};
            padding:5px 10px;border-radius:0 4px 4px 0;margin-bottom:4px">
  <span style="font-weight:bold;font-size:9pt;color:{rec_fg}">{rec}</span>
  <span style="font-size:7.5pt;color:{rec_fg};margin-left:8px">
    Score: {report.credit_score:.0f}/100
  </span>
  <p style="margin-top:3px;font-size:8.5pt;color:#1a1a2e">
    {report.recommendation_rationale}
  </p>
</div>
{notes_block}

<p style="font-size:6.5pt;color:#ccc;margin-top:8px">
  Esta análise é para uso interno e não constitui recomendação pública de investimento.
</p>

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def render_pdf(html: str) -> bytes:
    """Converte HTML para PDF via WeasyPrint. Levanta ImportError se não instalado."""
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except ImportError:
        raise ImportError(
            "WeasyPrint não instalado. Execute: pip install weasyprint"
        )
    except Exception as exc:
        raise RuntimeError(f"Erro ao gerar PDF: {exc}") from exc
