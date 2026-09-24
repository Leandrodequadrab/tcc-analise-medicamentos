"""Importador transacional e idempotente do relatório oficial Rel_0024."""

import argparse
from pathlib import Path
import time

from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from database import obter_url_banco_aplicacao
from models.rel0024 import (
    ImportacaoRel0024,
    ItemRel0024,
    ProdutoRel0024,
    TABELAS_REL0024,
    VendaRel0024,
)

from .conexao_farmacia import PASTA_PROJETO
from .validar_rel0024 import ErroRel0024, ler_rel0024, validar_rel0024


ARQUIVO_PADRAO = PASTA_PROJETO / "dados" / "6 meses.xlsx"
BANCO_DESTINO = "tcc_medicamentos"
TAMANHO_LOTE = 1000


class ErroImportacaoRel0024(RuntimeError):
    """Indica que a importação deve sofrer rollback."""


def _em_lotes(registros, tamanho=TAMANHO_LOTE):
    for inicio in range(0, len(registros), tamanho):
        yield registros[inicio : inicio + tamanho]


def _confirmar_destino(conexao):
    banco = conexao.execute(text("SELECT current_database()")).scalar_one()
    if banco != BANCO_DESTINO:
        raise ErroImportacaoRel0024(
            f"Destino recusado: esperado {BANCO_DESTINO!r}, recebido {banco!r}."
        )
    return banco


def _criar_estrutura(engine):
    with engine.connect() as conexao:
        _confirmar_destino(conexao)
    TABELAS_REL0024[0].metadata.create_all(
        bind=engine, tables=list(TABELAS_REL0024), checkfirst=True
    )
    inspetor = inspect(engine)
    ausentes = {
        tabela.name for tabela in TABELAS_REL0024
    } - set(inspetor.get_table_names())
    if ausentes:
        raise ErroImportacaoRel0024(
            "Tabelas Rel_0024 ausentes: " + ", ".join(sorted(ausentes))
        )


def _validar_para_importacao(resultado, validacao):
    problemas = []
    if resultado["linhas_invalidas"]:
        problemas.append("linhas inválidas")
    if validacao["vendas_conflitantes"]:
        problemas.append("vendas com dados conflitantes")
    if validacao["produtos_conflitantes"]:
        problemas.append("produtos com descrições conflitantes")
    if validacao["divergencias_totais_quantidade"]:
        problemas.append("totais de quantidade divergentes")
    if validacao["divergencias_totais_valor"]:
        problemas.append("totais financeiros divergentes")
    if not validacao["total_geral_quantidade_confere"]:
        problemas.append("Total Geral de quantidade divergente")
    if not validacao["total_geral_valor_confere"]:
        problemas.append("Total Geral financeiro divergente")
    if len(validacao["filiais"]) != 1:
        problemas.append("mais de uma filial no mesmo arquivo")
    if problemas:
        raise ErroImportacaoRel0024(
            "Importação interrompida: " + "; ".join(problemas) + "."
        )


def _produtos_do_arquivo(itens):
    produtos = {}
    for item in itens:
        chave = (item["filial"], item["codigo_produto"])
        produtos[chave] = item["descricao_produto"]
    return produtos


def _vendas_do_arquivo(itens):
    vendas = {}
    for item in itens:
        chave = (item["filial"], item["numero_venda"])
        vendas[chave] = {
            "filial": item["filial"],
            "numero_venda": item["numero_venda"],
            "data": item["data"],
            "hora_original": item["hora_bruta"],
            "tipo_original": item["tipo"] or None,
        }
    return vendas


