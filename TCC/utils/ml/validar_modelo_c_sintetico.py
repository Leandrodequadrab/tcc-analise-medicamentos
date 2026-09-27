"""Valida o Modelo C congelado com aumentos sintéticos conhecidos.

A validação usa cópias em memória. Cada quantidade injetada é comparada com
as referências calculadas sobre os dados reais anteriores; a injeção não é
propagada para o histórico de datas posteriores.
"""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sqlalchemy.exc import SQLAlchemyError

from .comparar_modelos_relativos import ErroModelo
from .definir_modelo_validacao import (
    FEATURES_MODELO_C,
    MINIMO_VENDAS_POSITIVAS_MODELO_C,
    PARAMETROS_MODELO,
    executar_definicao,
    treinar_modelo_c,
)
from .diagnosticar_series import ErroDiagnostico


SEMENTE_EXPERIMENTO = 42
AMOSTRAS_POR_INTENSIDADE = 150
INTENSIDADES = OrderedDict(
    (
        ("moderado", 1.5),
        ("forte", 3.0),
        ("muito_forte", 6.0),
    )
)

CHAVES_OBSERVACAO = ["filial", "codigo_produto", "data"]
FEATURES_HISTORICAS_IMUTAVEIS = (
    "media_anterior_7d",
    "media_anterior_28d",
    "desvio_anterior_28d",
    "dias_desde_ultima_venda",
    "taxa_historica_dias_com_venda",
    "vendas_positivas_anteriores",
    "media_positiva_historica",
    "mediana_positiva_historica",
    "taxa_recente_anterior_28d",
    "diferenca_taxa_recente_historica",
    "razao_media_7d_media_28d",
)


def selecionar_injecoes(controle):
    """Seleciona, com semente fixa, uma observação por produto."""

    candidatos = controle.loc[
        (~controle["aumento_atipico"])
        & (controle["quantidade_dia"] > 0)
        & (controle["vendas_positivas_anteriores"] >= MINIMO_VENDAS_POSITIVAS_MODELO_C)
        & np.isfinite(controle["media_positiva_historica"])
        & (controle["media_positiva_historica"] > 0)
    ].copy()
    candidatos = candidatos.sort_values(CHAVES_OBSERVACAO, kind="stable")

    rng = np.random.default_rng(SEMENTE_EXPERIMENTO)
    indices_por_produto = []
    for _, grupo in candidatos.groupby(
        ["filial", "codigo_produto"], sort=True
    ):
        indices_por_produto.append(rng.choice(grupo.index.to_numpy()))

    total_injecoes = AMOSTRAS_POR_INTENSIDADE * len(INTENSIDADES)
    if len(indices_por_produto) < total_injecoes:
        raise ErroModelo(
            "Não há produtos elegíveis suficientes para selecionar "
            f"{total_injecoes} injeções distintas."
        )

    selecionados = rng.choice(
        np.asarray(indices_por_produto), size=total_injecoes, replace=False
    )
    rotulos = np.repeat(
        list(INTENSIDADES), AMOSTRAS_POR_INTENSIDADE
    ).astype(object)
    rng.shuffle(rotulos)

    plano = pd.DataFrame(
        {
            "indice_observacao": selecionados,
            "intensidade": rotulos,
        }
    ).sort_values("indice_observacao", kind="stable")
    plano["multiplicador"] = plano["intensidade"].map(INTENSIDADES)
    plano = plano.merge(
        controle.reset_index(names="indice_observacao")[
            [
                "indice_observacao",
                *CHAVES_OBSERVACAO,
                "descricao_original",
                "quantidade_dia",
                "media_positiva_historica",
            ]
        ],
        on="indice_observacao",
        how="left",
        validate="one_to_one",
    )
    plano = plano.rename(
        columns={
            "quantidade_dia": "quantidade_original",
            "media_positiva_historica": "referencia_historica_anterior",
        }
    )
    base_relativa = plano[
        ["quantidade_original", "referencia_historica_anterior"]
    ].max(axis=1)
    plano["quantidade_sintetica"] = np.ceil(
        base_relativa * plano["multiplicador"]
    ).astype(float)
    if plano.duplicated(["filial", "codigo_produto"]).any():
        raise ErroModelo("A seleção incluiu mais de uma injeção por produto.")
    if not (
        plano["quantidade_sintetica"] > plano["quantidade_original"]
    ).all():
        raise ErroModelo("A regra de injeção não produziu somente aumentos.")
    return plano.reset_index(drop=True), len(candidatos), len(indices_por_produto)


