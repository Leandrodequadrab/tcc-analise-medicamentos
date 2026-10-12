from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from utils.chave_analitica import criar_chaves_analiticas
from utils.ml.diagnosticar_series import (
    SQL_RESUMO_BANCO,
    _confirmar_conexao,
    _criar_engine_somente_leitura,
    carregar_agregacao_diaria,
)
from utils.ml.modelo_c_chave_analitica import (
    construir_diagnostico_chave,
    executar_modelo_c_hibrido_em_diagnostico,
    resumir_execucao_hibrida,
)


CODIGOS_CONTINUOS = {"36", "44", "56"}
TIPOS_CONTINUOS = {
    "36": "MED GENERICO CONTINUO",
    "44": "MED PROPAGADO CONTINUO",
    "56": "MED SIMILAR CONTINUO",
}
CAMINHO_CSV = Path(__file__).resolve().parents[2] / "dados" / "cadastro_dcb_farmacia.csv"


def _normalizar_codigo(valor):
    if valor is None or pd.isna(valor):
        return None
    codigo = str(valor).strip()
    if codigo.endswith(".0") and codigo[:-2].isdigit():
        codigo = codigo[:-2]
    return codigo or None


def _normalizar_tipo(valor):
    if valor is None or pd.isna(valor):
        return None
    codigo = str(valor).strip()
    if codigo.endswith(".0") and codigo[:-2].isdigit():
        codigo = codigo[:-2]
    return codigo or None


def _ler_csv(caminho):
    erro = None
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            return pd.read_csv(
                caminho,
                sep=";",
                dtype={"cod_reduzido": "string", "cod_tipo": "string"},
                encoding=encoding,
            ), encoding
        except UnicodeDecodeError as atual:
            erro = atual
    raise ValueError(f"Não foi possível decodificar o cadastro CSV: {erro}")


def _hash_arquivo(caminho):
    resumo = hashlib.sha256()
    with Path(caminho).open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _resumo_banco_somente_leitura():
    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            banco, leitura = _confirmar_conexao(conexao)
            resumo = dict(conexao.execute(SQL_RESUMO_BANCO).mappings().one())
            return banco, leitura, resumo
    finally:
        engine.dispose()


def _mapear_tipo_csv(cadastro):
    obrigatorias = {"cod_reduzido", "nom_produto", "cod_dcb", "nom_dcb", "cod_tipo", "nom_tipo"}
    ausentes = obrigatorias.difference(cadastro.columns)
    if ausentes:
        raise ValueError(
            "Colunas ausentes no CSV: " + ", ".join(sorted(ausentes))
        )

    tipos = cadastro.loc[:, ["cod_reduzido", "cod_tipo", "nom_tipo"]].copy()
    tipos["_codigo_join"] = tipos["cod_reduzido"].map(_normalizar_codigo)
    tipos["cod_tipo"] = tipos["cod_tipo"].map(_normalizar_tipo)
    tipos["nom_tipo"] = tipos["nom_tipo"].astype("string").str.strip()
    tipos = tipos.loc[tipos["_codigo_join"].notna()]

    conflitos = (
        tipos.groupby("_codigo_join")["cod_tipo"].nunique(dropna=True)
        .loc[lambda serie: serie > 1]
    )
    if not conflitos.empty:
        raise ValueError(
            "O CSV associa códigos a mais de um cod_tipo. Exemplos: "
            + ", ".join(conflitos.index.astype(str).tolist()[:10])
        )

    return tipos.drop_duplicates("_codigo_join", keep="first"), int(len(conflitos))


def _percentual(parte, total):
    return 0.0 if not total else 100.0 * parte / total


def _produto_tipo(catalogo, mapa_tipos):
    produtos = catalogo.copy()
    produtos["_codigo_join"] = produtos["codigo_produto"].map(_normalizar_codigo)
    codigos_csv = set(mapa_tipos["_codigo_join"].dropna())
    produtos["_codigo_encontrado_csv"] = produtos["_codigo_join"].isin(codigos_csv)
    produtos = produtos.merge(
        mapa_tipos[["_codigo_join", "cod_tipo", "nom_tipo"]],
        on="_codigo_join",
        how="left",
        validate="many_to_one",
        suffixes=("_tcc", "_csv"),
    )
    return produtos


