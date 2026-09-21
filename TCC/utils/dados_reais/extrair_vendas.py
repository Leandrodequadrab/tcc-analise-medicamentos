"""Extrai uma amostra configurável e somente leitura das vendas reais."""

import os
from datetime import date

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    ARQUIVO_ENV,
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)
from .inspecionar_estrutura import SQL_CHAVES, TABELAS_ALVO
from .validar_extracao import gerar_relatorio_qualidade


COLUNAS_EXTRACAO = (
    "cod_rede",
    "cod_filial",
    "num_nota",
    "num_sequencial",
    "data",
    "hora",
    "cod_reduzido",
    "cod_produto",
    "nom_produto",
    "produto_encontrado",
    "quantidade",
    "valor_unitario",
    "valor_total",
    "promocao",
    "prescrito",
    "venda_excluida",
    "item_excluido",
    "produto_ativo",
    "produto_excluido",
)

CONSTRAINTS_ESPERADAS = {
    "cadcvend_pkey": {
        "tipo": "PRIMARY KEY",
        "colunas": "cod_rede, cod_filial, num_nota",
        "tabela_destino": None,
        "colunas_destino": None,
    },
    "cadivend_pkey": {
        "tipo": "PRIMARY KEY",
        "colunas": "cod_rede, cod_filial, num_nota, num_sequencial",
        "tabela_destino": None,
        "colunas_destino": None,
    },
    "cadivend_num_nota_fkey": {
        "tipo": "FOREIGN KEY",
        "colunas": "cod_rede, cod_filial, num_nota",
        "tabela_destino": "cadcvend",
        "colunas_destino": "cod_rede, cod_filial, num_nota",
    },
    "cadivend_cod_reduzido_fkey": {
        "tipo": "FOREIGN KEY",
        "colunas": "cod_rede, cod_reduzido",
        "tabela_destino": "cadprodu",
        "colunas_destino": "cod_rede, cod_reduzido",
    },
    "cadprodu_pkey": {
        "tipo": "PRIMARY KEY",
        "colunas": "cod_rede, cod_reduzido",
        "tabela_destino": None,
        "colunas_destino": None,
    },
}

SQL_REDES_DA_AMOSTRA = text(
    """
    SELECT DISTINCT cod_rede
    FROM public.cadcvend
    WHERE cod_filial = :filial
      AND dat_emissao BETWEEN :data_inicial AND :data_final
    ORDER BY cod_rede
    """
)

SQL_EXTRACAO = text(
    """
    SELECT
        item.cod_rede AS cod_rede,
        item.cod_filial AS cod_filial,
        item.num_nota AS num_nota,
        item.num_sequencial AS num_sequencial,
        venda.dat_emissao AS data,
        venda.hor_emissao AS hora,
        item.cod_reduzido AS cod_reduzido,
        produto.cod_produto AS cod_produto,
        produto.nom_produto AS nom_produto,
        CASE
            WHEN produto.cod_reduzido IS NULL THEN FALSE
            ELSE TRUE
        END AS produto_encontrado,
        item.qtd_produto AS quantidade,
        item.vlr_unitario AS valor_unitario,
        item.vlr_total AS valor_total,
        item.flg_promocao AS promocao,
        item.flg_prescrito AS prescrito,
        venda.flg_excluido AS venda_excluida,
        item.flg_excluido AS item_excluido,
        produto.flg_ativo AS produto_ativo,
        produto.flg_excluido AS produto_excluido
    FROM public.cadivend AS item
    INNER JOIN public.cadcvend AS venda
      ON venda.cod_rede = item.cod_rede
     AND venda.cod_filial = item.cod_filial
     AND venda.num_nota = item.num_nota
    LEFT JOIN public.cadprodu AS produto
      ON produto.cod_rede = item.cod_rede
     AND produto.cod_reduzido = item.cod_reduzido
    WHERE venda.cod_rede = :rede
      AND venda.cod_filial = :filial
      AND venda.dat_emissao BETWEEN :data_inicial AND :data_final
    ORDER BY
        venda.dat_emissao,
        venda.hor_emissao,
        item.cod_rede,
        item.cod_filial,
        item.num_nota,
        item.num_sequencial
    LIMIT :limite
    """
)


class ErroAmostra(RuntimeError):
    """Indica que a amostra não pode ser extraída com segurança."""


def carregar_filtros_amostra():
    """Valida os quatro filtros obrigatórios definidos pelo usuário."""

    load_dotenv(ARQUIVO_ENV)
    nomes = ("FILIAL_TESTE", "DATA_INICIAL", "DATA_FINAL", "LIMITE_REGISTROS")
    ausentes = [nome for nome in nomes if not os.getenv(nome)]
    if ausentes:
        raise ErroConfiguracaoFarmacia(
            "Configure no .env os filtros: " + ", ".join(ausentes)
        )

    try:
        filial = int(os.environ["FILIAL_TESTE"])
        limite = int(os.environ["LIMITE_REGISTROS"])
        data_inicial = date.fromisoformat(os.environ["DATA_INICIAL"])
        data_final = date.fromisoformat(os.environ["DATA_FINAL"])
    except ValueError as erro:
        raise ErroConfiguracaoFarmacia(
            "FILIAL_TESTE e LIMITE_REGISTROS devem ser inteiros; "
            "as datas devem usar AAAA-MM-DD."
        ) from erro

    if data_inicial > data_final:
        raise ErroConfiguracaoFarmacia(
            "DATA_INICIAL não pode ser posterior a DATA_FINAL."
        )
    if not 1 <= limite <= 10_000:
        raise ErroConfiguracaoFarmacia(
            "LIMITE_REGISTROS deve estar entre 1 e 10000."
        )

    return {
        "filial": filial,
        "data_inicial": data_inicial,
        "data_final": data_final,
        "limite": limite,
    }


