import pandas as pd
from sqlalchemy.exc import SQLAlchemyError

from database import Venda, db


COLUNAS_OBRIGATORIAS = (
    "data",
    "medicamento",
    "categoria",
    "quantidade",
    "cidade",
    "prescricao",
)

LIMITES_TEXTO = {
    "medicamento": 150,
    "categoria": 100,
    "cidade": 100,
}


class ErroValidacaoCSV(ValueError):
    """Erro encontrado no conteúdo do arquivo CSV."""


def _mostrar_linhas(indices):
    """Converte índices do DataFrame em números de linhas do arquivo CSV."""

    linhas = [str(indice + 2) for indice in indices[:5]]
    complemento = "..." if len(indices) > 5 else ""
    return ", ".join(linhas) + complemento


def _ler_e_validar_csv(arquivo):
    """Lê o CSV e devolve um DataFrame validado e normalizado."""

    try:
        dados = pd.read_csv(arquivo, encoding="utf-8-sig")
    except UnicodeDecodeError as erro:
        raise ErroValidacaoCSV("O arquivo deve estar codificado em UTF-8.") from erro
    except pd.errors.EmptyDataError as erro:
        raise ErroValidacaoCSV("O arquivo CSV está vazio.") from erro
    except pd.errors.ParserError as erro:
        raise ErroValidacaoCSV("Não foi possível interpretar o arquivo CSV.") from erro

    dados.columns = dados.columns.str.strip().str.lower()
    colunas_ausentes = [
        coluna for coluna in COLUNAS_OBRIGATORIAS if coluna not in dados.columns
    ]

    if colunas_ausentes:
        raise ErroValidacaoCSV(
            "Colunas obrigatórias ausentes: " + ", ".join(colunas_ausentes) + "."
        )

    if dados.empty:
        raise ErroValidacaoCSV("O arquivo CSV não possui registros.")

    dados = dados.loc[:, list(COLUNAS_OBRIGATORIAS)].copy()

    for coluna, limite in LIMITES_TEXTO.items():
        dados[coluna] = dados[coluna].astype("string").str.strip()
        valores_vazios = dados[coluna].isna() | dados[coluna].eq("")
        if valores_vazios.any():
            raise ErroValidacaoCSV(
                f"A coluna '{coluna}' possui valores vazios nas linhas "
                f"{_mostrar_linhas(dados.index[valores_vazios].tolist())}."
            )

        valores_longos = dados[coluna].str.len() > limite
        if valores_longos.any():
            raise ErroValidacaoCSV(
                f"A coluna '{coluna}' ultrapassa {limite} caracteres nas linhas "
                f"{_mostrar_linhas(dados.index[valores_longos].tolist())}."
            )

    datas = pd.to_datetime(dados["data"], format="%Y-%m-%d", errors="coerce")
    datas_invalidas = datas.isna()
    if datas_invalidas.any():
        raise ErroValidacaoCSV(
            "A coluna 'data' deve usar o formato AAAA-MM-DD. Linhas inválidas: "
            f"{_mostrar_linhas(dados.index[datas_invalidas].tolist())}."
        )
    dados["data"] = datas.dt.date

    quantidades = pd.to_numeric(dados["quantidade"], errors="coerce")
    quantidades_invalidas = (
        quantidades.isna()
        | quantidades.le(0)
        | quantidades.gt(2_147_483_647)
        | quantidades.mod(1).ne(0)
    )
    if quantidades_invalidas.any():
        raise ErroValidacaoCSV(
            "A coluna 'quantidade' deve conter números inteiros positivos. "
            "Linhas inválidas: "
            f"{_mostrar_linhas(dados.index[quantidades_invalidas].tolist())}."
        )
    dados["quantidade"] = quantidades.astype(int)

    valores_prescricao = (
        dados["prescricao"].astype("string").str.strip().str.lower()
    )
    prescricoes = valores_prescricao.map(
        {
            "sim": True,
            "true": True,
            "1": True,
            "não": False,
            "nao": False,
            "false": False,
            "0": False,
        }
    )
    prescricoes_invalidas = prescricoes.isna()
    if prescricoes_invalidas.any():
        raise ErroValidacaoCSV(
            "A coluna 'prescricao' aceita sim/não, true/false ou 1/0. "
            "Linhas inválidas: "
            f"{_mostrar_linhas(dados.index[prescricoes_invalidas].tolist())}."
        )
    dados["prescricao"] = prescricoes.astype(bool)

    return dados


def importar_vendas_csv(arquivo):
    """Valida o CSV e insere todas as vendas em uma única transação."""

    dados = _ler_e_validar_csv(arquivo)
    vendas = [Venda(**registro) for registro in dados.to_dict(orient="records")]

    try:
        db.session.add_all(vendas)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise

    return len(vendas)
