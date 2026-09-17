"""Funções de limpeza e pré-processamento dos dados de vendas."""

import pandas as pd


COLUNAS_OBRIGATORIAS = (
    "data",
    "medicamento",
    "categoria",
    "quantidade",
    "cidade",
    "prescricao",
)


def tratar_valores_nulos(dados):
    """Troca textos vazios por valores nulos, sem preencher dados desconhecidos."""

    resultado = dados.copy()
    colunas_texto = ["medicamento", "categoria", "cidade", "prescricao"]

    for coluna in colunas_texto:
        resultado[coluna] = resultado[coluna].astype("string")
        resultado[coluna] = resultado[coluna].replace(r"^\s*$", pd.NA, regex=True)

    return resultado


def remover_espacos_extras(dados):
    """Remove espaços das extremidades e reduz espaços repetidos nos textos."""

    resultado = dados.copy()
    colunas_texto = ["medicamento", "categoria", "cidade", "prescricao"]

    for coluna in colunas_texto:
        resultado[coluna] = resultado[coluna].str.strip()
        resultado[coluna] = resultado[coluna].str.replace(r"\s+", " ", regex=True)

    return resultado


def padronizar_medicamentos(dados):
    """Padroniza nomes de medicamentos usando iniciais maiúsculas."""

    resultado = dados.copy()
    resultado["medicamento"] = resultado["medicamento"].str.lower().str.title()
    return resultado


def padronizar_categorias(dados):
    """Padroniza os nomes das categorias."""

    resultado = dados.copy()
    resultado["categoria"] = resultado["categoria"].str.lower().str.title()
    return resultado


def padronizar_cidades(dados):
    """Padroniza os nomes das cidades."""

    resultado = dados.copy()
    resultado["cidade"] = resultado["cidade"].str.lower().str.title()
    return resultado


def converter_datas(dados):
    """Converte datas AAAA-MM-DD ou DD/MM/AAAA para o tipo do Pandas."""

    resultado = dados.copy()
    textos = resultado["data"].astype("string").str.strip()
    datas_iso = pd.to_datetime(textos, format="%Y-%m-%d", errors="coerce")
    datas_brasileiras = pd.to_datetime(textos, format="%d/%m/%Y", errors="coerce")
    resultado["data"] = datas_iso.fillna(datas_brasileiras)
    return resultado


def converter_quantidades(dados):
    """Converte quantidades para número; valores não numéricos viram nulos."""

    resultado = dados.copy()
    resultado["quantidade"] = pd.to_numeric(
        resultado["quantidade"], errors="coerce"
    )
    return resultado


def padronizar_prescricao(dados):
    """Converte diferentes representações de prescrição para True ou False."""

    resultado = dados.copy()
    valores = resultado["prescricao"].str.lower()
    resultado["prescricao"] = valores.map(
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
    return resultado


def identificar_registros_invalidos(dados):
    """Retorna os motivos de invalidez de cada registro."""

    motivos = pd.Series([[] for _ in range(len(dados))], index=dados.index)

    def adicionar_motivo(mascara, mensagem):
        for indice in dados.index[mascara]:
            motivos.at[indice].append(mensagem)

    adicionar_motivo(dados["data"].isna(), "data ausente ou inválida")
    adicionar_motivo(
        dados["medicamento"].isna(), "medicamento ausente"
    )
    adicionar_motivo(dados["categoria"].isna(), "categoria ausente")
    adicionar_motivo(dados["cidade"].isna(), "cidade ausente")
    adicionar_motivo(
        dados["quantidade"].isna()
        | dados["quantidade"].le(0)
        | dados["quantidade"].mod(1).ne(0),
        "quantidade ausente ou inválida",
    )
    adicionar_motivo(
        dados["prescricao"].isna(), "prescricao ausente ou inválida"
    )

    return motivos.apply(lambda itens: "; ".join(itens))


def remover_registros_duplicados(dados):
    """Separa registros duplicados e mantém a primeira ocorrência."""

    mascara_duplicados = dados.duplicated(
        subset=list(COLUNAS_OBRIGATORIAS), keep="first"
    )
    dados_sem_duplicados = dados.loc[~mascara_duplicados].copy()
    duplicados = dados.loc[mascara_duplicados].copy()
    return dados_sem_duplicados, duplicados


def limpar_dados(dados):
    """Executa todas as transformações e retorna dados e relatório separados."""

    colunas_ausentes = [
        coluna for coluna in COLUNAS_OBRIGATORIAS if coluna not in dados.columns
    ]
    if colunas_ausentes:
        raise ValueError(
            "Colunas obrigatórias ausentes: " + ", ".join(colunas_ausentes)
        )

    originais = dados.loc[:, list(COLUNAS_OBRIGATORIAS)].copy(deep=True)
    originais = originais.reset_index(drop=True)
    tratados = tratar_valores_nulos(originais)
    tratados = remover_espacos_extras(tratados)
    tratados = padronizar_medicamentos(tratados)
    tratados = padronizar_categorias(tratados)
    tratados = padronizar_cidades(tratados)
    tratados = converter_datas(tratados)
    tratados = converter_quantidades(tratados)
    tratados = padronizar_prescricao(tratados)

    motivos = identificar_registros_invalidos(tratados)
    mascara_invalidos = motivos.ne("")

    invalidos = originais.loc[mascara_invalidos].copy()
    invalidos.insert(0, "linha_csv", invalidos.index + 2)
    invalidos["motivo_invalido"] = motivos.loc[mascara_invalidos]

    validos = tratados.loc[~mascara_invalidos].copy()
    validos, duplicados_tratados = remover_registros_duplicados(validos)

    duplicados = originais.loc[duplicados_tratados.index].copy()
    duplicados.insert(0, "linha_csv", duplicados.index + 2)

    validos["quantidade"] = validos["quantidade"].astype("int64")
    validos = validos.reset_index(drop=True)
    invalidos = invalidos.reset_index(drop=True)
    duplicados = duplicados.reset_index(drop=True)

    relatorio = {
        "quantidade_antes": len(originais),
        "quantidade_depois": len(validos),
        "duplicados_removidos": len(duplicados),
        "registros_invalidos": len(invalidos),
    }

    return {
        "dados_limpos": validos,
        "registros_invalidos": invalidos,
        "registros_duplicados": duplicados,
        "relatorio": relatorio,
    }
