"""Processa um Rel_0024 em memória, sem gravar vendas no banco consolidado."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text
from werkzeug.utils import secure_filename
from utils.chave_analitica import carregar_cadastro_dcb
from utils.ml.modelo_c_chave_analitica import (
    CHAVES_SERIE as CHAVES_HIBRIDAS,
    construir_diagnostico_chave,
    executar_modelo_c_hibrido_em_diagnostico,
)

from utils.classificacao_medicamentos import (
    SEM_CLASSIFICACAO,
    classificar_elegibilidade,
)
from utils.ml.diagnosticar_series import (
    _criar_engine_somente_leitura,
    _confirmar_conexao,
)

from .importar_rel0024 import ErroImportacaoRel0024, _validar_para_importacao
from .validar_rel0024 import ler_rel0024, validar_rel0024


EXTENSOES_PERMITIDAS = frozenset({".xls", ".xlsx"})
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
        produtos_chave = catalogo.copy()
        produtos_chave["descricao"] = produtos_chave["descricao_original"]
        try:
            cadastro_dcb = carregar_cadastro_dcb()
        except (OSError, ValueError) as erro:
            raise ErroNovaAnalise(
                "O cadastro auxiliar DCB não pôde ser validado. "
                "Confira o arquivo local e suas associações.",
                status_http=503,
            ) from erro
        diagnostico = construir_diagnostico_chave(
            serie_vendas,
            produtos_chave,
            cadastro_dcb,
        )
        pipeline = executar_modelo_c_hibrido_em_diagnostico(
            diagnostico,
            permitir_historico_insuficiente=True,
        )
        resultados = pipeline["resultado"]
        classificacoes_chave = (
            diagnostico["catalogo_chaves"]
            .groupby(list(CHAVES_HIBRIDAS), as_index=False, dropna=False)
            .agg(
                classificacoes_operacionais=(
                    "classificacao_operacional",
                    lambda valores: tuple(sorted(set(map(str, valores)))),
                )
            )
        )
        if not resultados.empty:
            resultados = resultados.merge(
                classificacoes_chave,
                on=list(CHAVES_HIBRIDAS),
                how="left",
                validate="many_to_one",
            )
    else:
        resultados = pd.DataFrame()

    catalogo_chaves = (
        pipeline["diagnostico"]["catalogo_chaves"]
        if pipeline is not None
        else pd.DataFrame(columns=list(CHAVES_HIBRIDAS))
    )
    chaves_analiticas = int(
        catalogo_chaves[list(CHAVES_HIBRIDAS)].drop_duplicates().shape[0]
    )
    chaves_modelaveis = (
        int(resultados[list(CHAVES_HIBRIDAS)].drop_duplicates().shape[0])
        if not resultados.empty
        else 0
    )
    if chaves_modelaveis:
        chaves_com_historico = resultados[list(CHAVES_HIBRIDAS)].drop_duplicates()
        produtos_modelaveis = int(
            catalogo_chaves.merge(
                chaves_com_historico,
                on=list(CHAVES_HIBRIDAS),
                how="inner",
                validate="many_to_one",
            )["codigo_produto"].nunique()
        )
    else:
        produtos_modelaveis = 0
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
        )
        if not aumentos.empty
        else aumentos
    )
    tabela_aumentos = []
    associacoes_por_chave = {}
    if pipeline is not None:
        catalogo_produtos_chave = pipeline["diagnostico"]["catalogo_chaves"]
        for (tipo, chave), grupo in catalogo_produtos_chave.groupby(
            list(CHAVES_HIBRIDAS), dropna=False, sort=False
        ):
            associacoes_por_chave[(str(tipo), chave)] = [
                {
                    "codigo": int(produto.codigo_produto),
                    "descricao": str(produto.descricao_original),
                }
                for produto in grupo[
                    ["codigo_produto", "descricao_original"]
                ].drop_duplicates().sort_values("codigo_produto").itertuples(index=False)
            ]
    for linha in aumentos.itertuples(index=False):
        referencia = float(linha.media_positiva_historica)
        quantidade = float(linha.quantidade_dia)
        tipo_chave = str(linha.tipo_chave)
        codigos = tuple(linha.codigos_produto)
        descricoes = tuple(linha.descricoes_associadas)
        codigo_produto = (
            int(linha.codigo_produto)
            if pd.notna(linha.codigo_produto)
            else None
        )
        tabela_aumentos.append(
            {
                "data": linha.data.date().isoformat(),
                "tipo_chave": tipo_chave,
                "chave_analitica": str(linha.chave_analitica),
                "codigo_produto": codigo_produto,
                "produto": str(linha.descricao_original),
                "nom_dcb": str(linha.nom_dcb) if pd.notna(linha.nom_dcb) else None,
                "codigos_produto": [int(codigo) for codigo in codigos],
                "descricoes_associadas": [str(texto) for texto in descricoes],
                "associacoes_produtos": associacoes_por_chave.get(
                    (tipo_chave, linha.chave_analitica), []
                ),
                "produtos_associados": len(codigos),
                "classificacoes_operacionais": list(
                    getattr(linha, "classificacoes_operacionais", ()) or ()
                ),
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
            "produtos_historico_insuficiente": produtos_elegiveis - produtos_modelaveis,
            "historico_insuficiente": chaves_analiticas - chaves_modelaveis,
            "chaves_analiticas": chaves_analiticas,
            "chaves_modelaveis": chaves_modelaveis,
            "chaves_historico_insuficiente": chaves_analiticas - chaves_modelaveis,
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
        "aumentos_limitados": False,
        "mensagem_historico": (
            "Arquivo processado, mas o período não fornece histórico suficiente "
            "para modelar produtos elegíveis."
            if produtos_elegiveis and produtos_modelaveis == 0
            else None
        ),
        "metodologia": {
            "nivel": "filial + chave analítica híbrida + dia",
            "fonte_historico": "somente o arquivo desta análise",
            "modelo": "Modelo C híbrido congelado; resultados não persistidos",
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
