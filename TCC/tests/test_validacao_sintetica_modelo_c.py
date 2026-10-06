import unittest

import numpy as np
import pandas as pd

from utils.ml.validar_modelo_c_sintetico import (
    INTENSIDADES,
    _atualizar_features_da_observacao,
    injetar_aumentos,
)
from utils.ml.definir_modelo_validacao import FEATURES_MODELO_C


class TestValidacaoSinteticaModeloC(unittest.TestCase):
    @staticmethod
    def criar_controle():
        quantidade = 450
        dados = {
            "filial": np.full(quantidade, 6),
            "codigo_produto": np.arange(10000, 10000 + quantidade),
            "data": pd.date_range("2025-05-01", periods=quantidade),
            "descricao_original": [f"Produto {i}" for i in range(quantidade)],
            "classificacao_operacional": ["MED_OUTRO"] * quantidade,
            "elegivel_analise_automedicacao": [True] * quantidade,
            "quantidade_dia": np.full(quantidade, 2.0),
            "media_positiva_historica": np.full(quantidade, 4.0),
            "vendas_positivas_anteriores": np.full(quantidade, 10),
            "aumento_atipico": np.zeros(quantidade, dtype=bool),
        }
        for feature in FEATURES_MODELO_C:
            dados[feature] = np.ones(quantidade, dtype=float)
        dados.update(
            {
                "media_anterior_7d": np.full(quantidade, 3.0),
                "media_anterior_28d": np.full(quantidade, 3.0),
                "desvio_anterior_28d": np.full(quantidade, 1.0),
                "mediana_positiva_historica": np.full(quantidade, 4.0),
                "taxa_recente_anterior_28d": np.full(quantidade, 0.5),
                "razao_quantidade_media_28d": np.full(quantidade, 2 / 3),
                "diferenca_quantidade_media_28d": np.full(quantidade, -1.0),
                "desvio_relativo_media_28d": np.full(quantidade, -1 / 3),
                "mediana_positiva_anterior_28d": np.full(quantidade, 4.0),
                "mad_positivo_anterior_28d": np.full(quantidade, 1.0),
                "desvio_robusto_mediana_28d": np.full(quantidade, -1.34898),
                "log1p_quantidade_dia": np.log1p(np.full(quantidade, 2.0)),
            }
        )
        return pd.DataFrame(dados)

    def test_injecoes_reproduziveis_formula_e_alocacao(self):
        controle = self.criar_controle()
        original = controle.copy(deep=True)

        experimento, plano, candidatos, produtos = injetar_aumentos(controle)
        _, plano_repetido, _, _ = injetar_aumentos(controle)

        self.assertEqual(len(plano), 450)
        self.assertEqual(plano["codigo_produto"].nunique(), 450)
        self.assertFalse(plano.duplicated(["filial", "codigo_produto"]).any())
        self.assertEqual(candidatos, 450)
        self.assertEqual(produtos, 450)
        self.assertEqual(plano["multiplicador"].value_counts().to_dict(), {
            fator: 150 for fator in INTENSIDADES.values()
        })
        self.assertTrue(plano.equals(plano_repetido))

        esperado = np.ceil(
            np.maximum(
                plano["quantidade_original"],
                plano["referencia_historica_anterior"],
            ) * plano["multiplicador"]
        )
        np.testing.assert_array_equal(plano["quantidade_sintetica"], esperado)
        pd.testing.assert_frame_equal(controle, original)
        self.assertTrue((experimento.loc[plano["indice_observacao"], "quantidade_dia"].to_numpy() == esperado).all())

    def test_atualizacao_nao_muda_features_historicas(self):
        controle = self.criar_controle()
        experimento = controle.copy(deep=True)
        indices = pd.Index([0, 1])
        experimento.loc[indices, "quantidade_dia"] = [8.0, 12.0]

        _atualizar_features_da_observacao(experimento, indices)

        for coluna in (
            "media_anterior_7d",
            "media_anterior_28d",
            "desvio_anterior_28d",
            "vendas_positivas_anteriores",
            "media_positiva_historica",
            "mediana_positiva_historica",
        ):
            np.testing.assert_array_equal(experimento[coluna], controle[coluna])


if __name__ == "__main__":
    unittest.main()
