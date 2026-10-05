from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import unittest
from unittest.mock import patch

from openpyxl import Workbook
from sqlalchemy import text

from app import app
from utils.dados_reais.servico_nova_analise import analisar_dados_rel0024
from utils.ml.definir_modelo_validacao import executar_modelo_c_em_diagnostico
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
        self.assertEqual(antes, depois)

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
                "utils.dados_reais.servico_nova_analise.executar_modelo_c_em_diagnostico",
                wraps=executar_modelo_c_em_diagnostico,
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


if __name__ == "__main__":
    unittest.main()
