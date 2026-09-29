"""Serviço de aplicação para executar e filtrar o Modelo C congelado."""

from datetime import date

import pandas as pd

from utils.classificacao_medicamentos import MED_CONTROLADO

from .definir_modelo_validacao import executar_modelo_c


LIMITE_EXIBICAO = 100
CHAVES_RESULTADO = ["filial", "codigo_produto", "data"]


class ErroAnaliseIA(RuntimeError):
    """Erro de entrada ou indisponibilidade tratável pela interface Flask."""

    def __init__(self, mensagem, status_http=400):
        super().__init__(mensagem)
        self.status_http = status_http


def _inteiro_opcional(valor, nome):
    if valor in (None, ""):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError) as erro:
        raise ErroAnaliseIA(f"{nome} deve ser um número inteiro.") from erro
    if numero < 0:
        raise ErroAnaliseIA(f"{nome} não pode ser negativo.")
    return numero


def _data_opcional(valor, nome):
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError as erro:
        raise ErroAnaliseIA(
            f"{nome} deve estar no formato AAAA-MM-DD."
        ) from erro


def normalizar_filtros(
    filial=None,
    codigo_produto=None,
    data_inicial=None,
    data_final=None,
):
    """Valida filtros recebidos por query string ou chamada Python."""

    filtros = {
        "filial": _inteiro_opcional(filial, "Filial"),
        "codigo_produto": _inteiro_opcional(
            codigo_produto, "Código do produto"
        ),
        "data_inicial": _data_opcional(data_inicial, "Data inicial"),
        "data_final": _data_opcional(data_final, "Data final"),
    }
    if (
        filtros["data_inicial"]
        and filtros["data_final"]
        and filtros["data_inicial"] > filtros["data_final"]
    ):
        raise ErroAnaliseIA(
            "A data inicial não pode ser posterior à data final."
        )
    return filtros


def _filtrar_identificadores(dados, filtros):
    resultado = dados
    if filtros["filial"] is not None:
        resultado = resultado.loc[
            resultado["filial"] == filtros["filial"]
        ]
    if filtros["codigo_produto"] is not None:
        resultado = resultado.loc[
            resultado["codigo_produto"] == filtros["codigo_produto"]
        ]
    return resultado


def _filtrar_periodo(dados, filtros):
    resultado = dados
    if filtros["data_inicial"] is not None:
        resultado = resultado.loc[
            resultado["data"].dt.date >= filtros["data_inicial"]
        ]
    if filtros["data_final"] is not None:
        resultado = resultado.loc[
            resultado["data"].dt.date <= filtros["data_final"]
        ]
    return resultado


def _serializar_resultados(resultados):
    colunas = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "cod_tipo",
        "nom_tipo",
        "classificacao_operacional",
        "data",
        "quantidade_dia",
        "media_positiva_historica",
        "score_samples",
        "decision_function",
        "classificacao_isolation_forest",
        "anomalia",
        "aumento_atipico",
    ]
    ordenados = resultados.loc[resultados["aumento_atipico"]].sort_values(
        ["decision_function", "data"],
        ascending=[True, False],
        kind="stable",
    ).head(LIMITE_EXIBICAO)
    registros = []
    for linha in ordenados[colunas].itertuples(index=False):
        registros.append(
            {
                "filial": int(linha.filial),
                "codigo_produto": int(linha.codigo_produto),
                "descricao": str(linha.descricao_original),
                "cod_tipo": (
                    None if pd.isna(linha.cod_tipo) else int(linha.cod_tipo)
                ),
                "nom_tipo": (
                    None if pd.isna(linha.nom_tipo) else str(linha.nom_tipo)
                ),
                "classificacao_operacional": str(
                    linha.classificacao_operacional
                ),
                "data": linha.data.date().isoformat(),
                "quantidade_diaria": float(linha.quantidade_dia),
                "referencia_historica": float(
                    linha.media_positiva_historica
                ),
                "diferenca_historica": float(
                    linha.quantidade_dia - linha.media_positiva_historica
                ),
                "razao_historica": float(
                    linha.quantidade_dia / linha.media_positiva_historica
                ),
                "score_samples": float(linha.score_samples),
                "decision_function": float(linha.decision_function),
                "classificacao": int(
                    linha.classificacao_isolation_forest
                ),
                "anomalia": bool(linha.anomalia),
                "aumento_atipico": bool(linha.aumento_atipico),
            }
        )
    return registros


