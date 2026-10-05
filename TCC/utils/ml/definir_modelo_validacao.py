"""Define o modelo candidato à validação sintética após comparar A, B e C.

O Modelo C trata esparsidade exigindo histórico positivo mínimo e substitui
razões instáveis por diferenças de logaritmos contra o histórico positivo do
próprio produto. Tudo permanece em memória e em modo somente leitura.
"""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy.exc import SQLAlchemyError

from .comparar_modelos_relativos import (
    ErroModelo,
    PARAMETROS_MODELO,
    _distribuicao_por_volume,
    _metricas_modelo,
    _top_20,
    _verificar_reprodutibilidade,
    adicionar_features_relativas,
    executar_comparacao,
)
from .detectar_anomalias import preparar_observacoes_modelo
from .diagnosticar_series import ErroDiagnostico, diagnosticar_series


MINIMO_VENDAS_POSITIVAS_MODELO_C = 7
LIMITES_AVALIADOS = (7, 14, 28)

FEATURES_MODELO_C = (
    "desvio_log_media_positiva_historica",
    "desvio_log_mediana_positiva_historica",
    "razao_media_7d_media_28d",
    "taxa_historica_dias_com_venda",
    "dias_desde_ultima_venda",
    "diferenca_taxa_recente_historica",
)

FEATURES_INTERPRETACAO_C = (
    "quantidade_dia",
    "media_anterior_7d",
    "media_anterior_28d",
    "diferenca_quantidade_media_28d",
    "razao_quantidade_media_28d",
    "mediana_positiva_anterior_28d",
    "mad_positivo_anterior_28d",
)


def calcular_historico_positivo(serie_features):
    """Expõe o contexto positivo anterior usado pelo Modelo C."""

    serie = serie_features.copy().sort_values(
        ["filial", "codigo_produto", "data"], kind="stable"
    )
    chaves = [serie["filial"], serie["codigo_produto"]]
    positiva = (serie["quantidade_dia"] > 0).astype(int)
    quantidade_positiva = serie["quantidade_dia"].where(
        serie["quantidade_dia"] > 0, 0.0
    )

    serie["vendas_positivas_anteriores"] = (
        positiva.groupby(chaves, sort=False).cumsum() - positiva
    )
    soma_positiva_acumulada = quantidade_positiva.groupby(
        chaves, sort=False
    ).cumsum() - quantidade_positiva
    serie["media_positiva_historica"] = (
        soma_positiva_acumulada / serie["vendas_positivas_anteriores"]
    )
    serie["mediana_positiva_historica"] = quantidade_positiva.where(
        positiva == 1
    ).groupby(chaves, sort=False).transform(
        lambda valores: valores.shift(1).expanding(min_periods=1).median()
    )
    serie["taxa_recente_anterior_28d"] = positiva.astype(float).groupby(
        chaves, sort=False
    ).transform(
        lambda valores: valores.shift(1).rolling(28, min_periods=28).mean()
    )
    serie["diferenca_taxa_recente_historica"] = (
        serie["taxa_recente_anterior_28d"]
        - serie["taxa_historica_dias_com_venda"]
    )
    return serie


def _adicionar_historico_positivo(comparacao, serie_historica=None):
    """Anexa às observações as estatísticas positivas calculadas no passado."""

    if serie_historica is None:
        serie_historica = calcular_historico_positivo(
            comparacao["preparo"]["serie_features"]
        )

    campos = [
        "filial",
        "codigo_produto",
        "data",
        "vendas_positivas_anteriores",
        "media_positiva_historica",
        "mediana_positiva_historica",
        "taxa_recente_anterior_28d",
        "diferenca_taxa_recente_historica",
    ]
    observacoes = comparacao["resultado_b"].merge(
        serie_historica[campos],
        on=["filial", "codigo_produto", "data"],
        how="left",
        validate="one_to_one",
    )
    return observacoes


