"""
Entrypoint — define navegação e tema global.
Run: streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Mercado de Crédito",
    page_icon="📊",
    layout="wide",
)

pg = st.navigation(
    [
        st.Page("pages/mercado.py",              title="Análise Mercado",    icon="📊"),
        st.Page("pages/3_Oscilacoes.py",         title="Análise de Risco",   icon="📉"),
        st.Page("pages/7_Passivo_EQI.py",        title="Passivo EQI",        icon="📊"),
        st.Page("pages/4_Admin.py",              title="Admin",              icon="⚙️"),
    ]
)
pg.run()
