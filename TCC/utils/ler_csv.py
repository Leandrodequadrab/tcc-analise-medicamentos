from pathlib import Path

import pandas as pd


CAMINHO_CSV = Path(__file__).resolve().parents[1] / "dados" / "vendas_ficticias.csv"

vendas = pd.read_csv(
    CAMINHO_CSV,
    encoding="utf-8",
    parse_dates=["data"],
    true_values=["sim"],
    false_values=["nao"],
)

print("PRIMEIRAS LINHAS")
print(vendas.head())

print("\nQUANTIDADE DE REGISTROS")
print(len(vendas))

print("\nNOMES DAS COLUNAS")
print(vendas.columns.tolist())

print("\nTIPOS DOS DADOS")
print(vendas.dtypes)
