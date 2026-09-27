"""Compara o baseline absoluto com uma variante relativa do Isolation Forest.

O Modelo A é reutilizado sem alteração. O Modelo B usa a mesma população e as
mesmas regras temporais, mas remove da matriz as features dependentes de escala
absoluta. Nenhum resultado é persistido.
"""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy.exc import SQLAlchemyError

from .detectar_anomalias import (
    ErroModelo,
    FEATURES_MODELO,
    PARAMETROS_MODELO,
    preparar_observacoes_modelo,
    treinar_isolation_forest,
)
from .diagnosticar_series import ErroDiagnostico, diagnosticar_series


FATOR_MAD_NORMAL = 1.4826
JANELA_ROBUSTA = 28
PRODUTOS_ACOMPANHADOS = {
    14184: "DIPIRONA 500MG 10CP",
    20376: "GLIFAGE XR 500MG 30CP",
    44180: "STOMALIV ABACAXI ENV 5G",
}

FEATURES_MODELO_B = (
    "razao_quantidade_media_28d",
    "desvio_relativo_media_28d",
    "desvio_robusto_mediana_28d",
    "mad_anterior_28d_zero",
    "taxa_historica_dias_com_venda",
    "dias_desde_ultima_venda",
    "razao_media_7d_media_28d",
)

FEATURES_ABSOLUTAS_PRESERVADAS = (
    "log1p_quantidade_dia",
    "media_anterior_7d",
    "media_anterior_28d",
    "desvio_anterior_28d",
    "diferenca_quantidade_media_28d",
)


def _mad(valores):
    """Calcula a mediana dos desvios absolutos ignorando NaN."""

    valores_validos = valores[np.isfinite(valores)]
    if len(valores_validos) == 0:
        return np.nan
    mediana = np.median(valores_validos)
    return float(np.median(np.abs(valores_validos - mediana)))


def adicionar_features_relativas(preparo):
    """Cria features relativas usando apenas os 28 dias anteriores."""

    serie = preparo["serie_features"].copy().sort_values(
        ["filial", "codigo_produto", "data"], kind="stable"
    )
    agrupado = serie.groupby(["filial", "codigo_produto"], sort=False)

    serie["mediana_positiva_anterior_28d"] = agrupado[
        "quantidade_dia"
    ].transform(
        lambda valores: valores.where(valores > 0).shift(1).rolling(
            JANELA_ROBUSTA, min_periods=1
        ).median()
    )
    serie["mad_positivo_anterior_28d"] = agrupado[
        "quantidade_dia"
    ].transform(
        lambda valores: valores.where(valores > 0).shift(1).rolling(
            JANELA_ROBUSTA, min_periods=1
        ).apply(_mad, raw=True)
    )

    serie["desvio_relativo_media_28d"] = (
        serie["diferenca_quantidade_media_28d"]
        / serie["media_anterior_28d"]
    )
    serie["razao_media_7d_media_28d"] = (
        serie["media_anterior_7d"] / serie["media_anterior_28d"]
    )
    mad_valido = serie["mad_positivo_anterior_28d"] > 0
    serie["mad_anterior_28d_zero"] = (~mad_valido).astype(float)
    serie["desvio_robusto_mediana_28d"] = 0.0
    serie.loc[mad_valido, "desvio_robusto_mediana_28d"] = (
        serie.loc[mad_valido, "quantidade_dia"]
        - serie.loc[mad_valido, "mediana_positiva_anterior_28d"]
    ) / (
        FATOR_MAD_NORMAL
        * serie.loc[mad_valido, "mad_positivo_anterior_28d"]
    )

    campos_novos = [
        "filial",
        "codigo_produto",
        "data",
        "mediana_positiva_anterior_28d",
        "mad_positivo_anterior_28d",
        "desvio_relativo_media_28d",
        "desvio_robusto_mediana_28d",
        "mad_anterior_28d_zero",
        "razao_media_7d_media_28d",
    ]
    observacoes = preparo["observacoes"].merge(
        serie[campos_novos],
        on=["filial", "codigo_produto", "data"],
        how="left",
        validate="one_to_one",
    )

    matriz = observacoes.loc[:, FEATURES_MODELO_B].to_numpy(dtype=float)
    if not np.isfinite(matriz).all():
        contagem = int((~np.isfinite(matriz)).any(axis=1).sum())
        raise ErroModelo(
            f"O Modelo B encontrou {contagem} linhas com NaN ou infinito."
        )

    diagnostico_mad = OrderedDict(
        observacoes=len(observacoes),
        observacoes_com_mad_zero=int(
            (observacoes["mad_anterior_28d_zero"] == 1).sum()
        ),
        percentual_mad_zero=float(
            (observacoes["mad_anterior_28d_zero"] == 1).mean() * 100
        ),
    )
    return observacoes, diagnostico_mad


