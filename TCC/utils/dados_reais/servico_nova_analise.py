"""Processa um Rel_0024 em memória, sem gravar vendas no banco consolidado."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text
from werkzeug.utils import secure_filename

from utils.classificacao_medicamentos import (
    SEM_CLASSIFICACAO,
    classificar_elegibilidade,
)
from utils.ml.definir_modelo_validacao import executar_modelo_c_em_diagnostico
from utils.ml.diagnosticar_series import (
    _criar_engine_somente_leitura,
    _confirmar_conexao,
    construir_calendario_completo,
)

from .importar_rel0024 import ErroImportacaoRel0024, _validar_para_importacao
from .validar_rel0024 import ler_rel0024, validar_rel0024


EXTENSOES_PERMITIDAS = frozenset({".xls", ".xlsx"})
LIMITE_AUMENTOS = 100
CONSULTA_CATALOGO = text(
    """
    SELECT cod_reduzido, cod_tipo, nom_tipo, nom_produto
    FROM public.cadastro_produtos_farmacia
    WHERE cod_rede = 1 AND cod_reduzido IN :codigos
    """
).bindparams(bindparam("codigos", expanding=True))


class ErroNovaAnalise(RuntimeError):
    """Falha recuperável de validação/processamento do arquivo isolado."""

    def __init__(self, mensagem, status_http=400):
        super().__init__(mensagem)
        self.status_http = status_http


def consultar_catalogo_local(codigos):
    """Busca metadados apenas no espelho local, em transação read-only."""

    codigos = sorted({int(codigo) for codigo in codigos})
    if not codigos:
        return {}

    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            _confirmar_conexao(conexao)
            linhas = conexao.execute(
                CONSULTA_CATALOGO,
                {"codigos": codigos},
            ).mappings()
            return {int(linha["cod_reduzido"]): dict(linha) for linha in linhas}
    finally:
        engine.dispose()


def _catalogo_classificado(produtos_descricoes, metadados, filial):
    linhas = []
    for codigo, descricoes in sorted(produtos_descricoes.items()):
        descricao = next(iter(descricoes))
        cadastro = metadados.get(int(codigo))
        tipo = cadastro.get("nom_tipo") if cadastro else None
        cod_tipo = cadastro.get("cod_tipo") if cadastro else None
        if tipo is None or not str(tipo).strip():
            classificacao, elegivel = SEM_CLASSIFICACAO, False
            origem = None
        else:
            classificacao, elegivel = classificar_elegibilidade(tipo)
            origem = "cadastro_produtos_farmacia_local"
        linhas.append(
            {
                "filial": int(filial),
                "codigo_produto": int(codigo),
                "descricao_original": str(descricao),
                "cod_tipo": cod_tipo,
                "nom_tipo": tipo,
                "origem_classificacao": origem,
                "classificacao_operacional": classificacao,
                "elegivel_analise_automedicacao": bool(elegivel),
            }
        )
    return pd.DataFrame(linhas)


def _diagnostico_em_memoria(serie_vendas, catalogo):
    chaves = ["filial", "codigo_produto"]
    resumo = (
        serie_vendas.groupby(chaves, as_index=False)
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
    resumo["dias_no_intervalo_individual"] = (
        resumo["ultima_venda"] - resumo["primeira_venda"]
    ).dt.days + 1
    resumo["dias_sem_venda"] = (
        resumo["dias_no_intervalo_individual"] - resumo["dias_com_venda"]
    )
    resumo["frequencia_no_intervalo"] = (
        resumo["dias_com_venda"] / resumo["dias_no_intervalo_individual"]
    )
    serie_completa = construir_calendario_completo(serie_vendas, resumo)
    produtos_med = int(
        catalogo["classificacao_operacional"].str.startswith("MED_").sum()
    )
    elegiveis = int(catalogo["elegivel_analise_automedicacao"].sum())
    return {
        "serie_vendas": serie_vendas,
        "serie_a": serie_vendas,
        "serie_b": serie_completa,
        "resumo_produtos": resumo,
        "catalogo_classificado": catalogo,
        "universo_operacional": {
            "filtro_aplicado": True,
            "produtos_catalogo": len(catalogo),
            "produtos_med": produtos_med,
            "produtos_nao_med": len(catalogo) - produtos_med,
            "produtos_elegiveis": elegiveis,
            "produtos_excluidos": len(catalogo) - elegiveis,
        },
        "periodo": {
            "data_inicial": serie_vendas["data"].min().date(),
            "data_final": serie_vendas["data"].max().date(),
            "dias": int(
                (serie_vendas["data"].max() - serie_vendas["data"].min()).days
                + 1
            ),
        },
    }


def _serie_diaria_elegivel(itens, catalogo):
    colunas = [
        "filial",
        "codigo_produto",
        "data",
        "quantidade",
    ]
    vendas = pd.DataFrame(
        [
            {
                "filial": int(item["filial"]),
                "codigo_produto": int(item["codigo_produto"]),
                "data": item["data"],
                "quantidade": float(item["quantidade"]),
            }
            for item in itens
            if str(item.get("tipo", "Venda")).strip().casefold() == "venda"
        ],
        columns=colunas,
    )
    if vendas.empty:
        return pd.DataFrame()
    vendas["data"] = pd.to_datetime(vendas["data"], errors="coerce")
    if vendas["data"].isna().any() or not np.isfinite(vendas["quantidade"]).all():
        raise ErroNovaAnalise("O arquivo contém data ou quantidade inválida.")
    if (vendas["quantidade"] < 0).any():
        raise ErroNovaAnalise(
            "O arquivo contém quantidade negativa em movimento Venda; revise o relatório."
        )

    diaria = (
        vendas.groupby(["filial", "codigo_produto", "data"], as_index=False)
        .agg(quantidade_total=("quantidade", "sum"))
    )
    diaria = diaria.merge(
        catalogo,
        on=["filial", "codigo_produto"],
        how="left",
        validate="many_to_one",
    )
    return diaria.loc[
        diaria["elegivel_analise_automedicacao"].fillna(False)
    ].copy()


def analisar_dados_rel0024(
    itens,
    produtos_descricoes,
    metadados_catalogo,
    *,
    nome_arquivo,
    sha256,
    validacao,
):
    """Aplica validação operacional e o mesmo Modelo C somente a estes itens."""

    if len(validacao["filiais"]) != 1 or not itens:
        raise ErroNovaAnalise(
            "O relatório precisa conter vendas de exatamente uma filial.")
    filial = int(validacao["filiais"][0])
    catalogo = _catalogo_classificado(
        produtos_descricoes,
        metadados_catalogo,
        filial,
    )
    total_produtos = len(catalogo)
    produtos_elegiveis = int(
        catalogo["elegivel_analise_automedicacao"].sum()
    )
    produtos_inelegiveis = total_produtos - produtos_elegiveis
    classificacoes = Counter(catalogo["classificacao_operacional"].astype(str))

    serie_vendas = _serie_diaria_elegivel(itens, catalogo)
    pipeline = None
    if not serie_vendas.empty:
        diagnostico = _diagnostico_em_memoria(serie_vendas, catalogo)
        pipeline = executar_modelo_c_em_diagnostico(
            diagnostico,
            permitir_historico_insuficiente=True,
        )
        resultados = pipeline["resultado"]
    else:
        resultados = pd.DataFrame()

    produtos_modelaveis = (
        int(
            resultados[["filial", "codigo_produto"]]
            .drop_duplicates()
            .shape[0]
        )
        if not resultados.empty
        else 0
    )
    observacoes = len(resultados)
    outliers = int(resultados["anomalia"].sum()) if observacoes else 0
    aumentos = resultados.loc[resultados["aumento_atipico"]].copy() if observacoes else pd.DataFrame()
    periodo_inicial = validacao["primeira_data"]
    periodo_final = validacao["ultima_data"]
    meses = pd.period_range(periodo_inicial, periodo_final, freq="M")

    vendas_por_data = (
        serie_vendas.groupby("data")["quantidade_total"].sum()
        if not serie_vendas.empty
        else pd.Series(dtype=float)
    )
    datas = pd.date_range(periodo_inicial, periodo_final, freq="D")
    vendas_diarias = [float(vendas_por_data.get(data, 0.0)) for data in datas]
    aumentos_mensais = Counter(
        aumentos["data"].dt.to_period("M").astype(str).tolist()
    ) if not aumentos.empty else Counter()

    aumentos = (
        aumentos.sort_values(
            ["decision_function", "data"],
            ascending=[True, False],
            kind="stable",
        ).head(LIMITE_AUMENTOS)
        if not aumentos.empty
        else aumentos
    )
    tabela_aumentos = []
    for linha in aumentos.itertuples(index=False):
        referencia = float(linha.media_positiva_historica)
        quantidade = float(linha.quantidade_dia)
        tabela_aumentos.append(
            {
                "data": linha.data.date().isoformat(),
                "codigo_produto": int(linha.codigo_produto),
                "produto": str(linha.descricao_original),
                "classificacao": str(linha.classificacao_operacional),
                "classificacao_modelo": int(
                    linha.classificacao_isolation_forest
                ),
                "quantidade": quantidade,
                "media_historica": referencia,
                "score_samples": float(linha.score_samples),
                "decision_function": float(linha.decision_function),
                "diferenca": quantidade - referencia,
            }
        )

    denominador = observacoes if observacoes else None
    return {
        "arquivo": str(nome_arquivo),
        "sha256": str(sha256),
        "filial": filial,
        "periodo": {
            "inicial": periodo_inicial.isoformat(),
            "final": periodo_final.isoformat(),
        },
        "metricas": {
            "produtos_encontrados": total_produtos,
            "produtos_elegiveis": produtos_elegiveis,
            "produtos_inelegiveis": produtos_inelegiveis,
            "produtos_modelaveis": produtos_modelaveis,
            "historico_insuficiente": produtos_elegiveis - produtos_modelaveis,
            "observacoes_avaliadas": observacoes,
            "outliers": outliers,
            "aumentos_atipicos": int(
                resultados["aumento_atipico"].sum()
            ) if observacoes else 0,
            "outliers_percentual": (
                round(outliers * 100 / denominador, 2) if denominador else None
            ),
            "aumentos_percentual": (
                round(int(resultados["aumento_atipico"].sum()) * 100 / denominador, 2)
                if denominador else None
            ),
            "linhas_venda": int(validacao["linhas_venda"]),
            "linhas_devolucao_ignoradas": int(validacao["linhas_devolucao"]),
        },
        "classificacoes": {
            "labels": list(classificacoes.keys()),
            "valores": [int(classificacoes[chave]) for chave in classificacoes],
        },
        "graficos": {
            "vendas_temporais": {
                "labels": [data.date().isoformat() for data in datas],
                "valores": vendas_diarias,
            },
            "aumentos_mensais": {
                "labels": [str(mes) for mes in meses],
                "valores": [int(aumentos_mensais.get(str(mes), 0)) for mes in meses],
            },
        },
        "aumentos": tabela_aumentos,
        "aumentos_limitados": (
            int(resultados["aumento_atipico"].sum()) > LIMITE_AUMENTOS
            if observacoes else False
        ),
        "mensagem_historico": (
            "Arquivo processado, mas o período não fornece histórico suficiente "
            "para modelar produtos elegíveis."
            if produtos_elegiveis and produtos_modelaveis == 0
            else None
        ),
        "metodologia": {
            "nivel": "filial + código do produto + dia",
            "fonte_historico": "somente o arquivo desta análise",
            "modelo": "Modelo C congelado; resultados não persistidos",
        },
    }


def analisar_upload_rel0024(arquivo):
    """Salva o upload apenas em diretório temporário e remove-o ao terminar."""

    if arquivo is None or not arquivo.filename:
        raise ErroNovaAnalise("Selecione um relatório Rel_0024.")
    nome_seguro = secure_filename(arquivo.filename)
    if not nome_seguro:
        raise ErroNovaAnalise("O nome do arquivo enviado é inválido.")
    extensao = Path(nome_seguro).suffix.lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        raise ErroNovaAnalise("Envie um arquivo com extensão .xlsx ou .xls.")

    with tempfile.TemporaryDirectory(prefix="nova_analise_rel0024_") as pasta:
        caminho = Path(pasta) / nome_seguro
        try:
            arquivo.save(caminho)
            if not caminho.is_file() or caminho.stat().st_size == 0:
                raise ErroNovaAnalise("O arquivo enviado está vazio.")
            resultado = ler_rel0024(caminho)
            validacao = validar_rel0024(resultado)
            try:
                _validar_para_importacao(resultado, validacao)
            except ErroImportacaoRel0024 as erro:
                raise ErroNovaAnalise(
                    "O relatório não passou pelas validações Rel_0024: "
                    + str(erro)
                ) from erro
            if not resultado["itens"]:
                raise ErroNovaAnalise("O relatório não contém linhas Venda.")
            codigos = resultado["produtos_descricoes"].keys()
            metadados = consultar_catalogo_local(codigos)
            return analisar_dados_rel0024(
                resultado["itens"],
                resultado["produtos_descricoes"],
                metadados,
                nome_arquivo=nome_seguro,
                sha256=resultado["hash_sha256"],
                validacao=validacao,
            )
        except ErroNovaAnalise:
            raise
        except (OSError, ValueError) as erro:
            raise ErroNovaAnalise(
                "O arquivo não pôde ser processado. Confira se é um Rel_0024 válido."
            ) from erro
