"""Execução do Modelo C congelado sobre séries por chave analítica híbrida."""

import numpy as np
import pandas as pd

from utils.agregacao_chave_analitica import agregar_vendas_por_chave
from utils.chave_analitica import (
    carregar_cadastro_dcb,
    criar_chaves_analiticas,
)
from utils.ml.definir_modelo_validacao import (
    PARAMETROS_MODELO,
    executar_modelo_c_em_diagnostico,
)
from utils.ml.diagnosticar_series import carregar_agregacao_diaria


CHAVES_SERIE = ("tipo_chave", "chave_analitica")
CHAVES_OBSERVACAO = (*CHAVES_SERIE, "data")


def _ordenar_unicos(valores):
    return tuple(sorted(pd.unique(pd.Series(valores).dropna()).tolist(), key=str))


def construir_diagnostico_chave(serie_vendas, produtos, cadastro_dcb=None):
    """Cria em memória a grade diária elegível e o sumário de séries híbridas."""

    chaves_produto = criar_chaves_analiticas(produtos, cadastro_dcb)
    agregada = agregar_vendas_por_chave(serie_vendas, produtos, cadastro_dcb)
    if chaves_produto["chave_analitica"].isna().any():
        raise ValueError("Produto elegível sem chave analítica.")

    metadados = (
        chaves_produto.groupby(list(CHAVES_SERIE), as_index=False, dropna=False)
        .agg(
            nom_dcb=("nom_dcb", "first"),
            codigos_produto=("codigo_produto", _ordenar_unicos),
            descricoes_associadas=("descricao", _ordenar_unicos),
            filiais=("filial", _ordenar_unicos),
        )
    )
    resumo = (
        agregada.groupby(list(CHAVES_SERIE), as_index=False, dropna=False)
        .agg(
            primeira_venda=("data", "min"),
            ultima_venda=("data", "max"),
            dias_com_venda=("data", "nunique"),
            quantidade_total=("quantidade_dia", "sum"),
        )
    )
    resumo["dias_no_intervalo_individual"] = (
        resumo["ultima_venda"] - resumo["primeira_venda"]
    ).dt.days + 1
    resumo["dias_sem_venda"] = (
        resumo["dias_no_intervalo_individual"] - resumo["dias_com_venda"]
    )
    resumo["elegivel_analise_automedicacao"] = True

    calendarios = [
        pd.DataFrame(
            {
                CHAVES_SERIE[0]: chave.tipo_chave,
                CHAVES_SERIE[1]: chave.chave_analitica,
                "data": pd.date_range(chave.primeira_venda, chave.ultima_venda),
            }
        )
        for chave in resumo.itertuples(index=False)
    ]
    grade = pd.concat(calendarios, ignore_index=True)
    grade = grade.merge(
        agregada[[*CHAVES_OBSERVACAO, "quantidade_dia"]],
        on=list(CHAVES_OBSERVACAO),
        how="left",
        validate="one_to_one",
    )
    grade["teve_registro_venda"] = grade["quantidade_dia"].notna()
    grade["quantidade_total"] = grade["quantidade_dia"].fillna(0.0)
    grade = grade.drop(columns="quantidade_dia")
    grade = grade.merge(
        metadados,
        on=list(CHAVES_SERIE),
        how="left",
        validate="many_to_one",
    )
    grade["tipo_chave"] = grade["tipo_chave"].astype(str)
    grade["descricao_original"] = [
        descricoes[0]
        if tipo == "PRODUTO" and len(descricoes) == 1
        else " | ".join(descricoes)
        for tipo, descricoes in zip(
            grade["tipo_chave"], grade["descricoes_associadas"], strict=True
        )
    ]
    grade["descricao"] = grade["descricao_original"]
    grade["codigo_produto"] = [
        codigos[0]
        if tipo == "PRODUTO" and len(codigos) == 1
        else None
        for tipo, codigos in zip(
            grade["tipo_chave"], grade["codigos_produto"], strict=True
        )
    ]
    grade["filial"] = [
        filiais[0] if len(filiais) == 1 else None
        for filiais in grade["filiais"]
    ]

    total_original = float(
        serie_vendas.merge(
            chaves_produto[["filial", "codigo_produto"]].drop_duplicates(),
            on=["filial", "codigo_produto"],
            how="inner",
            validate="many_to_one",
        )["quantidade_total"].sum()
    )
    total_agregado = float(agregada["quantidade_dia"].sum())
    if not np.isclose(total_original, total_agregado, rtol=0, atol=0):
        raise ValueError(
            "A construção das séries alterou unidades: "
            f"antes={total_original}, depois={total_agregado}."
        )

    diagnostico = {
        "serie_b": grade,
        "resumo_produtos": resumo,
        "catalogo_chaves": chaves_produto,
        "agregacao_chave": agregada,
        "quantidade_elegivel_antes": total_original,
        "quantidade_agregada": total_agregado,
        "chaves_totais": int(
            chaves_produto[list(CHAVES_SERIE)].drop_duplicates().shape[0]
        ),
    }
    return diagnostico