def _chaves_elegiveis(produtos, cadastro, aplicar_exclusao=True):
    elegiveis = produtos.loc[
        produtos["elegivel_analise_automedicacao"]
    ].copy()
    catalogo = cadastro
    if not aplicar_exclusao:
        catalogo = cadastro.drop(columns=["cod_tipo"], errors="ignore")
    return criar_chaves_analiticas(elegiveis, catalogo)


def _resumo_temporal(serie_vendas, produtos, cadastro, aplicar_exclusao=True):
    catalogo = cadastro
    if not aplicar_exclusao:
        catalogo = cadastro.drop(columns=["cod_tipo"], errors="ignore")
    diagnostico = construir_diagnostico_chave(
        serie_vendas, produtos, catalogo
    )
    pipeline = executar_modelo_c_hibrido_em_diagnostico(
        diagnostico, permitir_historico_insuficiente=True
    )
    geral = resumir_execucao_hibrida(pipeline)["geral"]
    return {
        "chaves": geral["chaves_totais"],
        "modelaveis": geral["series_modelaveis"],
        "historico_insuficiente": geral["series_historico_insuficiente"],
        "observacoes_modelaveis": geral["observacoes_modelaveis"],
    }


def executar_diagnostico(caminho_csv=CAMINHO_CSV):
    caminho_csv = Path(caminho_csv)
    hash_csv_inicial = _hash_arquivo(caminho_csv)
    dados = carregar_agregacao_diaria()
    if dados["banco"] != "tcc_medicamentos" or dados["somente_leitura"] != "on":
        raise RuntimeError("A fonte do TCC não está confirmada como somente leitura.")

    cadastro, encoding = _ler_csv(caminho_csv)
    mapa_tipos, _ = _mapear_tipo_csv(cadastro)
    produtos = _produto_tipo(dados["catalogo_classificado"], mapa_tipos)
    produtos["_produto_continuo"] = produtos["cod_tipo_csv"].isin(
        CODIGOS_CONTINUOS
    )

    total_csv = len(cadastro)
    codigos_unicos_csv = int(
        cadastro["cod_reduzido"].map(_normalizar_codigo).nunique(dropna=True)
    )
    contagem_csv_tipo = {
        codigo: int((cadastro["cod_tipo"].map(_normalizar_tipo) == codigo).sum())
        for codigo in sorted(CODIGOS_CONTINUOS)
    }
    produtos_base = int(
        produtos[["filial", "codigo_produto"]].drop_duplicates().shape[0]
    )
    base_sem_tipo = int(produtos["cod_tipo_csv"].isna().sum())
    base_sem_codigo_csv = int((~produtos["_codigo_encontrado_csv"]).sum())
    tipo_no_csv = produtos["cod_tipo_csv"].isin(CODIGOS_CONTINUOS)
    total_continuos = int(
        produtos.loc[tipo_no_csv, ["filial", "codigo_produto"]]
        .drop_duplicates()
        .shape[0]
    )

    produtos_elegiveis = produtos.loc[
        produtos["elegivel_analise_automedicacao"]
    ].copy()
    produtos_elegiveis_chave = _chaves_elegiveis(
        produtos, cadastro, aplicar_exclusao=False
    )
    elegiveis = int(
        produtos_elegiveis[["filial", "codigo_produto"]].drop_duplicates().shape[0]
    )
    elegiveis_sem_tipo = int(
        produtos_elegiveis.loc[
            produtos_elegiveis["cod_tipo_csv"].isna(),
            ["filial", "codigo_produto"],
        ].drop_duplicates().shape[0]
    )
    elegiveis_continuos = int(
        produtos_elegiveis.loc[
            produtos_elegiveis["cod_tipo_csv"].isin(CODIGOS_CONTINUOS),
            ["filial", "codigo_produto"],
        ].drop_duplicates().shape[0]
    )
    elegiveis_dcb = int(
        produtos_elegiveis_chave.loc[
            produtos_elegiveis_chave["tipo_chave"] == "DCB",
            ["filial", "codigo_produto"],
        ].drop_duplicates().shape[0]
    )
    elegiveis_sem_dcb = elegiveis - elegiveis_dcb
    elegiveis_continuos_dcb = int(
        produtos_elegiveis_chave.loc[
            (produtos_elegiveis_chave["tipo_chave"] == "DCB")
            & produtos_elegiveis_chave["codigo_produto"]
            .map(_normalizar_codigo)
            .isin(
                set(
                    produtos_elegiveis.loc[
                        produtos_elegiveis["cod_tipo_csv"].isin(CODIGOS_CONTINUOS),
                        "codigo_produto",
                    ].map(_normalizar_codigo)
                )
            ),
            ["filial", "codigo_produto"],
        ].drop_duplicates().shape[0]
    )
    elegiveis_continuos_sem_dcb = elegiveis_continuos - elegiveis_continuos_dcb

    serie = dados["serie_vendas"].copy()
    serie["_codigo_join"] = serie["codigo_produto"].map(_normalizar_codigo)
    serie = serie.merge(
        produtos[["filial", "codigo_produto", "cod_tipo_csv", "_produto_continuo"]],
        on=["filial", "codigo_produto"],
        how="left",
        validate="many_to_one",
    )
    elegiveis_produtos_chave = produtos_elegiveis_chave.copy()
    elegiveis_produtos_chave["_produto_continuo"] = (
        elegiveis_produtos_chave["codigo_produto"]
        .map(_normalizar_codigo)
        .isin(
            set(
                produtos_elegiveis.loc[
                    produtos_elegiveis["cod_tipo_csv"].isin(CODIGOS_CONTINUOS),
                    "codigo_produto",
                ].map(_normalizar_codigo)
            )
        )
    )

    chaves_antes = elegiveis_produtos_chave[
        ["tipo_chave", "chave_analitica"]
    ].drop_duplicates()
    chaves_depois = elegiveis_produtos_chave.loc[
        ~elegiveis_produtos_chave["_produto_continuo"],
        ["tipo_chave", "chave_analitica"],
    ].drop_duplicates()
    chaves_dcb_antes = set(
        chaves_antes.loc[chaves_antes["tipo_chave"] == "DCB", "chave_analitica"]
    )
    chaves_dcb_depois = set(
        chaves_depois.loc[chaves_depois["tipo_chave"] == "DCB", "chave_analitica"]
    )
    dcb_sumidas = chaves_dcb_antes - chaves_dcb_depois
    dcb_mistas = (
        elegiveis_produtos_chave.loc[
            elegiveis_produtos_chave["tipo_chave"] == "DCB"
        ]
        .groupby("chave_analitica")
        .agg(
            possui_continuo=("_produto_continuo", "any"),
            possui_nao_continuo=("_produto_continuo", lambda valores: (~valores).any()),
            codigos=("codigo_produto", lambda valores: sorted(set(map(str, valores)))),
        )
    )
    dcb_mistas = dcb_mistas.loc[
        dcb_mistas["possui_continuo"] & dcb_mistas["possui_nao_continuo"]
    ]

    vendas_continuas = serie.loc[
        serie["elegivel_analise_automedicacao"] & serie["_produto_continuo"]
    ]
    produtos_por_tipo = (
        produtos_elegiveis.loc[
            produtos_elegiveis["cod_tipo_csv"].isin(CODIGOS_CONTINUOS)
        ]
        .groupby("cod_tipo_csv")["codigo_produto"]
        .nunique()
    )
    unidades_por_tipo = vendas_continuas.groupby("cod_tipo_csv")[
        "quantidade_total"
    ].sum()
    dias_positivos_por_tipo = (
        vendas_continuas.loc[vendas_continuas["quantidade_total"] > 0]
        .groupby("cod_tipo_csv")["data"]
        .count()
    )
    dias_por_tipo = pd.DataFrame(
        {
            "produtos_elegiveis": produtos_por_tipo,
            "unidades": unidades_por_tipo,
            "dias_positivos_produto_dia": dias_positivos_por_tipo,
        }
    ).fillna(0)
    dcb_por_tipo = (
        produtos_elegiveis_chave.loc[
            produtos_elegiveis_chave["cod_tipo_csv"].isin(CODIGOS_CONTINUOS)
        ]
        .assign(com_dcb=lambda quadro: quadro["tipo_chave"] == "DCB")
        .groupby("cod_tipo_csv")["com_dcb"]
        .sum()
    )

    resumo_antes = _resumo_temporal(
        dados["serie_vendas"], produtos, cadastro, aplicar_exclusao=False
    )
    produtos_simulados = produtos.copy()
    produtos_simulados.loc[
        produtos_simulados["_produto_continuo"],
        "elegivel_analise_automedicacao",
    ] = False
    resumo_depois = _resumo_temporal(
        dados["serie_vendas"], produtos_simulados, cadastro
    )

    exemplos = produtos_elegiveis.loc[
        produtos_elegiveis["cod_tipo_csv"].isin(CODIGOS_CONTINUOS),
        ["codigo_produto", "descricao_original", "cod_tipo_csv", "nom_tipo_csv"],
    ].drop_duplicates("codigo_produto").head(10)
    exemplos = exemplos.merge(
        produtos_elegiveis_chave[["codigo_produto", "cod_dcb", "nom_dcb"]]
        .drop_duplicates("codigo_produto"),
        on="codigo_produto",
        how="left",
        validate="one_to_one",
    )

    banco_depois, leitura_depois, resumo_banco_depois = _resumo_banco_somente_leitura()
    hash_csv_final = _hash_arquivo(caminho_csv)
    integridade = (
        banco_depois == dados["banco"]
        and leitura_depois == "on"
        and resumo_banco_depois == dados["resumo_banco"]
        and hash_csv_final == hash_csv_inicial
    )

    return {
        "encoding": encoding,
        "csv_linhas": total_csv,
        "csv_colunas": list(cadastro.columns),
        "csv_codigos_unicos": codigos_unicos_csv,
        "csv_contagem_tipo": contagem_csv_tipo,
        "csv_codigos_duplicados": int(cadastro["cod_reduzido"].map(_normalizar_codigo).duplicated().sum()),
        "total_produtos": produtos_base,
        "produtos_sem_tipo": base_sem_tipo,
        "produtos_sem_codigo_csv": base_sem_codigo_csv,
        "produtos_continuos": total_continuos,
        "percentual_continuos": _percentual(total_continuos, produtos_base),
        "elegiveis": elegiveis,
        "elegiveis_sem_tipo": elegiveis_sem_tipo,
        "elegiveis_continuos": elegiveis_continuos,
        "percentual_elegiveis_continuos": _percentual(elegiveis_continuos, elegiveis),
        "elegiveis_dcb": elegiveis_dcb,
        "elegiveis_continuos_dcb": elegiveis_continuos_dcb,
        "elegiveis_sem_dcb": elegiveis_sem_dcb,
        "elegiveis_continuos_sem_dcb": elegiveis_continuos_sem_dcb,
        "elegiveis_restantes": elegiveis - elegiveis_continuos,
        "elegiveis_dcb_restantes": elegiveis_dcb - elegiveis_continuos_dcb,
        "elegiveis_sem_dcb_restantes": elegiveis_sem_dcb - elegiveis_continuos_sem_dcb,
        "chaves_antes": len(chaves_antes),
        "chaves_depois": len(chaves_depois),
        "dcb_sumidas": len(dcb_sumidas),
        "dcb_mistas": int(len(dcb_mistas)),
        "dcb_mistas_exemplos": dcb_mistas.head(10).reset_index().to_dict("records"),
        "dias_por_tipo": dias_por_tipo,
        "dcb_por_tipo": dcb_por_tipo,
        "exemplos": exemplos,
        "temporal_antes": resumo_antes,
        "temporal_depois": resumo_depois,
        "banco": dados["banco"],
        "somente_leitura": dados["somente_leitura"],
        "banco_inalterado": integridade,
        "csv_inalterado": hash_csv_final == hash_csv_inicial,
        "resumo_banco": dados["resumo_banco"],
    }


