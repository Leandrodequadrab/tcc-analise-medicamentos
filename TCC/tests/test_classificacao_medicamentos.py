import unittest

from utils.classificacao_medicamentos import (
    MED_ANTIMICROBIANO,
    MED_CONTROLADO,
    MED_OTC_INTERNO,
    MED_OUTRO,
    NAO_MED,
    classificar_tipo_medicamento,
    elegivel_analise_automedicacao,
)


class ClassificacaoMedicamentosTestCase(unittest.TestCase):
    def test_mapeamentos_exigidos(self):
        casos = {
            "MED PROPAGADO OTC": MED_OTC_INTERNO,
            "MED GENERICO CONTROLADO": MED_CONTROLADO,
            "MED PROPAGADO CONTROLADO": MED_CONTROLADO,
            "MED SIMILAR CONTROLADO": MED_CONTROLADO,
            "MED GENERICO ANTIMICROBIANO": MED_ANTIMICROBIANO,
            "MED PROPAGADO ANTIMICROBIANO": MED_ANTIMICROBIANO,
            "MED GENERICO CONTINUO": MED_OUTRO,
            "MED PROPAGADO COM TARJA": MED_OUTRO,
            "NAO MEDICAMENTO": NAO_MED,
            "LENCO UMEDECIDO / TOALHA INF": NAO_MED,
            None: NAO_MED,
            "": NAO_MED,
        }
        for descricao, esperado in casos.items():
            with self.subTest(descricao=descricao):
                self.assertEqual(
                    classificar_tipo_medicamento(descricao), esperado
                )

    def test_med_precisa_ser_primeiro_token_completo(self):
        for descricao in ("NAO MEDICAMENTO", "PARAMEDICO", "TIPO MED OTC"):
            with self.subTest(descricao=descricao):
                self.assertEqual(
                    classificar_tipo_medicamento(descricao), NAO_MED
                )

    def test_precedencia(self):
        self.assertEqual(
            classificar_tipo_medicamento(
                "MED OTC ANTIMICROBIANO CONTROLADO"
            ),
            MED_CONTROLADO,
        )
        self.assertEqual(
            classificar_tipo_medicamento("MED OTC ANTIMICROBIANO"),
            MED_ANTIMICROBIANO,
        )

    def test_elegibilidade_operacional(self):
        self.assertFalse(elegivel_analise_automedicacao(NAO_MED))
        self.assertFalse(elegivel_analise_automedicacao(MED_CONTROLADO))
        self.assertTrue(elegivel_analise_automedicacao(MED_OTC_INTERNO))
        self.assertTrue(elegivel_analise_automedicacao(MED_ANTIMICROBIANO))
        self.assertTrue(elegivel_analise_automedicacao(MED_OUTRO))


if __name__ == "__main__":
    unittest.main()
