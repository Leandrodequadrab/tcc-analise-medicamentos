"""Executa e compara duas vezes o Modelo C por chave analítica híbrida."""

import sys

from sqlalchemy.exc import SQLAlchemyError

from utils.chave_analitica import carregar_cadastro_dcb
from utils.ml.definir_modelo_validacao import FEATURES_MODELO_C
from utils.ml.diagnosticar_series import ErroDiagnostico, carregar_agregacao_diaria
from utils.ml.modelo_c_chave_analitica import (
    comparar_reprodutibilidade,
    construir_diagnostico_chave,
    executar_modelo_c_hibrido_em_diagnostico,
    parametros_efetivos,
    resumo_losartana,
    resumir_execucao_hibrida,
)
from utils.ml.definir_modelo_validacao import ErroModelo


BASELINE_ANTIGO = {
    "series": 2414,
    "series_modelaveis": 980,
    "series_historico_insuficiente": 1434,
    "observacoes_modelaveis": 28954,
    "outliers": 4235,
    "aumentos_atipicos": 1702,
    "chaves_com_aumento_atipico": 432,
}


def executar_relatorio():
    dados = carregar_agregacao_diaria()
    diagnostico = construir_diagnostico_chave(
        dados["serie_vendas"],
        dados["catalogo_classificado"],
        carregar_cadastro_dcb(),
    )
    diagnostico["banco"] = dados["banco"]
    diagnostico["somente_leitura"] = dados["somente_leitura"]

    primeiro = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
    segundo = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
    metricas = resumir_execucao_hibrida(primeiro)
    reproducibilidade = comparar_reprodutibilidade(primeiro, segundo)
    return {
        "primeiro": primeiro,
        "segundo": segundo,
        "metricas": metricas,
        "reprodutibilidade": reproducibilidade,
        "losartana": resumo_losartana(primeiro),
        "parametros": parametros_efetivos(primeiro),
        "banco": dados["banco"],
        "somente_leitura": dados["somente_leitura"],
    }


def imprimir_relatorio(relatorio):
    geral = relatorio["metricas"]["geral"]
    por_tipo = relatorio["metricas"]["por_tipo"]
    print("1. SEGURANÇA E ESCOPO")
    print(f"Banco: {relatorio['banco']}")
    print(f"transaction_read_only: {relatorio['somente_leitura']}")
    print("Resultados calculados em memória; nenhuma persistência ou sgfpod1.")

    print("\n2. FEATURES E CRITÉRIOS PRESERVADOS")
    for indice, feature in enumerate(FEATURES_MODELO_C, 1):
        print(f"{indice}. {feature}")
    print("Mínimo histórico: 7 dias positivos anteriores.")
    print("Aumento atípico: outlier e quantidade atual > média positiva anterior.")
    print(f"Parâmetros efetivos: {relatorio['parametros']}")

    print("\n3. RESULTADOS DA REPRESENTAÇÃO HÍBRIDA")
    for chave, valor in geral.items():
        print(f"{chave}: {valor}")
    print("\nPor tipo de chave:")
    for tipo, valores in por_tipo.items():
        print(tipo, valores)

    print("\n4. COMPARAÇÃO COM O BASELINE ANTIGO POR PRODUTO")
    print(f"{'métrica':43} {'antigo':>12} {'híbrido':>12} {'diferença':>12}")
    novo = {
        "series": geral["chaves_totais"],
        "series_modelaveis": geral["series_modelaveis"],
        "series_historico_insuficiente": geral[
            "series_historico_insuficiente"
        ],
        "observacoes_modelaveis": geral["observacoes_modelaveis"],
        "outliers": geral["outliers"],
        "aumentos_atipicos": geral["aumentos_atipicos"],
        "chaves_com_aumento_atipico": geral["chaves_com_aumento_atipico"],
    }
    for metrica, antigo in BASELINE_ANTIGO.items():
        valor_novo = novo[metrica]
        print(f"{metrica:43} {antigo:12d} {valor_novo:12d} {valor_novo-antigo:12d}")
    print("Diferenças são quantitativas; contagens maiores/menores não indicam")
    print("por si só melhor ou pior desempenho.")

    print("\n5. LOSARTANA POTÁSSICA")
    losartana = relatorio["losartana"]
    if losartana is None:
        print("Chave não encontrada no conjunto elegível.")
    else:
        print(f"Códigos agrupados: {losartana['codigos_agrupados']} {losartana['codigos']}")
        print(f"Período: {losartana['data_inicial'].date()} a {losartana['data_final'].date()}")
        print(f"Dias positivos: {losartana['dias_positivos']}")
        print(
            "Dias positivos anteriores máximos: "
            f"{losartana['vendas_positivas_anteriores_max']}"
        )
        print(f"Histórico suficiente/modelável: {losartana['historico_suficiente']}")
        print(f"Observações modeláveis: {losartana['observacoes_modelaveis']}")
        print(f"Outliers: {losartana['outliers']}")
        print(f"Aumentos atípicos: {losartana['aumentos_atipicos']}")
        if losartana["ocorrencia"] is None:
            print("Nenhuma ocorrência atípica para exibir.")
        else:
            print("Ocorrência:", losartana["ocorrencia"])

    print("\n6. REPRODUTIBILIDADE (EXECUÇÕES A → B)")
    for chave, valor in relatorio["reprodutibilidade"].items():
        print(f"{chave}: {valor}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        relatorio = executar_relatorio()
        imprimir_relatorio(relatorio)
        if not relatorio["reprodutibilidade"]["identicos"]:
            raise RuntimeError("As duas execuções do Modelo C divergiram.")
    except (ErroDiagnostico, ErroModelo, RuntimeError, ValueError) as erro:
        print("EXECUÇÃO HÍBRIDA INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