def _atualizar_features_da_observacao(experimento, indices):
    """Atualiza somente campos dependentes da quantidade do próprio dia."""

    atual = experimento.loc[indices, "quantidade_dia"]
    media_28 = experimento.loc[indices, "media_anterior_28d"]
    media_positiva = experimento.loc[indices, "media_positiva_historica"]
    mediana_positiva = experimento.loc[
        indices, "mediana_positiva_historica"
    ]

    experimento.loc[indices, "log1p_quantidade_dia"] = np.log1p(atual)
    experimento.loc[indices, "diferenca_quantidade_media_28d"] = (
        atual - media_28
    )
    experimento.loc[indices, "razao_quantidade_media_28d"] = (
        atual / media_28
    )
    experimento.loc[indices, "desvio_relativo_media_28d"] = (
        (atual - media_28) / media_28
    )
    experimento.loc[
        indices, "desvio_log_media_positiva_historica"
    ] = np.log1p(atual) - np.log1p(media_positiva)
    experimento.loc[
        indices, "desvio_log_mediana_positiva_historica"
    ] = np.log1p(atual) - np.log1p(mediana_positiva)

    if "desvio_robusto_mediana_28d" in experimento:
        mad = experimento.loc[indices, "mad_positivo_anterior_28d"]
        mad_valido = mad > 0
        indices_mad = mad.index[mad_valido]
        experimento.loc[indices, "desvio_robusto_mediana_28d"] = 0.0
        experimento.loc[
            indices_mad, "desvio_robusto_mediana_28d"
        ] = (
            experimento.loc[indices_mad, "quantidade_dia"]
            - experimento.loc[indices_mad, "mediana_positiva_anterior_28d"]
        ) / (1.4826 * experimento.loc[indices_mad, "mad_positivo_anterior_28d"])


def injetar_aumentos(controle):
    """Cria a cópia experimental sem recalcular ou contaminar históricos."""

    experimento = controle.copy(deep=True)
    plano, candidatos, produtos_candidatos = selecionar_injecoes(controle)
    mapa_quantidades = plano.set_index("indice_observacao")[
        "quantidade_sintetica"
    ]
    indices = mapa_quantidades.index
    experimento.loc[indices, "quantidade_dia"] = mapa_quantidades
    _atualizar_features_da_observacao(experimento, indices)

    for coluna in FEATURES_HISTORICAS_IMUTAVEIS:
        original = controle[coluna].to_numpy()
        modificado = experimento[coluna].to_numpy()
        if not np.array_equal(original, modificado, equal_nan=True):
            raise ErroModelo(
                f"A injeção alterou indevidamente a referência histórica {coluna}."
            )

    matriz = experimento.loc[:, FEATURES_MODELO_C].to_numpy(dtype=float)
    if not np.isfinite(matriz).all():
        raise ErroModelo("A matriz experimental contém NaN ou infinito.")
    return experimento, plano, candidatos, produtos_candidatos


def calcular_metricas(resultado, plano):
    """Calcula a matriz de confusão contra os rótulos sintéticos."""

    injetados = np.zeros(len(resultado), dtype=bool)
    injetados[plano["indice_observacao"].to_numpy()] = True
    previstos = resultado["aumento_atipico"].to_numpy(dtype=bool)

    tp = int(np.sum(injetados & previstos))
    fn = int(np.sum(injetados & ~previstos))
    fp = int(np.sum(~injetados & previstos))
    tn = int(np.sum(~injetados & ~previstos))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    especificidade = tn / (tn + fp) if tn + fp else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return OrderedDict(
        TP=tp,
        FP=fp,
        FN=fn,
        TN=tn,
        precision=precision,
        recall=recall,
        f1_score=f1,
        especificidade=especificidade,
        taxa_deteccao_injetadas=recall,
    )


