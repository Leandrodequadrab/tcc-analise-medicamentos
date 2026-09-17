from pathlib import Path

import pandas as pd

from limpeza import limpar_dados


PASTA_PROJETO = Path(__file__).resolve().parents[1]
CAMINHO_CSV = PASTA_PROJETO / "dados" / "vendas_com_problemas.csv"

dados = pd.read_csv(CAMINHO_CSV, encoding="utf-8-sig")
resultado = limpar_dados(dados)
relatorio = resultado["relatorio"]

print("RELATÓRIO DA LIMPEZA")
print(f"Quantidade antes da limpeza: {relatorio['quantidade_antes']}")
print(f"Quantidade depois da limpeza: {relatorio['quantidade_depois']}")
print(f"Duplicados removidos: {relatorio['duplicados_removidos']}")
print(f"Registros inválidos encontrados: {relatorio['registros_invalidos']}")

print("\nDADOS LIMPOS")
print(resultado["dados_limpos"].to_string(index=False))

print("\nREGISTROS DUPLICADOS REMOVIDOS")
print(resultado["registros_duplicados"].to_string(index=False))

print("\nREGISTROS INVÁLIDOS")
print(resultado["registros_invalidos"].to_string(index=False))