def _opcoes_produtos(catalogo):
    produtos = catalogo[
        ["filial", "codigo_produto", "descricao_original"]
    ].drop_duplicates().sort_values(
        ["descricao_original", "codigo_produto", "filial"], kind="stable"
    )
    return [
        {
            "filial": int(linha.filial),
            "codigo": int(linha.codigo_produto),
            "descricao": str(linha.descricao_original),
        }
        for linha in produtos.itertuples(index=False)
    ]


def _valor_ou_nulo(valor):
    return None if pd.isna(valor) else float(valor)


def _montar_grafico_temporal(pipeline, filtros):
    if filtros["codigo_produto"] is None:
        return {
            "disponivel": False,
            "mensagem": (
                "Selecione um produto para visualizar sua série temporal diária."
            ),
        }

    serie = _filtrar_identificadores(pipeline["serie_contexto"], filtros)
    series_distintas = serie[["filial", "codigo_produto"]].drop_duplicates()
    if len(series_distintas) != 1:
        return {
            "disponivel": False,
            "mensagem": (
                "Selecione também uma filial para visualizar uma única série."
            ),
        }

    serie = _filtrar_periodo(serie, filtros).copy()
    classificacoes = pipeline["resultado"][
        [*CHAVES_RESULTADO, "anomalia", "aumento_atipico"]
    ]
    serie = serie.merge(
        classificacoes,
        on=CHAVES_RESULTADO,
        how="left",
        validate="one_to_one",
    )
    serie[["anomalia", "aumento_atipico"]] = serie[
        ["anomalia", "aumento_atipico"]
    ].fillna(False).astype(bool)
    serie = serie.sort_values("data", kind="stable")
    descricao = str(serie["descricao_original"].iloc[0])
    filial = int(serie["filial"].iloc[0])
    codigo = int(serie["codigo_produto"].iloc[0])
    quantidades = [float(valor) for valor in serie["quantidade_dia"]]
    referencias = [
        _valor_ou_nulo(valor)
        for valor in serie["media_positiva_historica"]
    ]
    destaques = [
        quantidade if aumento else None
        for quantidade, aumento in zip(
            quantidades, serie["aumento_atipico"], strict=True
        )
    ]
    return {
        "disponivel": True,
        "mensagem": None,
        "titulo": f"{codigo} — {descricao} · Filial {filial}",
        "labels": [data.date().isoformat() for data in serie["data"]],
        "quantidades": quantidades,
        "referencias": referencias,
        "aumentos_atipicos": destaques,
    }


