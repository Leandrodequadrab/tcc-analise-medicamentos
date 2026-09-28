"""Valida a integracao 10D.4 sem persistir resultados do Modelo C."""

from __future__ import annotations

import sys
from unittest.mock import patch

import numpy as np
import pandas as pd

import app as aplicacao
from utils.classificacao_medicamentos import MED_CONTROLADO, NAO_MED

from . import servico_modelo_c
from .definir_modelo_validacao import executar_modelo_c


def _metricas(pipeline):
    resultado = pipeline["resultado"]
    diagnostico = pipeline["diagnostico"]
    modelaveis = len(
        resultado[["filial", "codigo_produto"]].drop_duplicates()
    )
    universo = diagnostico["universo_operacional"]
    total_universo = (
        universo["produtos_elegiveis"]
        if universo["filtro_aplicado"]
        else universo["produtos_catalogo"]
    )
    return {
        "produtos_universo": total_universo,
        "produtos_modelaveis": modelaveis,
        "historico_insuficiente": total_universo - modelaveis,
        "observacoes": len(resultado),
        "outliers": int(resultado["anomalia"].sum()),
        "aumentos": int(resultado["aumento_atipico"].sum()),
        "percentual_aumentos": float(resultado["aumento_atipico"].mean() * 100),
        "periodo": diagnostico["periodo"],
    }


def _reprodutivel(primeiro, segundo):
    colunas_chave = ["filial", "codigo_produto", "data"]
    a = primeiro["resultado"].sort_values(colunas_chave).reset_index(drop=True)
    b = segundo["resultado"].sort_values(colunas_chave).reset_index(drop=True)
    return {
        "mesmas_chaves": a[colunas_chave].equals(b[colunas_chave]),
        "mesmas_classificacoes": a[
            "classificacao_isolation_forest"
        ].equals(b["classificacao_isolation_forest"]),
        "mesmos_scores": bool(np.array_equal(a["score_samples"], b["score_samples"])),
        "mesmas_decisoes": bool(
            np.array_equal(a["decision_function"], b["decision_function"])
        ),
    }


def _testar_rotas(pipeline):
    catalogo = pipeline["diagnostico"]["catalogo_classificado"]
    resultado = pipeline["resultado"]
    marco = pd.Timestamp("2026-03-01")
    candidato = resultado.loc[resultado["data"] >= marco].iloc[0]
    controlado = catalogo.loc[
        catalogo["classificacao_operacional"] == MED_CONTROLADO
    ].iloc[0]
    nao_med = catalogo.loc[
        catalogo["classificacao_operacional"] == NAO_MED
    ].iloc[0]
    filial = int(candidato["filial"])
    produto = int(candidato["codigo_produto"])
    casos = {
        "sem_filtros": "/analise-ia",
        "filial": f"/analise-ia?filial={filial}",
        "produto_elegivel": f"/analise-ia?produto={produto}",
        "periodo": "/analise-ia?data_inicial=2026-03-01&data_final=2026-03-31",
        "combinado": (
            f"/analise-ia?filial={filial}&produto={produto}"
            "&data_inicial=2026-03-01&data_final=2026-03-31"
        ),
        "nao_med": (
            f"/analise-ia?filial={int(nao_med['filial'])}"
            f"&produto={int(nao_med['codigo_produto'])}"
        ),
        "controlado": (
            f"/analise-ia?filial={int(controlado['filial'])}"
            f"&produto={int(controlado['codigo_produto'])}"
        ),
    }
    respostas = {}
    with patch.object(
        servico_modelo_c, "executar_modelo_c", return_value=pipeline
    ):
        cliente = aplicacao.app.test_client()
        for nome, url in casos.items():
            resposta = cliente.get(url)
            respostas[nome] = {
                "status": resposta.status_code,
                "sem_erro_500": resposta.status_code != 500,
            }
    return respostas


def executar():
    baseline = executar_modelo_c(apenas_elegiveis_automedicacao=False)
    filtrado_1 = executar_modelo_c(apenas_elegiveis_automedicacao=True)
    filtrado_2 = executar_modelo_c(apenas_elegiveis_automedicacao=True)
    return {
        "baseline": _metricas(baseline),
        "filtrado": _metricas(filtrado_1),
        "universo": filtrado_1["diagnostico"]["universo_operacional"],
        "reprodutibilidade": _reprodutivel(filtrado_1, filtrado_2),
        "rotas": _testar_rotas(filtrado_1),
        "integridade": filtrado_1["diagnostico"]["resumo_banco"],
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    relatorio = executar()
    print("VALIDACAO DA INTEGRACAO 10D.4")
    for secao, valores in relatorio.items():
        print(f"\n{secao.upper()}")
        print(valores)


if __name__ == "__main__":
    main()
