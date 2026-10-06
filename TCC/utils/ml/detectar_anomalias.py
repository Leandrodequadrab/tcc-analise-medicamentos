"""Primeira versão do Isolation Forest para aumentos atípicos de vendas.

O módulo reutiliza a grade temporal da Etapa 10A, calcula todas as referências
com dados estritamente anteriores ao dia avaliado e não persiste resultados.
"""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sklearn import __version__ as SKLEARN_VERSION
from sklearn.ensemble import IsolationForest
from sqlalchemy.exc import SQLAlchemyError

from .diagnosticar_series import ErroDiagnostico, diagnosticar_series


RANDOM_STATE = 42
MINIMO_DIAS_DECORRIDOS = 28
JANELA_CURTA = 7
JANELA_LONGA = 28

PARAMETROS_MODELO = {
    "n_estimators": 200,
    "max_samples": "auto",
    "contamination": "auto",
    "max_features": 1.0,
    "bootstrap": False,
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
}

FEATURES_MODELO = (
    "log1p_quantidade_dia",
    "media_anterior_7d",
    "media_anterior_28d",
    "desvio_anterior_28d",
    "razao_quantidade_media_28d",
    "diferenca_quantidade_media_28d",
    "dias_desde_ultima_venda",
    "taxa_historica_dias_com_venda",
)


class ErroModelo(RuntimeError):
    """Indica que o pipeline não pode prosseguir de maneira confiável."""


def _adicionar_features_historicas(
    serie_completa, chaves_serie=("filial", "codigo_produto")
):
    """Calcula features usando shift(1), sem incluir o dia corrente."""

    chaves_serie = list(chaves_serie)
    serie = serie_completa.copy().sort_values(
        [*chaves_serie, "data"], kind="stable"
    )
    serie["quantidade_dia"] = pd.to_numeric(
        serie["quantidade_total"], errors="coerce"
    )
    agrupado = serie.groupby(chaves_serie, sort=False, dropna=False)

    serie["dias_historico"] = agrupado.cumcount()
    serie["log1p_quantidade_dia"] = np.log1p(serie["quantidade_dia"])
    serie["media_anterior_7d"] = agrupado["quantidade_dia"].transform(
        lambda valores: valores.shift(1).rolling(
            JANELA_CURTA, min_periods=JANELA_CURTA
        ).mean()
    )
    serie["media_anterior_28d"] = agrupado["quantidade_dia"].transform(
        lambda valores: valores.shift(1).rolling(
            JANELA_LONGA, min_periods=JANELA_LONGA
        ).mean()
    )
    serie["desvio_anterior_28d"] = agrupado["quantidade_dia"].transform(
        lambda valores: valores.shift(1).rolling(
            JANELA_LONGA, min_periods=JANELA_LONGA
        ).std(ddof=1)
    )

    venda_no_dia = serie["quantidade_dia"] > 0
    groupers = [serie[chave] for chave in chaves_serie]
    data_venda_acumulada = serie["data"].where(venda_no_dia).groupby(
        groupers, sort=False
    ).ffill()
    ultima_venda_anterior = data_venda_acumulada.groupby(
        groupers, sort=False
    ).shift(1)
    serie["dias_desde_ultima_venda"] = (
        serie["data"] - ultima_venda_anterior
    ).dt.days.astype("float64")
    serie["taxa_historica_dias_com_venda"] = venda_no_dia.astype(float).groupby(
        groupers, sort=False
    ).transform(
        lambda valores: valores.shift(1).expanding(min_periods=1).mean()
    )

    serie["razao_quantidade_media_28d"] = np.nan
    denominador_valido = serie["media_anterior_28d"] > 0
    serie.loc[
        denominador_valido, "razao_quantidade_media_28d"
    ] = (
        serie.loc[denominador_valido, "quantidade_dia"]
        / serie.loc[denominador_valido, "media_anterior_28d"]
    )
    serie["diferenca_quantidade_media_28d"] = (
        serie["quantidade_dia"] - serie["media_anterior_28d"]
    )
    return serie