def _sincronizar_produtos(conexao, produtos_arquivo):
    tabela = ProdutoRel0024.__table__
    filiais = sorted({chave[0] for chave in produtos_arquivo})
    encontrados = conexao.execute(
        select(
            tabela.c.id,
            tabela.c.filial,
            tabela.c.codigo_produto,
            tabela.c.descricao_original,
        ).where(tabela.c.filial.in_(filiais))
    ).mappings()
    existentes = {
        (linha["filial"], linha["codigo_produto"]): dict(linha)
        for linha in encontrados
    }
    for chave, descricao in produtos_arquivo.items():
        atual = existentes.get(chave)
        if atual and atual["descricao_original"] != descricao:
            raise ErroImportacaoRel0024(
                f"Descrição divergente para produto {chave}."
            )

    novos = [
        {
            "filial": chave[0],
            "codigo_produto": chave[1],
            "descricao_original": descricao,
        }
        for chave, descricao in produtos_arquivo.items()
        if chave not in existentes
    ]
    for lote in _em_lotes(novos):
        conexao.execute(insert(tabela), lote)

    mapa = {
        (linha.filial, linha.codigo_produto): linha.id
        for linha in conexao.execute(
            select(tabela.c.id, tabela.c.filial, tabela.c.codigo_produto).where(
                tabela.c.filial.in_(filiais)
            )
        )
        if (linha.filial, linha.codigo_produto) in produtos_arquivo
    }
    return mapa, len(novos), len(produtos_arquivo) - len(novos)


def _sincronizar_vendas(conexao, vendas_arquivo):
    tabela = VendaRel0024.__table__
    filiais = sorted({chave[0] for chave in vendas_arquivo})
    encontrados = conexao.execute(
        select(tabela).where(tabela.c.filial.in_(filiais))
    ).mappings()
    existentes = {
        (linha["filial"], linha["numero_venda"]): dict(linha)
        for linha in encontrados
    }
    campos = ("data", "hora_original", "tipo_original")
    for chave, venda in vendas_arquivo.items():
        atual = existentes.get(chave)
        if atual and any(atual[campo] != venda[campo] for campo in campos):
            raise ErroImportacaoRel0024(
                f"Dados divergentes para venda {chave}."
            )

    novos = [
        venda for chave, venda in vendas_arquivo.items() if chave not in existentes
    ]
    for lote in _em_lotes(novos):
        conexao.execute(insert(tabela), lote)

    mapa = {
        (linha.filial, linha.numero_venda): linha.id
        for linha in conexao.execute(
            select(tabela.c.id, tabela.c.filial, tabela.c.numero_venda).where(
                tabela.c.filial.in_(filiais)
            )
        )
        if (linha.filial, linha.numero_venda) in vendas_arquivo
    }
    return mapa, len(novos), len(vendas_arquivo) - len(novos)


def _buscar_itens_existentes(conexao, venda_ids):
    tabela = ItemRel0024.__table__
    existentes = {}
    ids = sorted(venda_ids)
    for lote in _em_lotes(ids, 5000):
        linhas = conexao.execute(
            select(tabela).where(tabela.c.venda_id.in_(lote))
        ).mappings()
        for linha in linhas:
            chave = (
                linha["venda_id"],
                linha["produto_id"],
                linha["ocorrencia_produto"],
            )
            existentes[chave] = dict(linha)
    return existentes


def _preparar_itens(itens, mapa_vendas, mapa_produtos, existentes):
    novos = []
    ja_existentes = 0
    campos_comparacao = (
        "quantidade",
        "valor_unitario",
        "percentual_desconto",
        "valor_desconto",
        "valor_acrescimo",
        "valor_total",
    )
    for item in itens:
        venda_id = mapa_vendas[(item["filial"], item["numero_venda"])]
        produto_id = mapa_produtos[(item["filial"], item["codigo_produto"])]
        chave = (venda_id, produto_id, item["ocorrencia_produto"])
        valores = {
            "venda_id": venda_id,
            "produto_id": produto_id,
            "ocorrencia_produto": item["ocorrencia_produto"],
            "quantidade": item["quantidade"],
            "valor_unitario": item["valor_unitario"],
            "percentual_desconto": item["percentual_desconto"],
            "valor_desconto": item["valor_desconto"],
            "valor_acrescimo": item["valor_acrescimo"],
            "valor_total": item["valor_total"],
            "linha_relatorio": item["linha_relatorio"],
        }
        atual = existentes.get(chave)
        if atual:
            if any(atual[campo] != valores[campo] for campo in campos_comparacao):
                raise ErroImportacaoRel0024(
                    "Item existente possui valores divergentes para a chave "
                    f"{chave}."
                )
            ja_existentes += 1
        else:
            novos.append(valores)
    return novos, ja_existentes