def confirmar_constraints(conexao):
    """Confirma novamente as PKs e FKs antes de executar qualquer JOIN."""

    registros = conexao.execute(
        SQL_CHAVES, {"tabelas": list(TABELAS_ALVO)}
    ).mappings().all()
    encontradas = {registro["constraint_name"]: registro for registro in registros}

    for nome, esperada in CONSTRAINTS_ESPERADAS.items():
        encontrada = encontradas.get(nome)
        if encontrada is None:
            raise ErroAmostra(f"Constraint obrigatória não encontrada: {nome}.")

        valores_reais = {
            "tipo": encontrada["constraint_type"],
            "colunas": encontrada["columns"],
            "tabela_destino": encontrada["foreign_table_name"],
            "colunas_destino": encontrada["foreign_columns"] or None,
        }
        if valores_reais != esperada:
            raise ErroAmostra(
                f"A definição da constraint {nome} mudou. Extração interrompida."
            )


def _identificar_rede(conexao, filtros):
    redes = conexao.execute(SQL_REDES_DA_AMOSTRA, filtros).scalars().all()
    if len(redes) > 1:
        raise ErroAmostra(
            "A filial e o período informados pertencem a mais de uma cod_rede: "
            + ", ".join(str(rede) for rede in redes)
            + ". Informe uma combinação não ambígua antes de extrair."
        )
    return redes[0] if redes else None


def extrair_amostra_vendas():
    """Confirma constraints, executa o SELECT limitado e devolve um DataFrame."""

    filtros = carregar_filtros_amostra()

    with conectar_farmacia_somente_leitura() as conexao:
        confirmar_constraints(conexao)
        rede = _identificar_rede(conexao, filtros)
        if rede is None:
            return pd.DataFrame(columns=COLUNAS_EXTRACAO), None, filtros

        parametros = {**filtros, "rede": rede}
        registros = conexao.execute(SQL_EXTRACAO, parametros).mappings().all()

    dados = pd.DataFrame(
        [dict(registro) for registro in registros], columns=COLUNAS_EXTRACAO
    )
    return dados, rede, filtros


def _imprimir_relatorio(relatorio):
    nomes = {
        "quantidade_linhas": "Quantidade de linhas extraídas",
        "vendas_distintas": "Quantidade de vendas distintas",
        "produtos_distintos": "Quantidade de produtos distintos",
        "filiais_presentes": "Filiais presentes",
        "data_minima": "Menor data",
        "data_maxima": "Maior data",
        "soma_quantidades": "Soma das quantidades",
        "produtos_sem_descricao": "Produtos sem descrição",
        "quantidades_nulas": "Quantidades nulas",
        "quantidades_zero": "Quantidades iguais a zero",
        "quantidades_negativas": "Quantidades negativas",
        "datas_invalidas": "Datas inválidas",
        "registros_com_identificador_ausente": "Registros com identificador ausente",
        "identificadores_ausentes_por_coluna": "Identificadores ausentes por coluna",
        "possiveis_duplicidades": "Possíveis duplicidades excedentes",
        "linhas_em_chaves_duplicadas": "Linhas em chaves duplicadas",
        "itens_com_correspondencia_cadcvend": "Itens com correspondência em cadcvend",
        "itens_com_correspondencia_cadprodu": "Itens com correspondência em cadprodu",
        "itens_sem_correspondencia_cadprodu": "Itens sem correspondência em cadprodu",
        "produtos_correspondentes_com_cod_produto_nulo": (
            "Produtos correspondentes com cod_produto nulo"
        ),
    }
    for chave, valor in relatorio.items():
        print(f"{nomes[chave]}: {valor}")


def main():
    try:
        dados, rede, filtros = extrair_amostra_vendas()
    except (ErroConfiguracaoFarmacia, ErroAmostra) as erro:
        print("EXTRAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONSULTAR O sgfpod1")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    print("FILTROS DA AMOSTRA")
    print(f"cod_rede confirmado: {rede}")
    print(f"Filial: {filtros['filial']}")
    print(f"Período: {filtros['data_inicial']} a {filtros['data_final']}")
    print(f"Limite: {filtros['limite']}")

    print("\nPRIMEIRAS LINHAS")
    print(dados.head().to_string(index=False))

    print("\nCOLUNAS")
    print(dados.columns.tolist())

    print("\nTIPOS")
    print(dados.dtypes)

    print("\nQUANTIDADE DE LINHAS")
    print(len(dados))

    if dados.empty:
        print("\nNenhum registro foi encontrado para os filtros informados.")
        return

    qualidade = gerar_relatorio_qualidade(dados)
    print("\nRELATÓRIO DE QUALIDADE")
    _imprimir_relatorio(qualidade["relatorio"])

    if not qualidade["possiveis_duplicidades"].empty:
        print("\nPRIMEIRAS POSSÍVEIS DUPLICIDADES")
        print(qualidade["possiveis_duplicidades"].head().to_string(index=False))


if __name__ == "__main__":
    main()
