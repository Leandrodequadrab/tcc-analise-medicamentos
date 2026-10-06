import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from utils.ml.validar_modelo_c_hibrido_sintetico import (
    FATORES,
    FEATURES_HISTORICAS_IMUTAVEIS,
    _executar_experimento,
    calcular_metricas,
    injetar_aumentos,
    selecionar_injecoes,
    verificar_reprodutibilidade,
)


class TestValidacaoSinteticaModeloCHibrido(unittest.TestCase):
    @staticmethod
    def criar_controle(numero_chaves=500, datas_por_chave=2):
        registros = []
        for chave_indice in range(numero_chaves):
            tipo = "DCB" if chave_indice % 2 == 0 else "PRODUTO"
            chave = (
                f"PRINCIPIO {chave_indice // 2}"
                if tipo == "DCB"
                else 100000 + chave_indice
            )
            for dia_indice in range(datas_por_chave):
                quantidade = 8.0 + (chave_indice % 5)
                media = 10.0
                registro = {
                    "tipo_chave": tipo,
                    "chave_analitica": chave,
                    "data": pd.Timestamp("2026-01-01")
                    + pd.Timedelta(days=dia_indice),
                    "quantidade_dia": quantidade,
                    "media_positiva_historica": media,
                    "mediana_positiva_historica": media,
                    "vendas_positivas_anteriores": 12,
                    "aumento_atipico": False,
                    "anomalia": False,
                    "codigos_produto": (chave_indice + 1,),
                    "descricoes_associadas": (f"Produto {chave_indice}",),
                    "nom_dcb": chave if tipo == "DCB" else np.nan,
                    "codigo_produto": (
                        np.nan if tipo == "DCB" else 100000 + chave_indice
                    ),
                    "descricao_original": f"Produto {chave_indice}",
                }
                for coluna in FEATURES_HISTORICAS_IMUTAVEIS:
                    registro[coluna] = 0.25
                registro["media_anterior_7d"] = 3.0
                registro["media_anterior_28d"] = 4.0
                registro["desvio_anterior_28d"] = 1.0
                registro["media_positiva_historica"] = media
                registro["mediana_positiva_historica"] = media
                registro["taxa_historica_dias_com_venda"] = 0.5
                registro["dias_desde_ultima_venda"] = 1.0
                registro["vendas_positivas_anteriores"] = 12
                registro["taxa_recente_anterior_28d"] = 0.5
                registro["diferenca_taxa_recente_historica"] = 0.1
                registro["razao_media_7d_media_28d"] = 0.75
                registro["mediana_positiva_anterior_28d"] = 9.0
                registro["mad_positivo_anterior_28d"] = 1.0
                registro["desvio_log_media_positiva_historica"] = (
                    np.log1p(quantidade) - np.log1p(media)
                )
                registro["desvio_log_mediana_positiva_historica"] = (
                    np.log1p(quantidade) - np.log1p(media)
                )
                registro["desvio_relativo_media_28d"] = (
                    quantidade - 4.0
                ) / 4.0
                registro["razao_quantidade_media_28d"] = quantidade / 4.0
                registros.append(registro)
        return pd.DataFrame(registros)

    def test_selecao_e_deterministica_e_usa_chaves_distintas(self):
        controle = self.criar_controle()
        plano_a, candidatos_a, chaves_a = selecionar_injecoes(controle)
        plano_b, candidatos_b, chaves_b = selecionar_injecoes(controle)
        pd.testing.assert_frame_equal(plano_a, plano_b)
        self.assertEqual(candidatos_a, candidatos_b)
        self.assertEqual(chaves_a, chaves_b)
        self.assertEqual(len(plano_a), 450)
        self.assertEqual(plano_a[["tipo_chave", "chave_analitica"]].drop_duplicates().shape[0], 450)

    def test_formula_usa_maximo_entre_quantidade_e_media(self):
        controle = self.criar_controle()
        _, plano, _, _ = injetar_aumentos(controle)
        esperado = np.ceil(
            np.maximum(
                plano["quantidade_original"],
                plano["referencia_historica_anterior"],
            )
            * plano["multiplicador"]
        )
        np.testing.assert_array_equal(plano["quantidade_sintetica"], esperado)

    def test_fatores_1_5_3_e_6_sao_distribuidos_e_calculados(self):
        _, plano, _, _ = injetar_aumentos(self.criar_controle())
        self.assertEqual(plano["fator"].value_counts().to_dict(), {"1.5x": 150, "3x": 150, "6x": 150})
        for fator, multiplicador in FATORES.items():
            linhas = plano.loc[plano["fator"] == fator]
            esperado = np.ceil(
                np.maximum(
                    linhas["quantidade_original"],
                    linhas["referencia_historica_anterior"],
                )
                * multiplicador
            )
            np.testing.assert_array_equal(linhas["quantidade_sintetica"], esperado)

    def test_historico_e_base_real_permanecem_inalterados(self):
        controle = self.criar_controle()
        original = controle.copy(deep=True)
        experimento, plano, _, _ = injetar_aumentos(controle)
        pd.testing.assert_frame_equal(controle, original)
        for coluna in FEATURES_HISTORICAS_IMUTAVEIS:
            np.testing.assert_array_equal(controle[coluna], experimento[coluna])
        indices = set(plano["indice_observacao"])
        fora_injecao = [i for i in experimento.index if i not in indices]
        np.testing.assert_array_equal(
            experimento.loc[fora_injecao, "quantidade_dia"],
            controle.loc[fora_injecao, "quantidade_dia"],
        )

    def test_nao_vaza_injecao_para_outra_data_da_mesma_chave(self):
        controle = self.criar_controle()
        experimento, plano, _, _ = injetar_aumentos(controle)
        selecao = plano.iloc[0]
        mascara_mesma_chave = (
            (controle.tipo_chave == selecao.tipo_chave)
            & (controle.chave_analitica == selecao.chave_analitica)
        )
        datas_nao_injetadas = controle.loc[
            mascara_mesma_chave
            & (controle.index != selecao.indice_observacao)
        ].index
        self.assertGreater(len(datas_nao_injetadas), 0)
        pd.testing.assert_series_equal(
            experimento.loc[datas_nao_injetadas, "quantidade_dia"],
            controle.loc[datas_nao_injetadas, "quantidade_dia"],
        )
        for coluna in FEATURES_HISTORICAS_IMUTAVEIS:
            np.testing.assert_array_equal(
                experimento.loc[datas_nao_injetadas, coluna],
                controle.loc[datas_nao_injetadas, coluna],
            )

    def test_matriz_confusao_e_metricas(self):
        resultado = pd.DataFrame(
            {"aumento_atipico": [True, False, True, False]}
        )
        plano = pd.DataFrame({"indice_observacao": [0, 1]})
        metricas = calcular_metricas(resultado, plano)
        self.assertEqual(
            {chave: metricas[chave] for chave in ("TP", "FP", "FN", "TN")},
            {"TP": 1, "FP": 1, "FN": 1, "TN": 1},
        )
        self.assertEqual(metricas["precision"], 0.5)
        self.assertEqual(metricas["recall"], 0.5)
        self.assertEqual(metricas["f1"], 0.5)
        self.assertEqual(metricas["specificity"], 0.5)

    def test_metricas_sem_divisao_por_zero(self):
        resultado = pd.DataFrame({"aumento_atipico": [False, False]})
        plano = pd.DataFrame({"indice_observacao": []}).astype(int)
        metricas = calcular_metricas(resultado, plano)
        self.assertEqual(metricas["TP"], 0)
        self.assertEqual(metricas["FN"], 0)
        self.assertEqual(metricas["precision"], 0)
        self.assertEqual(metricas["recall"], 0)
        self.assertEqual(metricas["f1"], 0)
        self.assertEqual(metricas["specificity"], 1)

    def test_validacao_separa_tipos_dcb_e_produto(self):
        controle = self.criar_controle(numero_chaves=450, datas_por_chave=1)
        with patch(
            "utils.ml.validar_modelo_c_hibrido_sintetico.treinar_modelo_c"
        ) as treinar:
            def treinar_fake(experimento):
                resultado = experimento.copy()
                resultado["anomalia"] = resultado["quantidade_dia"] >= 15
                resultado["aumento_atipico"] = (
                    resultado["anomalia"]
                    & (
                        resultado["quantidade_dia"]
                        > resultado["media_positiva_historica"]
                    )
                )
                resultado["classificacao_isolation_forest"] = np.where(
                    resultado["anomalia"], -1, 1
                )
                resultado["score_samples"] = -0.2
                resultado["decision_function"] = -0.1
                return object(), resultado

            treinar.side_effect = treinar_fake
            experimento = _executar_experimento(controle)
        por_tipo = experimento["resultados_por_tipo"].set_index("tipo_chave")
        self.assertEqual(por_tipo.loc["DCB", "injecoes"], 225)
        self.assertEqual(por_tipo.loc["PRODUTO", "injecoes"], 225)
        self.assertEqual(
            int(por_tipo.loc["DCB", "TP"] + por_tipo.loc["DCB", "FN"]), 225
        )
        self.assertEqual(
            int(por_tipo.loc["PRODUTO", "TP"] + por_tipo.loc["PRODUTO", "FN"]),
            225,
        )

    def test_reprodutibilidade_compara_plano_metricas_e_scores(self):
        controle = self.criar_controle(numero_chaves=450, datas_por_chave=1)

        def treinar_fake(experimento):
            resultado = experimento.copy()
            resultado["anomalia"] = resultado["quantidade_dia"] >= 15
            resultado["aumento_atipico"] = resultado["anomalia"]
            resultado["classificacao_isolation_forest"] = np.where(
                resultado["anomalia"], -1, 1
            )
            resultado["score_samples"] = -0.2
            resultado["decision_function"] = -0.1
            return object(), resultado

        with patch(
            "utils.ml.validar_modelo_c_hibrido_sintetico.treinar_modelo_c",
            side_effect=treinar_fake,
        ):
            primeiro = _executar_experimento(controle)
            segundo = _executar_experimento(controle)
        relatorio = verificar_reprodutibilidade(primeiro, segundo)
        self.assertTrue(relatorio["resultados_identicos"])


if __name__ == "__main__":
    unittest.main()