def _contar_duplicidades(conexao, tabela, colunas):
    grupos = (
        select(*colunas)
        .group_by(*colunas)
        .having(func.count() > 1)
        .subquery()
    )
    return conexao.execute(select(func.count()).select_from(grupos)).scalar_one()


def _contagens_finais(conexao):
    contagens = {}
    for tabela in TABELAS_REL0024:
        contagens[tabela.name] = conexao.execute(
            select(func.count()).select_from(tabela)
        ).scalar_one()
    contagens["duplicidades_produtos"] = _contar_duplicidades(
        conexao,
        ProdutoRel0024.__table__,
        [ProdutoRel0024.__table__.c.filial, ProdutoRel0024.__table__.c.codigo_produto],
    )
    contagens["duplicidades_vendas"] = _contar_duplicidades(
        conexao,
        VendaRel0024.__table__,
        [VendaRel0024.__table__.c.filial, VendaRel0024.__table__.c.numero_venda],
    )
    contagens["duplicidades_itens"] = _contar_duplicidades(
        conexao,
        ItemRel0024.__table__,
        [
            ItemRel0024.__table__.c.venda_id,
            ItemRel0024.__table__.c.produto_id,
            ItemRel0024.__table__.c.ocorrencia_produto,
        ],
    )
    return contagens


def importar_rel0024(caminho=ARQUIVO_PADRAO):
    inicio = time.perf_counter()
    resultado = ler_rel0024(Path(caminho))
    validacao = validar_rel0024(resultado)
    _validar_para_importacao(resultado, validacao)

    engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
    try:
        _criar_estrutura(engine)
        with engine.begin() as conexao:
            banco = _confirmar_destino(conexao)
            importacao_existente = conexao.execute(
                select(ImportacaoRel0024.__table__).where(
                    ImportacaoRel0024.__table__.c.hash_sha256
                    == resultado["hash_sha256"]
                )
            ).mappings().first()
            if importacao_existente:
                contagens = _contagens_finais(conexao)
                return {
                    "banco": banco,
                    "resultado": resultado,
                    "validacao": validacao,
                    "arquivo_ja_importado": True,
                    "produtos_inseridos": 0,
                    "produtos_existentes": validacao["produtos_distintos"],
                    "vendas_inseridas": 0,
                    "vendas_existentes": validacao["vendas_distintas"],
                    "itens_inseridos": 0,
                    "itens_existentes": validacao["itens"],
                    "contagens": contagens,
                    "tempo_total_segundos": time.perf_counter() - inicio,
                }

            produtos_arquivo = _produtos_do_arquivo(resultado["itens"])
            vendas_arquivo = _vendas_do_arquivo(resultado["itens"])
            mapa_produtos, produtos_inseridos, produtos_existentes = (
                _sincronizar_produtos(conexao, produtos_arquivo)
            )
            mapa_vendas, vendas_inseridas, vendas_existentes = (
                _sincronizar_vendas(conexao, vendas_arquivo)
            )
            existentes = _buscar_itens_existentes(
                conexao, set(mapa_vendas.values())
            )
            novos_itens, itens_existentes = _preparar_itens(
                resultado["itens"], mapa_vendas, mapa_produtos, existentes
            )

            importacao_id = conexao.execute(
                insert(ImportacaoRel0024.__table__)
                .values(
                    hash_sha256=resultado["hash_sha256"],
                    nome_arquivo=Path(caminho).name,
                    periodo_inicial=validacao["primeira_data"],
                    periodo_final=validacao["ultima_data"],
                    filial=validacao["filiais"][0],
                    quantidade_vendas=validacao["vendas_distintas"],
                    quantidade_itens_arquivo=validacao["itens"],
                    quantidade_itens_inseridos=len(novos_itens),
                    quantidade_produtos=validacao["produtos_distintos"],
                    soma_quantidades=validacao["soma_quantidades"],
                    soma_valores=validacao["soma_valores"],
                    registros_invalidos=validacao["linhas_invalidas"],
                )
                .returning(ImportacaoRel0024.__table__.c.id)
            ).scalar_one()
            for item in novos_itens:
                item["importacao_id"] = importacao_id
            for lote in _em_lotes(novos_itens):
                conexao.execute(insert(ItemRel0024.__table__), lote)

            contagens = _contagens_finais(conexao)
            if any(
                contagens[campo] != 0
                for campo in (
                    "duplicidades_produtos",
                    "duplicidades_vendas",
                    "duplicidades_itens",
                )
            ):
                raise ErroImportacaoRel0024(
                    "A validação encontrou duplicidades após a carga."
                )

        return {
            "banco": banco,
            "resultado": resultado,
            "validacao": validacao,
            "arquivo_ja_importado": False,
            "produtos_inseridos": produtos_inseridos,
            "produtos_existentes": produtos_existentes,
            "vendas_inseridas": vendas_inseridas,
            "vendas_existentes": vendas_existentes,
            "itens_inseridos": len(novos_itens),
            "itens_existentes": itens_existentes,
            "contagens": contagens,
            "tempo_total_segundos": time.perf_counter() - inicio,
        }
    finally:
        engine.dispose()


