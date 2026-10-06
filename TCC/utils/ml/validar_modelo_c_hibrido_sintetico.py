"""Valida em memória o Modelo C híbrido com aumentos sintéticos conhecidos."""

from collections import OrderedDict
import sys

import numpy as np
import pandas as pd
from sqlalchemy.exc import SQLAlchemyError

from utils.chave_analitica import carregar_cadastro_dcb
from utils.ml.definir_modelo_validacao import (
    FEATURES_MODELO_C,
    MINIMO_VENDAS_POSITIVAS_MODELO_C,
    PARAMETROS_MODELO,
    ErroModelo,
    treinar_modelo_c,
)
from utils.ml.diagnosticar_series import (
    ErroDiagnostico,
    _confirmar_conexao,
    _criar_engine_somente_leitura,
    carregar_agregacao_diaria,
)
from utils.ml.modelo_c_chave_analitica import (
    CHAVES_SERIE,
    construir_diagnostico_chave,
    executar_modelo_c_hibrido_em_diagnostico,
    resumir_execucao_hibrida,
)


SEED = 42
INJECOES_POR_FATOR = 150
FATORES = OrderedDict((("1.5x", 1.5), ("3x", 3.0), ("6x", 6.0)))
TOTAL_INJECOES = INJECOES_POR_FATOR * len(FATORES)
CHAVES_OBSERVACAO = [*CHAVES_SERIE, "data"]

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
    "mediana_positiva_anterior_28d",
    "mad_positivo_anterior_28d",
)