def executar_modelo_c_hibrido_em_diagnostico(
    diagnostico, *, permitir_historico_insuficiente=False
):
    """Aplica sem alteração o preparo, as seis features e o Isolation Forest C."""

    return executar_modelo_c_em_diagnostico(
        diagnostico,
        permitir_historico_insuficiente=permitir_historico_insuficiente,
        chaves_serie=CHAVES_SERIE,
    )


def executar_modelo_c_hibrido():
    """Lê tcc_medicamentos em modo somente leitura e executa C por chave DCB."""

    dados = carregar_agregacao_diaria()
    cadastro_dcb = carregar_cadastro_dcb()
    diagnostico = construir_diagnostico_chave(
        dados["serie_vendas"], dados["catalogo_classificado"], cadastro_dcb
    )
    diagnostico["banco"] = dados["banco"]
    diagnostico["somente_leitura"] = dados["somente_leitura"]
    return executar_modelo_c_hibrido_em_diagnostico(diagnostico)


def resumir_execucao_hibrida(pipeline):
    """Retorna métricas gerais e por tipo de chave, sem persistir resultados."""

    resultado = pipeline["resultado"]
    diagnostico = pipeline["diagnostico"]
    catalogo = diagnostico["catalogo_chaves"]
    chaves_catalogo = catalogo[list(CHAVES_SERIE)].drop_duplicates()
    chaves_modelaveis = resultado[list(CHAVES_SERIE)].drop_duplicates()

    def metricas_da_parte(linhas, total_series):
        series_modelaveis = (
            linhas[list(CHAVES_SERIE)].drop_duplicates().shape[0]
            if not linhas.empty
            else 0
        )
        observacoes = len(linhas)
        return {
            "series": int(total_series),
            "series_modelaveis": int(series_modelaveis),
            "series_historico_insuficiente": int(total_series - series_modelaveis),
            "observacoes_modelaveis": int(observacoes),
            "outliers": int(linhas["anomalia"].sum()) if observacoes else 0,
            "aumentos_atipicos": (
                int(linhas["aumento_atipico"].sum()) if observacoes else 0
            ),
            "chaves_com_aumento_atipico": (
                int(
                    linhas.loc[linhas["aumento_atipico"], list(CHAVES_SERIE)]
                    .drop_duplicates()
                    .shape[0]
                )
                if observacoes
                else 0
            ),
        }

    por_tipo = {}
    for tipo in ("DCB", "PRODUTO"):
        total = int((chaves_catalogo["tipo_chave"] == tipo).sum())
        linhas = resultado.loc[resultado["tipo_chave"] == tipo]
        por_tipo[tipo] = metricas_da_parte(linhas, total)

    geral = metricas_da_parte(resultado, len(chaves_catalogo))
    geral.update(
        {
            "chaves_totais": int(diagnostico["chaves_totais"]),
            "percentual_outliers": (
                float(resultado["anomalia"].mean() * 100)
                if len(resultado)
                else 0.0
            ),
            "percentual_aumentos_atipicos": (
                float(resultado["aumento_atipico"].mean() * 100)
                if len(resultado)
                else 0.0
            ),
            "quantidade_elegivel_antes": diagnostico[
                "quantidade_elegivel_antes"
            ],
            "quantidade_agregada": diagnostico["quantidade_agregada"],
        }
    )
    return {"geral": geral, "por_tipo": por_tipo}


