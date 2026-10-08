# Credit Analysis — CRI, CRA e Debêntures

Dashboard de análise de crédito para renda fixa corporativa.

## Como começar

```bash
cd credit-analysis

# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar credenciais (opcional — sem elas usa dados sintéticos)
cp .env.example .env
# edite .env com suas credenciais ANBIMA

# 3. Rodar o app
streamlit run app.py
```

## Estrutura

```
credit-analysis/
├── app.py                  # Dashboard Streamlit (ponto de entrada)
├── requirements.txt
├── .env.example
├── src/
│   ├── models.py           # Modelos Pydantic (Opportunity, PeerAsset, AnalysisResult)
│   ├── extractors.py       # Extração de dados de PDF e Excel
│   ├── data_sources.py     # Integração ANBIMA API + fallback local
│   ├── analysis.py         # Motor de score e análise
│   └── charts.py           # Visualizações Plotly
└── data/
    └── peers_sample.csv    # (opcional) CSV com peers para fallback offline
```

## Fluxo de uso

1. **Upload** do prospecto em PDF ou Excel — os campos são extraídos automaticamente
2. **Revisão** dos campos extraídos no formulário lateral
3. **Análise** — clique em "Analisar"
4. **Dashboard** exibe:
   - Score de crédito (0–100)
   - Comparação de spread com peers
   - Radar de risco/retorno
   - Fatores positivos e de risco
   - Tabela de peers

## Dados de peers

- **Com API ANBIMA**: dados reais de debêntures e CRI/CRA
  - Cadastre-se em https://developers.anbima.com.br/
- **Sem API**: gera 10 peers sintéticos para demonstração (não usar em análises reais)
- **CSV local**: coloque `data/peers_sample.csv` com colunas:
  `ticker, issuer, sector, asset_type, indexer, spread, duration_years, rating, is_incentivized`
