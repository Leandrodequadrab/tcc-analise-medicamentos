"""Importa de forma transacional e idempotente a amostra real configurada."""

import pandas as pd
from sqlalchemy import create_engine, func, insert, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError

from database import Venda, obter_url_banco_aplicacao
from models.dados_reais import ItemVenda, Produto, VendaReal

from .conexao_farmacia import ErroConfiguracaoFarmacia
from .extrair_vendas import ErroAmostra, extrair_amostra_vendas
from .validar_extracao import gerar_relatorio_qualidade


BANCO_FONTE = "sgfpod1"
BANCO_DESTINO = "tcc_medicamentos"

CHAVE_PRODUTO = ["cod_rede", "cod_reduzido"]
CHAVE_VENDA = ["cod_rede", "cod_filial", "num_nota"]
CHAVE_ITEM_ORIGEM = CHAVE_VENDA + ["num_sequencial"]

CAMPOS_PRODUTO = CHAVE_PRODUTO + [
    "cod_produto",
    "nom_produto",
    "produto_ativo",
    "produto_excluido",
]
CAMPOS_VENDA = CHAVE_VENDA + ["data", "hora", "venda_excluida"]


class ErroImportacao(RuntimeError):
    """Indica que a carga foi interrompida antes do commit."""


def _valor_python(valor):
    """Converte escalares do Pandas e representa ausências como None."""

    if pd.isna(valor):
        return None
    return valor.item() if hasattr(valor, "item") else valor


def _linhas_unicas_consistentes(dados, chave, campos, entidade):
    """Recusa valores divergentes para uma mesma chave da amostra."""

    if dados.empty:
        return dados[campos].copy()

    divergencias = (
        dados.groupby(chave, dropna=False)[campos]
        .nunique(dropna=False)
        .gt(1)
        .any(axis=1)
    )
    if divergencias.any():
        raise ErroImportacao(
            f"A amostra possui dados divergentes para a mesma chave de {entidade}."
        )
    return dados[campos].drop_duplicates(subset=chave, keep="first")


def _preparar_amostra(dados):
    if dados.empty:
        raise ErroImportacao("A amostra está vazia; nada foi importado.")

    qualidade = gerar_relatorio_qualidade(dados)
    resumo = qualidade["relatorio"]
    if resumo["possiveis_duplicidades"] != 0:
        raise ErroImportacao(
            "A amostra possui duplicidades na chave real de cadivend."
        )
    if resumo["registros_com_identificador_ausente"] != 0:
        raise ErroImportacao(
            "A amostra possui identificadores obrigatórios ausentes."
        )

    produtos_encontrados = dados.loc[dados["produto_encontrado"].eq(True)]
    produtos = _linhas_unicas_consistentes(
        produtos_encontrados,
        CHAVE_PRODUTO,
        CAMPOS_PRODUTO,
        "produto",
    )
    vendas = _linhas_unicas_consistentes(
        dados,
        CHAVE_VENDA,
        CAMPOS_VENDA,
        "venda",
    )
    return produtos, vendas, qualidade


def _tuplas_chave(dados, colunas):
    return [
        tuple(int(valor) for valor in linha)
        for linha in dados[colunas].itertuples(index=False, name=None)
    ]


def _carregar_produtos(conexao, produtos):
    tabela = Produto.__table__
    chaves = _tuplas_chave(produtos, CHAVE_PRODUTO)
    existentes = {}
    if chaves:
        consulta = select(
            tabela.c.id, tabela.c.cod_rede, tabela.c.cod_reduzido
        ).where(tuple_(tabela.c.cod_rede, tabela.c.cod_reduzido).in_(chaves))
        existentes = {
            (linha.cod_rede, linha.cod_reduzido): linha.id
            for linha in conexao.execute(consulta)
        }

    inseridos = 0
    mapa = dict(existentes)
    for registro in produtos.to_dict(orient="records"):
        chave = (int(registro["cod_rede"]), int(registro["cod_reduzido"]))
        if chave in mapa:
            continue
        valores = {
            campo: _valor_python(registro[campo]) for campo in CAMPOS_PRODUTO
        }
        valores["cod_rede"], valores["cod_reduzido"] = chave
        identificador = conexao.execute(
            insert(tabela).values(**valores).returning(tabela.c.id)
        ).scalar_one()
        mapa[chave] = identificador
        inseridos += 1

    return mapa, inseridos, len(existentes)


