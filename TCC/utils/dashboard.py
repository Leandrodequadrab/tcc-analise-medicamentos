"""Consultas Rel_0024 e adaptadores para os dados exibidos no dashboard."""

from datetime import date
import pandas as pd
from sqlalchemy import func, literal_column, select

from database import db
from models.rel0024 import (
    ImportacaoRel0024,
    ItemRel0024,
    ProdutoRel0024,
    VendaRel0024,
)
from utils.ml.servico_modelo_c import executar_analise_ia


CLASSIFICACOES_DASHBOARD = (
    "MED_OUTRO",
    "MED_OTC_INTERNO",
    "MED_ANTIMICROBIANO",
    "MED_CONTROLADO",
    "NAO_MED",
    "SEM_CLASSIFICACAO",
)


def _numero_json(valor):
    """Converte Decimal para número JSON sem descartar frações existentes."""

    numero = float(valor or 0)
    return int(numero) if numero.is_integer() else numero


def criar_dashboard_vazio(mensagem=None):
    return {
        "indicadores": {
            "periodo": None,
            "produtos": 0,
            "elegiveis": 0,
            "vendas": 0,
            "itens": 0,
            "unidades": 0,
        },
        "classificacoes": {
            "labels": list(CLASSIFICACOES_DASHBOARD),
            "valores": [0] * len(CLASSIFICACOES_DASHBOARD),
            "elegiveis": list(CLASSIFICACOES_DASHBOARD[:3]),
        },
        "vendas_mensais": {"labels": [], "valores": []},
        "produtos_mais_vendidos": [],
        "mensagem": mensagem,
        "sem_dados": True,
    }