def comparar_reprodutibilidade(primeiro, segundo, tolerancia=1e-12):
    """Compara rótulos, identificadores e scores entre duas execuções."""

    campos_chave = [*CHAVES_OBSERVACAO]
    colunas = [
        *campos_chave,
        "score_samples",
        "decision_function",
        "classificacao_isolation_forest",
        "anomalia",
        "aumento_atipico",
    ]
    a = primeiro["resultado"][colunas].sort_values(
        campos_chave, key=lambda col: col.astype(str), kind="stable"
    ).reset_index(drop=True)
    b = segundo["resultado"][colunas].sort_values(
        campos_chave, key=lambda col: col.astype(str), kind="stable"
    ).reset_index(drop=True)
    if len(a) != len(b):
        return {"identicos": False, "scores_dentro_tolerancia": False}
    identificadores_iguais = a[campos_chave].equals(b[campos_chave])
    classificacoes_iguais = a[
        ["classificacao_isolation_forest", "anomalia", "aumento_atipico"]
    ].equals(
        b[["classificacao_isolation_forest", "anomalia", "aumento_atipico"]]
    )
    scores_iguais = np.allclose(
        a[["score_samples", "decision_function"]].to_numpy(dtype=float),
        b[["score_samples", "decision_function"]].to_numpy(dtype=float),
        rtol=tolerancia,
        atol=tolerancia,
        equal_nan=False,
    )
    return {
        "observacoes_iguais": len(a),
        "identificadores_e_datas_iguais": bool(identificadores_iguais),
        "classificacoes_iguais": bool(classificacoes_iguais),
        "scores_dentro_tolerancia": bool(scores_iguais),
        "identicos": bool(
            identificadores_iguais and classificacoes_iguais and scores_iguais
        ),
    }


def resumo_losartana(pipeline):
    """Resume série DCB de Losartana e seleciona uma ocorrência se existir."""

    diag = pipeline["diagnostico"]
    grade = diag["serie_b"]
    contexto = pipeline["serie_contexto"]
    resultado = pipeline["resultado"]
    chave = grade.loc[
        (grade["tipo_chave"] == "DCB")
        & (grade["chave_analitica"] == "LOSARTANA POTÁSSICA"),
        list(CHAVES_SERIE),
    ].drop_duplicates()
    if chave.empty:
        return None
    mascara_serie = (grade["tipo_chave"] == "DCB") & (
        grade["chave_analitica"] == "LOSARTANA POTÁSSICA"
    )
    mascara_contexto = (contexto["tipo_chave"] == "DCB") & (
        contexto["chave_analitica"] == "LOSARTANA POTÁSSICA"
    )
    mascara_resultado = (resultado["tipo_chave"] == "DCB") & (
        resultado["chave_analitica"] == "LOSARTANA POTÁSSICA"
    )
    serie = grade.loc[mascara_serie]
    contexto_serie = contexto.loc[mascara_contexto]
    observacoes = resultado.loc[mascara_resultado]
    metadados = diag["catalogo_chaves"].loc[
        (diag["catalogo_chaves"]["tipo_chave"] == "DCB")
        & (diag["catalogo_chaves"]["chave_analitica"] == "LOSARTANA POTÁSSICA")
    ]
    ocorrencias = observacoes.loc[
        observacoes["aumento_atipico"],
        [
            "data",
            "quantidade_dia",
            "media_positiva_historica",
            "score_samples",
            "decision_function",
            "anomalia",
            "aumento_atipico",
        ],
    ].sort_values("decision_function")
    if ocorrencias.empty:
        ocorrencias = observacoes.loc[
            observacoes["anomalia"],
            [
                "data",
                "quantidade_dia",
                "media_positiva_historica",
                "score_samples",
                "decision_function",
                "anomalia",
                "aumento_atipico",
            ],
        ].sort_values("decision_function")
    return {
        "codigos_agrupados": int(metadados["codigo_produto"].nunique()),
        "codigos": _ordenar_unicos(metadados["codigo_produto"]),
        "data_inicial": serie["data"].min(),
        "data_final": serie["data"].max(),
        "dias_positivos": int((serie["quantidade_total"] > 0).sum()),
        "vendas_positivas_anteriores_max": int(
            contexto_serie["vendas_positivas_anteriores"].max()
        ),
        "historico_suficiente": not observacoes.empty,
        "observacoes_modelaveis": len(observacoes),
        "outliers": int(observacoes["anomalia"].sum()) if len(observacoes) else 0,
        "aumentos_atipicos": (
            int(observacoes["aumento_atipico"].sum()) if len(observacoes) else 0
        ),
        "ocorrencia": None if ocorrencias.empty else ocorrencias.iloc[0].to_dict(),
    }


def parametros_efetivos(pipeline):
    """Parâmetros efetivamente retidos pelo estimador treinado."""

    if pipeline["modelo"] is None:
        return dict(PARAMETROS_MODELO)
    return pipeline["modelo"].get_params(deep=False)
