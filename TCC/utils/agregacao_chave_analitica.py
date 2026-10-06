"""Agrega vendas diárias pela chave analítica híbrida, sem persistência."""

import pandas as pd

from utils.chave_analitica import criar_chaves_analiticas


COLUNAS_VENDAS = {"filial", "codigo_produto", "data", "quantidade_total"}
COLUNAS_PRODUTOS = {
    "filial",
    "codigo_produto",
    "elegivel_analise_automedicacao",
}
CHAVES_GRUPO = ["data", "tipo_chave", "chave_analitica"]


def _validar_colunas(quadro, obrigatorias, nome):
    ausentes = obrigatorias.difference(quadro.columns)
    if ausentes:
        raise ValueError(
            f"{nome} sem colunas obrigatórias: {', '.join(sorted(ausentes))}"
        )


def _valores_unicos_ordenados(valores):
    unicos = pd.unique(pd.Series(valores).dropna())
    return tuple(sorted(unicos.tolist(), key=lambda valor: str(valor)))


def preparar_vendas_elegiveis(serie_vendas, produtos, cadastro_dcb=None):
    """Associa vendas produto-dia às chaves híbridas e conserva metadados.

    `serie_vendas` é a agregação original filial+produto+data. Produtos sem
    DCB válida mantêm o próprio código como chave e nunca são agrupados pela
    descrição. Produtos inelegíveis são excluídos antes da soma analítica.
    """

    _validar_colunas(serie_vendas, COLUNAS_VENDAS, "Série de vendas")
    _validar_colunas(produtos, COLUNAS_PRODUTOS, "Catálogo de produtos")

    chave_produto = ["filial", "codigo_produto"]
    if produtos.duplicated(chave_produto).any():
        raise ValueError("O catálogo deve ter uma linha por filial e produto.")

    vendas = serie_vendas.copy()
    vendas["data"] = pd.to_datetime(vendas["data"], errors="coerce")
    vendas["quantidade_total"] = pd.to_numeric(
        vendas["quantidade_total"], errors="coerce"
    )
    if vendas["data"].isna().any():
        raise ValueError("Há datas ausentes ou inválidas na série de vendas.")
    if vendas["quantidade_total"].isna().any():
        raise ValueError("Há quantidades ausentes ou inválidas na série de vendas.")

    vinculadas = vendas.merge(
        produtos[chave_produto],
        on=chave_produto,
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    if (vinculadas["_merge"] != "both").any():
        exemplos = vinculadas.loc[
            vinculadas["_merge"] != "both", chave_produto
        ].drop_duplicates().head(5)
        raise ValueError(
            "Há vendas sem produto correspondente no catálogo: "
            + exemplos.to_dict("records").__repr__()
        )

    chaves = criar_chaves_analiticas(produtos, cadastro_dcb)
    metadados = [
        coluna
        for coluna in (
            "filial",
            "codigo_produto",
            "descricao_original",
            "descricao",
            "cod_dcb",
            "nom_dcb",
            "tipo_chave",
            "chave_analitica",
        )
        if coluna in chaves.columns
    ]
    if chaves.duplicated(chave_produto).any():
        raise ValueError("A aplicação da chave gerou associação ambígua.")

    vinculadas = vendas.merge(
        chaves[metadados],
        on=chave_produto,
        how="inner",
        validate="many_to_one",
    )
    if vinculadas["chave_analitica"].isna().any():
        raise ValueError("Há produto elegível sem chave analítica.")

    if "descricao_original" in vinculadas.columns:
        vinculadas["descricao_produto"] = vinculadas[
            "descricao_original"
        ].fillna(vinculadas.get("descricao"))
    elif "descricao" in vinculadas.columns:
        vinculadas["descricao_produto"] = vinculadas["descricao"]
    else:
        vinculadas["descricao_produto"] = None

    return vinculadas.drop(columns="_merge", errors="ignore")


def agregar_vendas_por_chave(serie_vendas, produtos, cadastro_dcb=None):
    """Retorna uma observação por data+tipo_chave+chave_analitica.

    Os metadados de produto são coletados em tuplas ordenadas; a quantidade é
    somada uma única vez na tabela de vendas já associada aos produtos.
    Lança ValueError se a soma pré e pós-agregação não for igual.
    """

    vinculadas = preparar_vendas_elegiveis(
        serie_vendas, produtos, cadastro_dcb
    )
    total_antes = vinculadas["quantidade_total"].sum()

    agregada = (
        vinculadas.groupby(CHAVES_GRUPO, as_index=False, dropna=False)
        .agg(
            quantidade_dia=("quantidade_total", "sum"),
            nom_dcb=("nom_dcb", "first")
            if "nom_dcb" in vinculadas.columns
            else ("chave_analitica", "first"),
            codigos_produto=("codigo_produto", _valores_unicos_ordenados),
            descricoes_associadas=("descricao_produto", _valores_unicos_ordenados),
            filiais=("filial", _valores_unicos_ordenados),
        )
    )

    def produto_unico(tipo, codigos):
        return codigos[0] if tipo == "PRODUTO" and len(codigos) == 1 else None

    def descricao_unica(tipo, descricoes):
        return descricoes[0] if tipo == "PRODUTO" and len(descricoes) == 1 else None

    agregada["codigo_produto"] = [
        produto_unico(tipo, codigos)
        for tipo, codigos in zip(agregada["tipo_chave"], agregada["codigos_produto"])
    ]
    agregada["descricao"] = [
        descricao_unica(tipo, descricoes)
        for tipo, descricoes in zip(
            agregada["tipo_chave"], agregada["descricoes_associadas"]
        )
    ]

    total_depois = agregada["quantidade_dia"].sum()
    if total_antes != total_depois:
        raise ValueError(
            "A agregação não conservou as quantidades: "
            f"antes={total_antes}, depois={total_depois}."
        )

    return agregada.sort_values(CHAVES_GRUPO).reset_index(drop=True)


def resumir_representacao(serie, chaves_serie):
    """Resume séries observadas, dias positivos e unidades da representação."""

    if serie.empty:
        return {
            "series": 0,
            "observacoes_positivas": 0,
            "unidades": 0,
            "series_7_ou_mais_dias_positivos": 0,
            "series_menos_de_7_dias_positivos": 0,
        }

    positivas = serie.loc[serie["quantidade_total"] > 0]
    dias_por_serie = positivas.groupby(chaves_serie)["data"].nunique()
    return {
        "series": int(serie[chaves_serie].drop_duplicates().shape[0]),
        "observacoes_positivas": int(len(positivas)),
        "unidades": serie["quantidade_total"].sum(),
        "series_7_ou_mais_dias_positivos": int((dias_por_serie >= 7).sum()),
        "series_menos_de_7_dias_positivos": int((dias_por_serie < 7).sum()),
    }