def _carregar_vendas(conexao, vendas):
    tabela = VendaReal.__table__
    chaves = _tuplas_chave(vendas, CHAVE_VENDA)
    existentes = {}
    if chaves:
        consulta = select(
            tabela.c.id,
            tabela.c.cod_rede,
            tabela.c.cod_filial,
            tabela.c.num_nota,
        ).where(
            tuple_(
                tabela.c.cod_rede,
                tabela.c.cod_filial,
                tabela.c.num_nota,
            ).in_(chaves)
        )
        existentes = {
            (linha.cod_rede, linha.cod_filial, linha.num_nota): linha.id
            for linha in conexao.execute(consulta)
        }

    inseridas = 0
    mapa = dict(existentes)
    for registro in vendas.to_dict(orient="records"):
        chave = tuple(int(registro[campo]) for campo in CHAVE_VENDA)
        if chave in mapa:
            continue
        valores = {
            campo: _valor_python(registro[campo]) for campo in CAMPOS_VENDA
        }
        for indice, campo in enumerate(CHAVE_VENDA):
            valores[campo] = chave[indice]
        identificador = conexao.execute(
            insert(tabela).values(**valores).returning(tabela.c.id)
        ).scalar_one()
        mapa[chave] = identificador
        inseridas += 1

    return mapa, inseridas, len(existentes)


def _carregar_itens(conexao, dados, mapa_vendas, mapa_produtos):
    tabela = ItemVenda.__table__
    itens_preparados = []
    for registro in dados.to_dict(orient="records"):
        chave_venda = tuple(int(registro[campo]) for campo in CHAVE_VENDA)
        venda_id = mapa_vendas[chave_venda]
        produto_id = None
        if registro["produto_encontrado"] is True:
            chave_produto = (
                int(registro["cod_rede"]),
                int(registro["cod_reduzido"]),
            )
            produto_id = mapa_produtos[chave_produto]

        itens_preparados.append(
            {
                "venda_id": venda_id,
                "produto_id": produto_id,
                "num_sequencial": int(registro["num_sequencial"]),
                "quantidade": _valor_python(registro["quantidade"]),
                "valor_unitario": _valor_python(registro["valor_unitario"]),
                "valor_total": _valor_python(registro["valor_total"]),
                "promocao": _valor_python(registro["promocao"]),
                "prescrito": _valor_python(registro["prescrito"]),
                "item_excluido": _valor_python(registro["item_excluido"]),
            }
        )

    chaves = [(item["venda_id"], item["num_sequencial"]) for item in itens_preparados]
    existentes = set()
    if chaves:
        consulta = select(tabela.c.venda_id, tabela.c.num_sequencial).where(
            tuple_(tabela.c.venda_id, tabela.c.num_sequencial).in_(chaves)
        )
        existentes = {tuple(linha) for linha in conexao.execute(consulta)}

    novos = [
        item
        for item in itens_preparados
        if (item["venda_id"], item["num_sequencial"]) not in existentes
    ]
    if novos:
        conexao.execute(insert(tabela), novos)
    return len(novos), len(existentes), chaves


def _contar_grupos_duplicados(conexao, tabela, colunas):
    grupos = (
        select(*colunas)
        .group_by(*colunas)
        .having(func.count() > 1)
        .subquery()
    )
    return conexao.execute(select(func.count()).select_from(grupos)).scalar_one()


def _validar_destino(conexao, chaves_itens, ids_vendas, ids_produtos):
    itens = ItemVenda.__table__
    produtos = Produto.__table__
    vendas = VendaReal.__table__
    ficticias = Venda.__table__

    consulta_amostra = select(
        func.count().label("itens"),
        func.count(func.distinct(itens.c.venda_id)).label("vendas"),
        func.count(func.distinct(itens.c.produto_id)).label("produtos"),
        func.count().filter(itens.c.produto_id.is_(None)).label("produto_nulo"),
    ).where(tuple_(itens.c.venda_id, itens.c.num_sequencial).in_(chaves_itens))
    amostra = conexao.execute(consulta_amostra).mappings().one()

    validacao = {
        "itens_sem_venda_id": conexao.execute(
            select(func.count()).select_from(itens).where(itens.c.venda_id.is_(None))
        ).scalar_one(),
        "itens_da_amostra": amostra["itens"],
        "vendas_distintas_da_amostra": amostra["vendas"],
        "produtos_relacionados_da_amostra": amostra["produtos"],
        "itens_com_produto_id_nulo": amostra["produto_nulo"],
        "duplicidades_itens": _contar_grupos_duplicados(
            conexao, itens, [itens.c.venda_id, itens.c.num_sequencial]
        ),
        "duplicidades_produtos": _contar_grupos_duplicados(
            conexao, produtos, [produtos.c.cod_rede, produtos.c.cod_reduzido]
        ),
        "duplicidades_vendas": _contar_grupos_duplicados(
            conexao,
            vendas,
            [vendas.c.cod_rede, vendas.c.cod_filial, vendas.c.num_nota],
        ),
        "total_produtos": conexao.execute(
            select(func.count()).select_from(produtos)
        ).scalar_one(),
        "total_vendas_reais": conexao.execute(
            select(func.count()).select_from(vendas)
        ).scalar_one(),
        "total_itens_venda": conexao.execute(
            select(func.count()).select_from(itens)
        ).scalar_one(),
        "total_vendas_ficticias": conexao.execute(
            select(func.count()).select_from(ficticias)
        ).scalar_one(),
    }

    esperado = {
        "itens_da_amostra": len(chaves_itens),
        "vendas_distintas_da_amostra": len(ids_vendas),
        "produtos_relacionados_da_amostra": len(ids_produtos),
    }
    for campo, valor in esperado.items():
        if validacao[campo] != valor:
            raise ErroImportacao(
                f"Validação inconsistente para {campo}: "
                f"esperado {valor}, encontrado {validacao[campo]}."
            )
    for campo in (
        "itens_sem_venda_id",
        "duplicidades_itens",
        "duplicidades_produtos",
        "duplicidades_vendas",
    ):
        if validacao[campo] != 0:
            raise ErroImportacao(
                f"Validação encontrou {validacao[campo]} em {campo}."
            )
    return validacao


