"""Investiga cod_produto, campos de controle e integridade dos JOINs."""

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)
from .extrair_vendas import ErroAmostra, extrair_amostra_vendas
from .validar_extracao import gerar_relatorio_qualidade


CAMPOS_CONTROLE = (
    "promocao",
    "prescrito",
    "venda_excluida",
    "item_excluido",
    "produto_ativo",
    "produto_excluido",
)

SQL_RESUMO_CADPRODU = text(
    """
    SELECT
        COUNT(*) AS quantidade_total_produtos,
        SUM(CASE WHEN cod_produto IS NULL THEN 1 ELSE 0 END)
            AS produtos_com_cod_produto_nulo,
        SUM(CASE WHEN cod_reduzido IS NULL THEN 1 ELSE 0 END)
            AS produtos_com_cod_reduzido_nulo,
        SUM(CASE WHEN nom_produto IS NULL THEN 1 ELSE 0 END)
            AS produtos_com_nom_produto_nulo
    FROM public.cadprodu
    """
)

SQL_EXEMPLOS_COD_PRODUTO_NULO = text(
    """
    SELECT
        cod_rede,
        cod_reduzido,
        cod_produto,
        nom_produto
    FROM public.cadprodu
    WHERE cod_reduzido IS NOT NULL
      AND cod_produto IS NULL
    ORDER BY cod_rede, cod_reduzido
    LIMIT 10
    """
)


def calcular_distribuicoes_controle(dados):
    """Conta valores literais, mantendo NULL como uma categoria explícita."""

    distribuicoes = {}
    for campo in CAMPOS_CONTROLE:
        contagem = dados[campo].astype("object").value_counts(
            dropna=False, sort=False
        )
        linhas = []
        for valor, total in contagem.items():
            if pd.isna(valor):
                valor_exibido = "NULL"
            elif isinstance(valor, str) and valor.strip() == "":
                valor_exibido = repr(valor)
            else:
                valor_exibido = str(valor)

            linhas.append(
                {"valor": valor_exibido, "frequencia": int(total)}
            )

        distribuicoes[campo] = pd.DataFrame(linhas).sort_values(
            "valor", kind="stable"
        )
    return distribuicoes


def investigar_produtos_controles():
    """Executa a investigação e devolve resultados tabulares."""

    with conectar_farmacia_somente_leitura() as conexao:
        resumo = dict(conexao.execute(SQL_RESUMO_CADPRODU).mappings().one())
        exemplos = conexao.execute(
            SQL_EXEMPLOS_COD_PRODUTO_NULO
        ).mappings().all()

    dados, rede, filtros = extrair_amostra_vendas()
    qualidade = gerar_relatorio_qualidade(dados) if not dados.empty else None

    return {
        "resumo_cadprodu": resumo,
        "exemplos_cod_produto_nulo": pd.DataFrame(
            [dict(linha) for linha in exemplos]
        ),
        "dados": dados,
        "rede": rede,
        "filtros": filtros,
        "distribuicoes": calcular_distribuicoes_controle(dados),
        "qualidade": qualidade,
    }


def main():
    try:
        resultado = investigar_produtos_controles()
    except (ErroConfiguracaoFarmacia, ErroAmostra) as erro:
        print("INVESTIGAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONSULTAR O sgfpod1")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    print("RESUMO DE cadprodu")
    for campo, valor in resultado["resumo_cadprodu"].items():
        print(f"{campo}: {valor}")

    print("\nEXEMPLOS COM cod_reduzido PREENCHIDO E cod_produto NULO")
    if resultado["exemplos_cod_produto_nulo"].empty:
        print("Nenhum exemplo encontrado.")
    else:
        print(resultado["exemplos_cod_produto_nulo"].to_string(index=False))

    print("\nFILTROS DA AMOSTRA")
    print(f"cod_rede: {resultado['rede']}")
    print(f"Filial: {resultado['filtros']['filial']}")
    print(
        f"Período: {resultado['filtros']['data_inicial']} "
        f"a {resultado['filtros']['data_final']}"
    )
    print(f"Linhas: {len(resultado['dados'])}")

    print("\nDISTRIBUIÇÃO DOS CAMPOS DE CONTROLE")
    for campo, distribuicao in resultado["distribuicoes"].items():
        print(f"\n{campo}")
        print(distribuicao.to_string(index=False))

    print("\nINTEGRIDADE DOS JOINS NA AMOSTRA")
    if resultado["qualidade"] is None:
        print("A amostra está vazia.")
    else:
        relatorio = resultado["qualidade"]["relatorio"]
        print(
            "Itens com correspondência em cadcvend: "
            f"{relatorio['itens_com_correspondencia_cadcvend']}"
        )
        print(
            "Itens com correspondência em cadprodu: "
            f"{relatorio['itens_com_correspondencia_cadprodu']}"
        )
        print(
            "Itens sem correspondência em cadprodu: "
            f"{relatorio['itens_sem_correspondencia_cadprodu']}"
        )
        print(
            "Correspondências em cadprodu com cod_produto nulo: "
            f"{relatorio['produtos_correspondentes_com_cod_produto_nulo']}"
        )


if __name__ == "__main__":
    main()