def analisar_limites(observacoes, total_produtos):
    """Mostra cobertura antes de fixar o mínimo escolhido para C."""

    linhas = []
    total_observacoes = len(observacoes)
    for limite in LIMITES_AVALIADOS:
        elegiveis = observacoes.loc[
            observacoes["vendas_positivas_anteriores"] >= limite
        ]
        produtos = elegiveis[["filial", "codigo_produto"]].drop_duplicates()
        linhas.append(
            {
                "minimo_vendas_positivas_anteriores": limite,
                "produtos_modelaveis": len(produtos),
                "percentual_produtos_total": (
                    len(produtos) * 100 / total_produtos
                ),
                "observacoes_modelaveis": len(elegiveis),
                "percentual_observacoes_base": (
                    len(elegiveis) * 100 / total_observacoes
                ),
                "produtos_historico_insuficiente": (
                    total_produtos - len(produtos)
                ),
            }
        )
    return pd.DataFrame(linhas)


def preparar_modelo_c(observacoes):
    """Aplica o mínimo escolhido e cria features estáveis e sem escala bruta."""

    dados = observacoes.loc[
        observacoes["vendas_positivas_anteriores"]
        >= MINIMO_VENDAS_POSITIVAS_MODELO_C
    ].copy()

    # log1p produz log((1+atual)/(1+referência)) por subtração de logs.
    # Isso permanece definido mesmo para referências pequenas, sem clipping.
    dados["desvio_log_media_positiva_historica"] = (
        np.log1p(dados["quantidade_dia"])
        - np.log1p(dados["media_positiva_historica"])
    )
    dados["desvio_log_mediana_positiva_historica"] = (
        np.log1p(dados["quantidade_dia"])
        - np.log1p(dados["mediana_positiva_historica"])
    )

    matriz = dados.loc[:, FEATURES_MODELO_C].to_numpy(dtype=float)
    linhas_finitas = np.isfinite(matriz).all(axis=1)
    excluidas_nao_finitas = int((~linhas_finitas).sum())
    dados = dados.loc[linhas_finitas].copy()
    if dados.empty:
        raise ErroModelo("O Modelo C não possui observações finitas.")
    if excluidas_nao_finitas:
        raise ErroModelo(
            f"O Modelo C encontrou {excluidas_nao_finitas} linhas não finitas."
        )

    return dados


def treinar_modelo_c(observacoes):
    """Treina C com os mesmos hiperparâmetros usados em A e B."""

    matriz = observacoes.loc[:, FEATURES_MODELO_C].to_numpy(dtype=float)
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
            > resultado["media_positiva_historica"]
        )
    )
    return modelo, resultado


def executar_modelo_c(apenas_elegiveis_automedicacao=False):
    """Executa diretamente o Modelo C congelado para reutilização pela aplicação.

    O caminho direto evita treinar os baselines A e B. A agregação, o calendário,
    as features temporais, a elegibilidade e os parâmetros permanecem os mesmos.
    """

    diagnostico = diagnosticar_series(
        apenas_elegiveis_automedicacao=apenas_elegiveis_automedicacao
    )
    return executar_modelo_c_em_diagnostico(diagnostico)


def executar_modelo_c_em_diagnostico(
    diagnostico, *, permitir_historico_insuficiente=False
):
    """Executa o nÃºcleo congelado sobre uma fonte jÃ¡ preparada em memÃ³ria."""

    preparo = preparar_observacoes_modelo(
        diagnostico,
        permitir_vazio=permitir_historico_insuficiente,
    )
    serie_contexto = calcular_historico_positivo(preparo["serie_features"])
    if preparo["observacoes"].empty:
        if not permitir_historico_insuficiente:
            raise ErroModelo("Nenhuma observaÃ§Ã£o atende aos critÃ©rios do modelo.")
        return {
            "diagnostico": diagnostico,
            "preparo": preparo,
            "modelo": None,
            "resultado": preparo["observacoes"].copy(),
            "serie_contexto": serie_contexto,
        }

    observacoes_relativas, _diagnostico_mad = adicionar_features_relativas(
        preparo
    )
    observacoes_historicas = _adicionar_historico_positivo(
        {
            "preparo": preparo,
            "resultado_b": observacoes_relativas,
        },
        serie_historica=serie_contexto,
    )
    observacoes_c = preparar_modelo_c(observacoes_historicas)
    modelo, resultado = treinar_modelo_c(observacoes_c)
    return {
        "diagnostico": diagnostico,
        "preparo": preparo,
        "modelo": modelo,
        "resultado": resultado,
        "serie_contexto": serie_contexto,
    }


