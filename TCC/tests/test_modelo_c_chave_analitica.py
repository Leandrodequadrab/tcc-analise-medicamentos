import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from utils.ml.definir_modelo_validacao import (
    FEATURES_MODELO_C,
    PARAMETROS_MODELO,
    calcular_historico_positivo,
    treinar_modelo_c,
)
from utils.ml.detectar_anomalias import _adicionar_features_historicas
from utils.ml.modelo_c_chave_analitica import (
    CHAVES_SERIE,
    construir_diagnostico_chave,
    executar_modelo_c_hibrido_em_diagnostico,
    resumir_execucao_hibrida,
)


class TestModeloCChaveAnalitica(unittest.TestCase):
    def setUp(self):
        self.produtos = pd.DataFrame(
            [
                {
                    "filial": 6,
                    "codigo_produto": 101,
                    "descricao_original": "MARCA A APRESENTACAO",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 102,
                    "descricao_original": "MARCA B APRESENTACAO",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 202,
                    "descricao_original": "PRODUTO SEM DCB",
                    "elegivel_analise_automedicacao": True,
                },
                {
                    "filial": 6,
                    "codigo_produto": 303,
                    "descricao_original": "PRODUTO INELEGIVEL",
                    "elegivel_analise_automedicacao": False,
                },
            ]
        )
        self.cadastro_dcb = pd.DataFrame(
            [
                {"cod_reduzido": "101", "cod_dcb": "1", "nom_dcb": "COMPOSTO + SAL"},
                {"cod_reduzido": "102", "cod_dcb": "1", "nom_dcb": "COMPOSTO+SAL"},
                {"cod_reduzido": "202", "cod_dcb": None, "nom_dcb": None},
            ]
        )
        datas = pd.date_range("2026-01-01", periods=45, freq="D")
        self.vendas = pd.DataFrame(
            [
                {
                    "filial": 6,
                    "codigo_produto": codigo,
                    "data": data,
                    "quantidade_total": quantidade,
                }
                for data in datas
                for codigo, quantidade in ((101, 1), (102, 2), (202, 4), (303, 10))
            ]
        )

    def diagnostico(self, vendas=None):
        return construir_diagnostico_chave(
            self.vendas if vendas is None else vendas,
            self.produtos,
            self.cadastro_dcb,
        )

    def pipeline(self):
        return executar_modelo_c_hibrido_em_diagnostico(self.diagnostico())

    def test_features_e_grupos_sao_calculados_por_chave_analitica(self):
        diagnostico = self.diagnostico()
        serie = _adicionar_features_historicas(
            diagnostico["serie_b"], chaves_serie=CHAVES_SERIE
        )
        dia = pd.Timestamp("2026-01-08")
        dcb = serie.loc[
            (serie.chave_analitica == "COMPOSTO + SAL") & (serie.data == dia)
        ].iloc[0]
        produto = serie.loc[
            (serie.chave_analitica == 202) & (serie.data == dia)
        ].iloc[0]
        self.assertEqual(dcb.media_anterior_7d, 3)
        self.assertEqual(produto.media_anterior_7d, 4)
        self.assertEqual(dcb.dias_historico, 7)
        self.assertEqual(produto.dias_historico, 7)

    def test_historico_de_outra_chave_nao_entra_na_serie(self):
        diagnostico = self.diagnostico()
        historico = calcular_historico_positivo(
            _adicionar_features_historicas(
                diagnostico["serie_b"], chaves_serie=CHAVES_SERIE
            ),
            chaves_serie=CHAVES_SERIE,
        )
        linha = historico.loc[
            (historico.chave_analitica == "COMPOSTO + SAL")
            & (historico.data == pd.Timestamp("2026-01-08"))
        ].iloc[0]
        self.assertEqual(linha.vendas_positivas_anteriores, 7)
        self.assertEqual(linha.media_positiva_historica, 3)

    def test_limite_de_sete_dias_positivos_anteriores_permanece(self):
        pipeline = self.pipeline()
        resultado = pipeline["resultado"]
        self.assertTrue((resultado["vendas_positivas_anteriores"] >= 7).all())
        self.assertTrue((resultado["data"] >= pd.Timestamp("2026-01-29")).all())

    def test_uso_continuo_e_antimicrobianos_nao_chegam_ao_modelo(self):
        produtos = self.produtos.copy(deep=True)
        cadastro = self.cadastro_dcb.copy(deep=True)
        cadastro["cod_tipo"] = None
        cadastro.loc[cadastro["cod_reduzido"] == "101", "cod_tipo"] = "36"
        cadastro.loc[cadastro["cod_reduzido"] == "102", "cod_tipo"] = "15"
        cadastro.loc[cadastro["cod_reduzido"] == "202", "cod_tipo"] = "10"
        cadastro.loc[cadastro["cod_reduzido"] == "303", "cod_tipo"] = "102"

        diagnostico = construir_diagnostico_chave(
            self.vendas, produtos, cadastro
        )
        catalogo_modelado = diagnostico["catalogo_chaves"]
        self.assertEqual(catalogo_modelado["codigo_produto"].tolist(), [202])
        self.assertEqual(set(catalogo_modelado["codigo_produto"]), {202})
        resultado = executar_modelo_c_hibrido_em_diagnostico(diagnostico)[
            "resultado"
        ]
        self.assertEqual(set(resultado["codigos_produto"].explode()), {202})

    def test_features_de_uma_data_nao_usam_quantidades_futuras(self):
        alteradas = self.vendas.copy()
        alteradas.loc[
            (alteradas.codigo_produto == 101)
            & (alteradas.data > pd.Timestamp("2026-01-20")),
            "quantidade_total",
        ] = 9000
        original = _adicionar_features_historicas(
            self.diagnostico()["serie_b"], chaves_serie=CHAVES_SERIE
        )
        futuro = _adicionar_features_historicas(
            self.diagnostico(alteradas)["serie_b"], chaves_serie=CHAVES_SERIE
        )
        chave = ("DCB", "COMPOSTO + SAL", pd.Timestamp("2026-01-15"))
        campos = [
            "media_anterior_7d",
            "media_anterior_28d",
            "taxa_historica_dias_com_venda",
            "dias_desde_ultima_venda",
        ]
        linha_a = original.set_index([*CHAVES_SERIE, "data"]).loc[chave, campos]
        linha_b = futuro.set_index([*CHAVES_SERIE, "data"]).loc[chave, campos]
        pd.testing.assert_series_equal(linha_a, linha_b)

    def test_dcb_composta_agregada_e_consumida_como_uma_serie(self):
        diagnostico = self.diagnostico()
        linha = diagnostico["agregacao_chave"].iloc[0]
        self.assertEqual(linha.tipo_chave, "DCB")
        self.assertEqual(linha.chave_analitica, "COMPOSTO + SAL")
        self.assertEqual(linha.quantidade_dia, 3)
        self.assertEqual(linha.codigos_produto, (101, 102))
        pipeline = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
        self.assertIn("COMPOSTO + SAL", pipeline["resultado"]["chave_analitica"].tolist())

    def test_produto_sem_dcb_permanece_serie_individual_no_modelo(self):
        pipeline = self.pipeline()
        produto = pipeline["resultado"].loc[
            pipeline["resultado"]["tipo_chave"] == "PRODUTO"
        ]
        self.assertFalse(produto.empty)
        self.assertEqual(set(produto["chave_analitica"]), {202})
        self.assertEqual(set(produto["codigo_produto"].dropna()), {202})

    def test_outlier_baixo_nao_e_aumento_atipico(self):
        observacoes = pd.DataFrame(
            {
                "quantidade_dia": [1.0, 5.0],
                "media_positiva_historica": [10.0, 4.0],
                **{feature: [0.0, 0.1] for feature in FEATURES_MODELO_C},
            }
        )

        class EstimadorFalso:
            def __init__(self, **_parametros):
                pass

            def fit_predict(self, _matriz):
                return np.array([-1, 1])

            def score_samples(self, _matriz):
                return np.array([-0.5, -0.1])

            def decision_function(self, _matriz):
                return np.array([-0.1, 0.3])

        with patch(
            "utils.ml.definir_modelo_validacao.IsolationForest", EstimadorFalso
        ):
            _, resultado = treinar_modelo_c(observacoes)
        self.assertTrue(bool(resultado.iloc[0].anomalia))
        self.assertFalse(bool(resultado.iloc[0].aumento_atipico))

    def test_resultado_do_modelo_e_deterministico(self):
        diagnostico = self.diagnostico()
        primeira = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
        segunda = executar_modelo_c_hibrido_em_diagnostico(diagnostico)
        pd.testing.assert_frame_equal(
            primeira["resultado"].reset_index(drop=True),
            segunda["resultado"].reset_index(drop=True),
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )

    def test_parametros_e_seis_features_permanecem_congelados(self):
        self.assertEqual(
            FEATURES_MODELO_C,
            (
                "desvio_log_media_positiva_historica",
                "desvio_log_mediana_positiva_historica",
                "razao_media_7d_media_28d",
                "taxa_historica_dias_com_venda",
                "dias_desde_ultima_venda",
                "diferenca_taxa_recente_historica",
            ),
        )
        self.assertEqual(
            PARAMETROS_MODELO,
            {
                "n_estimators": 200,
                "max_samples": "auto",
                "contamination": "auto",
                "max_features": 1.0,
                "bootstrap": False,
                "random_state": 42,
                "n_jobs": -1,
            },
        )

    def test_resumo_separa_dcb_e_produto(self):
        resumo = resumir_execucao_hibrida(self.pipeline())
        self.assertEqual(resumo["geral"]["chaves_totais"], 2)
        self.assertEqual(resumo["por_tipo"]["DCB"]["series"], 1)
        self.assertEqual(resumo["por_tipo"]["PRODUTO"]["series"], 1)
        self.assertEqual(resumo["geral"]["quantidade_elegivel_antes"], 315)
        self.assertEqual(resumo["geral"]["quantidade_agregada"], 315)


if __name__ == "__main__":
    unittest.main()
