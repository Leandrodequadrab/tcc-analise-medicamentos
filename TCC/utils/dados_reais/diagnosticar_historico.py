"""Diagnóstico agregado e somente leitura anterior à carga histórica."""

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


SQL_CONFIRMACAO = text(
    """
    SELECT
        current_database() AS banco,
        current_setting('transaction_read_only') AS somente_leitura
    """
)

SQL_DISTRIBUICAO_VENDA_EXCLUIDA = text(
    """
    SELECT flg_excluido AS valor_original, COUNT(*) AS quantidade
    FROM public.cadcvend
    WHERE cod_filial = :filial
    GROUP BY flg_excluido
    ORDER BY flg_excluido NULLS FIRST
    """
)

SQL_DISTRIBUICAO_ITEM_EXCLUIDO = text(
    """
    SELECT flg_excluido AS valor_original, COUNT(*) AS quantidade
    FROM public.cadivend
    WHERE cod_filial = :filial
    GROUP BY flg_excluido
    ORDER BY flg_excluido NULLS FIRST
    """
)

SQL_DISTRIBUICAO_PROMOCAO = text(
    """
    SELECT flg_promocao AS valor_original, COUNT(*) AS quantidade
    FROM public.cadivend
    WHERE cod_filial = :filial
    GROUP BY flg_promocao
    ORDER BY flg_promocao NULLS FIRST
    """
)

SQL_DISTRIBUICAO_PRESCRITO = text(
    """
    SELECT flg_prescrito AS valor_original, COUNT(*) AS quantidade
    FROM public.cadivend
    WHERE cod_filial = :filial
    GROUP BY flg_prescrito
    ORDER BY flg_prescrito NULLS FIRST
    """
)

SQL_DISTRIBUICAO_PRODUTO_ATIVO = text(
    """
    SELECT produto.flg_ativo AS valor_original, COUNT(*) AS quantidade
    FROM (
        SELECT DISTINCT cod_rede, cod_reduzido
        FROM public.cadivend
        WHERE cod_filial = :filial
          AND cod_reduzido IS NOT NULL
    ) AS usado
    INNER JOIN public.cadprodu AS produto
      ON produto.cod_rede = usado.cod_rede
     AND produto.cod_reduzido = usado.cod_reduzido
    GROUP BY produto.flg_ativo
    ORDER BY produto.flg_ativo NULLS FIRST
    """
)

SQL_DISTRIBUICAO_PRODUTO_EXCLUIDO = text(
    """
    SELECT produto.flg_excluido AS valor_original, COUNT(*) AS quantidade
    FROM (
        SELECT DISTINCT cod_rede, cod_reduzido
        FROM public.cadivend
        WHERE cod_filial = :filial
          AND cod_reduzido IS NOT NULL
    ) AS usado
    INNER JOIN public.cadprodu AS produto
      ON produto.cod_rede = usado.cod_rede
     AND produto.cod_reduzido = usado.cod_reduzido
    GROUP BY produto.flg_excluido
    ORDER BY produto.flg_excluido NULLS FIRST
    """
)

DISTRIBUICOES = {
    "cadcvend.flg_excluido (vendas)": SQL_DISTRIBUICAO_VENDA_EXCLUIDA,
    "cadivend.flg_excluido (itens)": SQL_DISTRIBUICAO_ITEM_EXCLUIDO,
    "cadivend.flg_promocao (itens)": SQL_DISTRIBUICAO_PROMOCAO,
    "cadivend.flg_prescrito (itens)": SQL_DISTRIBUICAO_PRESCRITO,
    "cadprodu.flg_ativo (produtos distintos usados na filial)": (
        SQL_DISTRIBUICAO_PRODUTO_ATIVO
    ),
    "cadprodu.flg_excluido (produtos distintos usados na filial)": (
        SQL_DISTRIBUICAO_PRODUTO_EXCLUIDO
    ),
}

SQL_CRUZAMENTO_EXCLUIDOS = text(
    """
    SELECT
        venda.flg_excluido AS venda_excluida,
        item.flg_excluido AS item_excluido,
        COUNT(*) AS quantidade_itens
    FROM public.cadivend AS item
    INNER JOIN public.cadcvend AS venda
      ON venda.cod_rede = item.cod_rede
     AND venda.cod_filial = item.cod_filial
     AND venda.num_nota = item.num_nota
    WHERE venda.cod_filial = :filial
    GROUP BY venda.flg_excluido, item.flg_excluido
    ORDER BY venda.flg_excluido NULLS FIRST, item.flg_excluido NULLS FIRST
    """
)