def importar_amostra_tcc():
    """Extrai da fonte protegida e grava a amostra em uma transação."""

    dados, rede, filtros = extrair_amostra_vendas()
    produtos, vendas, qualidade = _preparar_amostra(dados)

    engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
    try:
        with engine.begin() as conexao:
            banco_destino = conexao.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if banco_destino != BANCO_DESTINO:
                raise ErroImportacao(
                    f"Destino recusado: esperado {BANCO_DESTINO!r}, "
                    f"recebido {banco_destino!r}."
                )

            mapa_produtos, produtos_inseridos, produtos_existentes = (
                _carregar_produtos(conexao, produtos)
            )
            mapa_vendas, vendas_inseridas, vendas_existentes = _carregar_vendas(
                conexao, vendas
            )
            itens_inseridos, itens_existentes, chaves_itens = _carregar_itens(
                conexao, dados, mapa_vendas, mapa_produtos
            )
            validacao = _validar_destino(
                conexao,
                chaves_itens,
                set(mapa_vendas.values()),
                set(mapa_produtos.values()),
            )

        return {
            "fonte": BANCO_FONTE,
            "fonte_somente_leitura": True,
            "destino": banco_destino,
            "rede": rede,
            "filtros": filtros,
            "extraidos": qualidade["relatorio"],
            "produtos": {
                "inseridos": produtos_inseridos,
                "ja_existentes": produtos_existentes,
                "sem_correspondencia": int(
                    dados["produto_encontrado"].eq(False).sum()
                ),
            },
            "vendas": {
                "inseridas": vendas_inseridas,
                "ja_existentes": vendas_existentes,
            },
            "itens": {
                "inseridos": itens_inseridos,
                "ja_existentes": itens_existentes,
            },
            "validacao": validacao,
        }
    finally:
        engine.dispose()


def _imprimir_resultado(resultado):
    filtros = resultado["filtros"]
    extraidos = resultado["extraidos"]

    print("IMPORTAÇÃO DA AMOSTRA REAL")
    print(f"\nFonte: {resultado['fonte']}")
    print("Fonte somente leitura: SIM")
    print(f"Destino: {resultado['destino']}")
    print("\nFiltros:")
    print(f"Filial: {filtros['filial']}")
    print(f"Período: {filtros['data_inicial']} a {filtros['data_final']}")
    print(f"Limite: {filtros['limite']}")
    print("\nExtraídos:")
    print(f"Itens extraídos: {extraidos['quantidade_linhas']}")
    print(f"Vendas distintas: {extraidos['vendas_distintas']}")
    print(f"Produtos distintos: {extraidos['produtos_distintos']}")

    print("\nProdutos:")
    print(f"Inseridos: {resultado['produtos']['inseridos']}")
    print(f"Já existentes: {resultado['produtos']['ja_existentes']}")
    print(
        "Sem correspondência na origem: "
        f"{resultado['produtos']['sem_correspondencia']}"
    )
    print("\nVendas:")
    print(f"Inseridas: {resultado['vendas']['inseridas']}")
    print(f"Já existentes: {resultado['vendas']['ja_existentes']}")
    print("\nItens:")
    print(f"Inseridos: {resultado['itens']['inseridos']}")
    print(f"Já existentes: {resultado['itens']['ja_existentes']}")

    validacao = resultado["validacao"]
    print("\nCONTAGEM FINAL NO DESTINO")
    print(f"produtos: {validacao['total_produtos']}")
    print(f"vendas_reais: {validacao['total_vendas_reais']}")
    print(f"itens_venda: {validacao['total_itens_venda']}")
    print(f"vendas (fictícia): {validacao['total_vendas_ficticias']}")

    print("\nVALIDAÇÃO PÓS-IMPORTAÇÃO")
    for campo, valor in validacao.items():
        if not campo.startswith("total_"):
            print(f"{campo.replace('_', ' ').capitalize()}: {valor}")


def main():
    try:
        resultado = importar_amostra_tcc()
    except (ErroConfiguracaoFarmacia, ErroAmostra, ErroImportacao) as erro:
        print("IMPORTAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("IMPORTAÇÃO REVERTIDA POR ERRO DE BANCO")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    _imprimir_resultado(resultado)


if __name__ == "__main__":
    main()
