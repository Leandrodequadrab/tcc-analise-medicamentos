import unittest
from unittest.mock import patch

from app import app
from utils.dashboard import criar_dashboard_vazio, obter_dados_dashboard
from utils.ml.servico_modelo_c import ErroAnaliseIA


class DashboardRel0024TestCase(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.cliente = app.test_client()

    def test_consultas_dashboard_usam_totais_rel0024(self):
        with app.app_context():
            dados = obter_dados_dashboard()

        self.assertEqual(dados["indicadores"]["produtos"], 6110)
        self.assertEqual(dados["indicadores"]["elegiveis"], 2414)
        self.assertEqual(dados["indicadores"]["vendas"], 56756)
        self.assertEqual(dados["indicadores"]["itens"], 108166)
        self.assertEqual(dados["indicadores"]["unidades"], 158385)
        self.assertEqual(
            dados["indicadores"]["periodo"],
            {"inicial": "2025-04-01", "final": "2026-03-31"},
        )
        self.assertEqual(
            dict(zip(dados["classificacoes"]["labels"], dados["classificacoes"]["valores"])),
            {
                "MED_OUTRO": 1509,
                "MED_OTC_INTERNO": 768,
                "MED_ANTIMICROBIANO": 137,
                "MED_CONTROLADO": 390,
                "NAO_MED": 3290,
                "SEM_CLASSIFICACAO": 16,
            },
        )
        self.assertEqual(len(dados["vendas_mensais"]["labels"]), 12)
        self.assertEqual(sum(dados["vendas_mensais"]["valores"]), 158385)
        self.assertEqual(len(dados["produtos_mais_vendidos"]), 10)
        self.assertTrue(
            all(
                item["classificacao"]
                in {"MED_OUTRO", "MED_OTC_INTERNO", "MED_ANTIMICROBIANO"}
                for item in dados["produtos_mais_vendidos"]
            )
        )

    def test_dashboard_responde_200_e_mostra_rel0024(self):
        resposta = self.cliente.get("/dashboard")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(b"An\xc3\xa1lise de Padr\xc3\xb5es", resposta.data)
        self.assertIn(b"Rel_0024", resposta.data)
        self.assertNotIn(b"Vendas por cidade", resposta.data)

    def test_dashboard_trata_ausencia_de_dados(self):
        with patch(
            "app.obter_dados_dashboard",
            return_value=criar_dashboard_vazio("Importe um Rel_0024."),
        ):
            resposta = self.cliente.get("/dashboard")

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(b"Importe um Rel_0024", resposta.data)
        self.assertIn(b"Importe um Rel_0024", resposta.data)

    def test_endpoint_ia_delega_ao_adaptador_do_servico(self):
        resumo = {
            "disponivel": True,
            "metricas": {"produtos_modelaveis": 1},
            "aumentos_mensais": {"labels": [], "valores": []},
            "destaques": [],
        }
        with patch("app.obter_resumo_ia_dashboard", return_value=resumo) as servico:
            resposta = self.cliente.get("/api/dashboard/resumo-ia")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json, resumo)
        servico.assert_called_once_with()

    def test_adaptador_reutiliza_servico_e_ordena_destaques_por_score(self):
        metricas = {
            "produtos_modelaveis": 980,
            "observacoes_avaliadas": 100,
            "outliers": 15,
            "aumentos_atipicos": 6,
        }
        resultados = [
            {
                "score_samples": float(indice),
                "codigo_produto": indice,
                "data": "2026-01-01",
            }
            for indice in range(12, 0, -1)
        ]
        analise = {
            "metricas": metricas,
            "aumentos_por_mes": {"labels": ["2026-01"], "valores": [6]},
            "resultados": resultados,
        }
        with patch(
            "utils.dashboard.executar_analise_ia", return_value=analise
        ) as modelo_c:
            from utils.dashboard import obter_resumo_ia_dashboard

            resumo = obter_resumo_ia_dashboard()

        modelo_c.assert_called_once_with()
        self.assertEqual(resumo["metricas"]["outliers_percentual"], 15)
        self.assertEqual(resumo["metricas"]["aumentos_percentual"], 6)
        self.assertEqual(len(resumo["destaques"]), 10)
        self.assertEqual(
            [item["score_samples"] for item in resumo["destaques"]],
            list(map(float, range(1, 11))),
        )

    def test_endpoint_ia_retorna_erro_amigavel(self):
        with patch(
            "app.obter_resumo_ia_dashboard",
            side_effect=ErroAnaliseIA("Não há dados Rel_0024.", 422),
        ):
            resposta = self.cliente.get("/api/dashboard/resumo-ia")

        self.assertEqual(resposta.status_code, 422)
        self.assertFalse(resposta.json["disponivel"])
        self.assertIn("Não há dados", resposta.json["mensagem"])

    def test_rotas_importacao_e_analise_detalhada_preservadas(self):
        importar = self.cliente.get("/importar-rel0024")
        self.assertEqual(importar.status_code, 200)

        resposta_ia = {
            "metricas": {
                "produtos_modelaveis": 1,
                "produtos_historico_insuficiente": 0,
                "observacoes_avaliadas": 1,
                "outliers": 0,
                "aumentos_atipicos": 0,
                "periodo_analisado": {"data_inicial": "2025-04-01", "data_final": "2026-03-31"},
                "produtos_catalogo": 1,
                "produtos_med": 1,
                "produtos_elegiveis": 1,
                "produtos_excluidos": 0,
            },
            "filtros": {},
            "filiais_disponiveis": [6],
            "produtos_disponiveis": [],
            "grafico_temporal": {"disponivel": False, "mensagem": "Selecione um produto."},
            "resultados": [],
            "total_resultados_filtrados": 0,
            "limite_exibicao": 100,
            "resultados_limitados": False,
        }
        with patch("app.executar_analise_ia", return_value=resposta_ia):
            analise = self.cliente.get("/analise-ia")
        self.assertEqual(analise.status_code, 200)
        self.assertIn(b"An\xc3\xa1lise de Aumentos At\xc3\xadpicos", analise.data)


if __name__ == "__main__":
    unittest.main()
