"""Enriquece metadados de tipo do catálogo Rel_0024 de forma controlada.

O ``sgfpod1`` é consultado exclusivamente pela conexão protegida de leitura.
No ``tcc_medicamentos``, somente campos de classificação ainda ausentes podem
ser preenchidos; valores existentes nunca são sobrescritos.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import sys

from sqlalchemy import bindparam, create_engine, text

from database import obter_url_banco_aplicacao
from utils.classificacao_medicamentos import (
    CLASSIFICACOES_MED,
    classificar_elegibilidade,
)

from .conexao_farmacia import conectar_farmacia_somente_leitura


BANCO_TCC = "tcc_medicamentos"
ORIGEM = "sgfpod1.cadprodu->cadtipro|regra_primeiro_token_MED_10D4"

SQL_CATALOGO = text(
    """
    SELECT id, filial, codigo_produto, descricao_original,
           cod_tipo, nom_tipo, origem_classificacao
    FROM public.produtos_rel0024
    ORDER BY filial, codigo_produto
    """
)
SQL_REDES_CODIGOS = text(
    """
    SELECT cod_rede, cod_reduzido AS codigo_produto
    FROM public.cadprodu
    WHERE cod_reduzido IN :codigos
    ORDER BY cod_rede, cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))
SQL_TIPOS = text(
    """
    SELECT p.cod_reduzido AS codigo_produto, p.cod_tipo, t.nom_tipo
    FROM public.cadprodu AS p
    LEFT JOIN public.cadtipro AS t
      ON t.cod_rede = p.cod_rede AND t.cod_tipo = p.cod_tipo
    WHERE p.cod_rede = :cod_rede
      AND p.cod_reduzido IN :codigos
    ORDER BY p.cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))
SQL_ATUALIZAR = text(
    """
    UPDATE public.produtos_rel0024
       SET cod_tipo = :cod_tipo_depois,
           nom_tipo = :nom_tipo_depois,
           origem_classificacao = :origem_depois
     WHERE id = :id
       AND cod_tipo IS NOT DISTINCT FROM :cod_tipo_antes
       AND nom_tipo IS NOT DISTINCT FROM :nom_tipo_antes
       AND origem_classificacao IS NOT DISTINCT FROM :origem_antes
    """
)
SQL_INTEGRIDADE = text(
    """
    SELECT
      (SELECT COUNT(*) FROM public.importacoes_rel0024) AS importacoes,
      (SELECT COUNT(*) FROM public.produtos_rel0024) AS produtos,
      (SELECT COUNT(*) FROM public.vendas_rel0024) AS vendas,
      (SELECT COUNT(*) FROM public.itens_rel0024) AS itens,
      (SELECT COALESCE(SUM(quantidade), 0) FROM public.itens_rel0024)
        AS soma_quantidades,
      (SELECT MIN(data) FROM public.vendas_rel0024) AS periodo_minimo,
      (SELECT MAX(data) FROM public.vendas_rel0024) AS periodo_maximo,
      (SELECT ARRAY_AGG(DISTINCT filial ORDER BY filial)
         FROM public.vendas_rel0024) AS filiais,
      (SELECT COUNT(*) FROM public.divergencias_descricao_rel0024)
        AS divergencias_descricao,
      (SELECT COUNT(*) FROM public.vendas) AS vendas_ficticias,
      (SELECT COUNT(*) FROM public.itens_rel0024 i
         LEFT JOIN public.vendas_rel0024 v ON v.id = i.venda_id
        WHERE v.id IS NULL) AS itens_sem_venda,
      (SELECT COUNT(*) FROM public.itens_rel0024 i
         LEFT JOIN public.produtos_rel0024 p ON p.id = i.produto_id
        WHERE p.id IS NULL) AS itens_sem_produto,
      (SELECT COUNT(*) FROM public.itens_rel0024 i
         LEFT JOIN public.importacoes_rel0024 x ON x.id = i.importacao_id
        WHERE x.id IS NULL) AS itens_sem_importacao
    """
)

CAMPOS_IMUTAVEIS = (
    "importacoes",
    "produtos",
    "vendas",
    "itens",
    "soma_quantidades",
    "periodo_minimo",
    "periodo_maximo",
    "filiais",
    "divergencias_descricao",
    "vendas_ficticias",
    "itens_sem_venda",
    "itens_sem_produto",
    "itens_sem_importacao",
)


def _linhas(conexao, consulta, parametros=None):
    return [
        dict(linha)
        for linha in conexao.execute(
            consulta, parametros or {}
        ).mappings()
    ]


def _confirmar_tcc(conexao):
    banco = conexao.execute(text("SELECT current_database()")).scalar_one()
    if banco != BANCO_TCC:
        raise RuntimeError(
            f"Banco de destino recusado: esperado {BANCO_TCC!r}, "
            f"obtido {banco!r}."
        )


def _estado_metadados(produto):
    valores = (
        produto["cod_tipo"],
        produto["nom_tipo"],
        produto["origem_classificacao"],
    )
    preenchidos = sum(valor is not None for valor in valores)
    if preenchidos == 0:
        return "pendente_total"
    if preenchidos == len(valores):
        return "enriquecido"
    return "parcial"


def _selecionar_rede(catalogo, redes_codigos):
    """Reutiliza a rede que cobre todos os produtos previamente auditados."""

    codigos_por_rede = defaultdict(set)
    for linha in redes_codigos:
        codigos_por_rede[linha["cod_rede"]].add(linha["codigo_produto"])
    referencia = {
        produto["codigo_produto"]
        for produto in catalogo
        if produto["origem_classificacao"] is not None
    }
    candidatos = [
        rede
        for rede, codigos in codigos_por_rede.items()
        if referencia.issubset(codigos)
    ]
    if not candidatos:
        raise RuntimeError(
            "Nenhuma rede do sgfpod1 cobre todos os produtos já auditados."
        )
    maior_cobertura = max(len(codigos_por_rede[rede]) for rede in candidatos)
    melhores = [
        rede
        for rede in candidatos
        if len(codigos_por_rede[rede]) == maior_cobertura
    ]
    if len(melhores) != 1:
        raise RuntimeError(
            "Não foi possível determinar uma única rede pela cobertura do "
            "catálogo previamente auditado."
        )
    return melhores[0], {
        rede: len(codigos) for rede, codigos in codigos_por_rede.items()
    }


def planejar_enriquecimento(catalogo, tipos_por_codigo):
    """Planeja preenchimentos sem sobrescrever nenhum metadado existente."""

    atualizacoes = []
    divergencias = []
    nao_encontrados = []
    encontrados = 0

    for produto in catalogo:
        codigo = produto["codigo_produto"]
        origem = tipos_por_codigo.get(codigo)
        if origem is None:
            if _estado_metadados(produto) != "enriquecido":
                nao_encontrados.append(
                    {
                        "filial": produto["filial"],
                        "codigo_produto": codigo,
                        "descricao_original": produto["descricao_original"],
                    }
                )
            continue
        encontrados += 1

        conflitos = {}
        for campo in ("cod_tipo", "nom_tipo"):
            valor_local = produto[campo]
            valor_origem = origem[campo]
            if valor_local is not None and valor_local != valor_origem:
                conflitos[campo] = {
                    "tcc": valor_local,
                    "sgfpod1": valor_origem,
                }
        origem_local = produto["origem_classificacao"]
        if origem_local is not None and origem_local != ORIGEM:
            conflitos["origem_classificacao"] = {
                "tcc": origem_local,
                "esperada": ORIGEM,
            }
        if conflitos:
            divergencias.append(
                {
                    "filial": produto["filial"],
                    "codigo_produto": codigo,
                    "divergencias": conflitos,
                }
            )
            continue

        cod_tipo_depois = (
            produto["cod_tipo"]
            if produto["cod_tipo"] is not None
            else origem["cod_tipo"]
        )
        nom_tipo_depois = (
            produto["nom_tipo"]
            if produto["nom_tipo"] is not None
            else origem["nom_tipo"]
        )
        origem_depois = origem_local or ORIGEM
        antes = (
            produto["cod_tipo"],
            produto["nom_tipo"],
            origem_local,
        )
        depois = (cod_tipo_depois, nom_tipo_depois, origem_depois)
        if antes == depois:
            continue
        atualizacoes.append(
            {
                "id": produto["id"],
                "filial": produto["filial"],
                "codigo_produto": codigo,
                "cod_tipo_antes": produto["cod_tipo"],
                "nom_tipo_antes": produto["nom_tipo"],
                "origem_antes": origem_local,
                "cod_tipo_depois": cod_tipo_depois,
                "nom_tipo_depois": nom_tipo_depois,
                "origem_depois": origem_depois,
            }
        )

    return {
        "atualizacoes": atualizacoes,
        "divergencias": divergencias,
        "nao_encontrados": nao_encontrados,
        "encontrados": encontrados,
    }


def reconciliar_classificacoes(catalogo):
    contagens = Counter()
    elegiveis = 0
    for produto in catalogo:
        if produto["nom_tipo"] is None:
            contagens["SEM_CLASSIFICACAO"] += 1
            continue
        classificacao, elegivel = classificar_elegibilidade(
            produto["nom_tipo"]
        )
        contagens[classificacao] += 1
        elegiveis += int(elegivel)
    contagens["MED_TOTAL"] = sum(
        contagens[classificacao] for classificacao in CLASSIFICACOES_MED
    )
    contagens["ELEGIVEIS"] = elegiveis
    return dict(contagens)


def _validar_integridade(antes, depois):
    divergentes = {
        campo: (antes[campo], depois[campo])
        for campo in CAMPOS_IMUTAVEIS
        if antes[campo] != depois[campo]
    }
    if divergentes:
        raise RuntimeError(
            "Integridade violada; a transação será revertida: "
            f"{divergentes}."
        )
    orfaos = sum(
        int(depois[campo])
        for campo in (
            "itens_sem_venda",
            "itens_sem_produto",
            "itens_sem_importacao",
        )
    )
    if orfaos:
        raise RuntimeError(
            "Foram encontrados registros órfãos; enriquecimento recusado."
        )


def executar():
    engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
    try:
        with engine.connect() as tcc:
            _confirmar_tcc(tcc)
            catalogo = _linhas(tcc, SQL_CATALOGO)
            estado_antes = dict(tcc.execute(SQL_INTEGRIDADE).mappings().one())
        if not catalogo:
            raise RuntimeError("O catálogo produtos_rel0024 está vazio.")
        if any(
            estado_antes[campo]
            for campo in (
                "itens_sem_venda",
                "itens_sem_produto",
                "itens_sem_importacao",
            )
        ):
            raise RuntimeError(
                "A base possui registros órfãos; enriquecimento interrompido."
            )

        estados_antes = Counter(_estado_metadados(p) for p in catalogo)
        codigos = sorted({produto["codigo_produto"] for produto in catalogo})
        if len(codigos) != len(catalogo):
            raise RuntimeError(
                "O catálogo possui códigos repetidos entre os produtos."
            )

        with conectar_farmacia_somente_leitura() as farmacia:
            modo = farmacia.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
            if modo != "on":
                raise RuntimeError("sgfpod1 não está em modo somente leitura.")
            redes_codigos = _linhas(
                farmacia, SQL_REDES_CODIGOS, {"codigos": codigos}
            )
            cod_rede, coberturas = _selecionar_rede(
                catalogo, redes_codigos
            )
            tipos = _linhas(
                farmacia,
                SQL_TIPOS,
                {"cod_rede": cod_rede, "codigos": codigos},
            )

        tipos_por_codigo = {
            linha["codigo_produto"]: linha for linha in tipos
        }
        if len(tipos_por_codigo) != len(tipos):
            raise RuntimeError(
                "O sgfpod1 retornou mais de um cadastro para o mesmo código."
            )
        plano = planejar_enriquecimento(catalogo, tipos_por_codigo)

        with engine.begin() as tcc:
            _confirmar_tcc(tcc)
            if plano["atualizacoes"]:
                resultado = tcc.execute(
                    SQL_ATUALIZAR, plano["atualizacoes"]
                )
                if resultado.rowcount != len(plano["atualizacoes"]):
                    raise RuntimeError(
                        "O catálogo mudou durante o enriquecimento; "
                        "transação revertida."
                    )
            estado_depois = dict(
                tcc.execute(SQL_INTEGRIDADE).mappings().one()
            )
            _validar_integridade(estado_antes, estado_depois)

        with engine.connect() as tcc:
            catalogo_depois = _linhas(tcc, SQL_CATALOGO)
        estados_depois = Counter(
            _estado_metadados(p) for p in catalogo_depois
        )
        return {
            "banco_farmacia": "sgfpod1",
            "farmacia_read_only": modo,
            "banco_tcc": BANCO_TCC,
            "cod_rede": cod_rede,
            "coberturas_por_rede": coberturas,
            "origem": ORIGEM,
            "integridade_antes": estado_antes,
            "integridade_depois": estado_depois,
            "estados_metadados_antes": dict(estados_antes),
            "estados_metadados_depois": dict(estados_depois),
            "produtos_encontrados": plano["encontrados"],
            "produtos_nao_encontrados": plano["nao_encontrados"],
            "produtos_enriquecidos": len(plano["atualizacoes"]),
            "divergencias": plano["divergencias"],
            "classificacoes": reconciliar_classificacoes(catalogo_depois),
        }
    finally:
        engine.dispose()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    resultado = executar()
    print("ENRIQUECIMENTO CONTROLADO CONCLUÍDO")
    print(f"sgfpod1 transaction_read_only: {resultado['farmacia_read_only']}")
    print(f"Rede validada: {resultado['cod_rede']}")
    print(f"Origem registrada: {resultado['origem']}")
    print(f"Metadados antes: {resultado['estados_metadados_antes']}")
    print(f"Metadados depois: {resultado['estados_metadados_depois']}")
    print(f"Produtos encontrados: {resultado['produtos_encontrados']}")
    print(
        "Produtos não encontrados: "
        f"{len(resultado['produtos_nao_encontrados'])}"
    )
    print(f"Produtos enriquecidos: {resultado['produtos_enriquecidos']}")
    print(f"Divergências preservadas: {len(resultado['divergencias'])}")
    for nome, quantidade in sorted(resultado["classificacoes"].items()):
        print(f"{nome}: {quantidade}")
    print("Vendas, itens, importações e quantidades permaneceram inalterados.")


if __name__ == "__main__":
    main()