def _produtos_acompanhados(tres_resultados):
    codigos = {
        14184: "DIPIRONA 500MG 10CP",
        20376: "GLIFAGE XR 500MG 30CP",
        44180: "STOMALIV ABACAXI ENV 5G",
    }
    linhas = []
    for modelo, resultados in tres_resultados.items():
        for codigo, produto in codigos.items():
            dados = resultados.loc[resultados["codigo_produto"] == codigo]
            linhas.append(
                {
                    "modelo": modelo,
                    "codigo_produto": codigo,
                    "produto": produto,
                    "observacoes": len(dados),
                    "anomalias": int(dados["anomalia"].sum()),
                    "percentual_anomalias": (
                        float(dados["anomalia"].mean() * 100)
                        if len(dados)
                        else 0.0
                    ),
                    "aumentos_atipicos": int(
                        dados["aumento_atipico"].sum()
                    ),
                }
            )
    return pd.DataFrame(linhas)


def executar_definicao():
    """Compara A/B/C e devolve o Modelo C candidato à próxima etapa."""

    comparacao = executar_comparacao()
    observacoes_historicas = _adicionar_historico_positivo(comparacao)
    total_produtos = len(comparacao["diagnostico"]["resumo_produtos"])
    analise_limites = analisar_limites(observacoes_historicas, total_produtos)
    observacoes_c = preparar_modelo_c(observacoes_historicas)

    modelo_c_1, resultado_c_1 = treinar_modelo_c(observacoes_c)
    _modelo_c_2, resultado_c_2 = treinar_modelo_c(observacoes_c)
    reproducibilidade_c = _verificar_reprodutibilidade(
        resultado_c_1, resultado_c_2
    )
    if not reproducibilidade_c["resultados_identicos"]:
        raise ErroModelo("O Modelo C não foi reproduzível.")

    metricas_c = _metricas_modelo(resultado_c_1)
    tres_resultados = {
        "A": comparacao["resultado_a"],
        "B": comparacao["resultado_b"],
        "C": resultado_c_1,
    }
    resumo_comparativo = pd.DataFrame(
        [
            {
                "modelo": nome,
                "produtos_modelaveis": len(
                    resultados[["filial", "codigo_produto"]]
                    .drop_duplicates()
                ),
                "produtos_historico_insuficiente": (
                    total_produtos
                    - len(
                        resultados[["filial", "codigo_produto"]]
                        .drop_duplicates()
                    )
                ),
                **metricas["resumo"],
            }
            for nome, metricas, resultados in (
                (
                    "A",
                    comparacao["metricas_a"],
                    comparacao["resultado_a"],
                ),
                (
                    "B",
                    comparacao["metricas_b"],
                    comparacao["resultado_b"],
                ),
                ("C", metricas_c, resultado_c_1),
            )
        ]
    )

    produtos_c = resultado_c_1[
        ["filial", "codigo_produto"]
    ].drop_duplicates()
    return {
        "comparacao_ab": comparacao,
        "analise_limites": analise_limites,
        "modelo_c": modelo_c_1,
        "resultado_c": resultado_c_1,
        "metricas_c": metricas_c,
        "resumo_comparativo": resumo_comparativo,
        "distribuicao_volume_c": _distribuicao_por_volume(resultado_c_1),
        "top_20_c": _top_20(resultado_c_1),
        "produtos_acompanhados": _produtos_acompanhados(tres_resultados),
        "reprodutibilidade_c": reproducibilidade_c,
        "produtos_modelaveis_c": len(produtos_c),
        "produtos_historico_insuficiente": total_produtos - len(produtos_c),
    }


