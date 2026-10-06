"""Diagnóstico somente leitura da agregação temporal pela chave híbrida."""

from utils.agregacao_chave_analitica import (
    agregar_vendas_por_chave,
    preparar_vendas_elegiveis,
    resumir_representacao,
)
from utils.chave_analitica import carregar_cadastro_dcb, criar_chaves_analiticas
from utils.ml.diagnosticar_series import carregar_agregacao_diaria


def executar_diagnostico():
    dados = carregar_agregacao_diaria()
    vendas = dados["serie_vendas"]
    catalogo = dados["catalogo_classificado"]
    cadastro_dcb = carregar_cadastro_dcb()

    chaves_catalogo = criar_chaves_analiticas(catalogo, cadastro_dcb)
    elegiveis = preparar_vendas_elegiveis(vendas, catalogo, cadastro_dcb)
    agregada = agregar_vendas_por_chave(vendas, catalogo, cadastro_dcb)

    antiga = (
        elegiveis.groupby(
            ["filial", "codigo_produto", "data"], as_index=False
        )["quantidade_total"]
        .sum()
    )
    resumo_antigo = resumir_representacao(
        antiga, ["filial", "codigo_produto"]
    )
    nova_para_resumo = agregada.rename(
        columns={"quantidade_dia": "quantidade_total"}
    )
    resumo_novo = resumir_representacao(
        nova_para_resumo, ["tipo_chave", "chave_analitica"]
    )

    losartana = elegiveis.loc[
        (elegiveis["tipo_chave"] == "DCB")
        & (elegiveis["chave_analitica"] == "LOSARTANA POTÁSSICA")
    ]
    dias_compartilhados = (
        losartana.groupby("data")["codigo_produto"].nunique().loc[lambda s: s >= 2]
    )
    if dias_compartilhados.empty:
        exemplo_losartana = None
    else:
        data_exemplo = dias_compartilhados.index.min()
        exemplo_losartana = losartana.loc[losartana["data"] == data_exemplo].copy()
        quantidade_itens = exemplo_losartana["quantidade_total"].sum()
        quantidade_agregada = agregada.loc[
            (agregada["data"] == data_exemplo)
            & (agregada["chave_analitica"] == "LOSARTANA POTÁSSICA"),
            "quantidade_dia",
        ].sum()
        if quantidade_itens != quantidade_agregada:
            raise ValueError("A soma do exemplo de Losartana não fechou.")

    return {
        "banco": dados["banco"],
        "somente_leitura": dados["somente_leitura"],
        "catalogo_total": len(catalogo),
        "produtos_elegiveis": len(chaves_catalogo),
        "produtos_dcb": int((chaves_catalogo["tipo_chave"] == "DCB").sum()),
        "produtos_sem_dcb": int((chaves_catalogo["tipo_chave"] == "PRODUTO").sum()),
        "dcbs_distintas": int(
            chaves_catalogo.loc[
                chaves_catalogo["tipo_chave"] == "DCB", "chave_analitica"
            ].nunique()
        ),
        "chaves_catalogo_distintas": int(
            chaves_catalogo[["tipo_chave", "chave_analitica"]]
            .drop_duplicates()
            .shape[0]
        ),
        "chaves_ausentes": int(chaves_catalogo["chave_analitica"].isna().sum()),
        "quantidade_elegiveis_antes": elegiveis["quantidade_total"].sum(),
        "quantidade_depois": agregada["quantidade_dia"].sum(),
        "series_chave_observadas": resumo_novo["series"],
        "resumo_antigo": resumo_antigo,
        "resumo_novo": resumo_novo,
        "exemplo_losartana": exemplo_losartana,
        "agregada": agregada,
    }


def main():
    relatorio = executar_diagnostico()
    print("BANCO / SEGURANÇA")
    print(
        f"banco={relatorio['banco']} | "
        f"transaction_read_only={relatorio['somente_leitura']}"
    )
    print("\nCATÁLOGO E CHAVES")
    print(f"Produtos no catálogo: {relatorio['catalogo_total']}")
    print(f"Produtos elegíveis: {relatorio['produtos_elegiveis']}")
    print(f"Com DCB: {relatorio['produtos_dcb']}")
    print(f"Fallback PRODUTO: {relatorio['produtos_sem_dcb']}")
    print(f"DCBs distintas: {relatorio['dcbs_distintas']}")
    print(f"Chaves distintas no catálogo: {relatorio['chaves_catalogo_distintas']}")
    print(f"Elegíveis sem chave: {relatorio['chaves_ausentes']}")
    print(f"Séries chave observadas: {relatorio['series_chave_observadas']}")
    print("\nCONSERVAÇÃO DE QUANTIDADES")
    print(f"Elegíveis antes da agregação: {relatorio['quantidade_elegiveis_antes']}")
    print(f"Depois da agregação: {relatorio['quantidade_depois']}")
    print("\nCOMPARAÇÃO DE REPRESENTAÇÕES")
    print("Antiga (filial + código + data):", relatorio["resumo_antigo"])
    print("Nova (data + tipo_chave + chave_analitica):", relatorio["resumo_novo"])
    print("\nEXEMPLO LOSARTANA POTÁSSICA")
    exemplo = relatorio["exemplo_losartana"]
    if exemplo is None:
        print("Não há data com vendas de dois ou mais códigos da DCB.")
    else:
        print(
            exemplo[["data", "codigo_produto", "quantidade_total"]]
            .sort_values(["data", "codigo_produto"])
            .to_string(index=False)
        )
        print(
            "Total diário consolidado:",
            exemplo["quantidade_total"].sum(),
        )
    print("\nA agregação foi executada em memória; nenhum resultado foi persistido.")


if __name__ == "__main__":
    main()
