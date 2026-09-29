"""Prepara e diagnostica séries diárias do Rel_0024 sem treinar modelos.

As consultas usam exclusivamente o banco tcc_medicamentos em modo somente
leitura. As séries completas são construídas apenas em memória e não são
gravadas no PostgreSQL nem em arquivos.
"""

from collections import OrderedDict
import re
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from database import obter_url_banco_aplicacao
from utils.classificacao_medicamentos import (
    CLASSIFICACOES_MED,
)


BANCO_ESPERADO = "tcc_medicamentos"

SQL_RESUMO_BANCO = text(
    """
    SELECT
        (SELECT COUNT(*) FROM public.importacoes_rel0024) AS importacoes,
        (SELECT COUNT(*) FROM public.produtos_rel0024) AS produtos,
        (SELECT COUNT(*) FROM public.vendas_rel0024) AS vendas,
        (SELECT COUNT(*) FROM public.itens_rel0024) AS itens,
        (SELECT MIN(periodo_inicial) FROM public.importacoes_rel0024)
            AS periodo_inicial_importado,
        (SELECT MAX(periodo_final) FROM public.importacoes_rel0024)
            AS periodo_final_importado,
        (SELECT MIN(data) FROM public.vendas_rel0024) AS primeira_data_venda,
        (SELECT MAX(data) FROM public.vendas_rel0024) AS ultima_data_venda,
        (SELECT SUM(quantidade) FROM public.itens_rel0024) AS soma_quantidades
    """
)

SQL_QUALIDADE_ORIGEM = text(
    """
    SELECT
        SUM(CASE WHEN quantidade IS NULL THEN 1 ELSE 0 END)
            AS quantidades_nulas,
        SUM(CASE WHEN quantidade::text = 'NaN' THEN 1 ELSE 0 END)
            AS quantidades_nao_numericas,
        SUM(CASE WHEN quantidade = 0 THEN 1 ELSE 0 END)
            AS quantidades_zero,
        SUM(CASE WHEN quantidade < 0 THEN 1 ELSE 0 END)
            AS quantidades_negativas,
        SUM(CASE WHEN v.data IS NULL THEN 1 ELSE 0 END)
            AS datas_nulas,
        SUM(CASE WHEN v.filial <> p.filial THEN 1 ELSE 0 END)
            AS divergencias_filial
    FROM public.itens_rel0024 AS i
    JOIN public.vendas_rel0024 AS v ON v.id = i.venda_id
    JOIN public.produtos_rel0024 AS p ON p.id = i.produto_id
    """
)

SQL_SERIE_DIARIA = text(
    """
    SELECT
        v.filial,
        p.codigo_produto,
        p.descricao_original,
        p.cod_tipo,
        p.nom_tipo,
        p.origem_classificacao,
        p.classificacao_operacional,
        p.elegivel_analise_automedicacao,
        v.data,
        SUM(i.quantidade) AS quantidade_total
    FROM public.itens_rel0024 AS i
    JOIN public.vendas_rel0024 AS v ON v.id = i.venda_id
    JOIN public.produtos_rel0024 AS p ON p.id = i.produto_id
    GROUP BY
        v.filial,
        p.codigo_produto,
        p.descricao_original,
        p.cod_tipo,
        p.nom_tipo,
        p.origem_classificacao,
        p.classificacao_operacional,
        p.elegivel_analise_automedicacao,
        v.data
    ORDER BY v.filial, p.codigo_produto, v.data
    """
)

SQL_CATALOGO_CLASSIFICACAO = text(
    """
    SELECT filial, codigo_produto, descricao_original,
           cod_tipo, nom_tipo, origem_classificacao,
           classificacao_operacional, elegivel_analise_automedicacao
    FROM public.produtos_rel0024
    ORDER BY filial, codigo_produto
    """
)


class ErroDiagnostico(RuntimeError):
    """Indica que o diagnóstico não pode continuar com segurança."""