def detalhar_injecoes(resultado, plano):
    """Anexa previsões, scores e recall por intensidade ao plano."""

    colunas_resultado = [
        "classificacao_isolation_forest",
        "anomalia",
        "aumento_atipico",
        "decision_function",
        "score_samples",
    ]
    previsoes = resultado.loc[
        plano["indice_observacao"], colunas_resultado
    ].reset_index(drop=True)
    detalhes = pd.concat(
        [plano.reset_index(drop=True), previsoes], axis=1
    ).rename(columns={"aumento_atipico": "detectada_como_aumento"})
    recall_intensidade = (
        detalhes.groupby(
            ["intensidade", "multiplicador"], as_index=False, sort=False
        )
        .agg(
            injetadas=("detectada_como_aumento", "size"),
            detectadas=("detectada_como_aumento", "sum"),
        )
        .assign(
            recall=lambda dados: dados["detectadas"] / dados["injetadas"]
        )
    )
    ordem = {nome: posicao for posicao, nome in enumerate(INTENSIDADES)}
    recall_intensidade["ordem"] = recall_intensidade["intensidade"].map(ordem)
    recall_intensidade = recall_intensidade.sort_values("ordem").drop(
        columns="ordem"
    )
    return detalhes, recall_intensidade


def comparar_controle_experimento(controle, experimento, plano):
    injetados = np.zeros(len(experimento), dtype=bool)
    injetados[plano["indice_observacao"].to_numpy()] = True
    return pd.DataFrame(
        [
            {
                "cenario": "controle",
                "observacoes": len(controle),
                "outliers": int(controle["anomalia"].sum()),
                "aumentos_atipicos": int(controle["aumento_atipico"].sum()),
                "aumentos_em_nao_injetadas": int(
                    controle.loc[~injetados, "aumento_atipico"].sum()
                ),
            },
            {
                "cenario": "experimento",
                "observacoes": len(experimento),
                "outliers": int(experimento["anomalia"].sum()),
                "aumentos_atipicos": int(experimento["aumento_atipico"].sum()),
                "aumentos_em_nao_injetadas": int(
                    experimento.loc[~injetados, "aumento_atipico"].sum()
                ),
            },
        ]
    )


def _executar_experimento(controle):
    observacoes, plano, candidatos, produtos_candidatos = injetar_aumentos(
        controle
    )
    modelo, resultado = treinar_modelo_c(observacoes)
    metricas = calcular_metricas(resultado, plano)
    detalhes, recall_intensidade = detalhar_injecoes(resultado, plano)
    return {
        "modelo": modelo,
        "resultado": resultado,
        "plano": plano,
        "detalhes": detalhes,
        "recall_intensidade": recall_intensidade,
        "metricas": metricas,
        "candidatos": candidatos,
        "produtos_candidatos": produtos_candidatos,
    }


def verificar_reprodutibilidade(primeiro, segundo):
    mesmo_plano = primeiro["plano"].equals(segundo["plano"])
    mesmas_classes = np.array_equal(
        primeiro["resultado"]["classificacao_isolation_forest"].to_numpy(),
        segundo["resultado"]["classificacao_isolation_forest"].to_numpy(),
    )
    diferenca_decision = float(
        np.max(
            np.abs(
                primeiro["resultado"]["decision_function"].to_numpy()
                - segundo["resultado"]["decision_function"].to_numpy()
            )
        )
    )
    diferenca_score = float(
        np.max(
            np.abs(
                primeiro["resultado"]["score_samples"].to_numpy()
                - segundo["resultado"]["score_samples"].to_numpy()
            )
        )
    )
    return OrderedDict(
        selecao_e_injecao_identicas=mesmo_plano,
        classificacoes_identicas=mesmas_classes,
        diferenca_maxima_decision_function=diferenca_decision,
        diferenca_maxima_score_samples=diferenca_score,
        resultados_identicos=(
            mesmo_plano
            and mesmas_classes
            and diferenca_decision == 0.0
            and diferenca_score == 0.0
        ),
    )


def executar_validacao():
    """Executa controle e duas repetições idênticas do experimento."""

    definicao = executar_definicao()
    controle = definicao["resultado_c"].copy().reset_index(drop=True)
    primeiro = _executar_experimento(controle)
    segundo = _executar_experimento(controle)
    reproducibilidade = verificar_reprodutibilidade(primeiro, segundo)
    if not reproducibilidade["resultados_identicos"]:
        raise ErroModelo("A validação sintética não foi reproduzível.")

    primeiro["controle"] = controle
    primeiro["comparacao"] = comparar_controle_experimento(
        controle, primeiro["resultado"], primeiro["plano"]
    )
    primeiro["reprodutibilidade"] = reproducibilidade
    primeiro["banco"] = definicao["comparacao_ab"]["diagnostico"]["banco"]
    primeiro["somente_leitura"] = definicao["comparacao_ab"]["diagnostico"][
        "somente_leitura"
    ]
    return primeiro


