"""Importador transacional e idempotente do relatório oficial Rel_0024."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import time

from sqlalchemy import create_engine, func, insert, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from database import obter_url_banco_aplicacao
from models.rel0024 import (
    CadastroProdutoFarmacia,
    DivergenciaDescricaoRel0024,
    ImportacaoRel0024,
    ItemRel0024,
    ProdutoRel0024,
    TABELAS_REL0024,
    VendaRel0024,
)
from utils.classificacao_medicamentos import (
    SEM_CLASSIFICACAO,
    classificar_elegibilidade,
)

from .conexao_farmacia import PASTA_PROJETO
from .validar_rel0024 import (
    ErroRel0024,
    calcular_hash_rel0024,
    ler_rel0024,
    validar_rel0024,
)


ARQUIVO_PADRAO = PASTA_PROJETO / "dados" / "6 meses.xlsx"
BANCO_DESTINO = "tcc_medicamentos"
TAMANHO_LOTE = 1000

COLUNAS_AUDITORIA = {
    "quantidade_linhas_lidas": "INTEGER NOT NULL DEFAULT 0",
    "quantidade_linhas_nao_venda": "INTEGER NOT NULL DEFAULT 0",
    "quantidade_linhas_venda": "INTEGER NOT NULL DEFAULT 0",
    "quantidade_linhas_devolucao": "INTEGER NOT NULL DEFAULT 0",
    "soma_quantidades_devolucao": "NUMERIC(16, 4) NOT NULL DEFAULT 0",
    "soma_valores_devolucao": "NUMERIC(18, 2) NOT NULL DEFAULT 0",
    "soma_quantidades_liquida": "NUMERIC(16, 4) NOT NULL DEFAULT 0",
    "soma_valores_liquido": "NUMERIC(18, 2) NOT NULL DEFAULT 0",
    "produtos_inseridos": "INTEGER NOT NULL DEFAULT 0",
    "produtos_existentes": "INTEGER NOT NULL DEFAULT 0",
    "vendas_inseridas": "INTEGER NOT NULL DEFAULT 0",
    "vendas_existentes": "INTEGER NOT NULL DEFAULT 0",
    "itens_existentes": "INTEGER NOT NULL DEFAULT 0",
    "soma_quantidades_inseridas": "NUMERIC(16, 4) NOT NULL DEFAULT 0",
    "quantidade_conflitos": "INTEGER NOT NULL DEFAULT 0",
    "quantidade_divergencias_descricao": "INTEGER NOT NULL DEFAULT 0",
    "tempo_processamento_segundos": "NUMERIC(12, 3) NOT NULL DEFAULT 0",
}


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
    colunas_atuais = {
        coluna["name"]
        for coluna in inspetor.get_columns("importacoes_rel0024")
    }
    colunas_ausentes = {
        nome: definicao
        for nome, definicao in COLUNAS_AUDITORIA.items()
        if nome not in colunas_atuais
    }
    if colunas_ausentes:
        with engine.begin() as conexao:
            _confirmar_destino(conexao)
            for nome, definicao in colunas_ausentes.items():
                conexao.execute(text(
                    "ALTER TABLE public.importacoes_rel0024 "
                    f"ADD COLUMN IF NOT EXISTS {nome} {definicao}"
                ))
            if "produtos_inseridos" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET produtos_inseridos = quantidade_produtos"
                ))
            if "vendas_inseridas" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET vendas_inseridas = quantidade_vendas"
                ))
            if "soma_quantidades_inseridas" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET soma_quantidades_inseridas = soma_quantidades"
                ))
            if "quantidade_linhas_venda" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET quantidade_linhas_venda = quantidade_itens_arquivo"
                ))
            if "soma_quantidades_liquida" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET soma_quantidades_liquida = soma_quantidades"
                ))
            if "soma_valores_liquido" in colunas_ausentes:
                conexao.execute(text(
                    "UPDATE public.importacoes_rel0024 "
                    "SET soma_valores_liquido = soma_valores"
                ))

    colunas_produtos = {
        coluna["name"]
        for coluna in inspetor.get_columns("produtos_rel0024")
    }
    for nome, tipo in {
        "classificacao_operacional": "VARCHAR(40)",
        "elegivel_analise_automedicacao": "BOOLEAN",
    }.items():
        if nome not in colunas_produtos:
            with engine.begin() as conexao:
                _confirmar_destino(conexao)
                conexao.execute(
                    text(
                        "ALTER TABLE public.produtos_rel0024 "
                        f"ADD COLUMN {nome} {tipo}"
                    )
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
    if not validacao["totais_produto_quantidade_conferem_total_geral"]:
        problemas.append("soma dos Total Produto diverge do Total Geral")
    if not validacao["totais_produto_valor_conferem_total_geral"]:
        problemas.append(
            "soma financeira dos Total Produto diverge do Total Geral"
        )
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
    cadastro = CadastroProdutoFarmacia.__table__
    filiais = sorted({chave[0] for chave in produtos_arquivo})
    codigos = sorted({codigo for _filial, codigo in produtos_arquivo})
    metadados_locais = {
        linha.cod_reduzido: dict(linha._mapping)
        for linha in conexao.execute(
            select(cadastro).where(
                cadastro.c.cod_rede == 1,
                cadastro.c.cod_reduzido.in_(codigos),
            )
        )
    }

    def preparar_metadados(codigo):
        local = metadados_locais.get(codigo)
        tipo = local["cod_tipo"] if local else None
        nom_tipo = local["nom_tipo"] if local else None
        if nom_tipo is None or not str(nom_tipo).strip():
            classificacao, elegivel = SEM_CLASSIFICACAO, False
        else:
            classificacao, elegivel = classificar_elegibilidade(nom_tipo)
        return {
            "cod_tipo": tipo,
            "nom_tipo": nom_tipo,
            "origem_classificacao": (
                "cadastro_local_sgfpod1|regra_primeiro_token_MED_10D4"
                if local
                else None
            ),
            "classificacao_operacional": classificacao,
            "elegivel_analise_automedicacao": elegivel,
        }

    encontrados = conexao.execute(
        select(
            tabela.c.id,
            tabela.c.filial,
            tabela.c.codigo_produto,
            tabela.c.descricao_original,
            tabela.c.cod_tipo,
            tabela.c.nom_tipo,
            tabela.c.origem_classificacao,
            tabela.c.classificacao_operacional,
            tabela.c.elegivel_analise_automedicacao,
        ).where(tabela.c.filial.in_(filiais))
    ).mappings()
    existentes = {
        (linha["filial"], linha["codigo_produto"]): dict(linha)
        for linha in encontrados
    }
    divergencias_descricao = []
    for chave, descricao in produtos_arquivo.items():
        atual = existentes.get(chave)
        if atual and atual["descricao_original"] != descricao:
            divergencias_descricao.append(
                {
                    "filial": chave[0],
                    "codigo_produto": chave[1],
                    "descricao_banco": atual["descricao_original"],
                    "descricao_arquivo": descricao,
                }
            )

        if atual:
            esperado = preparar_metadados(chave[1])
            for campo in (
                "cod_tipo",
                "nom_tipo",
                "classificacao_operacional",
                "elegivel_analise_automedicacao",
            ):
                valor_atual = atual[campo]
                valor_esperado = esperado[campo]
                if valor_atual is not None and valor_atual != valor_esperado:
                    raise ErroImportacaoRel0024(
                        "Classificação divergente para o produto "
                        f"(filial={chave[0]}, codigo={chave[1]}), campo "
                        f"{campo}: banco={valor_atual!r}, "
                        f"cadastro local={valor_esperado!r}."
                    )
            valores = {
                nome: (
                    atual[nome]
                    if atual[nome] is not None
                    and nome != "origem_classificacao"
                    else esperado[nome]
                )
                for nome in esperado
            }
            if any(atual[nome] != valor for nome, valor in valores.items()):
                conexao.execute(
                    update(tabela)
                    .where(tabela.c.id == atual["id"])
                    .values(**valores)
                )

    novos = [
        {
            "filial": chave[0],
            "codigo_produto": chave[1],
            "descricao_original": descricao,
            **preparar_metadados(chave[1]),
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
    return (
        mapa,
        len(novos),
        len(produtos_arquivo) - len(novos),
        divergencias_descricao,
    )


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
    tabela_vendas = VendaRel0024.__table__
    existentes = defaultdict(list)
    ids = sorted(venda_ids)
    for lote in _em_lotes(ids, 5000):
        linhas = conexao.execute(
            select(
                tabela,
                tabela_vendas.c.data.label("data_venda"),
                tabela_vendas.c.hora_original,
                tabela_vendas.c.tipo_original,
            )
            .join(
                tabela_vendas,
                tabela_vendas.c.id == tabela.c.venda_id,
            )
            .where(tabela.c.venda_id.in_(lote))
        ).mappings()
        for linha in linhas:
            chave_grupo = (linha["venda_id"], linha["produto_id"])
            existentes[chave_grupo].append(dict(linha))
    return dict(existentes)


CAMPOS_ASSINATURA_ITEM = (
    "data",
    "hora",
    "tipo",
    "quantidade",
    "valor_unitario",
    "percentual_desconto",
    "valor_desconto",
    "valor_acrescimo",
    "valor_total",
)


def _assinatura_item_arquivo(item):
    return (
        item["data"],
        item["hora_bruta"],
        item["tipo"] or None,
        item["quantidade"],
        item["valor_unitario"],
        item["percentual_desconto"],
        item["valor_desconto"],
        item["valor_acrescimo"],
        item["valor_total"],
    )


def _assinatura_item_banco(item):
    return (
        item["data_venda"],
        item["hora_original"],
        item["tipo_original"],
        item["quantidade"],
        item["valor_unitario"],
        item["percentual_desconto"],
        item["valor_desconto"],
        item["valor_acrescimo"],
        item["valor_total"],
    )


def _detalhar_diferenca_multiconjunto(assinaturas_banco, assinaturas_arquivo):
    somente_banco = assinaturas_banco - assinaturas_arquivo
    somente_arquivo = assinaturas_arquivo - assinaturas_banco

    def formatar(contagem):
        detalhes = []
        for assinatura, multiplicidade in contagem.items():
            valores = dict(zip(CAMPOS_ASSINATURA_ITEM, assinatura))
            valores["multiplicidade"] = multiplicidade
            detalhes.append(valores)
        return detalhes

    return formatar(somente_banco), formatar(somente_arquivo)


def _preparar_itens(itens, mapa_vendas, mapa_produtos, existentes):
    novos = []
    ja_existentes = 0
    grupos_arquivo = defaultdict(list)
    for item in itens:
        venda_id = mapa_vendas[(item["filial"], item["numero_venda"])]
        produto_id = mapa_produtos[(item["filial"], item["codigo_produto"])]
        grupos_arquivo[(venda_id, produto_id)].append(item)

    for chave_grupo, itens_arquivo in grupos_arquivo.items():
        itens_banco = existentes.get(chave_grupo, [])
        if itens_banco:
            assinaturas_banco = Counter(
                _assinatura_item_banco(item) for item in itens_banco
            )
            assinaturas_arquivo = Counter(
                _assinatura_item_arquivo(item) for item in itens_arquivo
            )
            if assinaturas_banco != assinaturas_arquivo:
                somente_banco, somente_arquivo = (
                    _detalhar_diferenca_multiconjunto(
                        assinaturas_banco, assinaturas_arquivo
                    )
                )
                primeiro = itens_arquivo[0]
                chave_natural = (
                    primeiro["filial"],
                    primeiro["numero_venda"],
                    primeiro["codigo_produto"],
                )
                raise ErroImportacaoRel0024(
                    "Itens existentes possuem conteúdo divergente para o "
                    "grupo (filial, venda, produto) "
                    f"{chave_natural}. Somente no banco: {somente_banco}. "
                    f"Somente no arquivo: {somente_arquivo}."
                )
            ja_existentes += len(itens_arquivo)
            continue

        venda_id, produto_id = chave_grupo
        for item in itens_arquivo:
            novos.append(
                {
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
            )
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


def _buscar_importacao_por_hash(conexao, hash_sha256):
    return conexao.execute(
        select(ImportacaoRel0024.__table__).where(
            ImportacaoRel0024.__table__.c.hash_sha256 == hash_sha256
        )
    ).mappings().first()


def _buscar_divergencias_descricao(conexao, importacao_id):
    tabela = DivergenciaDescricaoRel0024.__table__
    return [
        dict(linha)
        for linha in conexao.execute(
            select(
                tabela.c.filial,
                tabela.c.codigo_produto,
                tabela.c.descricao_banco,
                tabela.c.descricao_arquivo,
            )
            .where(tabela.c.importacao_id == importacao_id)
            .order_by(tabela.c.filial, tabela.c.codigo_produto)
        ).mappings()
    ]


def _relatorio_hash_existente(
    conexao, importacao, caminho, hash_sha256, inicio
):
    validacao = {
        "filiais": [importacao["filial"]],
        "primeira_data": importacao["periodo_inicial"],
        "ultima_data": importacao["periodo_final"],
        "vendas_distintas": importacao["quantidade_vendas"],
        "itens": importacao["quantidade_itens_arquivo"],
        "produtos_distintos": importacao["quantidade_produtos"],
        "soma_quantidades": importacao["soma_quantidades"],
        "soma_valores": importacao["soma_valores"],
        "linhas_venda": importacao["quantidade_linhas_venda"],
        "linhas_devolucao": importacao["quantidade_linhas_devolucao"],
        "quantidade_vendas": importacao["soma_quantidades"],
        "valor_vendas": importacao["soma_valores"],
        "quantidade_devolucoes": importacao[
            "soma_quantidades_devolucao"
        ],
        "valor_devolucoes": importacao["soma_valores_devolucao"],
        "quantidade_liquida_calculada": importacao[
            "soma_quantidades_liquida"
        ],
        "valor_liquido_calculado": importacao["soma_valores_liquido"],
        "quantidade_total_geral": importacao["soma_quantidades_liquida"],
        "valor_total_geral": importacao["soma_valores_liquido"],
        "linhas_invalidas": importacao["registros_invalidos"],
        "chaves_repetidas": {},
    }
    resultado = {
        "arquivo": Path(caminho),
        "hash_sha256": hash_sha256,
        "aba_utilizada": "não reprocessada (hash já registrado)",
        "linhas_estrutura": importacao["quantidade_linhas_lidas"],
        "linhas_movimento": importacao["quantidade_itens_arquivo"],
        "linhas_venda": importacao["quantidade_linhas_venda"],
        "linhas_devolucao": importacao["quantidade_linhas_devolucao"],
        "linhas_nao_venda": importacao["quantidade_linhas_nao_venda"],
    }
    return {
        "banco": _confirmar_destino(conexao),
        "resultado": resultado,
        "validacao": validacao,
        "arquivo_ja_importado": True,
        "nome_arquivo": importacao["nome_arquivo"],
        "produtos_inseridos": 0,
        "produtos_existentes": importacao["quantidade_produtos"],
        "vendas_inseridas": 0,
        "vendas_existentes": importacao["quantidade_vendas"],
        "itens_inseridos": 0,
        "itens_existentes": importacao["quantidade_itens_arquivo"],
        "soma_quantidades_inseridas": 0,
        "linhas_nao_venda": importacao["quantidade_linhas_nao_venda"],
        "divergencias_descricao": _buscar_divergencias_descricao(
            conexao, importacao["id"]
        ),
        "contagens": _contagens_finais(conexao),
        "tempo_total_segundos": time.perf_counter() - inicio,
    }


def _importar_na_transacao(
    conexao, resultado, validacao, caminho, nome_arquivo, inicio
):
    banco = _confirmar_destino(conexao)
    importacao_existente = _buscar_importacao_por_hash(
        conexao, resultado["hash_sha256"]
    )
    if importacao_existente:
        return _relatorio_hash_existente(
            conexao,
            importacao_existente,
            caminho,
            resultado["hash_sha256"],
            inicio,
        )

    produtos_arquivo = _produtos_do_arquivo(resultado["itens"])
    vendas_arquivo = _vendas_do_arquivo(resultado["itens"])
    (
        mapa_produtos,
        produtos_inseridos,
        produtos_existentes,
        divergencias_descricao,
    ) = (
        _sincronizar_produtos(conexao, produtos_arquivo)
    )
    mapa_vendas, vendas_inseridas, vendas_existentes = (
        _sincronizar_vendas(conexao, vendas_arquivo)
    )
    existentes = _buscar_itens_existentes(conexao, set(mapa_vendas.values()))
    novos_itens, itens_existentes = _preparar_itens(
        resultado["itens"], mapa_vendas, mapa_produtos, existentes
    )
    soma_quantidades_inseridas = sum(
        (item["quantidade"] for item in novos_itens), start=0
    )
    tempo_ate_auditoria = time.perf_counter() - inicio

    importacao_id = conexao.execute(
        insert(ImportacaoRel0024.__table__)
        .values(
            hash_sha256=resultado["hash_sha256"],
            nome_arquivo=nome_arquivo,
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
            quantidade_linhas_lidas=resultado["linhas_estrutura"],
            quantidade_linhas_nao_venda=resultado["linhas_nao_venda"],
            quantidade_linhas_venda=resultado["linhas_venda"],
            quantidade_linhas_devolucao=resultado["linhas_devolucao"],
            soma_quantidades_devolucao=resultado[
                "quantidade_devolucoes"
            ],
            soma_valores_devolucao=resultado["valor_devolucoes"],
            soma_quantidades_liquida=resultado[
                "quantidade_liquida_calculada"
            ],
            soma_valores_liquido=resultado["valor_liquido_calculado"],
            produtos_inseridos=produtos_inseridos,
            produtos_existentes=produtos_existentes,
            vendas_inseridas=vendas_inseridas,
            vendas_existentes=vendas_existentes,
            itens_existentes=itens_existentes,
            soma_quantidades_inseridas=soma_quantidades_inseridas,
            quantidade_conflitos=0,
            quantidade_divergencias_descricao=len(
                divergencias_descricao
            ),
            tempo_processamento_segundos=tempo_ate_auditoria,
        )
        .returning(ImportacaoRel0024.__table__.c.id)
    ).scalar_one()
    for divergencia in divergencias_descricao:
        divergencia["importacao_id"] = importacao_id
    for lote in _em_lotes(divergencias_descricao):
        conexao.execute(
            insert(DivergenciaDescricaoRel0024.__table__), lote
        )
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
        "nome_arquivo": nome_arquivo,
        "produtos_inseridos": produtos_inseridos,
        "produtos_existentes": produtos_existentes,
        "vendas_inseridas": vendas_inseridas,
        "vendas_existentes": vendas_existentes,
        "itens_inseridos": len(novos_itens),
        "itens_existentes": itens_existentes,
        "soma_quantidades_inseridas": soma_quantidades_inseridas,
        "linhas_nao_venda": resultado["linhas_nao_venda"],
        "divergencias_descricao": divergencias_descricao,
        "contagens": contagens,
        "tempo_total_segundos": time.perf_counter() - inicio,
    }


def importar_rel0024(
    caminho=ARQUIVO_PADRAO,
    nome_arquivo=None,
    *,
    conexao_externa=None,
):
    """Importa um arquivo; ``conexao_externa`` existe apenas para testes isolados."""

    inicio = time.perf_counter()
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroRel0024(f"Arquivo não encontrado: {caminho}.")
    if caminho.suffix.lower() not in (".xls", ".xlsx"):
        raise ErroRel0024("O relatório deve possuir extensão .xls ou .xlsx.")
    nome_auditoria = Path(nome_arquivo or caminho.name).name[:255]
    hash_sha256 = calcular_hash_rel0024(caminho)

    engine = None
    if conexao_externa is None:
        engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
        _criar_estrutura(engine)
        with engine.connect() as conexao:
            importacao_existente = _buscar_importacao_por_hash(
                conexao, hash_sha256
            )
            if importacao_existente:
                relatorio = _relatorio_hash_existente(
                    conexao,
                    importacao_existente,
                    caminho,
                    hash_sha256,
                    inicio,
                )
                engine.dispose()
                return relatorio
    else:
        _confirmar_destino(conexao_externa)
        importacao_existente = _buscar_importacao_por_hash(
            conexao_externa, hash_sha256
        )
        if importacao_existente:
            return _relatorio_hash_existente(
                conexao_externa,
                importacao_existente,
                caminho,
                hash_sha256,
                inicio,
            )

    try:
        resultado = ler_rel0024(caminho)
        if resultado["hash_sha256"] != hash_sha256:
            raise ErroImportacaoRel0024(
                "O arquivo foi alterado durante o processamento."
            )
        validacao = validar_rel0024(resultado)
        _validar_para_importacao(resultado, validacao)

        if conexao_externa is not None:
            with conexao_externa.begin_nested():
                return _importar_na_transacao(
                    conexao_externa,
                    resultado,
                    validacao,
                    caminho,
                    nome_auditoria,
                    inicio,
                )
        with engine.begin() as conexao:
            return _importar_na_transacao(
                conexao,
                resultado,
                validacao,
                caminho,
                nome_auditoria,
                inicio,
            )
    finally:
        if engine is not None:
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
    print(f"Linhas físicas lidas: {resultado.get('linhas_estrutura', 0)}")
    print(f"Linhas de movimento: {resultado.get('linhas_movimento', 0)}")
    print(f"Linhas Venda: {resultado.get('linhas_venda', validacao['itens'])}")
    print(f"Linhas Devol: {resultado.get('linhas_devolucao', 0)}")
    print(f"Linhas não Venda ignoradas: {relatorio['linhas_nao_venda']}")
    print(
        f"Venda bruta - quantidade: {validacao['quantidade_vendas']}; "
        f"valor: {validacao['valor_vendas']}"
    )
    print(
        f"Devoluções - quantidade: {validacao['quantidade_devolucoes']}; "
        f"valor líquido: {validacao['valor_devolucoes']}"
    )
    print(
        "Total líquido calculado - quantidade: "
        f"{validacao['quantidade_liquida_calculada']}; valor: "
        f"{validacao['valor_liquido_calculado']}"
    )
    print(
        f"Total Geral informado - quantidade: {validacao['quantidade_total_geral']}; "
        f"valor: {validacao['valor_total_geral']}"
    )
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
    print(
        "Quantidade - lida em Venda: "
        f"{validacao['soma_quantidades']}; acrescentada: "
        f"{relatorio['soma_quantidades_inseridas']}"
    )
    print(
        "Divergências de descrição auditadas: "
        f"{len(relatorio['divergencias_descricao'])}"
    )
    for divergencia in relatorio["divergencias_descricao"]:
        print(
            f"- filial {divergencia['filial']}, produto "
            f"{divergencia['codigo_produto']}: "
            f"banco={divergencia['descricao_banco']!r}; "
            f"arquivo={divergencia['descricao_arquivo']!r}"
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