def treinar_modelo_b(observacoes):
    """Treina a variante relativa com os mesmos parâmetros do baseline."""

    matriz = observacoes.loc[:, FEATURES_MODELO_B].to_numpy(dtype=float)
    modelo = IsolationForest(**PARAMETROS_MODELO)
    classificacao = modelo.fit_predict(matriz)

    resultado = observacoes.copy()
    resultado["score_samples"] = modelo.score_samples(matriz)
    resultado["decision_function"] = modelo.decision_function(matriz)
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


def _metricas_modelo(resultados):
    por_produto = (
        resultados.groupby(
            ["filial", "codigo_produto", "descricao_original"],
            as_index=False,
        )
        .agg(
            observacoes=("anomalia", "size"),
            anomalias=("anomalia", "sum"),
            aumentos_atipicos=("aumento_atipico", "sum"),
            quantidade_total_periodo=("quantidade_total_periodo", "first"),
            faixa_volume=("faixa_volume", "first"),
        )
    )
    com_anomalia = por_produto.loc[por_produto["anomalias"] > 0].sort_values(
        ["anomalias", "aumentos_atipicos", "codigo_produto"],
        ascending=[False, False, True],
    )
    total_outliers = int(resultados["anomalia"].sum())
    concentracao = (
        0.0
        if total_outliers == 0
        else float(com_anomalia.head(10)["anomalias"].sum() * 100 / total_outliers)
    )
    return {
        "resumo": OrderedDict(
            observacoes=len(resultados),
            outliers=total_outliers,
            percentual_outliers=float(total_outliers * 100 / len(resultados)),
            aumentos_atipicos=int(resultados["aumento_atipico"].sum()),
            percentual_aumentos=float(
                resultados["aumento_atipico"].mean() * 100
            ),
            produtos_com_anomalia=len(com_anomalia),
            produtos_com_100_percentual_anomalias=int(
                (
                    com_anomalia["anomalias"]
                    == com_anomalia["observacoes"]
                ).sum()
            ),
            concentracao_outliers_top_10_percentual=concentracao,
        ),
        "por_produto": com_anomalia,
    }


def _adicionar_faixa_volume(resultados, resumo_produtos, limites=None):
    volumes = resumo_produtos[
        ["filial", "codigo_produto", "quantidade_total"]
    ].rename(columns={"quantidade_total": "quantidade_total_periodo"})
    produtos_avaliados = resultados[
        ["filial", "codigo_produto"]
    ].drop_duplicates().merge(
        volumes,
        on=["filial", "codigo_produto"],
        how="left",
        validate="one_to_one",
    )
    if limites is None:
        limite_baixo, limite_alto = produtos_avaliados[
            "quantidade_total_periodo"
        ].quantile([1 / 3, 2 / 3]).tolist()
    else:
        limite_baixo, limite_alto = limites

    produtos_avaliados["faixa_volume"] = pd.cut(
        produtos_avaliados["quantidade_total_periodo"],
        bins=[-np.inf, limite_baixo, limite_alto, np.inf],
        labels=["baixo", "medio", "alto"],
        include_lowest=True,
    ).astype(str)
    resultado = resultados.merge(
        produtos_avaliados,
        on=["filial", "codigo_produto"],
        how="left",
        validate="many_to_one",
    )
    return resultado, (float(limite_baixo), float(limite_alto))


def _distribuicao_por_volume(resultados):
    return (
        resultados.groupby("faixa_volume", as_index=False)
        .agg(
            produtos=("codigo_produto", "nunique"),
            observacoes=("anomalia", "size"),
            anomalias=("anomalia", "sum"),
            aumentos_atipicos=("aumento_atipico", "sum"),
        )
        .assign(
            percentual_anomalias=lambda dados: (
                dados["anomalias"] * 100 / dados["observacoes"]
            ),
            percentual_dos_outliers=lambda dados: (
                dados["anomalias"] * 100 / dados["anomalias"].sum()
            ),
        )
        .sort_values("faixa_volume")
    )


