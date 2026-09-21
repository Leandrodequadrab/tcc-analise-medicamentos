"""Diagnóstico somente leitura dos períodos com vendas em uma filial."""

import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    ARQUIVO_ENV,
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)


SQL_PERIODO_DISPONIVEL = text(
    """
    SELECT
        cod_rede,
        cod_filial,
        MIN(dat_emissao) AS primeira_data,
        MAX(dat_emissao) AS ultima_data,
        COUNT(*) AS quantidade_vendas
    FROM public.cadcvend
    WHERE cod_filial = :filial
    GROUP BY cod_rede, cod_filial
    ORDER BY cod_rede, cod_filial
    """
)

SQL_DIAS_RECENTES = text(
    """
    SELECT
        dat_emissao AS data,
        COUNT(*) AS quantidade_vendas
    FROM public.cadcvend
    WHERE cod_filial = :filial
      AND dat_emissao IS NOT NULL
    GROUP BY dat_emissao
    ORDER BY dat_emissao DESC
    LIMIT 10
    """
)


def carregar_filial_teste():
    """Lê e valida somente FILIAL_TESTE, sem alterar os outros filtros."""

    load_dotenv(ARQUIVO_ENV)
    valor = os.getenv("FILIAL_TESTE")
    if not valor:
        raise ErroConfiguracaoFarmacia("Configure FILIAL_TESTE no arquivo .env.")

    try:
        return int(valor)
    except ValueError as erro:
        raise ErroConfiguracaoFarmacia(
            "FILIAL_TESTE deve conter um número inteiro."
        ) from erro


def inspecionar_periodo_vendas():
    """Executa os dois SELECTs e devolve os resultados em DataFrames."""

    filial = carregar_filial_teste()
    parametros = {"filial": filial}

    with conectar_farmacia_somente_leitura() as conexao:
        periodo = conexao.execute(
            SQL_PERIODO_DISPONIVEL, parametros
        ).mappings().all()
        dias_recentes = conexao.execute(
            SQL_DIAS_RECENTES, parametros
        ).mappings().all()

    return {
        "filial": filial,
        "periodo": pd.DataFrame([dict(linha) for linha in periodo]),
        "dias_recentes": pd.DataFrame([dict(linha) for linha in dias_recentes]),
    }


def main():
    try:
        resultado = inspecionar_periodo_vendas()
    except ErroConfiguracaoFarmacia as erro:
        print("INSPEÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONSULTAR O sgfpod1")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    print(f"FILIAL CONSULTADA: {resultado['filial']}")

    print("\nPERÍODO DISPONÍVEL PARA A FILIAL")
    if resultado["periodo"].empty:
        print("Nenhuma venda encontrada para a filial configurada.")
    else:
        print(resultado["periodo"].to_string(index=False))

    print("\nDIAS MAIS RECENTES COM VENDAS")
    if resultado["dias_recentes"].empty:
        print("Nenhum dia com vendas foi encontrado.")
    else:
        print(resultado["dias_recentes"].to_string(index=False))


if __name__ == "__main__":
    main()