def _remover_comentarios_sql(comando):
    sem_blocos = re.sub(r"/\*.*?\*/", "", comando, flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", "", sem_blocos).strip()


def _criar_engine_somente_leitura():
    engine = create_engine(
        obter_url_banco_aplicacao(),
        pool_pre_ping=True,
        connect_args={
            "application_name": "tcc_diagnostico_ml_read_only",
            "options": "-c default_transaction_read_only=on -c statement_timeout=60000",
        },
    )

    @event.listens_for(engine, "before_cursor_execute")
    def bloquear_escrita(
        _conexao, _cursor, comando, _parametros, _contexto, _multiplos
    ):
        comando_limpo = _remover_comentarios_sql(comando).upper()
        if not re.match(r"^(SELECT|SHOW)\b", comando_limpo):
            raise PermissionError(
                "Comando bloqueado: o diagnóstico aceita somente SELECT e SHOW."
            )

    return engine


def _confirmar_conexao(conexao):
    banco = conexao.execute(text("SELECT current_database()" )).scalar_one()
    somente_leitura = conexao.execute(
        text("SHOW transaction_read_only")
    ).scalar_one()
    if banco != BANCO_ESPERADO:
        raise ErroDiagnostico(
            f"Banco recusado: esperado {BANCO_ESPERADO!r}, obtido {banco!r}."
        )
    if somente_leitura != "on":
        raise ErroDiagnostico(
            "A conexão com tcc_medicamentos não está em modo somente leitura."
        )
    return banco, somente_leitura


def carregar_agregacao_diaria():
    """Obtém a agregação filial+produto+data e diagnósticos da origem."""

    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            banco, somente_leitura = _confirmar_conexao(conexao)
            resumo = dict(conexao.execute(SQL_RESUMO_BANCO).mappings().one())
            qualidade = dict(
                conexao.execute(SQL_QUALIDADE_ORIGEM).mappings().one()
            )
            serie = pd.read_sql(SQL_SERIE_DIARIA, conexao)
            catalogo = pd.read_sql(SQL_CATALOGO_CLASSIFICACAO, conexao)
    finally:
        engine.dispose()

    if serie.empty:
        raise ErroDiagnostico("A agregação diária do Rel_0024 está vazia.")

    serie["data"] = pd.to_datetime(serie["data"], errors="coerce")
    serie["quantidade_total"] = pd.to_numeric(
        serie["quantidade_total"], errors="coerce"
    )
    if catalogo["elegivel_analise_automedicacao"].isna().any():
        raise ErroDiagnostico(
            "O catálogo possui produtos sem elegibilidade persistida; "
            "a análise foi interrompida."
        )
    serie["elegivel_analise_automedicacao"] = serie[
        "elegivel_analise_automedicacao"
    ].astype(bool)
    catalogo["elegivel_analise_automedicacao"] = catalogo[
        "elegivel_analise_automedicacao"
    ].astype(bool)
    return {
        "banco": banco,
        "somente_leitura": somente_leitura,
        "resumo_banco": resumo,
        "qualidade_origem": qualidade,
        "serie_vendas": serie,
        "catalogo_classificado": catalogo,
    }


def construir_calendario_completo(serie_vendas, resumo_produtos):
    """Cria em memória a série B entre a primeira e a última venda individual."""

    calendario_global = pd.DataFrame(
        {
            "data": pd.date_range(
                serie_vendas["data"].min(),
                serie_vendas["data"].max(),
                freq="D",
            )
        }
    )
    base_produtos = resumo_produtos[
        [
            "filial",
            "codigo_produto",
            "descricao_original",
            "cod_tipo",
            "nom_tipo",
            "origem_classificacao",
            "classificacao_operacional",
            "elegivel_analise_automedicacao",
            "primeira_venda",
            "ultima_venda",
        ]
    ]
    calendario = base_produtos.merge(calendario_global, how="cross")
    calendario = calendario.loc[
        calendario["data"].between(
            calendario["primeira_venda"], calendario["ultima_venda"]
        )
    ]
    serie_completa = calendario.merge(
        serie_vendas,
        on=[
            "filial",
            "codigo_produto",
            "descricao_original",
            "cod_tipo",
            "nom_tipo",
            "origem_classificacao",
            "classificacao_operacional",
            "elegivel_analise_automedicacao",
            "data",
        ],
        how="left",
        validate="one_to_one",
    )
    serie_completa["teve_registro_venda"] = serie_completa[
        "quantidade_total"
    ].notna()
    serie_completa["quantidade_total"] = serie_completa[
        "quantidade_total"
    ].fillna(0.0)
    return serie_completa


def _estatisticas_quantidade(serie):
    valores = serie["quantidade_total"]
    return OrderedDict(
        media=float(valores.mean()),
        mediana=float(valores.median()),
        desvio_padrao=float(valores.std(ddof=1)),
        maximo=float(valores.max()),
    )


def _distribuicao_dias_venda(resumo_produtos):
    valores = resumo_produtos["dias_com_venda"]
    return OrderedDict(
        minimo=int(valores.min()),
        p25=float(valores.quantile(0.25)),
        mediana=float(valores.median()),
        media=float(valores.mean()),
        p75=float(valores.quantile(0.75)),
        p90=float(valores.quantile(0.90)),
        p95=float(valores.quantile(0.95)),
        maximo=int(valores.max()),
    )


def diagnosticar_series(apenas_elegiveis_automedicacao=False):
    """Compara as séries A e B e retorna métricas sem treinar ML."""

    dados = carregar_agregacao_diaria()
    serie_completa_origem = dados["serie_vendas"]
    soma_agregada_origem = float(
        serie_completa_origem["quantidade_total"].sum()
    )
    soma_origem = float(dados["resumo_banco"]["soma_quantidades"])
    if not np.isclose(soma_agregada_origem, soma_origem):
        raise ErroDiagnostico(
            "A soma da serie diaria diverge da soma dos itens importados."
        )

    serie_a = serie_completa_origem
    if apenas_elegiveis_automedicacao:
        serie_a = serie_a.loc[
            serie_a["elegivel_analise_automedicacao"]
        ].copy()
        if serie_a.empty:
            raise ErroDiagnostico(
                "Nenhum produto elegivel para a analise operacional foi encontrado."
            )
    chaves = ["filial", "codigo_produto"]

    duplicidades = int(serie_a.duplicated(chaves + ["data"]).sum())
    datas_invalidas = int(serie_a["data"].isna().sum())
    quantidades_invalidas_agregadas = int(
        (~np.isfinite(serie_a["quantidade_total"])).sum()
    )
    zeros_agregados = int((serie_a["quantidade_total"] == 0).sum())
    negativos_agregados = int((serie_a["quantidade_total"] < 0).sum())

    resumo_produtos = (
        serie_a.groupby(chaves, as_index=False)
        .agg(
            descricao_original=("descricao_original", "first"),
            cod_tipo=("cod_tipo", "first"),
            nom_tipo=("nom_tipo", "first"),
            origem_classificacao=("origem_classificacao", "first"),
            classificacao_operacional=("classificacao_operacional", "first"),
            elegivel_analise_automedicacao=(
                "elegivel_analise_automedicacao", "first"
            ),
            primeira_venda=("data", "min"),
            ultima_venda=("data", "max"),
            dias_com_venda=("data", "nunique"),
            quantidade_total=("quantidade_total", "sum"),
            media_dia_com_venda=("quantidade_total", "mean"),
        )
    )
    resumo_produtos["dias_no_intervalo_individual"] = (
        resumo_produtos["ultima_venda"] - resumo_produtos["primeira_venda"]
    ).dt.days + 1
    resumo_produtos["dias_sem_venda"] = (
        resumo_produtos["dias_no_intervalo_individual"]
        - resumo_produtos["dias_com_venda"]
    )
    resumo_produtos["frequencia_no_intervalo"] = (
        resumo_produtos["dias_com_venda"]
        / resumo_produtos["dias_no_intervalo_individual"]
    )

    serie_b = construir_calendario_completo(serie_a, resumo_produtos)
    observacoes_adicionadas = len(serie_b) - len(serie_a)
    if observacoes_adicionadas != int(resumo_produtos["dias_sem_venda"].sum()):
        raise ErroDiagnostico(
            "A quantidade de zeros adicionados diverge dos dias sem venda."
        )
    data_inicial = serie_a["data"].min()
    data_final = serie_a["data"].max()
    dias_periodo = int((data_final - data_inicial).days + 1)
    poucos_dias = OrderedDict(
        ate_1=int((resumo_produtos["dias_com_venda"] <= 1).sum()),
        ate_3=int((resumo_produtos["dias_com_venda"] <= 3).sum()),
        ate_7=int((resumo_produtos["dias_com_venda"] <= 7).sum()),
        ate_14=int((resumo_produtos["dias_com_venda"] <= 14).sum()),
    )

    colunas_exibicao = [
        "filial",
        "codigo_produto",
        "descricao_original",
        "dias_com_venda",
        "dias_no_intervalo_individual",
        "dias_sem_venda",
        "quantidade_total",
    ]
    mais_frequentes = resumo_produtos.sort_values(
        ["dias_com_venda", "quantidade_total"], ascending=[False, False]
    ).head(10)[colunas_exibicao]
    maiores_quantidades = resumo_produtos.sort_values(
        ["quantidade_total", "dias_com_venda"], ascending=[False, False]
    ).head(10)[colunas_exibicao]
    menos_frequentes = resumo_produtos.sort_values(
        ["dias_com_venda", "quantidade_total"], ascending=[True, False]
    ).head(10)[colunas_exibicao]

    catalogo = dados["catalogo_classificado"]
    contagens_classificacao = {
        str(chave): int(valor)
        for chave, valor in catalogo["classificacao_operacional"]
        .value_counts(dropna=False)
        .items()
    }
    produtos_med = int(
        catalogo["classificacao_operacional"].isin(CLASSIFICACOES_MED).sum()
    )
    produtos_elegiveis = int(
        catalogo["elegivel_analise_automedicacao"].sum()
    )

    return {
        **dados,
        "serie_a": serie_a,
        "serie_b": serie_b,
        "resumo_produtos": resumo_produtos,
        "universo_operacional": {
            "filtro_aplicado": bool(apenas_elegiveis_automedicacao),
            "produtos_catalogo": len(catalogo),
            "produtos_med": produtos_med,
            "produtos_nao_med": len(catalogo) - produtos_med,
            "produtos_elegiveis": produtos_elegiveis,
            "produtos_excluidos": len(catalogo) - produtos_elegiveis,
            "contagens_classificacao": contagens_classificacao,
        },
        "periodo": {
            "data_inicial": data_inicial.date(),
            "data_final": data_final.date(),
            "dias": dias_periodo,
        },
        "qualidade_agregacao": {
            "duplicidades": duplicidades,
            "datas_invalidas": datas_invalidas,
            "quantidades_invalidas": quantidades_invalidas_agregadas,
            "quantidades_zero": zeros_agregados,
            "quantidades_negativas": negativos_agregados,
        },
        "distribuicao_dias_venda": _distribuicao_dias_venda(resumo_produtos),
        "poucos_dias": poucos_dias,
        "mais_frequentes": mais_frequentes,
        "maiores_quantidades": maiores_quantidades,
        "menos_frequentes": menos_frequentes,
        "estatisticas_a": _estatisticas_quantidade(serie_a),
        "estatisticas_b": _estatisticas_quantidade(serie_b),
        "impacto_zeros": {
            "observacoes_adicionadas": int(observacoes_adicionadas),
            "aumento_percentual": float(observacoes_adicionadas * 100 / len(serie_a)),
            "zeros_na_serie_b": int((serie_b["quantidade_total"] == 0).sum()),
            "percentual_zeros_serie_b": float(
                (serie_b["quantidade_total"] == 0).mean() * 100
            ),
            "produtos_com_intervalos_sem_venda": int(
                (resumo_produtos["dias_sem_venda"] > 0).sum()
            ),
            "media_dias_sem_venda": float(
                resumo_produtos["dias_sem_venda"].mean()
            ),
            "mediana_dias_sem_venda": float(
                resumo_produtos["dias_sem_venda"].median()
            ),
            "maximo_dias_sem_venda": int(
                resumo_produtos["dias_sem_venda"].max()
            ),
        },
    }


def _formatar_numero(valor):
    if isinstance(valor, float):
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{valor:,}".replace(",", ".")


def _imprimir_mapa(mapa):
    for nome, valor in mapa.items():
        print(f"- {nome}: {_formatar_numero(valor)}")


def _imprimir_ranking(titulo, dados):
    print(f"\n{titulo}")
    print(dados.to_string(index=False))


def imprimir_relatorio(resultado):
    resumo_banco = resultado["resumo_banco"]
    periodo = resultado["periodo"]
    serie_a = resultado["serie_a"]
    serie_b = resultado["serie_b"]
    resumo_produtos = resultado["resumo_produtos"]
    impacto = resultado["impacto_zeros"]

    print("1. SEGURANÇA E FONTE")
    print(f"- Banco: {resultado['banco']}")
    print(f"- transaction_read_only: {resultado['somente_leitura']}")
    print("- Fonte exclusiva: tabelas Rel_0024 do tcc_medicamentos.")
    print("- Nenhum dado foi alterado e nenhum modelo foi treinado.")

    print("\n2. RESUMO DO PERÍODO")
    print(f"- Período observado: {periodo['data_inicial']} a {periodo['data_final']}")
    print(f"- Número de dias corridos: {periodo['dias']}")
    print(f"- Vendas no banco: {_formatar_numero(resumo_banco['vendas'])}")
    print(f"- Itens no banco: {_formatar_numero(resumo_banco['itens'])}")
    print(f"- Produtos cadastrados: {_formatar_numero(resumo_banco['produtos'])}")
    print(f"- Produtos com vendas: {_formatar_numero(len(resumo_produtos))}")
    print(f"- Soma das unidades: {_formatar_numero(float(resumo_banco['soma_quantidades']))}")

    print("\n3. QUALIDADE DOS DADOS")
    print("Itens de origem:")
    _imprimir_mapa(resultado["qualidade_origem"])
    print("Agregação filial+produto+data:")
    _imprimir_mapa(resultado["qualidade_agregacao"])

    print("\n4. REPRESENTAÇÃO A — SOMENTE DIAS COM VENDA")
    print(f"- Observações produto-dia: {_formatar_numero(len(serie_a))}")
    print("- Estatísticas globais da quantidade diária por produto:")
    _imprimir_mapa(resultado["estatisticas_a"])
    print("- Distribuição dos dias com venda por produto:")
    _imprimir_mapa(resultado["distribuicao_dias_venda"])

    print("\n5. PRODUTOS COM POUCOS DIAS DE VENDA")
    print("Os limites abaixo são cortes diagnósticos explícitos, não regras de exclusão:")
    _imprimir_mapa(resultado["poucos_dias"])
    _imprimir_ranking("Exemplos com menor frequência", resultado["menos_frequentes"])

    _imprimir_ranking(
        "6. PRODUTOS COM MAIOR FREQUÊNCIA DE VENDA",
        resultado["mais_frequentes"],
    )
    _imprimir_ranking(
        "7. PRODUTOS COM MAIOR QUANTIDADE TOTAL",
        resultado["maiores_quantidades"],
    )

    print("\n8. DIAS SEM VENDA E REPRESENTAÇÃO B")
    print("- Calendário individual: primeira até última venda de cada produto.")
    print(f"- Observações após completar o calendário: {_formatar_numero(len(serie_b))}")
    print(f"- Produtos com pelo menos um intervalo sem venda: {_formatar_numero(impacto['produtos_com_intervalos_sem_venda'])}")
    print(f"- Dias sem venda adicionados: {_formatar_numero(impacto['observacoes_adicionadas'])}")
    print(f"- Média de dias sem venda por produto: {_formatar_numero(impacto['media_dias_sem_venda'])}")
    print(f"- Mediana de dias sem venda por produto: {_formatar_numero(impacto['mediana_dias_sem_venda'])}")
    print(f"- Máximo de dias sem venda em um produto: {_formatar_numero(impacto['maximo_dias_sem_venda'])}")

    print("\n9. IMPACTO DO PREENCHIMENTO COM ZERO")
    print(f"- Crescimento no número de observações: {_formatar_numero(impacto['aumento_percentual'])}%")
    print(f"- Percentual de zeros na série B: {_formatar_numero(impacto['percentual_zeros_serie_b'])}%")
    print("- Estatísticas globais da quantidade diária por produto após preenchimento:")
    _imprimir_mapa(resultado["estatisticas_b"])

    print("\n10. COMPARAÇÃO E RECOMENDAÇÃO")
    print("- Série A preserva apenas o comportamento nos dias ativos, mas elimina a informação temporal das ausências de venda.")
    print("- Série B preserva intervalos, frequência, defasagens e janelas móveis; por isso é a base temporal recomendada para detectar aumentos atípicos.")
    print("- Como a série B possui muitos zeros, a proposta é calcular o contexto no calendário completo e pontuar prioritariamente dias com quantidade positiva.")
    print("- Produtos muito esparsos devem ser segmentados ou submetidos a um mínimo de histórico; caso contrário, qualquer venda pode parecer incomum.")
    print("- A série A deve permanecer como análise de sensibilidade, sem substituição silenciosa da estratégia.")

    print("\n11. FEATURES PROPOSTAS PARA A PRÓXIMA ETAPA")
    print("- quantidade_dia")
    print("- log1p_quantidade_dia, reduzindo a diferença de escala entre produtos")
    print("- quantidade_lag_1, quantidade_lag_7 e quantidade_lag_14")
    print("- media_movel_7 e media_movel_28 usando somente dias anteriores")
    print("- desvio_movel_7 e desvio_movel_28 usando somente dias anteriores")
    print("- diferenca_e_razao_para_media_movel, com tratamento de denominador zero")
    print("- desvio robusto em relação à mediana histórica/MAD, quando houver histórico suficiente")
    print("- dias_desde_ultima_venda")
    print("- taxa_historica_de_dias_com_venda")
    print("- dia_da_semana em codificação cíclica seno/cosseno")
    print("- O código do produto identifica a série, mas não deve entrar como número ordinal no modelo.")

    print("\n12. LIMITES DE INTERPRETAÇÃO")
    print("- Nenhuma anomalia foi calculada nesta etapa.")
    print("- Um aumento atípico futuro indicará apenas comportamento de venda fora do padrão histórico.")
    print("- Isso não comprova doença, surto, diagnóstico ou automedicação.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(diagnosticar_series())
    except (ErroDiagnostico, RuntimeError) as erro:
        print("DIAGNÓSTICO INTERROMPIDO COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("ERRO AO CONSULTAR tcc_medicamentos")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem técnica: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