def preparar_observacoes_modelo(
    diagnostico,
    *,
    permitir_vazio=False,
    chaves_serie=("filial", "codigo_produto"),
):
    """Aplica elegibilidade e devolve somente dias positivos modeláveis."""

    chaves_serie = list(chaves_serie)
    resumo_produtos = diagnostico["resumo_produtos"].copy()
    resumo_produtos["dias_decorridos"] = (
        resumo_produtos["ultima_venda"] - resumo_produtos["primeira_venda"]
    ).dt.days
    produtos_intervalo_elegivel = resumo_produtos.loc[
        resumo_produtos["dias_decorridos"] >= MINIMO_DIAS_DECORRIDOS,
        chaves_serie,
    ]
    chaves_elegiveis = pd.MultiIndex.from_frame(produtos_intervalo_elegivel)

    serie = _adicionar_features_historicas(
        diagnostico["serie_b"], chaves_serie=chaves_serie
    )
    indice_serie = pd.MultiIndex.from_frame(serie[chaves_serie])
    serie["produto_intervalo_elegivel"] = indice_serie.isin(chaves_elegiveis)

    dias_positivos = serie["quantidade_dia"] > 0
    historico_completo = serie["dias_historico"] >= JANELA_LONGA
    candidatos = serie.loc[
        serie["produto_intervalo_elegivel"]
        & dias_positivos
        & historico_completo
    ].copy()

    media_zero = candidatos["media_anterior_28d"] == 0
    matriz_numerica = candidatos.loc[:, FEATURES_MODELO].to_numpy(dtype=float)
    linhas_finitas = np.isfinite(matriz_numerica).all(axis=1)
    linhas_modelaveis = ~media_zero & linhas_finitas
    observacoes = candidatos.loc[linhas_modelaveis].copy()

    if observacoes.empty and not permitir_vazio:
        raise ErroModelo("Nenhuma observação atende aos critérios do modelo.")
    if observacoes.duplicated([*chaves_serie, "data"]).any():
        raise ErroModelo("Foram encontradas observações produto-dia duplicadas.")
    if not np.isfinite(
        observacoes.loc[:, FEATURES_MODELO].to_numpy(dtype=float)
    ).all():
        raise ErroModelo("A matriz final ainda contém NaN ou infinito.")

    produtos_modelados = observacoes[chaves_serie].drop_duplicates()
    total_produtos = len(resumo_produtos)
    return {
        "serie_features": serie,
        "observacoes": observacoes,
        "elegibilidade": OrderedDict(
            produtos_totais=total_produtos,
            produtos_com_28_dias_decorridos=len(produtos_intervalo_elegivel),
            produtos_excluidos_intervalo_curto=(
                total_produtos - len(produtos_intervalo_elegivel)
            ),
            produtos_com_observacao_modelavel=len(produtos_modelados),
            produtos_intervalo_elegivel_sem_observacao=(
                len(produtos_intervalo_elegivel) - len(produtos_modelados)
            ),
            produtos_excluidos_no_resultado=(
                total_produtos - len(produtos_modelados)
            ),
            dias_positivos_totais=int(dias_positivos.sum()),
            candidatos_positivos_com_28_dias_anteriores=len(candidatos),
            candidatos_excluidos_media_28d_zero=int(media_zero.sum()),
            candidatos_excluidos_outros_nan_ou_infinito=int(
                ((~linhas_finitas) & (~media_zero)).sum()
            ),
            candidatos_excluidos_total=int((~linhas_modelaveis).sum()),
            observacoes_avaliadas=len(observacoes),
        ),
    }


def treinar_isolation_forest(observacoes):
    """Treina uma execução reproduzível e anexa scores às observações."""

    matriz = observacoes.loc[:, FEATURES_MODELO].to_numpy(dtype=float)
    modelo = IsolationForest(**PARAMETROS_MODELO)
    classificacao = modelo.fit_predict(matriz)
    score_samples = modelo.score_samples(matriz)
    decision_function = modelo.decision_function(matriz)

    resultado = observacoes.copy()
    resultado["score_samples"] = score_samples
    resultado["decision_function"] = decision_function
    resultado["classificacao_isolation_forest"] = classificacao
    resultado["anomalia"] = classificacao == -1
    resultado["aumento_atipico"] = (
        resultado["anomalia"]
        & (
            resultado["quantidade_dia"]
            > resultado["media_anterior_28d"]
        )
    )
    return modelo, resultado


def _distribuicao_scores(resultados, campo):
    valores = resultados[campo]
    return OrderedDict(
        minimo=float(valores.min()),
        p01=float(valores.quantile(0.01)),
        p05=float(valores.quantile(0.05)),
        p25=float(valores.quantile(0.25)),
        mediana=float(valores.median()),
        media=float(valores.mean()),
        p75=float(valores.quantile(0.75)),
        p95=float(valores.quantile(0.95)),
        p99=float(valores.quantile(0.99)),
        maximo=float(valores.max()),
    )