def obter_dados_dashboard():
    """Consulta exclusivamente as tabelas reais do Rel_0024."""

    contagens = db.session.execute(
        select(
            select(func.count(ProdutoRel0024.id)).scalar_subquery().label("produtos"),
            select(func.count(ProdutoRel0024.id))
            .where(ProdutoRel0024.elegivel_analise_automedicacao.is_(True))
            .scalar_subquery()
            .label("elegiveis"),
            select(func.count(VendaRel0024.id)).scalar_subquery().label("vendas"),
            select(func.count(ItemRel0024.id)).scalar_subquery().label("itens"),
            select(func.coalesce(func.sum(ItemRel0024.quantidade), 0))
            .scalar_subquery()
            .label("unidades"),
            select(func.min(ImportacaoRel0024.periodo_inicial))
            .scalar_subquery()
            .label("periodo_inicial"),
            select(func.max(ImportacaoRel0024.periodo_final))
            .scalar_subquery()
            .label("periodo_final"),
        )
    ).one()

    periodo = None
    if contagens.periodo_inicial and contagens.periodo_final:
        periodo = {
            "inicial": contagens.periodo_inicial.isoformat(),
            "final": contagens.periodo_final.isoformat(),
        }

    classificacoes_query = (
        select(
            ProdutoRel0024.classificacao_operacional,
            func.count(ProdutoRel0024.id).label("total"),
        )
        .group_by(ProdutoRel0024.classificacao_operacional)
    )
    classificacoes_existentes = {
        (linha.classificacao_operacional or "SEM_CLASSIFICACAO"): int(linha.total)
        for linha in db.session.execute(classificacoes_query)
    }
    classificacoes = {
        "labels": list(CLASSIFICACOES_DASHBOARD),
        "valores": [
            classificacoes_existentes.get(nome, 0)
            for nome in CLASSIFICACOES_DASHBOARD
        ],
        "elegiveis": list(CLASSIFICACOES_DASHBOARD[:3]),
    }

    vendas_mensais = {"labels": [], "valores": []}
    produtos_mais_vendidos = []
    if periodo:
        mes_sql = func.to_char(
            VendaRel0024.data, literal_column("'YYYY-MM'")
        )
        vendas_por_mes_query = (
            select(
                mes_sql.label("mes"),
                func.coalesce(func.sum(ItemRel0024.quantidade), 0).label("unidades"),
            )
            .join(ItemRel0024, ItemRel0024.venda_id == VendaRel0024.id)
            .where(
                VendaRel0024.data >= date.fromisoformat(periodo["inicial"]),
                VendaRel0024.data <= date.fromisoformat(periodo["final"]),
            )
            .group_by(mes_sql)
            .order_by(mes_sql)
        )
        totais_mensais = {
            linha.mes: _numero_json(linha.unidades)
            for linha in db.session.execute(vendas_por_mes_query)
        }
        inicio = date.fromisoformat(periodo["inicial"]).replace(day=1)
        fim = date.fromisoformat(periodo["final"]).replace(day=1)
        meses = pd.period_range(inicio, fim, freq="M")
        vendas_mensais = {
            "labels": [mes.strftime("%Y-%m") for mes in meses],
            "valores": [
                totais_mensais.get(mes.strftime("%Y-%m"), 0) for mes in meses
            ],
        }

        produtos_query = (
            select(
                ProdutoRel0024.codigo_produto,
                ProdutoRel0024.descricao_original,
                ProdutoRel0024.classificacao_operacional,
                func.coalesce(func.sum(ItemRel0024.quantidade), 0).label("quantidade"),
            )
            .join(ItemRel0024, ItemRel0024.produto_id == ProdutoRel0024.id)
            .where(ProdutoRel0024.elegivel_analise_automedicacao.is_(True))
            .group_by(
                ProdutoRel0024.id,
                ProdutoRel0024.codigo_produto,
                ProdutoRel0024.descricao_original,
                ProdutoRel0024.classificacao_operacional,
            )
            .order_by(func.sum(ItemRel0024.quantidade).desc(), ProdutoRel0024.codigo_produto)
            .limit(10)
        )
        produtos_mais_vendidos = [
            {
                "codigo": linha.codigo_produto,
                "descricao": linha.descricao_original,
                "classificacao": linha.classificacao_operacional,
                "quantidade": _numero_json(linha.quantidade),
            }
            for linha in db.session.execute(produtos_query)
        ]

    sem_dados = contagens.vendas == 0 or contagens.itens == 0
    return {
        "indicadores": {
            "periodo": periodo,
            "produtos": int(contagens.produtos),
            "elegiveis": int(contagens.elegiveis),
            "vendas": int(contagens.vendas),
            "itens": int(contagens.itens),
            "unidades": _numero_json(contagens.unidades),
        },
        "classificacoes": classificacoes,
        "vendas_mensais": vendas_mensais,
        "produtos_mais_vendidos": produtos_mais_vendidos,
        "mensagem": (
            "Ainda não há dados Rel_0024. Importe um relatório para preencher o dashboard."
            if sem_dados
            else None
        ),
        "sem_dados": sem_dados,
    }


def obter_resumo_ia_dashboard():
    """Adapta a saída do serviço congelado sem executar ML neste módulo."""

    analise = executar_analise_ia()
    metricas = analise["metricas"]
    observacoes = metricas["observacoes_avaliadas"]
    aumentos = analise["aumentos_por_mes"]
    destaques = sorted(
        analise["resultados"], key=lambda item: item["score_samples"]
    )[:10]
    return {
        "disponivel": True,
        "metricas": {
            "produtos_modelaveis": metricas["produtos_modelaveis"],
            "observacoes_avaliadas": observacoes,
            "outliers": metricas["outliers"],
            "outliers_percentual": (
                metricas["outliers"] * 100 / observacoes if observacoes else 0
            ),
            "aumentos_atipicos": metricas["aumentos_atipicos"],
            "aumentos_percentual": (
                metricas["aumentos_atipicos"] * 100 / observacoes
                if observacoes
                else 0
            ),
        },
        "aumentos_mensais": aumentos,
        "destaques": destaques,
    }