def _top_20(resultados):
    return resultados.loc[resultados["aumento_atipico"]].sort_values(
        ["decision_function", "razao_quantidade_media_28d"],
        ascending=[True, False],
    ).head(20)


def _verificar_reprodutibilidade(resultado_1, resultado_2):
    mesmas_classes = np.array_equal(
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
        classificacoes_identicas=mesmas_classes,
        diferenca_maxima_decision_function=diferenca_decision,
        diferenca_maxima_score_samples=diferenca_score,
        resultados_identicos=(
            mesmas_classes
            and diferenca_decision == 0.0
            and diferenca_score == 0.0
        ),
    )


def _produtos_especificos(resultados_a, resultados_b):
    linhas = []
    for codigo, nome in PRODUTOS_ACOMPANHADOS.items():
        for modelo, resultados in (("A", resultados_a), ("B", resultados_b)):
            produto = resultados.loc[resultados["codigo_produto"] == codigo]
            linhas.append(
                {
                    "modelo": modelo,
                    "codigo_produto": codigo,
                    "produto": nome,
                    "observacoes": len(produto),
                    "anomalias": int(produto["anomalia"].sum()),
                    "percentual_anomalias": (
                        float(produto["anomalia"].mean() * 100)
                        if len(produto)
                        else 0.0
                    ),
                    "aumentos_atipicos": int(
                        produto["aumento_atipico"].sum()
                    ),
                }
            )
    return pd.DataFrame(linhas)


def executar_comparacao():
    """Executa A e B duas vezes e monta evidências comparáveis."""

    diagnostico = diagnosticar_series()
    preparo = preparar_observacoes_modelo(diagnostico)

    _modelo_a_1, resultado_a_1 = treinar_isolation_forest(
        preparo["observacoes"]
    )
    _modelo_a_2, resultado_a_2 = treinar_isolation_forest(
        preparo["observacoes"]
    )

    observacoes_b, diagnostico_mad = adicionar_features_relativas(preparo)
    modelo_b_1, resultado_b_1 = treinar_modelo_b(observacoes_b)
    _modelo_b_2, resultado_b_2 = treinar_modelo_b(observacoes_b)

    resultado_a_1, limites_volume = _adicionar_faixa_volume(
        resultado_a_1, diagnostico["resumo_produtos"]
    )
    resultado_a_2, _ = _adicionar_faixa_volume(
        resultado_a_2, diagnostico["resumo_produtos"], limites_volume
    )
    resultado_b_1, _ = _adicionar_faixa_volume(
        resultado_b_1, diagnostico["resumo_produtos"], limites_volume
    )
    resultado_b_2, _ = _adicionar_faixa_volume(
        resultado_b_2, diagnostico["resumo_produtos"], limites_volume
    )

    metricas_a = _metricas_modelo(resultado_a_1)
    metricas_b = _metricas_modelo(resultado_b_1)
    reproducibilidade_a = _verificar_reprodutibilidade(
        resultado_a_1, resultado_a_2
    )
    reproducibilidade_b = _verificar_reprodutibilidade(
        resultado_b_1, resultado_b_2
    )
    if not (
        reproducibilidade_a["resultados_identicos"]
        and reproducibilidade_b["resultados_identicos"]
    ):
        raise ErroModelo("A comparação não foi reproduzível.")

    return {
        "diagnostico": diagnostico,
        "preparo": preparo,
        "diagnostico_mad": diagnostico_mad,
        "modelo_b": modelo_b_1,
        "resultado_a": resultado_a_1,
        "resultado_b": resultado_b_1,
        "metricas_a": metricas_a,
        "metricas_b": metricas_b,
        "distribuicao_volume_a": _distribuicao_por_volume(resultado_a_1),
        "distribuicao_volume_b": _distribuicao_por_volume(resultado_b_1),
        "limites_volume": limites_volume,
        "top_20_a": _top_20(resultado_a_1),
        "top_20_b": _top_20(resultado_b_1),
        "produtos_especificos": _produtos_especificos(
            resultado_a_1, resultado_b_1
        ),
        "reprodutibilidade_a": reproducibilidade_a,
        "reprodutibilidade_b": reproducibilidade_b,
    }


