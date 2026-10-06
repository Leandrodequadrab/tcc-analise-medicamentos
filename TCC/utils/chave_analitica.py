"""Constrói chaves analíticas híbridas sem alterar vendas ou dados de origem."""

from pathlib import Path
import re

import pandas as pd


COLUNAS_CADASTRO_DCB = {"cod_reduzido", "cod_dcb", "nom_dcb"}
COLUNAS_PRODUTOS = {
    "codigo_produto",
    "elegivel_analise_automedicacao",
}
CAMINHO_CADASTRO_DCB = (
    Path(__file__).resolve().parents[1]
    / "dados"
    / "cadastro_dcb_farmacia.csv"
)


def normalizar_nome_dcb(valor):
    """Normaliza apenas caixa e espaços, preservando o conteúdo da DCB."""

    if valor is None or pd.isna(valor):
        return None

    nome = re.sub(r"\s*\+\s*", " + ", str(valor).strip().upper())
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome or None


def carregar_cadastro_dcb(caminho=None):
    """Lê o CSV local sem modificar o arquivo ou acessar o banco da farmácia."""

    origem = Path(caminho) if caminho is not None else CAMINHO_CADASTRO_DCB
    cadastro = pd.read_csv(
        origem,
        dtype={"cod_reduzido": "string", "cod_dcb": "string", "nom_dcb": "string"},
        encoding="utf-8-sig",
    )
    ausentes = COLUNAS_CADASTRO_DCB.difference(cadastro.columns)
    if ausentes:
        raise ValueError(
            "O cadastro DCB não contém as colunas obrigatórias: "
            + ", ".join(sorted(ausentes))
        )
    return cadastro


def _normalizar_codigo(valor):
    """Concilia códigos inteiros exportados como número ou texto sem zeros."""

    if valor is None or pd.isna(valor):
        return None
    codigo = str(valor).strip()
    if re.fullmatch(r"\d+\.0+", codigo):
        codigo = codigo.split(".", 1)[0]
    return codigo or None


def _elegivel(valor):
    """Interpreta a flag sem tratar texto não vazio, como 'False', como True."""

    if valor is None or pd.isna(valor):
        return False
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().casefold() in {"true", "1", "sim", "yes"}


def _mapa_dcb_valida(cadastro_dcb):
    ausentes = COLUNAS_CADASTRO_DCB.difference(cadastro_dcb.columns)
    if ausentes:
        raise ValueError(
            "O cadastro DCB não contém as colunas obrigatórias: "
            + ", ".join(sorted(ausentes))
        )

    cadastro = cadastro_dcb.loc[:, sorted(COLUNAS_CADASTRO_DCB)].copy()
    cadastro["_codigo_mapa"] = cadastro["cod_reduzido"].map(_normalizar_codigo)
    cadastro["cod_dcb"] = cadastro["cod_dcb"].map(
        lambda valor: None if pd.isna(valor) or not str(valor).strip() else str(valor).strip()
    )
    cadastro["nom_dcb"] = cadastro["nom_dcb"].map(normalizar_nome_dcb)

    validas = cadastro.loc[
        cadastro["_codigo_mapa"].notna()
        & cadastro["cod_dcb"].notna()
        & cadastro["nom_dcb"].notna(),
        ["_codigo_mapa", "cod_dcb", "nom_dcb"],
    ].drop_duplicates()

    ambiguos = validas.groupby("_codigo_mapa").size()
    ambiguos = ambiguos.loc[ambiguos > 1]
    if not ambiguos.empty:
        exemplos = ", ".join(ambiguos.index.astype(str).tolist()[:10])
        raise ValueError(
            "Há códigos de produto associados a mais de uma DCB válida; "
            f"mapeamento interrompido. Exemplos: {exemplos}"
        )

    return validas.drop_duplicates("_codigo_mapa").set_index("_codigo_mapa")


def criar_chaves_analiticas(produtos, cadastro_dcb=None):
    """Retorna produtos elegíveis com uma chave DCB ou código individual.

    A função filtra produtos inelegíveis, usa o CSV DCB local (ou o DataFrame
    informado) e não agrega vendas nem altera o conjunto recebido.
    """

    ausentes = COLUNAS_PRODUTOS.difference(produtos.columns)
    if ausentes:
        raise ValueError(
            "Os produtos não contêm as colunas obrigatórias: "
            + ", ".join(sorted(ausentes))
        )

    origem = produtos.copy()
    origem["_elegivel"] = origem[
        "elegivel_analise_automedicacao"
    ].map(_elegivel)
    resultado = origem.loc[origem["_elegivel"]].copy()
    if resultado["codigo_produto"].isna().any():
        raise ValueError("Produto elegível sem codigo_produto; chave não criada.")

    resultado["_codigo_mapa"] = resultado["codigo_produto"].map(
        _normalizar_codigo
    )
    if resultado["_codigo_mapa"].isna().any():
        raise ValueError("Produto elegível com código inválido; chave não criada.")

    if cadastro_dcb is None:
        cadastro_dcb = carregar_cadastro_dcb()
    mapa = _mapa_dcb_valida(cadastro_dcb)

    resultado = resultado.join(mapa, on="_codigo_mapa", how="left")
    possui_dcb = resultado["nom_dcb"].notna() & resultado["cod_dcb"].notna()
    resultado["tipo_chave"] = possui_dcb.map(
        {True: "DCB", False: "PRODUTO"}
    )
    chaves = []
    for linha in resultado.itertuples(index=False):
        if linha.tipo_chave == "DCB":
            chaves.append(linha.nom_dcb)
        else:
            chaves.append(linha.codigo_produto)
    resultado["chave_analitica"] = pd.Series(
        chaves, index=resultado.index, dtype=object
    )

    if "descricao_original" in resultado.columns:
        resultado["descricao"] = resultado["descricao_original"]
    elif "descricao" not in resultado.columns:
        resultado["descricao"] = None

    resultado = resultado.drop(columns=["_elegivel", "_codigo_mapa"])
    return resultado.reset_index(drop=True)