SQL_INTEGRIDADE = """
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
INTEGRIDADE_ESPERADA = {
    "importacoes": 2,
    "produtos": 6110,
    "vendas": 56756,
    "itens": 108166,
    "quantidade": 158385,
    "divergencias_descricao": 1,
}
UNIVERSO_ESPERADO = {
    "chaves": 1094,
    "modelaveis": 520,
    "historico_insuficiente": 574,
    "observacoes": 26342,
}

BASELINE_ANTIGO = {
    "TP": 353,
    "FP": 1539,
    "FN": 97,
    "TN": 26965,
    "precision": 0.186575,
    "recall": 0.784444,
    "f1": 0.301452,
    "specificity": 0.946008,
    "recall_por_fator": {"1.5x": 0.42, "3x": 0.933333, "6x": 1.0},
}


def _integridade_banco():
    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            _confirmar_conexao(conexao)
            from sqlalchemy import text

            return dict(conexao.execute(text(SQL_INTEGRIDADE)).mappings().one())
    finally:
        engine.dispose()


def validar_controle_hibrido(controle, diagnostico):
    """Garante que a entrada sintética corresponde ao universo híbrido 10G."""

    chaves = diagnostico["catalogo_chaves"]
    chaves_modelaveis = controle[list(CHAVES_SERIE)].drop_duplicates()
    resumo = resumir_execucao_hibrida(
        {
            "diagnostico": diagnostico,
            "resultado": controle,
        }
    )["geral"]
    observacoes_por_tipo = controle["tipo_chave"].value_counts().to_dict()
    if diagnostico.get("banco") != "tcc_medicamentos":
        raise ErroModelo("A validação deve usar exclusivamente tcc_medicamentos.")
    if diagnostico.get("somente_leitura") != "on":
        raise ErroModelo("tcc_medicamentos não está em modo somente leitura.")
    atual = {
        "chaves": int(chaves[list(CHAVES_SERIE)].drop_duplicates().shape[0]),
        "modelaveis": int(len(chaves_modelaveis)),
        "historico_insuficiente": int(
            chaves[list(CHAVES_SERIE)].drop_duplicates().shape[0]
            - len(chaves_modelaveis)
        ),
        "observacoes": len(controle),
    }
    if atual != UNIVERSO_ESPERADO:
        raise ErroModelo(
            f"Universo híbrido diverge do Modelo C validado: {atual}."
        )
    if resumo["quantidade_elegivel_antes"] != 100902.0 or resumo[
        "quantidade_agregada"
    ] != 100902.0:
        raise ErroModelo("A soma elegível deve fechar em 100.902 unidades.")
    if controle.duplicated(CHAVES_OBSERVACAO).any():
        raise ErroModelo("Há observações híbridas chave-data duplicadas.")
    if controle["chave_analitica"].isna().any():
        raise ErroModelo("Há observações sem chave analítica.")
    if not set(controle["tipo_chave"].unique()).issubset({"DCB", "PRODUTO"}):
        raise ErroModelo("Tipo de chave inesperado no controle.")
    if int(observacoes_por_tipo.get("DCB", 0)) + int(
        observacoes_por_tipo.get("PRODUTO", 0)
    ) != len(controle):
        raise ErroModelo("Tipo de chave ausente em observações do controle.")
    if not (controle["vendas_positivas_anteriores"] >= 7).all():
        raise ErroModelo("O controle contém histórico positivo inferior a 7 dias.")
    return atual


def selecionar_injecoes(controle):
    """Seleciona até uma observação por chave e distribui três fatores."""

    candidatos = controle.loc[
        (~controle["aumento_atipico"])
        & (controle["quantidade_dia"] > 0)
        & (
            controle["vendas_positivas_anteriores"]
            >= MINIMO_VENDAS_POSITIVAS_MODELO_C
        )
        & np.isfinite(controle["media_positiva_historica"])
        & (controle["media_positiva_historica"] > 0)
    ].copy()
    candidatos["_chave_ordem"] = candidatos["chave_analitica"].astype(str)
    candidatos = candidatos.sort_values(
        ["tipo_chave", "_chave_ordem", "data"], kind="stable"
    )

    rng = np.random.default_rng(SEED)
    indices_por_chave = [
        rng.choice(grupo.index.to_numpy())
        for _, grupo in candidatos.groupby(
            list(CHAVES_SERIE), sort=False, dropna=False
        )
    ]
    if len(indices_por_chave) < TOTAL_INJECOES:
        raise ErroModelo(
            f"Amostra insuficiente: há {len(indices_por_chave)} chaves com "
            f"candidatos para {TOTAL_INJECOES} injeções distintas."
        )

    indices_selecionados = rng.choice(
        np.asarray(indices_por_chave), size=TOTAL_INJECOES, replace=False
    )
    fatores = np.repeat(list(FATORES), INJECOES_POR_FATOR).astype(object)
    rng.shuffle(fatores)
    plano = pd.DataFrame(
        {
            "indice_observacao": indices_selecionados,
            "fator": fatores,
        }
    ).sort_values("indice_observacao", kind="stable")
    plano["multiplicador"] = plano["fator"].map(FATORES)

    controle_com_indice = controle.reset_index(drop=True).reset_index(
        names="indice_observacao"
    )
    metadados = [
        "indice_observacao",
        *CHAVES_OBSERVACAO,
        "quantidade_dia",
        "media_positiva_historica",
        "tipo_chave",
        "chave_analitica",
        "codigos_produto",
        "descricoes_associadas",
        "nom_dcb",
        "codigo_produto",
        "descricao_original",
    ]
    metadados = list(dict.fromkeys(coluna for coluna in metadados if coluna in controle_com_indice))
    plano = plano.merge(
        controle_com_indice[metadados],
        on="indice_observacao",
        how="left",
        validate="one_to_one",
    ).rename(
        columns={
            "quantidade_dia": "quantidade_original",
            "media_positiva_historica": "referencia_historica_anterior",
        }
    )
    base = np.maximum(
        plano["quantidade_original"].to_numpy(dtype=float),
        plano["referencia_historica_anterior"].to_numpy(dtype=float),
    )
    plano["quantidade_sintetica"] = np.ceil(
        base * plano["multiplicador"].to_numpy(dtype=float)
    )
    if plano.duplicated(list(CHAVES_SERIE)).any():
        raise ErroModelo("Foi selecionada mais de uma injeção por chave analítica.")
    if not (plano["quantidade_sintetica"] > plano["quantidade_original"]).all():
        raise ErroModelo("A injeção não produziu somente aumentos.")
    return plano.reset_index(drop=True), len(candidatos), len(indices_por_chave)


def _atualizar_features_da_observacao(experimento, indices):
    """Atualiza só as duas features que dependem da quantidade no próprio dia."""

    atual = experimento.loc[indices, "quantidade_dia"]
    media = experimento.loc[indices, "media_positiva_historica"]
    mediana = experimento.loc[indices, "mediana_positiva_historica"]
    experimento.loc[indices, "desvio_log_media_positiva_historica"] = (
        np.log1p(atual) - np.log1p(media)
    )
    experimento.loc[indices, "desvio_log_mediana_positiva_historica"] = (
        np.log1p(atual) - np.log1p(mediana)
    )


def injetar_aumentos(controle):
    """Devolve cópia experimental; históricos e demais datas ficam congelados."""

    base = controle.reset_index(drop=True).copy(deep=True)
    original = base.copy(deep=True)
    plano, candidatos, chaves_candidatas = selecionar_injecoes(base)
    novas_quantidades = plano.set_index("indice_observacao")[
        "quantidade_sintetica"
    ]
    indices = novas_quantidades.index
    experimento = base.copy(deep=True)
    experimento.loc[indices, "quantidade_dia"] = novas_quantidades
    _atualizar_features_da_observacao(experimento, indices)

    ausentes = set(FEATURES_HISTORICAS_IMUTAVEIS).difference(original.columns)
    if ausentes:
        raise ErroModelo(
            "O resultado C não possui os campos de histórico esperados: "
            + ", ".join(sorted(ausentes))
        )
    for coluna in FEATURES_HISTORICAS_IMUTAVEIS:
        if not np.array_equal(
            original[coluna].to_numpy(),
            experimento[coluna].to_numpy(),
            equal_nan=True,
        ):
            raise ErroModelo(f"A injeção alterou o histórico {coluna}.")

    for coluna in FEATURES_MODELO_C[2:]:
        if not np.array_equal(
            original[coluna].to_numpy(),
            experimento[coluna].to_numpy(),
            equal_nan=True,
        ):
            raise ErroModelo(f"A injeção alterou uma feature histórica: {coluna}.")
    matriz = experimento.loc[:, FEATURES_MODELO_C].to_numpy(dtype=float)
    if not np.isfinite(matriz).all():
        raise ErroModelo("A matriz sintética contém NaN ou infinito.")
    campos_intencionalmente_alterados = [
        *FEATURES_MODELO_C[:2],
        "quantidade_dia",
    ]
    if not original.drop(columns=campos_intencionalmente_alterados).equals(
        experimento.drop(columns=campos_intencionalmente_alterados)
    ):
        raise ErroModelo("A injeção alterou identificadores ou metadados.")
    return experimento, plano, candidatos, chaves_candidatas


def calcular_metricas(resultado, plano):
    """Calcula TP/FP/FN/TN e métricas padrão para o rótulo sintético."""

    injetados = np.zeros(len(resultado), dtype=bool)
    injetados[plano["indice_observacao"].to_numpy(dtype=int)] = True
    previstos = resultado["aumento_atipico"].to_numpy(dtype=bool)
    tp = int(np.sum(injetados & previstos))
    fn = int(np.sum(injetados & ~previstos))
    fp = int(np.sum(~injetados & previstos))
    tn = int(np.sum(~injetados & ~previstos))
    precisao = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    especificidade = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * precisao * recall / (precisao + recall) if precisao + recall else 0.0
    return OrderedDict(
        TP=tp,
        FP=fp,
        FN=fn,
        TN=tn,
        precision=precisao,
        recall=recall,
        f1=f1,
        specificity=especificidade,
        taxa_deteccao_injetadas=recall,
    )


def _executar_experimento(controle):
    experimento, plano, candidatos, chaves_candidatas = injetar_aumentos(controle)
    modelo, resultado = treinar_modelo_c(experimento)
    injetados = np.zeros(len(resultado), dtype=bool)
    injetados[plano["indice_observacao"].to_numpy(dtype=int)] = True
    detalhes = plano.copy()
    resultados_alvo = resultado.loc[
        plano["indice_observacao"].to_numpy(dtype=int),
        [
            "classificacao_isolation_forest",
            "anomalia",
            "aumento_atipico",
            "score_samples",
            "decision_function",
        ],
    ].reset_index(drop=True)
    detalhes = pd.concat([detalhes.reset_index(drop=True), resultados_alvo], axis=1)
    detalhes = detalhes.rename(
        columns={"aumento_atipico": "detectada_como_aumento"}
    )
    linhas_por_fator = []
    for fator, multiplicador in FATORES.items():
        grupo = detalhes.loc[detalhes["fator"] == fator]
        detectadas = int(grupo["detectada_como_aumento"].sum())
        linhas_por_fator.append(
            {
                "fator": fator,
                "multiplicador": multiplicador,
                "injetadas": len(grupo),
                "detectadas": detectadas,
                "nao_detectadas": len(grupo) - detectadas,
                "recall": detectadas / len(grupo) if len(grupo) else 0.0,
            }
        )
    por_fator = pd.DataFrame(linhas_por_fator)
    if len(plano) != TOTAL_INJECOES:
        raise ErroModelo("A validação precisa conter exatamente 450 injeções.")
    if plano[list(CHAVES_SERIE)].drop_duplicates().shape[0] != TOTAL_INJECOES:
        raise ErroModelo("A amostra deve conter 450 chaves analíticas distintas.")
    if por_fator["injetadas"].tolist() != [150, 150, 150]:
        raise ErroModelo("A distribuição por fator deve ser 150/150/150.")

    por_tipo = []
    for tipo in ("DCB", "PRODUTO"):
        grupo = detalhes.loc[detalhes["tipo_chave"] == tipo]
        tp = int(grupo["detectada_como_aumento"].sum())
        por_tipo.append(
            {
                "tipo_chave": tipo,
                "injecoes": len(grupo),
                "TP": tp,
                "FN": len(grupo) - tp,
                "recall": tp / len(grupo) if len(grupo) else 0.0,
            }
        )
    metricas = calcular_metricas(resultado, plano)
    if int(injetados.sum()) != TOTAL_INJECOES:
        raise ErroModelo("A máscara não contabilizou as 450 injeções.")
    return {
        "modelo": modelo,
        "experimento": experimento,
        "resultado": resultado,
        "plano": plano,
        "detalhes": detalhes,
        "recall_por_fator": por_fator,
        "resultados_por_tipo": pd.DataFrame(por_tipo),
        "metricas": metricas,
        "candidatos": candidatos,
        "chaves_candidatas": chaves_candidatas,
    }


def verificar_reprodutibilidade(primeiro, segundo, tolerancia=1e-12):
    """Compara seleção, quantidades, métricas, rótulos e scores."""

    planos_iguais = primeiro["plano"].equals(segundo["plano"])
    metricas_iguais = primeiro["metricas"] == segundo["metricas"]
    fatores_iguais = primeiro["recall_por_fator"].equals(
        segundo["recall_por_fator"]
    )
    labels_iguais = np.array_equal(
        primeiro["resultado"]["classificacao_isolation_forest"].to_numpy(),
        segundo["resultado"]["classificacao_isolation_forest"].to_numpy(),
    )
    score_iguais = all(
        np.allclose(
            primeiro["resultado"][coluna].to_numpy(dtype=float),
            segundo["resultado"][coluna].to_numpy(dtype=float),
            rtol=tolerancia,
            atol=tolerancia,
        )
        for coluna in ("score_samples", "decision_function")
    )
    return OrderedDict(
        mesmas_injecoes_e_quantidades=bool(planos_iguais),
        mesma_matriz_e_metricas=bool(metricas_iguais),
        mesmos_resultados_por_fator=bool(fatores_iguais),
        mesmas_classificacoes=bool(labels_iguais),
        scores_dentro_tolerancia=bool(score_iguais),
        resultados_identicos=bool(
            planos_iguais
            and metricas_iguais
            and fatores_iguais
            and labels_iguais
            and score_iguais
        ),
    )


def _validar_integridade_inicial():
    antes = _integridade_banco()
    if antes != INTEGRIDADE_ESPERADA:
        raise ErroModelo(
            f"Integridade inicial do banco divergente; interrompido: {antes}."
        )
    return antes


def executar_validacao_hibrida():
    """Executa controle e dois experimentos sem escrita, no universo final."""

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
        raise ErroModelo("Os parâmetros do Modelo C foram alterados.")
    integridade_antes = _validar_integridade_inicial()

    dados = carregar_agregacao_diaria()
    diagnostico = construir_diagnostico_chave(
        dados["serie_vendas"],
        dados["catalogo_classificado"],
        carregar_cadastro_dcb(),
    )
    diagnostico["banco"] = dados["banco"]
    diagnostico["somente_leitura"] = dados["somente_leitura"]
    controle_pipeline = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
    controle = controle_pipeline["resultado"].reset_index(drop=True).copy(deep=True)
    universo = validar_controle_hibrido(controle, diagnostico)

    primeiro = _executar_experimento(controle)
    segundo = _executar_experimento(controle)
    reproducibilidade = verificar_reprodutibilidade(primeiro, segundo)
    if not reproducibilidade["resultados_identicos"]:
        raise ErroModelo("A validação sintética híbrida não foi reproduzível.")

    integridade_depois = _integridade_banco()
    if integridade_depois != integridade_antes:
        raise ErroModelo("A validação alterou a integridade do banco.")
    primeiro.update(
        {
            "controle": controle,
            "controle_metricas": resumir_execucao_hibrida(
                controle_pipeline
            ),
            "comparacao": pd.DataFrame(
                [
                    {
                        "cenario": "controle",
                        "observacoes": len(controle),
                        "outliers": int(controle["anomalia"].sum()),
                        "aumentos_atipicos": int(
                            controle["aumento_atipico"].sum()
                        ),
                    },
                    {
                        "cenario": "experimento",
                        "observacoes": len(primeiro["resultado"]),
                        "outliers": int(primeiro["resultado"]["anomalia"].sum()),
                        "aumentos_atipicos": int(
                            primeiro["resultado"]["aumento_atipico"].sum()
                        ),
                    },
                ]
            ),
            "reprodutibilidade": reproducibilidade,
            "universo": universo,
            "integridade_antes": integridade_antes,
            "integridade_depois": integridade_depois,
            "banco": dados["banco"],
            "somente_leitura": dados["somente_leitura"],
            "parametros": controle_pipeline["modelo"].get_params(deep=False),
        }
    )
    return primeiro


def imprimir_relatorio(relatorio):
    print("1. UNIVERSO DO MODELO C HÍBRIDO ANTES DA INJEÇÃO")
    print(f"Banco: {relatorio['banco']} | read-only: {relatorio['somente_leitura']}")
    print(f"Chaves: {relatorio['universo']['chaves']}")
    print(f"Chaves modeláveis: {relatorio['universo']['modelaveis']}")
    print(f"Histórico insuficiente: {relatorio['universo']['historico_insuficiente']}")
    print(f"Observações: {relatorio['universo']['observacoes']}")
    print(f"Features: {FEATURES_MODELO_C}")
    print(f"Mínimo de positivos anteriores: {MINIMO_VENDAS_POSITIVAS_MODELO_C}")
    print(f"Parâmetros: {relatorio['parametros']}")

    print("\n2. SELEÇÃO E INJEÇÃO")
    print(f"Candidatos observação: {relatorio['candidatos']}")
    print(f"Chaves candidatas: {relatorio['chaves_candidatas']}")
    print(f"Injeções: {len(relatorio['plano'])}")
    print("Seed: 42; uma observação e no máximo uma injeção por chave.")
    print("Regra: ceil(max(quantidade_original, média_positiva_anterior) × fator).")
    print(relatorio["plano"]["fator"].value_counts().sort_index().to_string())

    print("\n3. MATRIZ DE CONFUSÃO E MÉTRICAS")
    for chave, valor in relatorio["metricas"].items():
        print(f"{chave}: {valor:.6f}" if isinstance(valor, float) else f"{chave}: {valor}")
    print("precision = TP/(TP+FP); recall = TP/(TP+FN)")
    print("F1 = 2·precision·recall/(precision+recall); specificity = TN/(TN+FP)")

    print("\n4. RECALL POR FATOR")
    tabela_fatores = relatorio["recall_por_fator"].copy()
    tabela_fatores["recall_percentual"] = tabela_fatores["recall"] * 100
    print(
        tabela_fatores[
            [
                "fator",
                "multiplicador",
                "injetadas",
                "detectadas",
                "nao_detectadas",
                "recall_percentual",
            ]
        ].to_string(index=False)
    )
    print("\n5. RESULTADOS POR TIPO DE CHAVE")
    print(relatorio["resultados_por_tipo"].to_string(index=False))
    print("\n6. CONTROLE × EXPERIMENTO")
    print(relatorio["comparacao"].to_string(index=False))

    print("\n7. COMPARAÇÃO QUANTITATIVA COM O BASELINE ANTIGO POR PRODUTO")
    for campo in ("TP", "FP", "FN", "TN"):
        print(f"{campo}: antigo={BASELINE_ANTIGO[campo]} | híbrido={relatorio['metricas'][campo]}")
    for campo in ("precision", "recall", "f1", "specificity"):
        print(
            f"{campo}: antigo={BASELINE_ANTIGO[campo]:.4%} | "
            f"híbrido={relatorio['metricas'][campo]:.4%}"
        )
    print("Recall por fator antigo:", BASELINE_ANTIGO["recall_por_fator"])
    print("Comparação numérica não prova superioridade metodológica.")

    print("\n8. LIMITAÇÕES E REPRODUTIBILIDADE")
    print("Rótulos negativos significam apenas 'não injetado'; podem incluir aumentos reais desconhecidos.")
    print("Logo, FP/precision contra esse rótulo têm interpretação limitada.")
    print("A validação sintética não indica doença, surto ou automedicação comprovada.")
    print(relatorio["reprodutibilidade"])
    print("\nIntegridade DB antes/depois:")
    print(relatorio["integridade_antes"])
    print("preservada:", relatorio["integridade_antes"] == relatorio["integridade_depois"])
    print("Nenhum dado sintético foi persistido.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        relatorio = executar_validacao_hibrida()
        imprimir_relatorio(relatorio)
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("VALIDAÇÃO SINTÉTICA HÍBRIDA INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
