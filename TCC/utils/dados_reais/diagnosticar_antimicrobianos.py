"""Mede em memoria o impacto de excluir tipos antimicrobianos do universo atual.

Somente consulta tcc_medicamentos em modo read-only. Nenhum resultado e
persistido; a exclusao de tipos 36/44/56 ja aplicada permanece como base.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.chave_analitica import criar_chaves_analiticas
from utils.dados_reais.diagnosticar_uso_continuo import (
    CAMINHO_CSV,
    _hash_arquivo,
    _ler_csv,
    _mapear_tipo_csv,
    _normalizar_codigo,
    _normalizar_tipo,
    _produto_tipo,
    _resumo_banco_somente_leitura,
    _resumo_temporal,
)
from utils.ml.diagnosticar_series import carregar_agregacao_diaria


TIPOS_ANTIMICROBIANOS = {
    "15": "MED GENERICO ANTIMICROBIANO",
    "102": "MED PROPAGADO ANTIMICROBIANO",
}


def _sem_nulos_unicos(quadro, colunas):
    return quadro[colunas].drop_duplicates().reset_index(drop=True)


def executar_diagnostico(caminho_csv=CAMINHO_CSV):
    caminho_csv = Path(caminho_csv)
    hash_csv_inicial = _hash_arquivo(caminho_csv)
    dados = carregar_agregacao_diaria()
    if dados["banco"] != "tcc_medicamentos" or dados["somente_leitura"] != "on":
        raise RuntimeError("Banco ou modo somente leitura não confirmado; diagnóstico interrompido.")

    cadastro, encoding = _ler_csv(caminho_csv)
    mapa_tipos, _ = _mapear_tipo_csv(cadastro)
    produtos = _produto_tipo(dados["catalogo_classificado"], mapa_tipos)
    produtos["cod_tipo_csv"] = produtos["cod_tipo_csv"].map(_normalizar_tipo)
    produtos["antimicrobiano"] = produtos["cod_tipo_csv"].isin(
        TIPOS_ANTIMICROBIANOS
    )
    elegiveis = produtos.loc[
        produtos["elegivel_analise_automedicacao"]
    ].copy()

    # Chaves do universo atual: a função oficial também aplica os tipos
    # contínuos 36/44/56 antes da agregação analítica.
    cadastro_base = cadastro.copy(deep=True)
    tipos_csv = cadastro_base["cod_tipo"].map(_normalizar_tipo)
    cadastro_base.loc[
        tipos_csv.isin(TIPOS_ANTIMICROBIANOS), "cod_tipo"
    ] = pd.NA
    chaves_atuais = criar_chaves_analiticas(elegiveis, cadastro_base)
    chave_produto = ["filial", "codigo_produto"]
    antimicrobianos_chaves = chaves_atuais.loc[
        chaves_atuais["cod_tipo_csv"].map(_normalizar_tipo).isin(
            TIPOS_ANTIMICROBIANOS
        )
    ].copy()

    serie = dados["serie_vendas"].copy()
    serie = serie.merge(
        elegiveis[chave_produto + ["cod_tipo_csv", "antimicrobiano"]],
        on=chave_produto,
        how="inner",
        validate="many_to_one",
    )
    serie_antimicrobianos = serie.loc[serie["antimicrobiano"]]

    distribuicao = []
    for codigo, nome in TIPOS_ANTIMICROBIANOS.items():
        produtos_tipo = antimicrobianos_chaves.loc[
            antimicrobianos_chaves["cod_tipo_csv"] == codigo
        ]
        codigos_tipo = set(produtos_tipo["codigo_produto"].map(_normalizar_codigo))
        vendas_tipo = serie_antimicrobianos.loc[
            (serie_antimicrobianos["cod_tipo_csv"] == codigo)
            & serie_antimicrobianos["codigo_produto"].map(_normalizar_codigo).isin(codigos_tipo)
        ]
        dcb = int((produtos_tipo["tipo_chave"] == "DCB").sum())
        distribuicao.append(
            {
                "cod_tipo": codigo,
                "nom_tipo": nome,
                "produtos_elegiveis": int(len(produtos_tipo)),
                "com_dcb": dcb,
                "sem_dcb": int(len(produtos_tipo) - dcb),
                "unidades_vendidas": float(vendas_tipo["quantidade_total"].sum()),
                "dias_positivos_produto_dia": int(
                    (vendas_tipo["quantidade_total"] > 0).sum()
                ),
            }
        )
    por_tipo = pd.DataFrame(distribuicao)

    produtos_simulados = produtos.copy()
    produtos_simulados.loc[
        produtos_simulados["elegivel_analise_automedicacao"]
        & produtos_simulados["antimicrobiano"],
        "elegivel_analise_automedicacao",
    ] = False
    chaves_simuladas = criar_chaves_analiticas(produtos_simulados, cadastro)
    chaves_dcb_atuais = set(
        chaves_atuais.loc[chaves_atuais["tipo_chave"] == "DCB", "chave_analitica"]
    )
    chaves_dcb_simuladas = set(
        chaves_simuladas.loc[
            chaves_simuladas["tipo_chave"] == "DCB", "chave_analitica"
        ]
    )
    grupos_dcb = chaves_atuais.loc[chaves_atuais["tipo_chave"] == "DCB"].copy()
    grupos_dcb["antimicrobiano"] = grupos_dcb["codigo_produto"].map(
        _normalizar_codigo
    ).isin(
        set(antimicrobianos_chaves["codigo_produto"].map(_normalizar_codigo))
    )
    dcb_composicao = grupos_dcb.groupby("chave_analitica")["antimicrobiano"].agg(
        possui_antimicrobiano="any",
        possui_nao_antimicrobiano=lambda valores: (~valores).any(),
    )
    dcb_mistas = dcb_composicao.loc[
        dcb_composicao["possui_antimicrobiano"]
        & dcb_composicao["possui_nao_antimicrobiano"]
    ]
    dcb_mistas_restantes = int(
        dcb_mistas.index.isin(chaves_dcb_simuladas).sum()
    )

    # Simulação do pipeline congelado sobre o catálogo em que os tipos 15/102
    # são inelegíveis. As observações de origem e o algoritmo não são alterados.
    temporal_atual = _resumo_temporal(
        dados["serie_vendas"], produtos, cadastro_base, aplicar_exclusao=True
    )
    temporal_simulado = _resumo_temporal(
        dados["serie_vendas"], produtos_simulados, cadastro, aplicar_exclusao=True
    )

    exemplos = antimicrobianos_chaves[
        [
            "codigo_produto",
            "descricao_original",
            "cod_tipo_csv",
            "nom_tipo_csv",
            "cod_dcb",
            "nom_dcb",
        ]
    ].drop_duplicates("codigo_produto").head(10).rename(
        columns={
            "descricao_original": "nom_produto",
            "cod_tipo_csv": "cod_tipo",
            "nom_tipo_csv": "nom_tipo",
        }
    )

    banco_final, leitura_final, resumo_final = _resumo_banco_somente_leitura()
    hash_csv_final = _hash_arquivo(caminho_csv)
    integridade = (
        banco_final == dados["banco"]
        and leitura_final == "on"
        and resumo_final == dados["resumo_banco"]
        and hash_csv_final == hash_csv_inicial
    )

    return {
        "encoding": encoding,
        "base_produtos_elegiveis": int(len(chaves_atuais)),
        "base_com_dcb": int((chaves_atuais["tipo_chave"] == "DCB").sum()),
        "base_sem_dcb": int((chaves_atuais["tipo_chave"] == "PRODUTO").sum()),
        "antimicrobianos": int(len(antimicrobianos_chaves)),
        "percentual_antimicrobianos": (
            100 * len(antimicrobianos_chaves) / len(chaves_atuais)
            if len(chaves_atuais)
            else 0.0
        ),
        "antimicrobianos_com_dcb": int(
            (antimicrobianos_chaves["tipo_chave"] == "DCB").sum()
        ),
        "antimicrobianos_sem_dcb": int(
            (antimicrobianos_chaves["tipo_chave"] == "PRODUTO").sum()
        ),
        "por_tipo": por_tipo,
        "simulado_produtos": int(len(chaves_simuladas)),
        "simulado_com_dcb": int((chaves_simuladas["tipo_chave"] == "DCB").sum()),
        "simulado_sem_dcb": int((chaves_simuladas["tipo_chave"] == "PRODUTO").sum()),
        "simulado_chaves_totais": int(
            chaves_simuladas[["tipo_chave", "chave_analitica"]].drop_duplicates().shape[0]
        ),
        "simulado_chaves_dcb": int(len(chaves_dcb_simuladas)),
        "simulado_chaves_produto": int(
            chaves_simuladas.loc[chaves_simuladas["tipo_chave"] == "PRODUTO", "chave_analitica"].nunique()
        ),
        "dcb_desapareceriam": int(len(chaves_dcb_atuais - chaves_dcb_simuladas)),
        "dcb_mistas": int(len(dcb_mistas)),
        "dcb_mistas_restantes": dcb_mistas_restantes,
        "temporal_atual": temporal_atual,
        "temporal_simulado": temporal_simulado,
        "exemplos": exemplos,
        "banco": banco_final,
        "somente_leitura": leitura_final,
        "resumo_banco": resumo_final,
        "banco_inalterado": integridade,
        "csv_inalterado": hash_csv_final == hash_csv_inicial,
    }


def main():
    resultado = executar_diagnostico()
    print("ANTIMICROBIANOS NO UNIVERSO ELEGÍVEL ATUAL (após exclusão 36/44/56)")
    print(
        f"Elegíveis: {resultado['base_produtos_elegiveis']:,} | "
        f"com DCB={resultado['base_com_dcb']:,} | sem DCB={resultado['base_sem_dcb']:,}"
    )
    print(
        f"Antimicrobianos: {resultado['antimicrobianos']:,} "
        f"({resultado['percentual_antimicrobianos']:.2f}%) | "
        f"com DCB={resultado['antimicrobianos_com_dcb']:,} | "
        f"sem DCB={resultado['antimicrobianos_sem_dcb']:,}"
    )
    print("\nDISTRIBUIÇÃO POR TIPO")
    print(resultado["por_tipo"].to_string(index=False))
    print("\nCENÁRIO SIMULADO: SEM USO CONTÍNUO E SEM ANTIMICROBIANOS")
    print(
        f"Produtos={resultado['simulado_produtos']:,}; "
        f"com DCB={resultado['simulado_com_dcb']:,}; "
        f"sem DCB={resultado['simulado_sem_dcb']:,}; "
        f"chaves totais={resultado['simulado_chaves_totais']:,}; "
        f"chaves DCB={resultado['simulado_chaves_dcb']:,}; "
        f"chaves PRODUTO={resultado['simulado_chaves_produto']:,}"
    )
    print("\nDCBs MISTAS")
    print(
        f"Desapareceriam: {resultado['dcb_desapareceriam']:,}; "
        f"mistas inicialmente: {resultado['dcb_mistas']:,}; "
        f"mistas preservadas: {resultado['dcb_mistas_restantes']:,}"
    )
    print("\nIMPACTO TEMPORAL (Modelo C congelado, em memória)")
    print(
        pd.DataFrame(
            [
                {"cenario": "Atual oficial", **resultado["temporal_atual"]},
                {"cenario": "Sem uso contínuo + sem antimicrobianos", **resultado["temporal_simulado"]},
            ]
        ).to_string(index=False)
    )
    print("\n10 EXEMPLOS REMOVIDOS")
    print(resultado["exemplos"].to_string(index=False))
    print("\nINTEGRIDADE")
    print(
        f"Banco={resultado['banco']} | transaction_read_only={resultado['somente_leitura']} | "
        f"inalterado={resultado['banco_inalterado']}"
    )
    print(f"CSV inalterado={resultado['csv_inalterado']}")
    if not resultado["banco_inalterado"] or not resultado["csv_inalterado"]:
        raise RuntimeError("Falha na verificação de integridade; diagnóstico não confirmado.")


if __name__ == "__main__":
    main()
