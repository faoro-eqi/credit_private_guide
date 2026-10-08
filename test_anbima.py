"""
Teste de conectividade com a API de debêntures/CRI/CRA.

Uso:
    cd credit-analysis
    python3 test_anbima.py

Pré-requisito: arquivo .env com DATA_B3_TOKEN.
"""

import sys
from pathlib import Path

# Garante que src/ é encontrado
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from src.data_sources import (
    DATA_B3_TOKEN,
    DATA_B3_BASE_URL,
    test_connection,
)


def main():
    print("=" * 60)
    print("  Teste de Conectividade — API Data B3")
    print("=" * 60)
    print(f"  Base URL : {DATA_B3_BASE_URL}")
    print(f"  Token   : {'configurado ✓' if DATA_B3_TOKEN else 'NÃO configurado ✗'}")
    print()

    if not DATA_B3_TOKEN:
        print("⚠️  DATA_B3_TOKEN não encontrado no .env")
        print("   Copie .env.example → .env e cole seu token.")
        sys.exit(1)

    print("Testando...")
    result = test_connection()

    if not result["endpoint_ok"]:
        print(f"❌ Endpoint falhou: {result['error']}")
        sys.exit(1)

    print("✅ Conexão OK!")
    for group, count in result["record_counts"].items():
        print(f"   {group}: {count} ativos")

    if result["sample_tickers"]:
        print(f"\n   Amostra de tickers: {', '.join(result['sample_tickers'][:6])}")

    print()
    print("Tudo certo! Rode o dashboard com:")
    print("  streamlit run app.py")


if __name__ == "__main__":
    main()