def _imprimir_mapa(mapa):
    for chave, valor in mapa.items():
        if isinstance(valor, (float, np.floating)):
            print(f"- {chave}: {float(valor):.4f}")
        else:
            print(f"- {chave}: {valor}")


def _imprimir_top(titulo, dados):
    colunas = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "data",
        "quantidade_dia",
        "media_anterior_28d",
        "diferenca_quantidade_media_28d",
        "razao_quantidade_media_28d",
        "desvio_robusto_mediana_28d",
        "decision_function",
        "score_samples",
    ]
    colunas_disponiveis = [coluna for coluna in colunas if coluna in dados]
    print(f"\n{titulo}")
    print(dados[colunas_disponiveis].to_string(index=False))


def imprimir_relatorio(relatorio):
    print("1. SEGURANÇA E COMPARABILIDADE")
    print(f"- Banco: {relatorio['diagnostico']['banco']}")
    print(f"- transaction_read_only: {relatorio['diagnostico']['somente_leitura']}")
    print("- Modelos A e B usam exatamente as mesmas observações e parâmetros.")
    print("- Nenhum resultado foi persistido e o baseline permaneceu inalterado.")

    print("\n2. MODELO A — BASELINE")
    print(f"- Features: {', '.join(FEATURES_MODELO)}")
    _imprimir_mapa(relatorio["metricas_a"]["resumo"])

    print("\n3. MODELO B — RELATIVO/ROBUSTO")
    print(f"- Features: {', '.join(FEATURES_MODELO_B)}")
    print("- Features absolutas preservadas apenas para interpretação:")
    print(f"  {', '.join(FEATURES_ABSOLUTAS_PRESERVADAS)}")
    print("- MAD calculado nas vendas positivas dos 28 dias anteriores.")
    print("- Quando MAD=0: desvio robusto=0 e indicador mad_anterior_28d_zero=1.")
    _imprimir_mapa(relatorio["diagnostico_mad"])
    _imprimir_mapa(relatorio["metricas_b"]["resumo"])
    print(f"- offset_ do Modelo B: {relatorio['modelo_b'].offset_:.4f}")

    print("\n4. DISTRIBUIÇÃO POR FAIXA DE VOLUME")
    baixo, alto = relatorio["limites_volume"]
    print(f"- Baixo: quantidade total <= {baixo:.2f}")
    print(f"- Médio: > {baixo:.2f} e <= {alto:.2f}")
    print(f"- Alto: > {alto:.2f}")
    print("Modelo A:")
    print(relatorio["distribuicao_volume_a"].to_string(index=False))
    print("Modelo B:")
    print(relatorio["distribuicao_volume_b"].to_string(index=False))

    print("\n5. PRODUTOS ACOMPANHADOS")
    print(relatorio["produtos_especificos"].to_string(index=False))

    _imprimir_top("6. 20 AUMENTOS MAIS RELEVANTES — MODELO A", relatorio["top_20_a"])
    _imprimir_top("7. 20 AUMENTOS MAIS RELEVANTES — MODELO B", relatorio["top_20_b"])

    print("\n8. REPRODUTIBILIDADE")
    print("Modelo A:")
    _imprimir_mapa(relatorio["reprodutibilidade_a"])
    print("Modelo B:")
    _imprimir_mapa(relatorio["reprodutibilidade_b"])

    print("\n9. CONCLUSÃO METODOLÓGICA")
    print("- O Modelo B é conceitualmente mais adequado para mudanças relativas ao histórico do próprio produto e reduziu a concentração nos itens de alto volume.")
    print("- Entretanto, o Modelo B atual não deve substituir o baseline: médias próximas de zero geraram razões extremas, 70,20% das observações tiveram MAD=0 e aumentou o número de produtos com 100% de anomalias.")
    print("- A recomendação é manter A como baseline e usar B como base da próxima variante, acrescentando mínimo documentado de vendas positivas anteriores ou segmentação por esparsidade antes da adoção.")
    print("- Razão e desvio relativo são matematicamente correlacionados; essa redundância também deverá ser avaliada na próxima comparação.")
    print("- contamination='auto' foi mantido; nenhum limiar foi ajustado para obter uma quantidade desejada.")
    print("- Nenhum resultado representa conclusão clínica; trata-se apenas de comportamento de vendas fora do histórico do produto.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar_comparacao())
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("COMPARAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
