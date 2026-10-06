import unittest

import pandas as pd

from utils.chave_analitica import (
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


if __name__ == "__main__":
    unittest.main()