def _comparar_execucoes(preparo_1, resultado_1, preparo_2, resultado_2):
    colunas_chave = ["filial", "codigo_produto", "data"]
    mesmas_features = preparo_1["observacoes"][
        colunas_chave + list(FEATURES_MODELO)
    ].equals(
        preparo_2["observacoes"][colunas_chave + list(FEATURES_MODELO)]
    )
    mesmas_classificacoes = np.array_equal(
        resultado_1["classificacao_isolation_forest"].to_numpy(),
        resultado_2["classificacao_isolation_forest"].to_numpy(),
    )
    diferenca_decision = float(
        np.max(
            np.abs(
                resultado_1["decision_function"].to_numpy()
                - resultado_2["decision_function"].to_numpy()
            )
        )
    )
    diferenca_score = float(
        np.max(
            np.abs(
                resultado_1["score_samples"].to_numpy()
                - resultado_2["score_samples"].to_numpy()
            )
        )
    )
    return OrderedDict(
        features_identicas=mesmas_features,
        classificacoes_identicas=mesmas_classificacoes,
        diferenca_maxima_decision_function=diferenca_decision,
        diferenca_maxima_score_samples=diferenca_score,
        resultados_identicos=(
            mesmas_features
            and mesmas_classificacoes
            and diferenca_decision == 0.0
            and diferenca_score == 0.0
        ),
    )


def executar_pipeline():
    """Executa preparação duas vezes e valida a reprodutibilidade do modelo."""

    diagnostico = diagnosticar_series()
    preparo_1 = preparar_observacoes_modelo(diagnostico)
    modelo_1, resultado_1 = treinar_isolation_forest(preparo_1["observacoes"])

    preparo_2 = preparar_observacoes_modelo(diagnostico)
    _modelo_2, resultado_2 = treinar_isolation_forest(preparo_2["observacoes"])
    reproducibilidade = _comparar_execucoes(
        preparo_1, resultado_1, preparo_2, resultado_2
    )
    if not reproducibilidade["resultados_identicos"]:
        raise ErroModelo("As duas execuções com random_state=42 divergiram.")

    outliers = resultado_1.loc[resultado_1["anomalia"]].copy()
    aumentos = resultado_1.loc[resultado_1["aumento_atipico"]].copy()
    top_20 = aumentos.sort_values(
        ["decision_function", "diferenca_quantidade_media_28d"],
        ascending=[True, False],
    ).head(20)

    resultado_1["mes"] = resultado_1["data"].dt.to_period("M").astype(str)
    anomalias_mes = (
        resultado_1.groupby("mes", as_index=False)
        .agg(
            observacoes=("anomalia", "size"),
            anomalias=("anomalia", "sum"),
            aumentos_atipicos=("aumento_atipico", "sum"),
        )
        .sort_values("mes")
    )
    anomalias_produto = (
        resultado_1.groupby(
            ["filial", "codigo_produto", "descricao_original"],
            as_index=False,
        )
        .agg(
            observacoes=("anomalia", "size"),
            anomalias=("anomalia", "sum"),
            aumentos_atipicos=("aumento_atipico", "sum"),
        )
        .assign(
            percentual_anomalias=lambda dados: (
                dados["anomalias"] * 100 / dados["observacoes"]
            )
        )
        .query("anomalias > 0")
        .sort_values(
            ["anomalias", "aumentos_atipicos", "codigo_produto"],
            ascending=[False, False, True],
        )
    )
    produtos_todas_observacoes_anomalas = int(
        (
            anomalias_produto["anomalias"]
            == anomalias_produto["observacoes"]
        ).sum()
    )
    concentracao_top_10 = float(
        anomalias_produto.head(10)["anomalias"].sum() * 100 / len(outliers)
    )

    return {
        "diagnostico": diagnostico,
        "preparo": preparo_1,
        "modelo": modelo_1,
        "resultados": resultado_1,
        "outliers": outliers,
        "aumentos": aumentos,
        "top_20": top_20,
        "anomalias_mes": anomalias_mes,
        "anomalias_produto": anomalias_produto,
        "diagnostico_vies_global": OrderedDict(
            produtos_com_anomalia=len(anomalias_produto),
            produtos_com_100_percentual_anomalias=(
                produtos_todas_observacoes_anomalas
            ),
            percentual_outliers_nos_10_produtos_com_mais_anomalias=(
                concentracao_top_10
            ),
        ),
        "reprodutibilidade": reproducibilidade,
        "distribuicao_score_samples": _distribuicao_scores(
            resultado_1, "score_samples"
        ),
        "distribuicao_decision_function": _distribuicao_scores(
            resultado_1, "decision_function"
        ),
    }


def _formatar(valor, casas=4):
    if isinstance(valor, (float, np.floating)):
        return f"{float(valor):.{casas}f}"
    return str(valor)


def _imprimir_mapa(mapa, casas=4):
    for chave, valor in mapa.items():
        print(f"- {chave}: {_formatar(valor, casas)}")


