"""Análises exploratórias calculadas com dados do PostgreSQL e Pandas."""

import pandas as pd
from sqlalchemy import select

from database import Venda, db


COLUNAS_ANALISE = ("data", "medicamento", "categoria", "quantidade", "cidade")
NOMES_MESES = (
    "Jan",
    "Fev",
    "Mar",
    "Abr",
    "Mai",
    "Jun",
    "Jul",
    "Ago",
    "Set",
    "Out",
    "Nov",
    "Dez",
)


def carregar_vendas_do_postgresql():
    """Consulta as vendas com SQLAlchemy e cria um DataFrame do Pandas."""

    consulta = select(
        Venda.data,
        Venda.medicamento,
        Venda.categoria,
        Venda.quantidade,
        Venda.cidade,
    ).order_by(Venda.data, Venda.id)
    registros = db.session.execute(consulta).mappings().all()
    dados = pd.DataFrame([dict(registro) for registro in registros], columns=COLUNAS_ANALISE)

    if dados.empty:
        return dados

    dados["data"] = pd.to_datetime(dados["data"], errors="coerce")
    dados["quantidade"] = pd.to_numeric(dados["quantidade"], errors="coerce")
    return dados.dropna(subset=list(COLUNAS_ANALISE)).copy()


def calcular_maior_volume(dados, coluna):
    """Retorna o item com a maior soma de unidades vendidas."""

    if dados.empty:
        return {"nome": "Sem dados", "unidades": 0}

    totais = (
        dados.groupby(coluna, as_index=False)["quantidade"]
        .sum()
        .sort_values(["quantidade", coluna], ascending=[False, True])
    )
    primeiro = totais.iloc[0]
    return {"nome": str(primeiro[coluna]), "unidades": int(primeiro["quantidade"])}


def calcular_media_diaria(dados):
    """Calcula a média do volume total de unidades entre os dias observados."""

    if dados.empty:
        return 0.0

    totais_diarios = dados.groupby("data")["quantidade"].sum()
    return round(float(totais_diarios.mean()), 2)


def calcular_totais_mensais(dados):
    """Soma unidades por mês e inclui meses sem vendas com total zero."""

    if dados.empty:
        return pd.Series(dtype="int64")

    totais = (
        dados.set_index("data")["quantidade"]
        .sort_index()
        .resample("MS")
        .sum()
    )
    return totais.astype("int64")


def _formatar_mes(data):
    return f"{NOMES_MESES[data.month - 1]}/{data.year}"


def calcular_crescimento_mensal(totais_mensais):
    """Calcula ((atual - anterior) / anterior) * 100 para meses consecutivos."""

    resultados = []
    anterior = None

    for data, total in totais_mensais.items():
        percentual = None
        if anterior is None:
            exibicao = "Sem período anterior"
        elif anterior == 0:
            exibicao = "Não calculável: período anterior igual a zero"
        else:
            percentual = round(((int(total) - anterior) / anterior) * 100, 2)
            exibicao = f"{percentual:+.2f}%"

        resultados.append(
            {
                "periodo": _formatar_mes(data),
                "total_unidades": int(total),
                "percentual": percentual,
                "exibicao": exibicao,
            }
        )
        anterior = int(total)

    return resultados


def calcular_variacao_medicamentos(dados, limite=10):
    """Calcula o desvio-padrão populacional dos totais diários por medicamento."""

    if dados.empty:
        return {"labels": [], "valores": []}

    totais_diarios = (
        dados.groupby(["data", "medicamento"], as_index=False)["quantidade"].sum()
    )
    matriz = totais_diarios.pivot(
        index="data", columns="medicamento", values="quantidade"
    ).fillna(0)
    variacoes = (
        matriz.std(axis=0, ddof=0)
        .rename("desvio_padrao")
        .reset_index()
        .sort_values(["desvio_padrao", "medicamento"], ascending=[False, True])
        .head(limite)
    )
    return {
        "labels": variacoes["medicamento"].astype(str).tolist(),
        "valores": [round(float(valor), 2) for valor in variacoes["desvio_padrao"]],
    }


def calcular_registros_por_cidade(dados):
    """Conta linhas de venda por cidade, sem somar a quantidade de unidades."""

    if dados.empty:
        return {"labels": [], "valores": []}

    contagens = (
        dados.groupby("cidade")
        .size()
        .rename("registros")
        .reset_index()
        .sort_values(["registros", "cidade"], ascending=[False, True])
    )
    return {
        "labels": contagens["cidade"].astype(str).tolist(),
        "valores": [int(valor) for valor in contagens["registros"]],
    }


def obter_analises():
    """Executa e organiza todas as análises usadas pela página."""

    dados = carregar_vendas_do_postgresql()
    totais_mensais = calcular_totais_mensais(dados)
    crescimento = calcular_crescimento_mensal(totais_mensais)

    return {
        "resumo": {
            "total_registros": len(dados),
            "total_unidades": int(dados["quantidade"].sum()) if not dados.empty else 0,
            "medicamento_maior_volume": calcular_maior_volume(dados, "medicamento"),
            "categoria_maior_volume": calcular_maior_volume(dados, "categoria"),
            "media_diaria": calcular_media_diaria(dados),
        },
        "graficos": {
            "totais_mensais": {
                "labels": [_formatar_mes(data) for data in totais_mensais.index],
                "valores": [int(valor) for valor in totais_mensais.values],
            },
            "crescimento_mensal": {
                "labels": [item["periodo"] for item in crescimento],
                "valores": [item["percentual"] for item in crescimento],
            },
            "variacao_medicamentos": calcular_variacao_medicamentos(dados),
            "registros_cidades": calcular_registros_por_cidade(dados),
        },
        "crescimento_mensal": crescimento,
    }
