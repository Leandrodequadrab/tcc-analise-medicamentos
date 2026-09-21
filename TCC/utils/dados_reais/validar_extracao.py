"""Relatório de qualidade da amostra real, sem modificar ou remover linhas."""

import pandas as pd


CHAVE_VENDA = ["cod_rede", "cod_filial", "num_nota"]
CHAVE_ITEM = ["cod_rede", "cod_filial", "num_nota", "num_sequencial"]
IDENTIFICADORES = CHAVE_ITEM + ["cod_reduzido"]


def _texto_ausente(serie):
    return serie.isna() | serie.astype("string").str.strip().eq("")


def gerar_relatorio_qualidade(dados):
    """Calcula indicadores de qualidade sem alterar o DataFrame recebido."""

    datas = pd.to_datetime(dados["data"], errors="coerce")
    quantidades = pd.to_numeric(dados["quantidade"], errors="coerce")
    identificadores_ausentes = dados[IDENTIFICADORES].isna()
    chave_item_completa = dados[CHAVE_ITEM].notna().all(axis=1)
    mascara_duplicidades = chave_item_completa & dados.duplicated(
        subset=CHAVE_ITEM, keep=False
    )
    duplicidades_excedentes = chave_item_completa & dados.duplicated(
        subset=CHAVE_ITEM, keep="first"
    )

    relatorio = {
        "quantidade_linhas": len(dados),
        "vendas_distintas": (
            dados[CHAVE_VENDA].dropna().drop_duplicates().shape[0]
        ),
        "produtos_distintos": (
            dados[["cod_rede", "cod_reduzido"]]
            .dropna()
            .drop_duplicates()
            .shape[0]
        ),
        "filiais_presentes": sorted(
            dados["cod_filial"].dropna().astype(int).unique().tolist()
        ),
        "data_minima": datas.min().date().isoformat() if datas.notna().any() else None,
        "data_maxima": datas.max().date().isoformat() if datas.notna().any() else None,
        "soma_quantidades": float(quantidades.sum()) if quantidades.notna().any() else 0.0,
        "produtos_sem_descricao": int(_texto_ausente(dados["nom_produto"]).sum()),
        "quantidades_nulas": int(quantidades.isna().sum()),
        "quantidades_zero": int(quantidades.eq(0).sum()),
        "quantidades_negativas": int(quantidades.lt(0).sum()),
        "datas_invalidas": int(datas.isna().sum()),
        "registros_com_identificador_ausente": int(
            identificadores_ausentes.any(axis=1).sum()
        ),
        "identificadores_ausentes_por_coluna": {
            coluna: int(total)
            for coluna, total in identificadores_ausentes.sum().items()
        },
        "possiveis_duplicidades": int(duplicidades_excedentes.sum()),
        "linhas_em_chaves_duplicadas": int(mascara_duplicidades.sum()),
        "itens_com_correspondencia_cadcvend": len(dados),
        "itens_com_correspondencia_cadprodu": int(
            dados["produto_encontrado"].eq(True).sum()
        ),
        "itens_sem_correspondencia_cadprodu": int(
            dados["produto_encontrado"].eq(False).sum()
        ),
        "produtos_correspondentes_com_cod_produto_nulo": int(
            (dados["produto_encontrado"].eq(True) & dados["cod_produto"].isna()).sum()
        ),
    }

    return {
        "relatorio": relatorio,
        "possiveis_duplicidades": dados.loc[mascara_duplicidades].copy(),
    }
