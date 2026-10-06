"""Sincroniza o cadastro mestre do sgfpod1 para o banco local do TCC.

O banco da farmácia é consultado pela conexão protegida, somente leitura.
Toda escrita, inclusive o espelho e o enriquecimento, ocorre numa transação
do ``tcc_medicamentos``.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
import sys

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import obter_url_banco_aplicacao
from models.rel0024 import CadastroProdutoFarmacia, ProdutoRel0024
from utils.classificacao_medicamentos import (
    CLASSIFICACOES_MED,
    SEM_CLASSIFICACAO,
    classificar_elegibilidade,
)

from .conexao_farmacia import conectar_farmacia_somente_leitura


BANCO_TCC = "tcc_medicamentos"
COD_REDE = 1
ORIGEM_CADASTRO = "sgfpod1.cadprodu+cadtipro"
ORIGEM_CLASSIFICACAO = (
    "cadastro_local_sgfpod1|regra_primeiro_token_MED_10D4"
)
LOTE = 1000

SQL_FONTE = text(
    """
    SELECT p.cod_rede, p.cod_reduzido, p.cod_tipo, t.nom_tipo,
           p.nom_produto, p.flg_ativo, p.flg_excluir
      FROM public.cadprodu AS p
      LEFT JOIN public.cadtipro AS t
        ON t.cod_rede = p.cod_rede AND t.cod_tipo = p.cod_tipo
     WHERE p.cod_rede = :cod_rede
     ORDER BY p.cod_reduzido
    """
)

SQL_ESTADO = text(
    """
    SELECT
      (SELECT COUNT(*) FROM public.importacoes_rel0024) AS importacoes,
      (SELECT COUNT(*) FROM public.produtos_rel0024) AS produtos,
      (SELECT COUNT(*) FROM public.vendas_rel0024) AS vendas,
      (SELECT COUNT(*) FROM public.itens_rel0024) AS itens,
      (SELECT COALESCE(SUM(quantidade), 0) FROM public.itens_rel0024)
        AS quantidade,
      (SELECT MIN(data) FROM public.vendas_rel0024) AS data_inicial,
      (SELECT MAX(data) FROM public.vendas_rel0024) AS data_final,
      (SELECT COUNT(*) FROM public.divergencias_descricao_rel0024)
        AS divergencias_descricao,
      (SELECT COUNT(*) FROM public.vendas) AS vendas_ficticias
    """
)

ESTADO_ESPERADO = {
    "importacoes": 2,
    "produtos": 6110,
    "vendas": 56756,
    "itens": 108166,
    "quantidade": Decimal("158385"),
    "data_inicial": date(2025, 4, 1),
    "data_final": date(2026, 3, 31),
    "divergencias_descricao": 1,
    "vendas_ficticias": 30,
}

COLUNAS_CLASSIFICACAO = {
    "classificacao_operacional": "VARCHAR(40)",
    "elegivel_analise_automedicacao": "BOOLEAN",
}


def _confirmar_tcc(conexao):
    banco = conexao.execute(text("SELECT current_database()")).scalar_one()
    if banco != BANCO_TCC:
        raise RuntimeError(
            f"Banco TCC recusado: esperado {BANCO_TCC!r}, recebido {banco!r}."
        )


def _validar_estado(estado, fase):
    diferencas = {
        campo: (estado[campo], esperado)
        for campo, esperado in ESTADO_ESPERADO.items()
        if estado[campo] != esperado
    }
    if diferencas:
        raise RuntimeError(
            f"Estado do banco TCC divergente {fase}; nenhuma sincronização "
            f"será confirmada: {diferencas}."
        )


def _ler_fonte():
    with conectar_farmacia_somente_leitura() as farmacia:
        modo = farmacia.execute(text("SHOW transaction_read_only")).scalar_one()
        if modo != "on":
            raise RuntimeError(
                "sgfpod1 não está em transaction_read_only=on; operação parada."
            )
        linhas = [
            dict(linha)
            for linha in farmacia.execute(
                SQL_FONTE, {"cod_rede": COD_REDE}
            ).mappings()
        ]
    chaves = [(linha["cod_rede"], linha["cod_reduzido"]) for linha in linhas]
    if not linhas or len(chaves) != len(set(chaves)):
        raise RuntimeError(
            "A origem retornou catálogo vazio ou chaves compostas duplicadas."
        )
    if any(linha["cod_rede"] != COD_REDE or linha["cod_reduzido"] is None
           for linha in linhas):
        raise RuntimeError("A origem contém chave de produto inválida.")
    return linhas


def _em_lotes(linhas, tamanho=LOTE):
    for inicio in range(0, len(linhas), tamanho):
        yield linhas[inicio : inicio + tamanho]


def _estado_classificacao(nom_tipo):
    if nom_tipo is None or not str(nom_tipo).strip():
        return SEM_CLASSIFICACAO, False
    return classificar_elegibilidade(nom_tipo)


def _sincronizar_mestre(conexao, fonte):
    tabela = CadastroProdutoFarmacia.__table__
    atuais = {
        (linha.cod_rede, linha.cod_reduzido): dict(linha._mapping)
        for linha in conexao.execute(select(tabela))
    }
    inseridos, atualizados, inalterados = [], [], []
    campos_fonte = (
        "cod_tipo", "nom_tipo", "nom_produto", "flg_ativo", "flg_excluir"
    )
    for linha in fonte:
        chave = (linha["cod_rede"], linha["cod_reduzido"])
        registro = {**linha, "origem": ORIGEM_CADASTRO}
        atual = atuais.get(chave)
        if atual is None:
            inseridos.append(registro)
        elif any(atual[campo] != registro[campo] for campo in campos_fonte) \
                or atual["origem"] != ORIGEM_CADASTRO:
            atualizados.append(registro)
        else:
            inalterados.append(chave)

    for lote in _em_lotes(inseridos + atualizados):
        comando = pg_insert(tabela).values(lote)
        comando = comando.on_conflict_do_update(
            index_elements=[tabela.c.cod_rede, tabela.c.cod_reduzido],
            set_={
                "cod_tipo": comando.excluded.cod_tipo,
                "nom_tipo": comando.excluded.nom_tipo,
                "nom_produto": comando.excluded.nom_produto,
                "flg_ativo": comando.excluded.flg_ativo,
                "flg_excluir": comando.excluded.flg_excluir,
                "origem": comando.excluded.origem,
                "sincronizado_em": func.now(),
            },
        )
        conexao.execute(comando)

    sem_tipo = sum(linha["cod_tipo"] is None for linha in fonte)
    total = conexao.execute(
        select(func.count()).select_from(tabela).where(
            tabela.c.cod_rede == COD_REDE
        )
    ).scalar_one()
    if total != len(fonte):
        raise RuntimeError(
            f"Catálogo local incompleto: origem={len(fonte)}, local={total}."
        )
    return {
        "inseridos": len(inseridos),
        "atualizados": len(atualizados),
        "inalterados": len(inalterados),
        "sem_tipo": sem_tipo,
        "total_final": total,
    }


def _enriquecer_catalogo_tcc(conexao):
    mestre = CadastroProdutoFarmacia.__table__
    produtos = ProdutoRel0024.__table__
    fonte = {
        linha.cod_reduzido: dict(linha._mapping)
        for linha in conexao.execute(
            select(mestre).where(mestre.c.cod_rede == COD_REDE)
        )
    }
    catalogo = list(conexao.execute(select(produtos)).mappings())
    alteracoes, divergencias = [], []
    for produto in catalogo:
        origem = fonte.get(produto["codigo_produto"])
        tipo = origem["cod_tipo"] if origem else None
        nom_tipo = origem["nom_tipo"] if origem else None
        classificacao, elegivel = _estado_classificacao(nom_tipo)
        proveniencia = ORIGEM_CLASSIFICACAO if origem else None

        conflitos = {}
        for campo, esperado in (
            ("cod_tipo", tipo),
            ("nom_tipo", nom_tipo),
            ("classificacao_operacional", classificacao),
            ("elegivel_analise_automedicacao", elegivel),
        ):
            atual = produto[campo]
            if atual is not None and atual != esperado:
                conflitos[campo] = {"atual": atual, "cadastro_local": esperado}
        if conflitos:
            divergencias.append(
                {
                    "filial": produto["filial"],
                    "codigo_produto": produto["codigo_produto"],
                    "divergencias": conflitos,
                }
            )
            continue

        novo = {
            "id": produto["id"],
            "cod_tipo_depois": produto["cod_tipo"] if produto["cod_tipo"] is not None else tipo,
            "nom_tipo_depois": produto["nom_tipo"] if produto["nom_tipo"] is not None else nom_tipo,
            "origem_depois": proveniencia or produto["origem_classificacao"],
            "classificacao_depois": classificacao,
            "elegivel_depois": elegivel,
            "cod_tipo_antes": produto["cod_tipo"],
            "nom_tipo_antes": produto["nom_tipo"],
            "origem_antes": produto["origem_classificacao"],
            "classificacao_antes": produto["classificacao_operacional"],
            "elegivel_antes": produto["elegivel_analise_automedicacao"],
        }
        if any(
            novo[campo + "_depois"] != novo[campo + "_antes"]
            for campo in (
                "cod_tipo", "nom_tipo", "origem", "classificacao", "elegivel"
            )
        ):
            alteracoes.append(novo)

    if alteracoes:
        resultado = conexao.execute(
            text(
                """
                UPDATE public.produtos_rel0024
                   SET cod_tipo = :cod_tipo_depois,
                       nom_tipo = :nom_tipo_depois,
                       origem_classificacao = :origem_depois,
                       classificacao_operacional = :classificacao_depois,
                       elegivel_analise_automedicacao = :elegivel_depois
                 WHERE id = :id
                   AND cod_tipo IS NOT DISTINCT FROM :cod_tipo_antes
                   AND nom_tipo IS NOT DISTINCT FROM :nom_tipo_antes
                   AND origem_classificacao IS NOT DISTINCT FROM :origem_antes
                   AND classificacao_operacional IS NOT DISTINCT FROM :classificacao_antes
                   AND elegivel_analise_automedicacao IS NOT DISTINCT FROM :elegivel_antes
                """
            ),
            alteracoes,
        )
        if resultado.rowcount != len(alteracoes):
            raise RuntimeError(
                "O catálogo Rel_0024 mudou durante o enriquecimento; rollback."
            )
    contagens = Counter(linha["classificacao_operacional"] for linha in catalogo)
    por_id = {item["id"]: item for item in catalogo}
    for item in alteracoes:
        por_id[item["id"]] = {
            **por_id[item["id"]],
            "cod_tipo": item["cod_tipo_depois"],
            "nom_tipo": item["nom_tipo_depois"],
            "origem_classificacao": item["origem_depois"],
            "classificacao_operacional": item["classificacao_depois"],
            "elegivel_analise_automedicacao": item["elegivel_depois"],
        }
    contagens = Counter(
        item["classificacao_operacional"] for item in por_id.values()
    )
    elegiveis = sum(
        bool(item["elegivel_analise_automedicacao"])
        for item in por_id.values()
    )
    contagens["MED_TOTAL"] = sum(
        contagens[categoria] for categoria in CLASSIFICACOES_MED
    )
    contagens["ELEGIVEIS"] = elegiveis
    contagens["NAO_ELEGIVEIS"] = len(catalogo) - elegiveis
    contagens["COM_TIPO"] = sum(
        item["nom_tipo"] is not None for item in por_id.values()
    )
    contagens["SEM_TIPO"] = len(catalogo) - contagens["COM_TIPO"]
    return {
        "total": len(catalogo),
        "atualizados": len(alteracoes),
        "divergencias": divergencias,
        "classificacoes": dict(contagens),
    }


def executar():
    """Lê o catálogo fonte e sincroniza/enriquece em transação no TCC."""

    fonte = _ler_fonte()
    engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
    try:
        with engine.begin() as tcc:
            _confirmar_tcc(tcc)
            estado_antes = dict(tcc.execute(SQL_ESTADO).mappings().one())
            _validar_estado(estado_antes, "antes da sincronização")

            CadastroProdutoFarmacia.metadata.create_all(
                bind=tcc,
                tables=[CadastroProdutoFarmacia.__table__],
                checkfirst=True,
            )
            colunas = {
                item["name"]
                for item in inspect(tcc).get_columns(
                    "produtos_rel0024", schema="public"
                )
            }
            for nome, tipo in COLUNAS_CLASSIFICACAO.items():
                if nome not in colunas:
                    tcc.execute(
                        text(
                            f"ALTER TABLE public.produtos_rel0024 "
                            f"ADD COLUMN {nome} {tipo}"
                        )
                    )

            sync = _sincronizar_mestre(tcc, fonte)
            enriquecimento = _enriquecer_catalogo_tcc(tcc)
            estado_depois = dict(tcc.execute(SQL_ESTADO).mappings().one())
            _validar_estado(estado_depois, "depois da sincronização")
            if estado_depois != estado_antes:
                raise RuntimeError(
                    "A sincronização alterou os dados de vendas; rollback."
                )
        return {
            "banco_farmacia": "sgfpod1",
            "transaction_read_only": "on",
            "cod_rede": COD_REDE,
            "catalogo_fonte": len(fonte),
            "sincronizacao": sync,
            "enriquecimento": enriquecimento,
            "integridade_antes": estado_antes,
            "integridade_depois": estado_depois,
        }
    finally:
        engine.dispose()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    resultado = executar()
    print("SINCRONIZAÇÃO DO CADASTRO MESTRE CONCLUÍDA")
    print(f"Origem: sgfpod1, rede {resultado['cod_rede']} (somente leitura)")
    print(f"Produtos lidos: {resultado['catalogo_fonte']}")
    print(f"Sincronização: {resultado['sincronizacao']}")
    print(f"Enriquecimento: {resultado['enriquecimento']}")
    print("Integridade Rel_0024: preservada")


if __name__ == "__main__":
    main()