SQL_VOLUME_ANUAL = text(
    """
    SELECT
        EXTRACT(YEAR FROM dat_emissao)::integer AS ano,
        COUNT(*) AS quantidade_vendas,
        MIN(dat_emissao) AS primeira_data,
        MAX(dat_emissao) AS ultima_data
    FROM public.cadcvend
    WHERE cod_filial = :filial
    GROUP BY EXTRACT(YEAR FROM dat_emissao)
    ORDER BY EXTRACT(YEAR FROM dat_emissao) NULLS FIRST
    """
)

SQL_DATA_LEGADA = text(
    """
    SELECT COUNT(*) AS vendas_em_1901_01_01
    FROM public.cadcvend
    WHERE cod_filial = :filial
      AND dat_emissao = DATE '1901-01-01'
    """
)

SQL_ULTIMOS_24_MESES = text(
    """
    SELECT
        TO_CHAR(periodo, 'YYYY-MM') AS ano_mes,
        quantidade_vendas
    FROM (
        SELECT
            DATE_TRUNC('month', dat_emissao)::date AS periodo,
            COUNT(*) AS quantidade_vendas
        FROM public.cadcvend
        WHERE cod_filial = :filial
          AND dat_emissao IS NOT NULL
        GROUP BY DATE_TRUNC('month', dat_emissao)
        ORDER BY periodo DESC
        LIMIT 24
    ) AS meses_recentes
    ORDER BY periodo
    """
)

SQL_FILIAIS = text(
    """
    SELECT
        cod_rede,
        cod_filial,
        MIN(dat_emissao) AS primeira_data,
        MAX(dat_emissao) AS ultima_data,
        COUNT(*) AS quantidade_vendas
    FROM public.cadcvend
    GROUP BY cod_rede, cod_filial
    ORDER BY cod_rede, cod_filial
    """
)

SQL_VOLUME_MENSAL = text(
    """
    SELECT
        CASE
            WHEN vendas.periodo IS NULL THEN NULL
            ELSE TO_CHAR(vendas.periodo, 'YYYY-MM')
        END AS ano_mes,
        vendas.quantidade_vendas,
        COALESCE(itens.quantidade_itens, 0) AS quantidade_itens,
        COALESCE(itens.soma_quantidade_produtos, 0)
            AS soma_quantidade_produtos
    FROM (
        SELECT
            DATE_TRUNC('month', dat_emissao)::date AS periodo,
            COUNT(*) AS quantidade_vendas
        FROM public.cadcvend
        WHERE cod_filial = :filial
        GROUP BY DATE_TRUNC('month', dat_emissao)
    ) AS vendas
    LEFT JOIN (
        SELECT
            DATE_TRUNC('month', venda.dat_emissao)::date AS periodo,
            COUNT(*) AS quantidade_itens,
            SUM(item.qtd_produto) AS soma_quantidade_produtos
        FROM public.cadivend AS item
        INNER JOIN public.cadcvend AS venda
          ON venda.cod_rede = item.cod_rede
         AND venda.cod_filial = item.cod_filial
         AND venda.num_nota = item.num_nota
        WHERE venda.cod_filial = :filial
        GROUP BY DATE_TRUNC('month', venda.dat_emissao)
    ) AS itens
      ON itens.periodo IS NOT DISTINCT FROM vendas.periodo
    ORDER BY vendas.periodo NULLS FIRST
    """
)

SQL_QUALIDADE_VENDAS = text(
    """
    SELECT
        COUNT(*) FILTER (WHERE dat_emissao IS NULL) AS datas_nulas,
        COUNT(*) FILTER (WHERE dat_emissao = DATE '1901-01-01')
            AS datas_1901_01_01
    FROM public.cadcvend
    WHERE cod_filial = :filial
    """
)

SQL_QUALIDADE_ITENS = text(
    """
    SELECT
        COUNT(*) FILTER (WHERE qtd_produto IS NULL) AS quantidades_nulas,
        COUNT(*) FILTER (WHERE qtd_produto = 0) AS quantidades_zero,
        COUNT(*) FILTER (WHERE qtd_produto < 0) AS quantidades_negativas,
        COUNT(*) FILTER (WHERE cod_reduzido IS NULL) AS cod_reduzido_nulo
    FROM public.cadivend
    WHERE cod_filial = :filial
    """
)


def carregar_filial_teste():
    """Lê somente a filial necessária para este diagnóstico."""

    load_dotenv(ARQUIVO_ENV)
    valor = os.getenv("FILIAL_TESTE")
    if not valor:
        raise ErroConfiguracaoFarmacia("Configure FILIAL_TESTE no arquivo .env.")
    try:
        return int(valor)
    except ValueError as erro:
        raise ErroConfiguracaoFarmacia(
            "FILIAL_TESTE deve ser um número inteiro."
        ) from erro


