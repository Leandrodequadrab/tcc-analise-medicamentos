"""Valida o Modelo C congelado com aumentos sintéticos conhecidos.

A validação usa cópias em memória. Cada quantidade injetada é comparada com
as referências calculadas sobre os dados reais anteriores; a injeção não é
propagada para o histórico de datas posteriores.
"""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .comparar_modelos_relativos import ErroModelo
from .definir_modelo_validacao import (
    FEATURES_MODELO_C,
    MINIMO_VENDAS_POSITIVAS_MODELO_C,
    PARAMETROS_MODELO,
    executar_modelo_c,
    treinar_modelo_c,
)
from .diagnosticar_series import (
    ErroDiagnostico,
    _confirmar_conexao,
    _criar_engine_somente_leitura,
)


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
PRODUTOS_ELEGIVEIS_ESPERADOS = 2414
PRODUTOS_MODELAVEIS_ESPERADOS = 980
OBSERVACOES_ESPERADAS = 28954
CONTAGENS_CLASSIFICACAO_ESPERADAS = {
    "MED_OUTRO": 1509,
    "MED_OTC_INTERNO": 768,
    "MED_ANTIMICROBIANO": 137,
    "MED_CONTROLADO": 390,
    "NAO_MED": 3290,
    "SEM_CLASSIFICACAO": 16,
}
CATEGORIAS_ELEGIVEIS = (
    "MED_OUTRO",
    "MED_OTC_INTERNO",
    "MED_ANTIMICROBIANO",
)

SQL_INTEGRIDADE = text(
    """
    SELECT
      (SELECT COUNT(*) FROM public.importacoes_rel0024) AS importacoes,
      (SELECT COUNT(*) FROM public.produtos_rel0024) AS produtos,
      (SELECT COUNT(*) FROM public.vendas_rel0024) AS vendas,
      (SELECT COUNT(*) FROM public.itens_rel0024) AS itens,
      (SELECT COALESCE(SUM(quantidade), 0) FROM public.itens_rel0024)
        AS quantidade,
      (SELECT COUNT(*) FROM public.divergencias_descricao_rel0024)
        AS divergencias_descricao
    """
)
INTEGRIDADE_ESPERADA = {
    "importacoes": 2,
    "produtos": 6110,
    "vendas": 56756,
    "itens": 108166,
    "quantidade": 158385,
    "divergencias_descricao": 1,
}


def _capturar_integridade_banco():
    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            _confirmar_conexao(conexao)
            return dict(conexao.execute(SQL_INTEGRIDADE).mappings().one())
    finally:
        engine.dispose()


def _validar_universo_final(execucao):
    """Falha antes de injetar se controle não for a saída exata da 10G."""

    diagnostico = execucao["diagnostico"]
    controle = execucao["resultado"]
    universo = diagnostico["universo_operacional"]
    contagens = universo["contagens_classificacao"]
    modelaveis = controle[["filial", "codigo_produto"]].drop_duplicates()
    contagens_observacoes = (
        controle["classificacao_operacional"].value_counts().to_dict()
    )

    if diagnostico["banco"] != "tcc_medicamentos":
        raise ErroModelo("A validação deve usar somente tcc_medicamentos.")
    if diagnostico["somente_leitura"] != "on":
        raise ErroModelo("tcc_medicamentos não está em modo somente leitura.")
    if universo["produtos_catalogo"] != 6110:
        raise ErroModelo("O catálogo Rel_0024 não contém os 6.110 produtos esperados.")
    periodo = diagnostico["resumo_banco"]
    if (
        periodo["periodo_inicial_importado"].isoformat() != "2025-04-01"
        or periodo["periodo_final_importado"].isoformat() != "2026-03-31"
    ):
        raise ErroModelo("O período não corresponde à base final da 10G.")
    if universo["produtos_elegiveis"] != PRODUTOS_ELEGIVEIS_ESPERADOS:
        raise ErroModelo("O universo elegível diverge dos 2.414 produtos da 10G.")
    if len(modelaveis) != PRODUTOS_MODELAVEIS_ESPERADOS:
        raise ErroModelo("O Modelo C não reproduziu os 980 produtos modeláveis da 10G.")
    if len(controle) != OBSERVACOES_ESPERADAS:
        raise ErroModelo("O Modelo C não reproduziu as 28.954 observações da 10G.")
    if any(contagens.get(chave, 0) != valor for chave, valor in
           CONTAGENS_CLASSIFICACAO_ESPERADAS.items()):
        raise ErroModelo("As contagens operacionais não coincidem com a 10G.")
    if not controle["elegivel_analise_automedicacao"].astype(bool).all():
        raise ErroModelo("Há observações inelegíveis no controle da validação.")
    if not set(controle["classificacao_operacional"].dropna().unique()).issubset(
        CATEGORIAS_ELEGIVEIS
    ):
        raise ErroModelo("Uma categoria excluída entrou no controle da validação.")
    if set(controle["filial"].unique()) != {6}:
        raise ErroModelo("O universo da validação não está restrito à filial 6.")
    if any(
        contagens_observacoes.get(categoria, 0) != 0
        for categoria in ("MED_CONTROLADO", "NAO_MED", "SEM_CLASSIFICACAO")
    ):
        raise ErroModelo("Observações excluídas foram encontradas no Modelo C.")

    return {
        "produtos_elegiveis": universo["produtos_elegiveis"],
        "produtos_modelaveis": len(modelaveis),
        "observacoes_avaliadas": len(controle),
        "contagens_classificacao": contagens,
        "contagens_observacoes_avaliadas": contagens_observacoes,
    }


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
                "classificacao_operacional",
                "elegivel_analise_automedicacao",
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
            nao_detectadas=lambda dados: dados["injetadas"] - dados["detectadas"],
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