def imprimir_relatorio(relatorio):
    resultado = relatorio["resultado"]
    validacao = relatorio["validacao"]
    extras_repetidos = sum(
        quantidade - 1
        for quantidade in validacao["chaves_repetidas"].values()
    )

    print("=== IMPORTAÇÃO REL_0024 ===")
    print(f"Arquivo: {resultado['arquivo']}")
    print(f"Aba utilizada: {resultado['aba_utilizada']}")
    print(f"Banco destino: {relatorio['banco']}")
    print("Mapeamento: A=venda, B=filial, G=data, K=hora bruta, Q=quantidade,")
    print("             S=valor unitário, T/U=desconto, Y=valor total")
    print("Produto: linha 'Total Produto: <código> <descrição>'")
    print("Colunas pessoais M/N/O ignoradas integralmente.")
    print(f"Filial encontrada: {validacao['filiais']}")
    print(f"Período: {validacao['primeira_data']} a {validacao['ultima_data']}")
    print(f"Vendas distintas: {validacao['vendas_distintas']}")
    print(f"Itens venda x produto: {validacao['itens']}")
    print(f"Produtos distintos: {validacao['produtos_distintos']}")
    print(f"Soma das quantidades: {validacao['soma_quantidades']}")
    print(f"Soma dos valores: {validacao['soma_valores']}")
    print(f"Registros inválidos: {validacao['linhas_invalidas']}")
    print(
        "Chaves venda+produto repetidas no arquivo: "
        f"{len(validacao['chaves_repetidas'])} "
        f"({extras_repetidos} ocorrências adicionais preservadas)"
    )
    print(f"Arquivo já importado: {relatorio['arquivo_ja_importado']}")
    print(
        f"Produtos - inseridos: {relatorio['produtos_inseridos']}; "
        f"existentes: {relatorio['produtos_existentes']}"
    )
    print(
        f"Vendas - inseridas: {relatorio['vendas_inseridas']}; "
        f"existentes: {relatorio['vendas_existentes']}"
    )
    print(
        f"Itens - inseridos: {relatorio['itens_inseridos']}; "
        f"existentes: {relatorio['itens_existentes']}"
    )
    print("CONTAGENS FINAIS")
    for campo, valor in relatorio["contagens"].items():
        print(f"- {campo}: {valor}")
    print(f"Tempo total: {relatorio['tempo_total_segundos']:.2f} segundos")


def main():
    parser = argparse.ArgumentParser(
        description="Importa um relatório Rel_0024 XLS/XLSX no banco do TCC."
    )
    parser.add_argument(
        "arquivo",
        nargs="?",
        default=str(ARQUIVO_PADRAO),
        help="Caminho do relatório; padrão: dados/6 meses.xlsx",
    )
    argumentos = parser.parse_args()
    try:
        relatorio = importar_rel0024(argumentos.arquivo)
    except (ErroRel0024, ErroImportacaoRel0024) as erro:
        print("IMPORTAÇÃO REL_0024 INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("IMPORTAÇÃO REL_0024 REVERTIDA POR ERRO DE BANCO")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None
    imprimir_relatorio(relatorio)


if __name__ == "__main__":
    main()