def imprimir_relatorio(relatorio):
    resultados = relatorio["resultados"]
    outliers = relatorio["outliers"]
    aumentos = relatorio["aumentos"]
    modelo = relatorio["modelo"]

    print("1. SEGURANÇA E ESCOPO")
    print(f"- Banco: {relatorio['diagnostico']['banco']}")
    print(f"- transaction_read_only: {relatorio['diagnostico']['somente_leitura']}")
    print("- Resultados mantidos somente em memória; nenhum INSERT/UPDATE/DELETE.")
    print("- O sgfpod1 e o dashboard não foram acessados.")

    print("\n2. CRITÉRIO DE ELEGIBILIDADE")
    print("- Exigidos pelo menos 28 dias decorridos entre primeira e última venda.")
    print("- Cada dia avaliado exige 28 dias de calendário anteriores completos.")
    print("- Somente dias com quantidade positiva são avaliados nesta versão.")
    print("- Média anterior de 28 dias igual a zero torna a razão indefinida; a observação é contada e excluída, sem imputação.")
    _imprimir_mapa(relatorio["preparo"]["elegibilidade"], casas=2)

    print("\n3. FEATURES E VAZAMENTO TEMPORAL")
    for feature in FEATURES_MODELO:
        print(f"- {feature}")
    print("- Médias, desvio, última venda e taxa histórica usam shift(1).")
    print("- Filial e código do produto identificam a série, mas não entram na matriz do modelo.")
    print("- Não foi aplicado StandardScaler: o Isolation Forest usa divisões por limiares em árvores e a ausência de escala mantém as features diretamente interpretáveis.")

    print("\n4. PARÂMETROS DO ISOLATION FOREST")
    print(f"- scikit-learn: {SKLEARN_VERSION}")
    for chave, valor in PARAMETROS_MODELO.items():
        print(f"- {chave}: {valor}")
    print(f"- max_samples_ efetivamente utilizado: {modelo.max_samples_}")
    print(f"- quantidade de features: {modelo.n_features_in_}")
    print(f"- offset_ calculado: {modelo.offset_:.4f}")
    print("- contamination='auto' usa offset_=-0,5; decision_function < 0 recebe classificação -1.")

    total = len(resultados)
    print("\n5. RESULTADO GERAL")
    print(f"- Observações avaliadas: {total}")
    print(f"- Outliers: {len(outliers)} ({len(outliers) * 100 / total:.2f}%)")
    print(f"- Aumentos atípicos: {len(aumentos)} ({len(aumentos) * 100 / total:.2f}%)")
    print("- Um outlier só é classificado como aumento quando quantidade_dia > media_anterior_28d.")

    print("\n6. DISTRIBUIÇÃO DOS SCORES")
    print("score_samples (menor = mais atípico):")
    _imprimir_mapa(relatorio["distribuicao_score_samples"])
    print("decision_function (negativo = outlier):")
    _imprimir_mapa(relatorio["distribuicao_decision_function"])

    print("\n7. 20 AUMENTOS ATÍPICOS MAIS RELEVANTES")
    colunas = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "data",
        "quantidade_dia",
        "media_anterior_28d",
        "diferenca_quantidade_media_28d",
        "razao_quantidade_media_28d",
        "decision_function",
        "score_samples",
        "classificacao_isolation_forest",
    ]
    if relatorio["top_20"].empty:
        print("Nenhum aumento atípico foi identificado.")
    else:
        print(relatorio["top_20"][colunas].to_string(index=False))

    print("\n8. ANOMALIAS POR MÊS")
    print(relatorio["anomalias_mes"].to_string(index=False))

    print("\n9. ANOMALIAS POR PRODUTO — 20 PRIMEIROS")
    print(relatorio["anomalias_produto"].head(20).to_string(index=False))
    print("Diagnóstico de concentração do modelo global:")
    _imprimir_mapa(relatorio["diagnostico_vies_global"], casas=2)

    print("\n10. REPRODUTIBILIDADE")
    _imprimir_mapa(relatorio["reprodutibilidade"])
    print("- A preparação das features e o treinamento foram executados duas vezes com random_state=42.")

    print("\n11. LIMITAÇÕES")
    print("- O histórico cobre apenas seis meses e não representa sazonalidade anual.")
    print("- A maioria dos produtos é esparsa; esta versão exclui parte deles pelo histórico mínimo.")
    print("- O modelo é global e pode misturar produtos com perfis de volume diferentes, embora razão, médias históricas e log1p reduzam esse efeito.")
    print("- Foram observados produtos com 100% dos dias avaliados classificados como anomalia; isso evidencia viés de escala/perfil e impede uso definitivo sem comparação com normalização por produto ou modelos segmentados.")
    print("- contamination='auto' não estima a prevalência real de anomalias; usa um limiar matemático padrão.")
    print("- Ainda não houve validação com anomalias sintéticas; isso pertence à Etapa 10C/Fase 5.")
    print("- Resultado atípico não comprova doença, surto, diagnóstico ou automedicação.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar_pipeline())
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("PIPELINE INTERROMPIDO COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