def executar_analise_ia(
    filial=None,
    codigo_produto=None,
    data_inicial=None,
    data_final=None,
):
    """Executa C no histórico completo e filtra somente a apresentação.

    Mesmo quando o usuário informa um período curto, todo o Rel_0024 é carregado
    para construir calendário e referências anteriores. O período é aplicado
    apenas após o treinamento e a pontuação do Modelo C.
    """

    filtros = normalizar_filtros(
        filial=filial,
        codigo_produto=codigo_produto,
        data_inicial=data_inicial,
        data_final=data_final,
    )
    pipeline = executar_modelo_c(apenas_elegiveis_automedicacao=True)
    diagnostico = pipeline["diagnostico"]
    catalogo_completo = diagnostico["catalogo_classificado"].copy()
    catalogo = catalogo_completo.loc[
        catalogo_completo["elegivel_analise_automedicacao"]
    ].copy()

    if filtros["filial"] is not None and not (
        catalogo_completo["filial"] == filtros["filial"]
    ).any():
        raise ErroAnaliseIA("Filial não encontrada nos dados Rel_0024.", 404)

    catalogo_completo_filtrado = _filtrar_identificadores(
        catalogo_completo, filtros
    )
    if filtros["codigo_produto"] is not None:
        if catalogo_completo_filtrado.empty:
            raise ErroAnaliseIA(
                "Produto não encontrado nos dados Rel_0024 para o filtro informado.",
                404,
            )
        if not catalogo_completo_filtrado[
            "elegivel_analise_automedicacao"
        ].all():
            classificacoes = set(
                catalogo_completo_filtrado["classificacao_operacional"]
            )
            if classificacoes == {MED_CONTROLADO}:
                mensagem = (
                    "Produto classificado como MED_CONTROLADO e excluído do "
                    "universo operacional desta análise."
                )
            else:
                mensagem = (
                    "Produto fora dos tipos MED elegíveis e excluído do "
                    "universo operacional desta análise."
                )
            raise ErroAnaliseIA(mensagem, 422)

    catalogo_filtrado = _filtrar_identificadores(catalogo, filtros)

    resultado_identidade = _filtrar_identificadores(
        pipeline["resultado"], filtros
    )
    produtos_existentes = catalogo_filtrado[
        ["filial", "codigo_produto"]
    ].drop_duplicates()
    produtos_modelaveis = resultado_identidade[
        ["filial", "codigo_produto"]
    ].drop_duplicates()
    historico_insuficiente = len(produtos_existentes) - len(
        produtos_modelaveis
    )

    if filtros["codigo_produto"] is not None and resultado_identidade.empty:
        raise ErroAnaliseIA(
            "Este produto ainda não possui histórico suficiente para análise pelo modelo.",
            422,
        )

    resultados = _filtrar_periodo(resultado_identidade, filtros).copy()
    if resultados.empty:
        raise ErroAnaliseIA(
            "Nenhuma observação modelável foi encontrada no período informado.",
            422,
        )

    periodo_origem = diagnostico["periodo"]
    periodo_inicial = filtros["data_inicial"] or periodo_origem["data_inicial"]
    periodo_final = filtros["data_final"] or periodo_origem["data_final"]
    metricas = {
        "periodo_analisado": {
            "data_inicial": periodo_inicial.isoformat(),
            "data_final": periodo_final.isoformat(),
        },
        "periodo_origem": {
            "data_inicial": periodo_origem["data_inicial"].isoformat(),
            "data_final": periodo_origem["data_final"].isoformat(),
        },
        "produtos_existentes": len(produtos_existentes),
        "produtos_catalogo": diagnostico["universo_operacional"][
            "produtos_catalogo"
        ],
        "produtos_med": diagnostico["universo_operacional"]["produtos_med"],
        "produtos_elegiveis": diagnostico["universo_operacional"][
            "produtos_elegiveis"
        ],
        "produtos_excluidos": diagnostico["universo_operacional"][
            "produtos_excluidos"
        ],
        "produtos_modelaveis": len(produtos_modelaveis),
        "produtos_historico_insuficiente": historico_insuficiente,
        "observacoes_avaliadas": len(resultados),
        "outliers": int(resultados["anomalia"].sum()),
        "aumentos_atipicos": int(resultados["aumento_atipico"].sum()),
    }
    filtros_exibicao = {
        chave: valor.isoformat() if isinstance(valor, date) else valor
        for chave, valor in filtros.items()
    }
    total_aumentos = int(resultados["aumento_atipico"].sum())
    serie_aumentos = resultados.loc[resultados["aumento_atipico"]].copy()
    serie_aumentos["mes"] = serie_aumentos["data"].dt.to_period("M")
    totais_aumentos_mensais = serie_aumentos.groupby("mes").size()
    meses = pd.period_range(
        periodo_origem["data_inicial"].strftime("%Y-%m"),
        periodo_origem["data_final"].strftime("%Y-%m"),
        freq="M",
    )
    return {
        "metricas": metricas,
        "aumentos_por_mes": {
            "labels": [mes.strftime("%Y-%m") for mes in meses],
            "valores": [int(totais_aumentos_mensais.get(mes, 0)) for mes in meses],
        },
        "filtros": filtros_exibicao,
        "filiais_disponiveis": sorted(
            int(valor) for valor in catalogo["filial"].unique()
        ),
        "produtos_disponiveis": _opcoes_produtos(
            catalogo.loc[
                catalogo["filial"] == filtros["filial"]
            ]
            if filtros["filial"] is not None
            else catalogo
        ),
        "grafico_temporal": _montar_grafico_temporal(pipeline, filtros),
        "resultados": _serializar_resultados(resultados),
        "total_resultados_filtrados": total_aumentos,
        "limite_exibicao": LIMITE_EXIBICAO,
        "resultados_limitados": total_aumentos > LIMITE_EXIBICAO,
        "metodologia": {
            "nivel": "filial + produto + data",
            "universo": (
                "tipos internos MED, exceto MED_CONTROLADO; "
                "classificação operacional, não regulatória"
            ),
            "historico_carregado_antes_do_filtro_periodo": True,
            "resultados_persistidos": False,
        },
    }