def _validar_protocolo_executado(experimento, universo):
    plano = experimento["plano"]
    if len(plano) != 450:
        raise ErroModelo(f"Esperadas 450 injeções, observadas {len(plano)}.")
    if plano["codigo_produto"].nunique() != 450:
        raise ErroModelo("As injeções não usaram 450 produtos distintos.")
    if plano.duplicated(["filial", "codigo_produto"]).any():
        raise ErroModelo("Foi encontrada mais de uma injeção por produto.")
    if not plano["elegivel_analise_automedicacao"].astype(bool).all():
        raise ErroModelo("A seleção incluiu produto inelegível.")
    if not set(plano["classificacao_operacional"].unique()).issubset(
        CATEGORIAS_ELEGIVEIS
    ):
        raise ErroModelo("A seleção incluiu categoria operacional excluída.")
    distribuicao = plano["multiplicador"].value_counts().to_dict()
    if any(distribuicao.get(fator, 0) != 150 for fator in (1.5, 3.0, 6.0)):
        raise ErroModelo("A alocação não contém exatamente 150 injeções por fator.")
    if len(experimento["resultado"]) != universo["observacoes_avaliadas"]:
        raise ErroModelo("A experimentação alterou o número de observações.")
    esperado = np.ceil(
        np.maximum(
            plano["quantidade_original"].to_numpy(dtype=float),
            plano["referencia_historica_anterior"].to_numpy(dtype=float),
        )
        * plano["multiplicador"].to_numpy(dtype=float)
    )
    if not np.array_equal(esperado, plano["quantidade_sintetica"].to_numpy()):
        raise ErroModelo("A quantidade não respeita a regra congelada da 10C.")
    metricas = experimento["metricas"]
    if metricas["TP"] + metricas["FN"] != 450:
        raise ErroModelo("A matriz de confusão não contabiliza as 450 injeções.")
    if sum(metricas[chave] for chave in ("TP", "FP", "FN", "TN")) != len(
        experimento["resultado"]
    ):
        raise ErroModelo("A matriz de confusão não cobre todas as observações.")
    por_fator = experimento["recall_intensidade"]
    if not (por_fator["injetadas"] == 150).all():
        raise ErroModelo("TP + FN não totaliza 150 para todos os fatores.")


def _resultados_por_classificacao(detalhes):
    linhas = []
    for categoria in CATEGORIAS_ELEGIVEIS:
        grupo = detalhes.loc[detalhes["classificacao_operacional"] == categoria]
        detectadas = int(grupo["detectada_como_aumento"].sum())
        linhas.append(
            {
                "classificacao_operacional": categoria,
                "injecoes": len(grupo),
                "detectadas": detectadas,
                "nao_detectadas": len(grupo) - detectadas,
                "recall": detectadas / len(grupo) if len(grupo) else 0.0,
            }
        )
    return pd.DataFrame(linhas)