def main():
    resultado = executar_diagnostico()
    print("VALIDAÇÃO DO CSV")
    print(f"Encoding: {resultado['encoding']}")
    print(f"Linhas: {resultado['csv_linhas']:,}")
    print(f"Colunas: {', '.join(resultado['csv_colunas'])}")
    print(f"cod_reduzido únicos: {resultado['csv_codigos_unicos']:,}")
    print(f"Duplicidades de cod_reduzido: {resultado['csv_codigos_duplicados']:,}")
    print("Registros por cod_tipo (CSV):")
    for codigo, nome in TIPOS_CONTINUOS.items():
        print(f"  {codigo} | {nome}: {resultado['csv_contagem_tipo'][codigo]:,}")

    print("\nUNIVERSO DO TCC")
    print(f"Produtos: {resultado['total_produtos']:,}")
    print(f"Uso contínuo: {resultado['produtos_continuos']:,} ({resultado['percentual_continuos']:.2f}%)")
    print(f"Produtos sem correspondência de tipo no CSV: {resultado['produtos_sem_tipo']:,}")
    print(f"Produtos sem código correspondente no CSV: {resultado['produtos_sem_codigo_csv']:,}")
    print(f"Elegíveis: {resultado['elegiveis']:,}")
    print(f"Elegíveis de uso contínuo: {resultado['elegiveis_continuos']:,} ({resultado['percentual_elegiveis_continuos']:.2f}%)")
    print(f"Elegíveis sem tipo confirmado no CSV: {resultado['elegiveis_sem_tipo']:,}")
    print(f"Elegíveis com DCB: total={resultado['elegiveis_dcb']:,}; contínuos={resultado['elegiveis_continuos_dcb']:,} ({_percentual(resultado['elegiveis_continuos_dcb'], resultado['elegiveis_dcb']):.2f}%); restantes={resultado['elegiveis_dcb_restantes']:,}")
    print(f"Elegíveis sem DCB: total={resultado['elegiveis_sem_dcb']:,}; contínuos={resultado['elegiveis_continuos_sem_dcb']:,} ({_percentual(resultado['elegiveis_continuos_sem_dcb'], resultado['elegiveis_sem_dcb']):.2f}%); restantes={resultado['elegiveis_sem_dcb_restantes']:,}")

    print("\nDISTRIBUIÇÃO DOS ELEGÍVEIS CONTÍNUOS")
    for codigo, nome in TIPOS_CONTINUOS.items():
        linha = resultado["dias_por_tipo"].loc[codigo] if codigo in resultado["dias_por_tipo"].index else None
        dcb = int(resultado["dcb_por_tipo"].get(codigo, 0))
        elegiveis = 0 if linha is None else int(linha["produtos_elegiveis"])
        unidades = 0 if linha is None else float(linha["unidades"])
        dias = 0 if linha is None else int(linha["dias_positivos_produto_dia"])
        print(f"{codigo} | {nome}: produtos={elegiveis:,}, com DCB={dcb:,}, sem DCB={elegiveis-dcb:,}, unidades={unidades:,.2f}, dias positivos produto-dia={dias:,}")

    print("\nIMPACTO NAS CHAVES HÍBRIDAS")
    print(f"Elegíveis restantes: {resultado['elegiveis_restantes']:,}")
    print(f"Com DCB restantes: {resultado['elegiveis_dcb_restantes']:,}")
    print(f"Sem DCB restantes: {resultado['elegiveis_sem_dcb_restantes']:,}")
    print(f"Chaves híbridas antes/depois: {resultado['chaves_antes']:,} / {resultado['chaves_depois']:,}")
    print(f"Chaves DCB que desapareceriam: {resultado['dcb_sumidas']:,}")
    print(f"Chaves DCB mistas (contínuo e não contínuo): {resultado['dcb_mistas']:,}")
    print(pd.DataFrame(resultado["dcb_mistas_exemplos"]).to_string(index=False))

    print("\nIMPACTO TEMPORAL PRELIMINAR")
    print(pd.DataFrame([{"cenário": "Atual", **resultado["temporal_antes"]}, {"cenário": "Excluindo cod_tipo 36, 44, 56", **resultado["temporal_depois"]}]).to_string(index=False))

    print("\n10 EXEMPLOS ELEGÍVEIS QUE SERIAM EXCLUÍDOS")
    print(resultado["exemplos"].to_string(index=False))
    print("\nINTEGRIDADE")
    print(f"Banco: {resultado['banco']} | transaction_read_only={resultado['somente_leitura']}")
    print(f"Banco inalterado: {resultado['banco_inalterado']}")
    print(f"CSV inalterado: {resultado['csv_inalterado']}")
    if not resultado["banco_inalterado"] or not resultado["csv_inalterado"]:
        raise RuntimeError("A validação de integridade falhou.")


if __name__ == "__main__":
    main()