def _imprimir_mapa(mapa):
    for chave, valor in mapa.items():
        if isinstance(valor, (float, np.floating)):
            print(f"- {chave}: {float(valor):.4f}")
        else:
            print(f"- {chave}: {valor}")


def imprimir_relatorio(relatorio):
    comparacao = relatorio["comparacao_ab"]
    print("1. SEGURANÇA E ESCOPO")
    print(f"- Banco: {comparacao['diagnostico']['banco']}")
    print(f"- transaction_read_only: {comparacao['diagnostico']['somente_leitura']}")
    print("- A e B permanecem intactos; C existe somente em memória.")

    print("\n2. ANÁLISE DO HISTÓRICO POSITIVO MÍNIMO")
    print(relatorio["analise_limites"].to_string(index=False))
    print("- Escolha: 7 vendas positivas anteriores.")
    print("- Justificativa: mantém aproximadamente 78,87% das observações anteriores; 14 e 28 reduzem a cobertura de produtos para 13,63% e 6,04% do catálogo.")

    print("\n3. MODELO C — FEATURES")
    for feature in FEATURES_MODELO_C:
        print(f"- {feature}")
    print("- Cada venda positiva anterior representa um dia-produto com quantidade maior que zero.")
    print("- Aumento atípico exige outlier e quantidade atual superior à média das vendas positivas anteriores.")
    print("- Features absolutas e MAD foram preservadas apenas para interpretação.")
    print("- Diferenças logarítmicas usam log((1+atual)/(1+referência positiva)), sem clipping e sem divisão por médias próximas de zero.")
    print(f"- Produtos modeláveis: {relatorio['produtos_modelaveis_c']}")
    print(f"- Histórico insuficiente para análise: {relatorio['produtos_historico_insuficiente']}")

    print("\n4. COMPARAÇÃO A × B × C")
    print(relatorio["resumo_comparativo"].to_string(index=False))

    print("\n5. DISTRIBUIÇÃO POR VOLUME")
    print("Modelo A:")
    print(comparacao["distribuicao_volume_a"].to_string(index=False))
    print("Modelo B:")
    print(comparacao["distribuicao_volume_b"].to_string(index=False))
    print("Modelo C:")
    print(relatorio["distribuicao_volume_c"].to_string(index=False))

    print("\n6. PRODUTOS ACOMPANHADOS")
    print(relatorio["produtos_acompanhados"].to_string(index=False))

    print("\n7. 20 AUMENTOS MAIS RELEVANTES — MODELO C")
    colunas = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "data",
        "quantidade_dia",
        "media_anterior_28d",
        "media_positiva_historica",
        "desvio_log_media_positiva_historica",
        "decision_function",
        "score_samples",
    ]
    print(relatorio["top_20_c"][colunas].to_string(index=False))

    print("\n8. REPRODUTIBILIDADE DO MODELO C")
    _imprimir_mapa(relatorio["reprodutibilidade_c"])
    print(f"- offset_: {relatorio['modelo_c'].offset_:.4f}")
    print(f"- Parâmetros: {PARAMETROS_MODELO}")

    print("\n9. MODELO RECOMENDADO PARA CONGELAMENTO")
    print("- Recomendação: Modelo C.")
    print("- Motivo: compara magnitude e frequência com o histórico positivo do próprio produto, elimina volume absoluto da matriz e exige cobertura histórica explícita.")
    print("- Produtos sem histórico suficiente permanecem com esse estado, sem receber classificação de anomalia.")
    print("- contamination='auto' e random_state=42 foram mantidos; nenhum ajuste foi feito para produzir uma quantidade desejada.")
    print("- A recomendação será submetida à validação sintética na Etapa 10C, sem conclusão clínica automática.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar_definicao())
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("DEFINIÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