def _distribuicao_mensal_injecoes(plano):
    meses = pd.period_range("2025-04", "2026-03", freq="M")
    contagens = plano.assign(mes=plano["data"].dt.to_period("M")).groupby(
        "mes"
    ).size()
    return pd.DataFrame(
        {
            "mes": meses.astype(str),
            "injecoes": [int(contagens.get(mes, 0)) for mes in meses],
        }
    )


def _analisar_falsos_negativos(detalhes):
    falsos_negativos = detalhes.loc[
        ~detalhes["detectada_como_aumento"]
    ].copy()
    resumo = []
    exemplos = {}
    for fator in (1.5, 3.0, 6.0):
        grupo = falsos_negativos.loc[
            falsos_negativos["multiplicador"] == fator
        ].sort_values("score_samples", ascending=True, kind="stable")
        resumo.append(
            {
                "fator": fator,
                "FN": len(grupo),
                "injetadas": 150,
                "proporcao_das_injetadas": len(grupo) / 150,
            }
        )
        exemplos[fator] = grupo[
            [
                "codigo_produto",
                "descricao_original",
                "classificacao_operacional",
                "data",
                "quantidade_original",
                "quantidade_sintetica",
                "referencia_historica_anterior",
                "multiplicador",
                "score_samples",
                "decision_function",
            ]
        ].head(5)
    return pd.DataFrame(resumo), exemplos


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
    """Executa a validação no universo final da 10G, sem comparar A/B."""

    parametros_esperados = {
        "n_estimators": 200,
        "max_samples": "auto",
        "contamination": "auto",
        "max_features": 1.0,
        "bootstrap": False,
        "random_state": 42,
        "n_jobs": -1,
    }
    if PARAMETROS_MODELO != parametros_esperados:
        raise ErroModelo(
            "Os parâmetros do Modelo C divergiram da configuração congelada."
        )

    integridade_antes = _capturar_integridade_banco()
    if integridade_antes != INTEGRIDADE_ESPERADA:
        raise ErroModelo(
            f"Integridade inicial divergente; validação interrompida: "
            f"{integridade_antes}."
        )

    modelo_c = executar_modelo_c(apenas_elegiveis_automedicacao=True)
    universo = _validar_universo_final(modelo_c)
    if modelo_c["modelo"].offset_ != -0.5:
        raise ErroModelo("O Modelo C não manteve offset_ = -0.5.")
    controle = modelo_c["resultado"].copy().reset_index(drop=True)
    primeiro = _executar_experimento(controle)
    segundo = _executar_experimento(controle)
    _validar_protocolo_executado(primeiro, universo)
    _validar_protocolo_executado(segundo, universo)
    reproducibilidade = verificar_reprodutibilidade(primeiro, segundo)
    if not reproducibilidade["resultados_identicos"]:
        raise ErroModelo("A validação sintética não foi reproduzível.")

    integridade_depois = _capturar_integridade_banco()
    if integridade_depois != integridade_antes:
        raise ErroModelo(
            "A validação modificou a base persistida; verifique imediatamente."
        )

    primeiro["controle"] = controle
    primeiro["comparacao"] = comparar_controle_experimento(
        controle, primeiro["resultado"], primeiro["plano"]
    )
    primeiro["reprodutibilidade"] = reproducibilidade
    primeiro["banco"] = modelo_c["diagnostico"]["banco"]
    primeiro["somente_leitura"] = modelo_c["diagnostico"]["somente_leitura"]
    primeiro["universo_final"] = universo
    primeiro["offset_controle"] = modelo_c["modelo"].offset_
    primeiro["integridade_antes"] = integridade_antes
    primeiro["integridade_depois"] = integridade_depois
    primeiro["resultados_classificacao"] = _resultados_por_classificacao(
        primeiro["detalhes"]
    )
    primeiro["distribuicao_mensal"] = _distribuicao_mensal_injecoes(
        primeiro["plano"]
    )
    (
        primeiro["falsos_negativos_por_fator"],
        primeiro["exemplos_falsos_negativos"],
    ) = _analisar_falsos_negativos(primeiro["detalhes"])
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

    universo = relatorio["universo_final"]
    print("1. UNIVERSO FINAL DA 10G E MODELO CONGELADO")
    print(f"- Banco: {relatorio['banco']}")
    print(f"- transaction_read_only: {relatorio['somente_leitura']}")
    print(f"- Produtos elegíveis: {universo['produtos_elegiveis']}")
    print(f"- Produtos modeláveis: {universo['produtos_modelaveis']}")
    print(f"- Observações avaliadas: {universo['observacoes_avaliadas']}")
    print("- Produtos do catálogo por classificação operacional (auditoria):")
    _imprimir_mapa(universo["contagens_classificacao"])
    print("- Observações avaliadas por classificação:")
    _imprimir_mapa(universo["contagens_observacoes_avaliadas"])
    print("- Observações avaliadas MED_CONTROLADO / NAO_MED / SEM_CLASSIFICACAO: 0")
    print(f"- Features: {', '.join(FEATURES_MODELO_C)}")
    print(f"- Parâmetros: {PARAMETROS_MODELO}")
    print(f"- offset_ do controle: {relatorio['offset_controle']}")
    print(f"- Histórico positivo mínimo: {MINIMO_VENDAS_POSITIVAS_MODELO_C}")
    print("- Validação integralmente em memória; nenhuma persistência realizada.")

    print("\n2. REGRA DE SELEÇÃO E INJEÇÃO")
    print(f"- Observações avaliadas: {len(relatorio['resultado'])}")
    print(f"- Observações candidatas: {relatorio['candidatos']}")
    print(f"- Produtos candidatos distintos: {relatorio['produtos_candidatos']}")
    print(f"- Anomalias injetadas: {len(relatorio['plano'])}")
    print("- Uma observação por produto; somente linhas que não eram aumento no controle.")
    print("- Quantidade e multiplicadores foram fixados antes da avaliação das métricas.")
    print("- Regra: ceil(max(quantidade original, média histórica positiva anterior) × fator).")
    print("- O fator incide sobre o maior valor, não necessariamente sobre a quantidade original.")
    for nome, multiplicador in INTENSIDADES.items():
        print(f"- {nome}: {AMOSTRAS_POR_INTENSIDADE} injeções, multiplicador {multiplicador:.1f}x")
    print("- A referência histórica de cada linha e de datas posteriores permaneceu igual à série real.")

    print("\n3. MATRIZ DE CONFUSÃO E MÉTRICAS")
    metricas = relatorio["metricas"]
    print(f"- TP: {metricas['TP']} | FP: {metricas['FP']} | FN: {metricas['FN']} | TN: {metricas['TN']}")
    for nome in ("precision", "recall", "f1_score", "especificidade", "taxa_deteccao_injetadas"):
        print(f"- {nome}: {metricas[nome]:.4%}")

    print("\n4. RECALL POR INTENSIDADE")
    tabela_recall = relatorio["recall_intensidade"].copy()
    tabela_recall["recall_percentual"] = tabela_recall["recall"] * 100
    print(
        tabela_recall[
            ["intensidade", "multiplicador", "injetadas", "detectadas", "nao_detectadas", "recall_percentual"]
        ].to_string(index=False)
    )

    print("\n5. INJEÇÕES POR CLASSIFICAÇÃO OPERACIONAL")
    print(relatorio["resultados_classificacao"].to_string(index=False))

    print("\n6. DISTRIBUIÇÃO MENSAL DAS INJEÇÕES")
    print(relatorio["distribuicao_mensal"].to_string(index=False))

    print("\n7. CONTROLE × EXPERIMENTO")
    print(relatorio["comparacao"].to_string(index=False))

    _imprimir_exemplos("8. EXEMPLOS DETECTADOS", detectadas)
    _imprimir_exemplos("9. EXEMPLOS NÃO DETECTADOS", nao_detectadas)
    print("\n10. FALSOS NEGATIVOS POR FATOR")
    print(relatorio["falsos_negativos_por_fator"].to_string(index=False))
    for fator, exemplos in relatorio["exemplos_falsos_negativos"].items():
        print(f"\nFalsos negativos — fator {fator:g}x (até 5 exemplos)")
        print("- Nenhum." if exemplos.empty else exemplos.to_string(index=False))

    print("\n11. REPRODUTIBILIDADE")
    _imprimir_mapa(relatorio["reprodutibilidade"])

    print("\n12. INTEGRIDADE DO BANCO (ANTES = DEPOIS)")
    print(relatorio["integridade_antes"])
    print(f"- Integridade preservada: {relatorio['integridade_antes'] == relatorio['integridade_depois']}")

    print("\n13. LIMITAÇÕES METODOLÓGICAS")
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