def _consultar_dataframe(conexao, consulta, parametros=None):
    linhas = conexao.execute(consulta, parametros or {}).mappings().all()
    return pd.DataFrame([dict(linha) for linha in linhas])


def diagnosticar_historico():
    """Executa apenas consultas agregadas na fonte protegida."""

    filial = carregar_filial_teste()
    parametros = {"filial": filial}

    with conectar_farmacia_somente_leitura() as conexao:
        confirmacao = dict(conexao.execute(SQL_CONFIRMACAO).mappings().one())
        distribuicoes = {
            nome: _consultar_dataframe(conexao, consulta, parametros)
            for nome, consulta in DISTRIBUICOES.items()
        }
        resultado = {
            "filial": filial,
            "confirmacao": confirmacao,
            "distribuicoes": distribuicoes,
            "cruzamento_excluidos": _consultar_dataframe(
                conexao, SQL_CRUZAMENTO_EXCLUIDOS, parametros
            ),
            "volume_anual": _consultar_dataframe(
                conexao, SQL_VOLUME_ANUAL, parametros
            ),
            "data_legada": dict(
                conexao.execute(SQL_DATA_LEGADA, parametros).mappings().one()
            ),
            "ultimos_24_meses": _consultar_dataframe(
                conexao, SQL_ULTIMOS_24_MESES, parametros
            ),
            "filiais": _consultar_dataframe(conexao, SQL_FILIAIS),
            "volume_mensal": _consultar_dataframe(
                conexao, SQL_VOLUME_MENSAL, parametros
            ),
            "qualidade_vendas": dict(
                conexao.execute(
                    SQL_QUALIDADE_VENDAS, parametros
                ).mappings().one()
            ),
            "qualidade_itens": dict(
                conexao.execute(
                    SQL_QUALIDADE_ITENS, parametros
                ).mappings().one()
            ),
        }
    return resultado


def _valor_exibicao(valor):
    if pd.isna(valor):
        return "NULL"
    if isinstance(valor, str) and valor.strip() == "":
        return repr(valor)
    return valor


def _imprimir_dataframe(titulo, dados, colunas_controle=()):
    print(f"\n{titulo}")
    if dados.empty:
        print("Nenhum registro encontrado.")
        return
    exibicao = dados.copy()
    for coluna in colunas_controle:
        exibicao[coluna] = exibicao[coluna].map(_valor_exibicao)
    print(exibicao.to_string(index=False))


def main():
    try:
        resultado = diagnosticar_historico()
    except (ErroConfiguracaoFarmacia, RuntimeError) as erro:
        print("DIAGNÓSTICO INTERROMPIDO COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONSULTAR O sgfpod1")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    print("DIAGNÓSTICO PRÉ-CARGA HISTÓRICA")
    print(f"Banco: {resultado['confirmacao']['banco']}")
    print(
        "transaction_read_only: "
        f"{resultado['confirmacao']['somente_leitura']}"
    )
    print(f"Filial configurada: {resultado['filial']}")

    print("\nDISTRIBUIÇÕES DOS CAMPOS DE CONTROLE")
    for nome, distribuicao in resultado["distribuicoes"].items():
        _imprimir_dataframe(nome, distribuicao, ("valor_original",))

    _imprimir_dataframe(
        "CRUZAMENTO venda_excluida x item_excluido",
        resultado["cruzamento_excluidos"],
        ("venda_excluida", "item_excluido"),
    )
    _imprimir_dataframe("VOLUME POR ANO", resultado["volume_anual"], ("ano",))
    print("\nDATA 1901-01-01 APRESENTADA SEPARADAMENTE")
    print(
        "Quantidade de vendas: "
        f"{resultado['data_legada']['vendas_em_1901_01_01']}"
    )
    _imprimir_dataframe(
        "ÚLTIMOS 24 MESES COM REGISTROS", resultado["ultimos_24_meses"]
    )
    _imprimir_dataframe("FILIAIS DISPONÍVEIS", resultado["filiais"])
    _imprimir_dataframe(
        "VOLUME MENSAL DA FILIAL TESTE",
        resultado["volume_mensal"],
        ("ano_mes",),
    )
    _imprimir_dataframe(
        "DISTRIBUIÇÃO COMPLETA DE flg_prescrito",
        resultado["distribuicoes"]["cadivend.flg_prescrito (itens)"],
        ("valor_original",),
    )

    print("\nINDICADORES OBJETIVOS DE QUALIDADE")
    for grupo in ("qualidade_vendas", "qualidade_itens"):
        for campo, valor in resultado[grupo].items():
            print(f"{campo}: {valor}")

    print("\nDiagnóstico concluído. Nenhum registro foi importado ou alterado.")


if __name__ == "__main__":
    main()
