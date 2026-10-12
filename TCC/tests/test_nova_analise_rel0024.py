from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import unittest
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import text

from app import app
from utils.dados_reais.servico_nova_analise import analisar_dados_rel0024
from utils.ml.modelo_c_chave_analitica import executar_modelo_c_hibrido_em_diagnostico
from utils.ml.diagnosticar_series import (
    _confirmar_conexao,
    _criar_engine_somente_leitura,
)


def _itens(periodo, codigos, deslocamento=0):
    itens = []
    inicio = date(2025, 1, 1) + timedelta(days=deslocamento)
    descricoes = {codigo: {f"PRODUTO TESTE {codigo}"} for codigo in codigos}
    for dia in range(periodo):
        for indice, codigo in enumerate(codigos):
            quantidade = Decimal(str(1 + ((dia + indice) % 4)))
            itens.append(
                {
                    "filial": 6,
                    "numero_venda": 100000 + dia * 10 + indice,
                    "codigo_produto": codigo,
                    "data": inicio + timedelta(days=dia),
                    "tipo": "Venda",
                    "quantidade": quantidade,
                }
            )
    validacao = {
        "filiais": [6],
        "primeira_data": inicio,
        "ultima_data": inicio + timedelta(days=periodo - 1),
        "linhas_venda": len(itens),
        "linhas_devolucao": 0,
    }
    return itens, descricoes, validacao


def _catalogo(codigos):
    return {
        codigo: {
            "cod_reduzido": codigo,
            "cod_tipo": 36,
            "nom_tipo": "MED GENERICO",
            "nom_produto": f"PRODUTO TESTE {codigo}",
        }
        for codigo in codigos
    }


def _arquivo_xlsx(periodo=12, codigo=900001):
    arquivo = BytesIO()
    livro = Workbook(write_only=True)
    planilha = livro.create_sheet("Rel_0024")
    cabecalho = [None] * 25
    cabecalho[0], cabecalho[1], cabecalho[5], cabecalho[6] = (
        "Venda", "Loja", "Tipo", "Emissão"
    )
    cabecalho[16], cabecalho[18], cabecalho[24] = "Qtd", "Vlr.Unit", "Vlr.Total"
    planilha.append(cabecalho)
    for dia in range(periodo):
        linha = [None] * 25
        linha[0] = 500000 + dia
        linha[1] = 6
        linha[5] = "Venda"
        linha[6] = date(2025, 1, 1) + timedelta(days=dia)
        linha[10] = "08:00:00"
        linha[16] = Decimal("1")
        linha[18] = Decimal("10")
        linha[19] = Decimal("0")
        linha[20] = Decimal("0")
        linha[21] = Decimal("0")
        linha[24] = Decimal("10")
        planilha.append(linha)
    total = [None] * 25
    total[0] = f"Total Produto: {codigo} PRODUTO TESTE {codigo}"
    total[15], total[24] = Decimal(str(periodo)), Decimal(str(periodo * 10))
    planilha.append(total)
    geral = [None] * 25
    geral[0] = "Total Geral:"
    geral[15], geral[24] = Decimal(str(periodo)), Decimal(str(periodo * 10))
    planilha.append(geral)
    livro.save(arquivo)
    arquivo.seek(0)
    return arquivo


