import unittest

import pandas as pd

from utils.chave_analitica import (
    CODIGOS_TIPO_ANTIMICROBIANO,
    CODIGOS_TIPO_USO_CONTINUO,
    anotar_antimicrobianos,
    anotar_uso_continuo,
    carregar_cadastro_dcb,
    criar_chaves_analiticas,
    normalizar_nome_dcb,
)


class ChaveAnaliticaTestCase(unittest.TestCase):
    def setUp(self):
        self.produtos = pd.DataFrame(
            [
                {
                    "filial": 6,
                    "codigo_produto": 24234,
                    "descricao_original": "LOSARTANA 50MG 30CP",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 46282,
                    "descricao_original": "LOSARTANA 50MG 30CP",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 90001,
                    "descricao_original": "PRODUTO SEM DCB",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 90002,
                    "descricao_original": "PRODUTO INELEGIVEL",
                    "elegivel_analise_automedicacao": False,
                },
            ]
        )
        self.cadastro_dcb = pd.DataFrame(
            [
                {
                    "cod_reduzido": "24234",
                    "cod_dcb": "05432",
                    "nom_dcb": "  LOSARTANA POTÁSSICA  ",
                },
                {
                    "cod_reduzido": "46282",
                    "cod_dcb": "05432",
                    "nom_dcb": "LOSARTANA   POTÁSSICA",
                },
                {
                    "cod_reduzido": "90001",
                    "cod_dcb": None,
                    "nom_dcb": None,
                },
                {
                    "cod_reduzido": "77777",
                    "cod_dcb": "00001",
                    "nom_dcb": "AMOXICILINA+CLAVULANATO DE POTÁSSIO",
                },
            ]
        )

    def test_produto_com_dcb_usa_nome_normalizado(self):
        resultado = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        losartana = resultado.loc[resultado["codigo_produto"] == 24234].iloc[0]
        self.assertEqual(losartana["tipo_chave"], "DCB")
        self.assertEqual(losartana["chave_analitica"], "LOSARTANA POTÁSSICA")
        self.assertEqual(losartana["descricao"], "LOSARTANA 50MG 30CP")

    def test_produto_sem_dcb_mantem_codigo_individual(self):
        resultado = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        produto = resultado.loc[resultado["codigo_produto"] == 90001].iloc[0]
        self.assertEqual(produto["tipo_chave"], "PRODUTO")
        self.assertEqual(produto["chave_analitica"], 90001)
        self.assertTrue(pd.isna(produto["cod_dcb"]))

    def test_dois_codigos_com_mesma_dcb_compartilham_chave(self):
        resultado = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        chaves = resultado.loc[
            resultado["codigo_produto"].isin([24234, 46282]), "chave_analitica"
        ].tolist()
        self.assertEqual(chaves, ["LOSARTANA POTÁSSICA"] * 2)

    def test_normalizacao_preserva_combinacao_com_mais(self):
        self.assertEqual(
            normalizar_nome_dcb("  AMOXICILINA+  CLAVULANATO DE POTÁSSIO "),
            "AMOXICILINA + CLAVULANATO DE POTÁSSIO",
        )
        cadastro = pd.DataFrame(
            [
                {
                    "cod_reduzido": "77777",
                    "cod_dcb": "00001",
                    "nom_dcb": "AMOXICILINA+CLAVULANATO DE POTÁSSIO",
                }
            ]
        )
        produtos = self.produtos.iloc[[0]].copy()
        produtos.loc[:, "codigo_produto"] = 77777
        resultado = criar_chaves_analiticas(produtos, cadastro)
        self.assertEqual(
            resultado.iloc[0]["chave_analitica"],
            "AMOXICILINA + CLAVULANATO DE POTÁSSIO",
        )

    def test_produto_inelegivel_fica_fora(self):
        resultado = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        self.assertNotIn(90002, resultado["codigo_produto"].tolist())
        self.assertEqual(len(resultado), 3)

    def test_todo_elegivel_tem_chave_e_total_fecha(self):
        resultado = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        self.assertEqual(len(resultado), 3)
        self.assertFalse(resultado["chave_analitica"].isna().any())
        self.assertEqual(
            resultado["tipo_chave"].value_counts().to_dict(),
            {"DCB": 2, "PRODUTO": 1},
        )

    def test_resultado_e_deterministico(self):
        primeiro = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        segundo = criar_chaves_analiticas(self.produtos, self.cadastro_dcb)
        pd.testing.assert_frame_equal(primeiro, segundo)

    def test_mapeamento_dcb_ambiguo_interrompe(self):
        cadastro = pd.concat(
            [
                self.cadastro_dcb,
                pd.DataFrame(
                    [
                        {
                            "cod_reduzido": "24234",
                            "cod_dcb": "99999",
                            "nom_dcb": "OUTRA SUBSTÂNCIA",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        with self.assertRaisesRegex(ValueError, "mais de uma DCB válida"):
            criar_chaves_analiticas(self.produtos, cadastro)

    def test_csv_mestre_local_tem_colunas_e_separador_esperados(self):
        cadastro = carregar_cadastro_dcb()
        self.assertTrue(
            {
                "cod_reduzido", "nom_produto", "cod_dcb", "nom_dcb",
                "cod_tipo", "nom_tipo",
            }.issubset(cadastro.columns)
        )

    def test_exclui_exclusivamente_tipos_36_44_56(self):
        produtos = pd.DataFrame(
            {"codigo_produto": [36, 44, 56, 57, 58, 59]}
        )
        cadastro = pd.DataFrame(
            {
                "cod_reduzido": ["36", "44", "56", "57", "58", "59"],
                "cod_tipo": ["36", "44", "56", "10", "90", None],
            }
        )
        anotados = anotar_uso_continuo(produtos, cadastro)
        self.assertEqual(
            anotados.loc[anotados["uso_continuo"], "codigo_produto"].tolist(),
            [36, 44, 56],
        )
        self.assertFalse(anotados.loc[anotados.codigo_produto == 57, "uso_continuo"].item())
        self.assertFalse(anotados.loc[anotados.codigo_produto == 58, "uso_continuo"].item())
        self.assertFalse(anotados.loc[anotados.codigo_produto == 59, "uso_continuo"].item())
        self.assertEqual(CODIGOS_TIPO_USO_CONTINUO, {"36", "44", "56"})

    def test_exclui_tipos_antimicrobianos_15_e_102_sem_inferir_por_nome(self):
        produtos = pd.DataFrame(
            {
                "codigo_produto": [401, 402, 403, 404, 405],
                "descricao_original": [
                    "AMOXICILINA",
                    "PRODUTO COMUM",
                    "PRODUTO COMUM",
                    "ANTIBIOTICO NO NOME",
                    "MEDICAMENTO CONTINUO",
                ],
                "elegivel_analise_automedicacao": [True] * 5,
            }
        )
        cadastro = pd.DataFrame(
            {
                "cod_reduzido": ["401", "402", "403", "404", "405"],
                "cod_tipo": ["15", "102", "10", None, "36"],
                "cod_dcb": ["1", "2", "3", None, "5"],
                "nom_dcb": ["DCB A", "DCB B", "DCB C", None, "DCB E"],
            }
        )
        anotados = anotar_antimicrobianos(produtos, cadastro)
        self.assertEqual(
            anotados.loc[anotados.antimicrobiano, "codigo_produto"].tolist(),
            [401, 402],
        )
        resultado = criar_chaves_analiticas(produtos, cadastro)
        self.assertEqual(resultado.codigo_produto.tolist(), [403, 404])
        self.assertEqual(CODIGOS_TIPO_ANTIMICROBIANO, {"15", "102"})

    def test_antimicrobiano_em_dcb_mista_nao_remove_produto_restante(self):
        produtos = pd.DataFrame(
            [
                {"codigo_produto": 501, "elegivel_analise_automedicacao": True},
                {"codigo_produto": 502, "elegivel_analise_automedicacao": True},
            ]
        )
        cadastro = pd.DataFrame(
            [
                {"cod_reduzido": "501", "cod_tipo": "15", "cod_dcb": "1", "nom_dcb": "SUBSTANCIA"},
                {"cod_reduzido": "502", "cod_tipo": "10", "cod_dcb": "1", "nom_dcb": "SUBSTANCIA"},
            ]
        )
        resultado = criar_chaves_analiticas(produtos, cadastro)
        self.assertEqual(resultado.codigo_produto.tolist(), [502])
        self.assertEqual(resultado.chave_analitica.tolist(), ["SUBSTANCIA"])

    def test_exclusao_preserva_dcb_mista_e_nao_altera_entrada(self):
        produtos = pd.DataFrame(
            [
                {"codigo_produto": 101, "elegivel_analise_automedicacao": True},
                {"codigo_produto": 102, "elegivel_analise_automedicacao": True},
            ]
        )
        cadastro = pd.DataFrame(
            [
                {"cod_reduzido": "101", "cod_dcb": "1", "nom_dcb": "A", "cod_tipo": "36"},
                {"cod_reduzido": "102", "cod_dcb": "1", "nom_dcb": "A", "cod_tipo": "10"},
            ]
        )
        original = produtos.copy(deep=True)
        resultado = criar_chaves_analiticas(produtos, cadastro)
        self.assertEqual(resultado.codigo_produto.tolist(), [102])
        self.assertEqual(resultado.chave_analitica.tolist(), ["A"])
        pd.testing.assert_frame_equal(produtos, original)

    def test_decisao_nao_usa_nome_dcb_ou_descricao(self):
        produtos = pd.DataFrame(
            {
                "codigo_produto": [201, 202],
                "elegivel_analise_automedicacao": [True, True],
                "descricao_original": ["USO CONTINUO", "PRODUTO COMUM"],
            }
        )
        cadastro = pd.DataFrame(
            [
                {"cod_reduzido": "201", "cod_tipo": "10", "cod_dcb": "1", "nom_dcb": "CONTINUO"},
                {"cod_reduzido": "202", "cod_tipo": "36", "cod_dcb": "2", "nom_dcb": "COMUM"},
            ]
        )
        resultado = criar_chaves_analiticas(produtos, cadastro)
        self.assertEqual(resultado.codigo_produto.tolist(), [201])


if __name__ == "__main__":
    unittest.main()