def _imprimir_mapa(mapa, percentual=False):
    for chave, valor in mapa.items():
        if isinstance(valor, (float, np.floating)):
            sufixo = "%" if percentual else ""
            escala = 100 if percentual else 1
            print(f"- {chave}: {float(valor) * escala:.4f}{sufixo}")
        else:
            print(f"- {chave}: {valor}")


def _imprimir_exemplos(titulo, detalhes):
    colunas = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "data",
        "quantidade_original",
        "quantidade_sintetica",
        "intensidade",
        "referencia_historica_anterior",
        "classificacao_isolation_forest",
        "decision_function",
        "score_samples",
    ]
    print(f"\n{titulo}")
    if detalhes.empty:
        print("- Nenhum exemplo neste grupo.")
    else:
        print(detalhes[colunas].head(10).to_string(index=False))


def imprimir_relatorio(relatorio):
    detalhes = relatorio["detalhes"]
    detectadas = detalhes.loc[detalhes["detectada_como_aumento"]].sort_values(
        "decision_function"
    )
    nao_detectadas = detalhes.loc[
        ~detalhes["detectada_como_aumento"]
    ].sort_values(["multiplicador", "decision_function"], ascending=[False, True])

    print("1. SEGURANÇA E MODELO CONGELADO")
    print(f"- Banco: {relatorio['banco']}")
    print(f"- transaction_read_only: {relatorio['somente_leitura']}")
    print(f"- Features: {', '.join(FEATURES_MODELO_C)}")
    print(f"- Parâmetros: {PARAMETROS_MODELO}")
    print(f"- Histórico positivo mínimo: {MINIMO_VENDAS_POSITIVAS_MODELO_C}")
    print("- Validação integralmente em memória; nenhuma persistência realizada.")

    print("\n2. REGRA DE SELEÇÃO E INJEÇÃO")
    print(f"- Observações avaliadas: {len(relatorio['resultado'])}")
    print(f"- Observações candidatas: {relatorio['candidatos']}")
    print(f"- Produtos candidatos distintos: {relatorio['produtos_candidatos']}")
    print(f"- Anomalias injetadas: {len(relatorio['plano'])}")
    print("- Uma observação por produto; somente linhas que não eram aumento no controle.")
    print("- Quantidade e multiplicadores foram fixados antes da avaliação das métricas.")
    print("- Quantidade sintética = ceil(max(original, média positiva anterior) × multiplicador).")
    for nome, multiplicador in INTENSIDADES.items():
        print(f"- {nome}: {AMOSTRAS_POR_INTENSIDADE} injeções, multiplicador {multiplicador:.1f}x")
    print("- A referência histórica de cada linha e de datas posteriores permaneceu igual à série real.")

    print("\n3. MATRIZ DE CONFUSÃO E MÉTRICAS")
    _imprimir_mapa(relatorio["metricas"])

    print("\n4. RECALL POR INTENSIDADE")
    tabela_recall = relatorio["recall_intensidade"].copy()
    tabela_recall["recall_percentual"] = tabela_recall["recall"] * 100
    print(
        tabela_recall[
            ["intensidade", "multiplicador", "injetadas", "detectadas", "recall_percentual"]
        ].to_string(index=False)
    )

    print("\n5. CONTROLE × EXPERIMENTO")
    print(relatorio["comparacao"].to_string(index=False))

    _imprimir_exemplos("6. EXEMPLOS DETECTADOS", detectadas)
    _imprimir_exemplos("7. EXEMPLOS NÃO DETECTADOS", nao_detectadas)

    print("\n8. REPRODUTIBILIDADE")
    _imprimir_mapa(relatorio["reprodutibilidade"])

    print("\n9. LIMITAÇÕES METODOLÓGICAS")
    print("- FP significa negativo apenas perante o rótulo sintético; dados reais podem conter aumentos legítimos desconhecidos.")
    print("- Precision, especificidade e a matriz completa devem ser interpretadas com essa limitação.")
    print("- O recall das injeções é a medida direta de recuperação dos aumentos conhecidos.")
    print("- O Isolation Forest foi reajustado no conjunto experimental, como método não supervisionado, sem mudar parâmetros ou limiar.")
    print("- Nenhum comportamento atípico permite concluir doença, surto ou automedicação comprovada.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar_validacao())
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("VALIDAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