def _contagens_base():
    engine = _criar_engine_somente_leitura()
    try:
        with engine.connect() as conexao:
            _confirmar_conexao(conexao)
            return dict(
                conexao.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM importacoes_rel0024) AS importacoes,
                          (SELECT count(*) FROM produtos_rel0024) AS produtos,
                          (SELECT count(*) FROM vendas_rel0024) AS vendas,
                          (SELECT count(*) FROM itens_rel0024) AS itens,
                          (SELECT sum(quantidade) FROM itens_rel0024) AS unidades
                        """
                    )
                ).mappings().one()
            )
    finally:
        engine.dispose()


class NovaAnaliseRel0024TestCase(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.cliente = app.test_client()

    def test_nova_rota_get_e_upload_invalido(self):
        resposta = self.cliente.get("/nova-analise")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(b"Nova an\xc3\xa1lise", resposta.data)
        self.assertIn(b"pelo menos 7 dias anteriores com vendas positivas", resposta.data)
        self.assertIn(b"nova_analise.css", resposta.data)
        self.assertIn(b"form-nova-analise", resposta.data)

        sem_arquivo = self.cliente.post("/nova-analise", data={})
        self.assertEqual(sem_arquivo.status_code, 400)

        extensao_invalida = self.cliente.post(
            "/nova-analise",
            data={"arquivo": (BytesIO(b"x"), "relatorio.csv")},
        )
        self.assertEqual(extensao_invalida.status_code, 400)

    def test_post_valido_curto_mostra_historico_insuficiente_sem_escrita(self):
        antes = _contagens_base()
        with patch(
            "utils.dados_reais.servico_nova_analise.consultar_catalogo_local",
            return_value=_catalogo([900001]),
        ):
            resposta = self.cliente.post(
                "/nova-analise",
                data={"arquivo": (_arquivo_xlsx(), "curto.xlsx")},
                content_type="multipart/form-data",
            )
        depois = _contagens_base()

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Histórico insuficiente".encode(), resposta.data)
        self.assertIn(b"1" , resposta.data)
        self.assertIn("Chaves analíticas".encode(), resposta.data)
        self.assertIn("Analisar outro arquivo".encode(), resposta.data)
        self.assertIn(b'href="/nova-analise"', resposta.data)
        self.assertEqual(antes, depois)

    def test_upload_exclui_antimicrobianos_antes_de_agregar_e_preserva_dcb_mista(self):
        codigos = [60001, 60002, 60003]
        itens, descricoes, validacao = _itens(60, codigos)
        cadastro = pd.DataFrame(
            [
                {"cod_reduzido": "60001", "nom_produto": "A", "cod_dcb": "1", "nom_dcb": "SUBSTANCIA X", "cod_tipo": "15", "nom_tipo": "MED GENERICO ANTIMICROBIANO"},
                {"cod_reduzido": "60002", "nom_produto": "B", "cod_dcb": "1", "nom_dcb": "SUBSTANCIA X", "cod_tipo": "102", "nom_tipo": "MED PROPAGADO ANTIMICROBIANO"},
                {"cod_reduzido": "60003", "nom_produto": "C", "cod_dcb": "1", "nom_dcb": "SUBSTANCIA X", "cod_tipo": "10", "nom_tipo": "MED GENERICO"},
            ]
        )
        with patch(
            "utils.dados_reais.servico_nova_analise.carregar_cadastro_dcb",
            return_value=cadastro,
        ):
            resultado = analisar_dados_rel0024(
                itens,
                descricoes,
                _catalogo(codigos),
                nome_arquivo="antimicrobianos.xlsx",
                sha256="e" * 64,
                validacao=validacao,
            )

        metricas = resultado["metricas"]
        self.assertEqual(metricas["produtos_elegiveis_antes"], 3)
        self.assertEqual(metricas["produtos_apos_uso_continuo"], 3)
        self.assertEqual(metricas["produtos_antimicrobianos_excluidos"], 2)
        self.assertEqual(metricas["produtos_elegiveis"], 1)
        self.assertEqual(metricas["chaves_analiticas"], 1)
        self.assertGreater(metricas["chaves_modelaveis"], 0)

    def test_produto_sem_catalogo_fica_sem_classificacao_e_inelegivel(self):
        itens, descricoes, validacao = _itens(5, [404404])
        resultado = analisar_dados_rel0024(
            itens,
            descricoes,
            {},
            nome_arquivo="sem_catalogo.xlsx",
            sha256="c" * 64,
            validacao=validacao,
        )

        self.assertEqual(resultado["metricas"]["produtos_encontrados"], 1)
        self.assertEqual(resultado["metricas"]["produtos_elegiveis"], 0)
        self.assertEqual(resultado["metricas"]["produtos_inelegiveis"], 1)
        self.assertEqual(resultado["metricas"]["observacoes_avaliadas"], 0)
        self.assertEqual(resultado["classificacoes"]["labels"], ["SEM_CLASSIFICACAO"])

    def test_a_b_a_tem_resultado_deterministico_e_usa_nucleo_comum(self):
        codigos_a = [101, 102, 103]
        itens_a, desc_a, valid_a = _itens(55, codigos_a)
        catalogo_a = _catalogo(codigos_a)
        codigos_b = [201, 202, 203]
        itens_b, desc_b, valid_b = _itens(45, codigos_b, deslocamento=100)

        def executar(itens, descricoes, catalogo, nome, validacao):
            return analisar_dados_rel0024(
                itens,
                descricoes,
                catalogo,
                nome_arquivo=nome,
                sha256=("a" if nome == "A.xlsx" else "b") * 64,
                validacao=validacao,
            )

        with (
            patch(
                "utils.dados_reais.servico_nova_analise.executar_modelo_c_hibrido_em_diagnostico",
                wraps=executar_modelo_c_hibrido_em_diagnostico,
            ) as nucleo_modelo,
            patch(
                "utils.ml.definir_modelo_validacao.diagnosticar_series",
                side_effect=AssertionError(
                    "A fonte consolidada não pode completar o histórico do arquivo."
                ),
            ),
        ):
            a1 = executar(itens_a, desc_a, catalogo_a, "A.xlsx", valid_a)
            executar(itens_b, desc_b, _catalogo(codigos_b), "B.xlsx", valid_b)
            a2 = executar(itens_a, desc_a, catalogo_a, "A.xlsx", valid_a)

        self.assertEqual(nucleo_modelo.call_count, 3)
        self.assertEqual(a1, a2)
        self.assertEqual(a1["metricas"]["produtos_encontrados"], 3)
        self.assertEqual(a1["metricas"]["produtos_elegiveis"], 3)
        self.assertGreater(a1["metricas"]["produtos_modelaveis"], 0)
        self.assertGreater(a1["metricas"]["observacoes_avaliadas"], 0)
        self.assertEqual(
            a1["metodologia"]["fonte_historico"],
            "somente o arquivo desta análise",
        )


    def test_upload_agrupa_dcb_e_preserva_produtos_sem_dcb(self):
        codigos = [30001, 30002, 900001]
        itens = []
        inicio = date(2025, 1, 1)
        descricoes = {
            30001: {"MEDICAMENTO A 10MG"},
            30002: {"MEDICAMENTO A 20MG"},
            900001: {"PRODUTO SEM DCB"},
        }
        for dia in range(65):
            for codigo in codigos:
                quantidade = 80 if codigo in (30001, 30002) and dia == 55 else 1
                itens.append(
                    {
                        "filial": 6,
                        "numero_venda": 700000 + dia * 10 + codigo % 10,
                        "codigo_produto": codigo,
                        "data": inicio + timedelta(days=dia),
                        "tipo": "Venda",
                        "quantidade": Decimal(str(quantidade)),
                    }
                )
        validacao = {
            "filiais": [6],
            "primeira_data": inicio,
            "ultima_data": inicio + timedelta(days=64),
            "linhas_venda": len(itens),
            "linhas_devolucao": 0,
        }
        catalogo = _catalogo(codigos)
        dcb = pd.DataFrame(
            [
                {"cod_reduzido": "30001", "cod_dcb": "123", "nom_dcb": "SUBSTANCIA A"},
                {"cod_reduzido": "30002", "cod_dcb": "123", "nom_dcb": "SUBSTANCIA A"},
            ]
        )
        with patch(
            "utils.dados_reais.servico_nova_analise.carregar_cadastro_dcb",
            return_value=dcb,
        ):
            resultado = analisar_dados_rel0024(
                itens, descricoes, catalogo, nome_arquivo="dcb.xlsx",
                sha256="d" * 64, validacao=validacao,
            )

        self.assertEqual(resultado["metricas"]["produtos_encontrados"], 3)
        self.assertEqual(resultado["metricas"]["chaves_analiticas"], 2)
        dcb_rows = [item for item in resultado["aumentos"] if item["tipo_chave"] == "DCB"]
        self.assertTrue(dcb_rows)
        self.assertEqual(dcb_rows[0]["nom_dcb"], "SUBSTANCIA A")
        self.assertEqual(dcb_rows[0]["produtos_associados"], 2)
        self.assertEqual(dcb_rows[0]["codigos_produto"], [30001, 30002])
        resultado_com_fallback = dict(resultado)
        resultado_com_fallback["aumentos"] = list(resultado["aumentos"])
        linha_sem_dcb = dict(dcb_rows[0])
        linha_sem_dcb.update(
            tipo_chave="PRODUTO",
            nom_dcb=None,
            produto="PRODUTO SEM DCB",
            codigo_produto=900001,
            codigos_produto=[900001],
            produtos_associados=1,
            associacoes_produtos=[],
        )
        resultado_com_fallback["aumentos"].append(linha_sem_dcb)
        with patch("app.analisar_upload_rel0024", return_value=resultado_com_fallback):
            resposta = self.cliente.post(
                "/nova-analise",
                data={"arquivo": (BytesIO(b"x"), "resultado.xlsx")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Tipo de chave".encode(), resposta.data)
        self.assertIn("SUBSTANCIA A".encode(), resposta.data)
        self.assertIn("30001 — MEDICAMENTO A 10MG".encode(), resposta.data)
        self.assertIn("Princípio ativo / DCB".encode(), resposta.data)
        self.assertIn("Não disponível no cadastro".encode(), resposta.data)
        self.assertIn(b"Diferen\xc3\xa7a vs. m\xc3\xa9dia", resposta.data)
        self.assertIn("Maior → menor".encode(), resposta.data)
        self.assertIn(b"01/01/2025", resposta.data)
        self.assertEqual(resposta.data.count(b'<tr data-date='), len(resultado_com_fallback["aumentos"]))
        self.assertIn(b'href="/nova-analise"', resposta.data)


if __name__ == "__main__":
    unittest.main()
