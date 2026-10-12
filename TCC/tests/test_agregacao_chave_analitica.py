import unittest

import pandas as pd

from utils.agregacao_chave_analitica import (
    agregar_vendas_por_chave,
    preparar_vendas_elegiveis,
)


class AgregacaoChaveAnaliticaTestCase(unittest.TestCase):
    def setUp(self):
        self.produtos = pd.DataFrame(
            [
                {
                    "filial": 6,
                    "codigo_produto": 101,
                    "descricao_original": "LOSARTANA 50MG CX 30",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 102,
                    "descricao_original": "LOSARTANA 50MG 30CP",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 202,
                    "descricao_original": "MESMA DESCRICAO",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 203,
                    "descricao_original": "MESMA DESCRICAO",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 303,
                    "descricao_original": "INELEGIVEL",
                    "elegivel_analise_automedicacao": False,
                },
                {
                    "filial": 6,
                    "codigo_produto": 404,
                    "descricao_original": "AMOXICILINA CLAVULANATO",
                    "elegivel_analise_automedicacao": True,
                },
            ]
        )
        self.cadastro_dcb = pd.DataFrame(
            [
                {"cod_reduzido": "101", "cod_dcb": "1", "nom_dcb": "LOSARTANA POTÁSSICA"},
                {"cod_reduzido": "102", "cod_dcb": "1", "nom_dcb": "  losartana   potássica "},
                {"cod_reduzido": "202", "cod_dcb": None, "nom_dcb": None},
                {"cod_reduzido": "203", "cod_dcb": None, "nom_dcb": None},
                {
                    "cod_reduzido": "404",
                    "cod_dcb": "4",
                    "nom_dcb": "AMOXICILINA + CLAVULANATO DE POTÁSSIO",
                },
            ]
        )
        self.vendas = pd.DataFrame(
            [
                {"filial": 6, "codigo_produto": 101, "data": "2026-01-01", "quantidade_total": 4},
                {"filial": 6, "codigo_produto": 102, "data": "2026-01-01", "quantidade_total": 6},
                {"filial": 6, "codigo_produto": 202, "data": "2026-01-01", "quantidade_total": 2},
                {"filial": 6, "codigo_produto": 203, "data": "2026-01-01", "quantidade_total": 7},
                {"filial": 6, "codigo_produto": 303, "data": "2026-01-01", "quantidade_total": 80},
                {"filial": 6, "codigo_produto": 404, "data": "2026-01-01", "quantidade_total": 3},
                {"filial": 6, "codigo_produto": 101, "data": "2026-01-02", "quantidade_total": 3},
                {"filial": 6, "codigo_produto": 102, "data": "2026-01-02", "quantidade_total": 1},
                {"filial": 6, "codigo_produto": 202, "data": "2026-01-02", "quantidade_total": 5},
                {"filial": 6, "codigo_produto": 203, "data": "2026-01-02", "quantidade_total": 2},
                {"filial": 6, "codigo_produto": 404, "data": "2026-01-02", "quantidade_total": 4},
            ]
        )

    def agregar(self):
        return agregar_vendas_por_chave(
            self.vendas, self.produtos, self.cadastro_dcb
        )

    def test_codigos_com_mesma_dcb_e_data_somam_quantidades(self):
        resultado = self.agregar()
        linha = resultado.loc[
            (resultado["data"] == pd.Timestamp("2026-01-01"))
            & (resultado["chave_analitica"] == "LOSARTANA POTÁSSICA")
        ].iloc[0]
        self.assertEqual(linha["quantidade_dia"], 10)
        self.assertEqual(linha["codigos_produto"], (101, 102))

    def test_mesma_dcb_em_datas_diferentes_gera_observacoes_distintas(self):
        resultado = self.agregar()
        losartana = resultado.loc[
            resultado["chave_analitica"] == "LOSARTANA POTÁSSICA"
        ]
        self.assertEqual(len(losartana), 2)
        self.assertEqual(losartana["quantidade_dia"].tolist(), [10, 4])

    def test_produtos_sem_dcb_permanecem_individuais_mesmo_com_mesma_descricao(self):
        resultado = self.agregar()
        dia = resultado.loc[resultado["data"] == pd.Timestamp("2026-01-01")]
        individuais = dia.loc[dia["tipo_chave"] == "PRODUTO"]
        self.assertIn(202, individuais["chave_analitica"].tolist())
        self.assertIn(203, individuais["chave_analitica"].tolist())
        self.assertEqual(individuais["codigo_produto"].nunique(), 2)

    def test_dcb_combinada_com_mais_permanece_uma_chave(self):
        resultado = self.agregar()
        combinada = resultado.loc[
            resultado["chave_analitica"]
            == "AMOXICILINA + CLAVULANATO DE POTÁSSIO"
        ]
        self.assertEqual(len(combinada), 2)
        self.assertEqual(combinada["codigos_produto"].tolist(), [(404,), (404,)])

    def test_quantidade_total_e_preservada(self):
        vinculadas = preparar_vendas_elegiveis(
            self.vendas, self.produtos, self.cadastro_dcb
        )
        resultado = self.agregar()
        self.assertEqual(vinculadas["quantidade_total"].sum(), 37)
        self.assertEqual(resultado["quantidade_dia"].sum(), 37)

    def test_produtos_inelegiveis_nao_entram(self):
        resultado = self.agregar()
        self.assertNotIn(303, resultado["codigos_produto"].explode().tolist())
        self.assertEqual(resultado["quantidade_dia"].sum(), 37)

    def test_todas_as_vendas_elegiveis_recebem_serie(self):
        vinculadas = preparar_vendas_elegiveis(
            self.vendas, self.produtos, self.cadastro_dcb
        )
        codigos_com_venda = set(vinculadas["codigo_produto"])
        codigos_com_chave = set(
            self.produtos.loc[
                self.produtos["elegivel_analise_automedicacao"],
                "codigo_produto",
            ]
        )
        self.assertEqual(codigos_com_venda, codigos_com_chave)
        self.assertTrue(vinculadas["chave_analitica"].notna().all())

    def test_uso_continuo_fica_fora_da_agregacao_e_totais_fecham(self):
        cadastro = self.cadastro_dcb.copy()
        cadastro["cod_tipo"] = None
        cadastro.loc[cadastro["cod_reduzido"] == "101", "cod_tipo"] = "36"
        cadastro.loc[cadastro["cod_reduzido"] == "102", "cod_tipo"] = "10"
        resultado = agregar_vendas_por_chave(
            self.vendas, self.produtos, cadastro
        )
        codigos_incluidos = set(resultado["codigos_produto"].explode())
        self.assertNotIn(101, codigos_incluidos)
        self.assertIn(102, codigos_incluidos)
        self.assertEqual(resultado["quantidade_dia"].sum(), 30)

    def test_antimicrobiano_fica_fora_da_agregacao(self):
        cadastro = self.cadastro_dcb.copy()
        cadastro["cod_tipo"] = None
        cadastro.loc[cadastro["cod_reduzido"] == "404", "cod_tipo"] = "15"
        cadastro.loc[cadastro["cod_reduzido"] == "102", "cod_tipo"] = "102"
        resultado = agregar_vendas_por_chave(
            self.vendas, self.produtos, cadastro
        )
        codigos_incluidos = set(resultado["codigos_produto"].explode())
        self.assertNotIn(404, codigos_incluidos)
        self.assertNotIn(102, codigos_incluidos)
        self.assertEqual(resultado["quantidade_dia"].sum(), 23)

    def test_resultado_e_deterministico(self):
        pd.testing.assert_frame_equal(self.agregar(), self.agregar())


if __name__ == "__main__":
    unittest.main()
