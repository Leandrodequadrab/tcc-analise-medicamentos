from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook
from sqlalchemy import create_engine, func, inspect, select, text

from app import app
from database import obter_url_banco_aplicacao
from models.rel0024 import (
    DivergenciaDescricaoRel0024,
    ImportacaoRel0024,
    ItemRel0024,
    ProdutoRel0024,
    VendaRel0024,
)
from utils.dados_reais.importar_rel0024 import (
    ErroImportacaoRel0024,
    _criar_estrutura,
    _preparar_itens,
    importar_rel0024,
)
from utils.dados_reais.validar_rel0024 import ler_rel0024, validar_rel0024


def _linha_movimento(
    numero, produto, quantidade, tipo="Venda", descricao=None
):
    valor_unitario = Decimal("10.00")
    valor_total = Decimal(str(quantidade)) * valor_unitario
    if tipo.casefold() == "devol":
        valor_total = -abs(valor_total)
    return {
        "numero": numero,
        "produto": produto,
        "descricao": descricao or f"PRODUTO TESTE {produto}",
        "quantidade": Decimal(str(quantidade)),
        "tipo": tipo,
        "valor_unitario": valor_unitario,
        "valor_total": valor_total,
    }


def _criar_relatorio(caminho, movimentos):
    workbook = Workbook(write_only=True)
    planilha = workbook.create_sheet("Rel_0024")
    cabecalho = [None] * 25
    cabecalho[0] = "Venda"
    cabecalho[1] = "Loja"
    cabecalho[5] = "Tipo"
    cabecalho[6] = "Emissão"
    cabecalho[16] = "Qtd"
    cabecalho[18] = "Vlr.Unit"
    cabecalho[24] = "Vlr.Total"
    planilha.append(cabecalho)

    por_produto = {}
    for movimento in movimentos:
        por_produto.setdefault(
            (movimento["produto"], movimento["descricao"]), []
        ).append(movimento)

    total_quantidade = Decimal("0")
    total_valor = Decimal("0")
    for (produto, descricao), grupo in por_produto.items():
        quantidade_produto = Decimal("0")
        valor_produto = Decimal("0")
        for movimento in grupo:
            linha = [None] * 25
            linha[0] = movimento["numero"]
            linha[1] = 6
            linha[5] = movimento["tipo"]
            linha[6] = datetime(2026, 4, 1)
            linha[10] = "10:00:00"
            linha[16] = movimento["quantidade"]
            linha[18] = movimento["valor_unitario"]
            linha[19] = Decimal("0")
            linha[20] = Decimal("0")
            linha[21] = Decimal("0")
            linha[24] = movimento["valor_total"]
            planilha.append(linha)
            if movimento["tipo"].casefold() == "devol":
                quantidade_produto -= abs(movimento["quantidade"])
                valor_produto -= abs(movimento["valor_total"])
            else:
                quantidade_produto += movimento["quantidade"]
                valor_produto += movimento["valor_total"]
        total = [None] * 25
        total[0] = f"Total Produto: {produto} {descricao}"
        total[15] = quantidade_produto
        total[24] = valor_produto
        planilha.append(total)
        total_quantidade += quantidade_produto
        total_valor += valor_produto

    geral = [None] * 25
    geral[0] = "Total Geral:"
    geral[15] = total_quantidade
    geral[24] = total_valor
    planilha.append(geral)
    workbook.save(caminho)


def _contagens(conexao):
    return {
        "importacoes": conexao.execute(
            select(func.count()).select_from(ImportacaoRel0024.__table__)
        ).scalar_one(),
        "produtos": conexao.execute(
            select(func.count()).select_from(ProdutoRel0024.__table__)
        ).scalar_one(),
        "vendas": conexao.execute(
            select(func.count()).select_from(VendaRel0024.__table__)
        ).scalar_one(),
        "itens": conexao.execute(
            select(func.count()).select_from(ItemRel0024.__table__)
        ).scalar_one(),
        "quantidade": conexao.execute(
            select(func.sum(ItemRel0024.__table__.c.quantidade))
        ).scalar_one(),
    }


def _item_para_pareamento(
    quantidade,
    ocorrencia,
    *,
    valor_total=None,
):
    quantidade = Decimal(str(quantidade))
    return {
        "filial": 6,
        "numero_venda": 900001,
        "codigo_produto": 800001,
        "ocorrencia_produto": ocorrencia,
        "data": date(2026, 4, 1),
        "hora_bruta": "10:00:00",
        "tipo": "Venda",
        "quantidade": quantidade,
        "valor_unitario": Decimal("10.00"),
        "percentual_desconto": Decimal("0"),
        "valor_desconto": Decimal("0"),
        "valor_acrescimo": Decimal("0"),
        "valor_total": (
            Decimal(str(valor_total))
            if valor_total is not None
            else quantidade * Decimal("10.00")
        ),
        "linha_relatorio": ocorrencia + 1,
    }


def _item_existente(item):
    return {
        "venda_id": 1,
        "produto_id": 2,
        "ocorrencia_produto": item["ocorrencia_produto"],
        "data_venda": item["data"],
        "hora_original": item["hora_bruta"],
        "tipo_original": item["tipo"],
        "quantidade": item["quantidade"],
        "valor_unitario": item["valor_unitario"],
        "percentual_desconto": item["percentual_desconto"],
        "valor_desconto": item["valor_desconto"],
        "valor_acrescimo": item["valor_acrescimo"],
        "valor_total": item["valor_total"],
        "linha_relatorio": item["linha_relatorio"],
    }


class PareamentoMulticonjuntoTestCase(unittest.TestCase):
    mapa_vendas = {(6, 900001): 1}
    mapa_produtos = {(6, 800001): 2}

    def _preparar(self, arquivo, banco):
        return _preparar_itens(
            arquivo,
            self.mapa_vendas,
            self.mapa_produtos,
            {(1, 2): banco},
        )

    def test_duas_ocorrencias_na_mesma_ordem(self):
        arquivo = [
            _item_para_pareamento(1, 1),
            _item_para_pareamento(2, 2),
        ]
        novos, existentes = self._preparar(
            arquivo, [_item_existente(item) for item in arquivo]
        )
        self.assertEqual(novos, [])
        self.assertEqual(existentes, 2)

    def test_duas_ocorrencias_em_ordem_invertida(self):
        banco_arquivo = [
            _item_para_pareamento(1, 1),
            _item_para_pareamento(2, 2),
        ]
        arquivo = [
            _item_para_pareamento(2, 1),
            _item_para_pareamento(1, 2),
        ]
        novos, existentes = self._preparar(
            arquivo, [_item_existente(item) for item in banco_arquivo]
        )
        self.assertEqual(novos, [])
        self.assertEqual(existentes, 2)

    def test_ocorrencias_identicas_preservam_multiplicidade(self):
        arquivo = [
            _item_para_pareamento(1, 1),
            _item_para_pareamento(1, 2),
        ]
        novos, existentes = self._preparar(
            arquivo, [_item_existente(item) for item in arquivo]
        )
        self.assertEqual(novos, [])
        self.assertEqual(existentes, 2)

    def test_quantidade_realmente_diferente_e_conflito(self):
        arquivo = [_item_para_pareamento(2, 1)]
        banco = [_item_existente(_item_para_pareamento(1, 1))]
        with self.assertRaisesRegex(
            ErroImportacaoRel0024, "conteúdo divergente"
        ):
            self._preparar(arquivo, banco)

    def test_valor_financeiro_realmente_diferente_e_conflito(self):
        arquivo = [_item_para_pareamento(1, 1, valor_total="9.99")]
        banco = [_item_existente(_item_para_pareamento(1, 1))]
        with self.assertRaisesRegex(
            ErroImportacaoRel0024, "conteúdo divergente"
        ):
            self._preparar(arquivo, banco)

    def test_ocorrencia_faltante_e_conflito(self):
        arquivo = [_item_para_pareamento(1, 1)]
        banco = [
            _item_existente(_item_para_pareamento(1, 1)),
            _item_existente(_item_para_pareamento(1, 2)),
        ]
        with self.assertRaisesRegex(
            ErroImportacaoRel0024, "multiplicidade.*1"
        ):
            self._preparar(arquivo, banco)

    def test_ocorrencia_adicional_e_conflito(self):
        arquivo = [
            _item_para_pareamento(1, 1),
            _item_para_pareamento(1, 2),
        ]
        banco = [_item_existente(_item_para_pareamento(1, 1))]
        with self.assertRaisesRegex(
            ErroImportacaoRel0024, "multiplicidade.*1"
        ):
            self._preparar(arquivo, banco)


class ImportacaoIncrementalRel0024TestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
        _criar_estrutura(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_a_b_c_d_incremental_hash_sobreposicao_e_conflito(self):
        with tempfile.TemporaryDirectory() as pasta, self.engine.connect() as conexao:
            transacao = conexao.begin()
            antes = _contagens(conexao)
            maior_venda = conexao.execute(
                select(func.max(VendaRel0024.__table__.c.numero_venda))
            ).scalar_one() or 0
            maior_produto = conexao.execute(
                select(func.max(ProdutoRel0024.__table__.c.codigo_produto))
            ).scalar_one() or 0
            venda_1, venda_2 = maior_venda + 1001, maior_venda + 1002
            produto_1, produto_2 = maior_produto + 1001, maior_produto + 1002
            arquivo_a = Path(pasta) / "novo.xlsx"
            arquivo_c = Path(pasta) / "parcialmente_sobreposto.xlsx"
            arquivo_d = Path(pasta) / "conflito.xlsx"
            venda = _linha_movimento(venda_1, produto_1, 2)
            devolucao = _linha_movimento(
                venda_1 + 5000, produto_1, 1, tipo="Devol"
            )
            nova = _linha_movimento(venda_2, produto_2, 4)
            _criar_relatorio(arquivo_a, [venda, devolucao])
            _criar_relatorio(arquivo_c, [venda, devolucao, nova])
            _criar_relatorio(
                arquivo_d, [_linha_movimento(venda_1, produto_1, 3)]
            )
            try:
                caso_a = importar_rel0024(
                    arquivo_a,
                    nome_arquivo="novo.xlsx",
                    conexao_externa=conexao,
                )
                self.assertEqual(caso_a["vendas_inseridas"], 1)
                self.assertEqual(caso_a["itens_inseridos"], 1)
                self.assertEqual(caso_a["linhas_nao_venda"], 1)
                self.assertEqual(caso_a["soma_quantidades_inseridas"], 2)
                self.assertEqual(
                    caso_a["validacao"]["quantidade_devolucoes"], 1
                )
                self.assertEqual(
                    caso_a["validacao"]["quantidade_liquida_calculada"], 1
                )

                with patch(
                    "utils.dados_reais.importar_rel0024.ler_rel0024",
                    side_effect=AssertionError("não deveria reprocessar"),
                ):
                    caso_b = importar_rel0024(
                        arquivo_a,
                        nome_arquivo="novo.xlsx",
                        conexao_externa=conexao,
                    )
                self.assertTrue(caso_b["arquivo_ja_importado"])
                self.assertEqual(caso_b["itens_inseridos"], 0)

                caso_c = importar_rel0024(
                    arquivo_c,
                    nome_arquivo="parcialmente_sobreposto.xlsx",
                    conexao_externa=conexao,
                )
                self.assertEqual(caso_c["produtos_inseridos"], 1)
                self.assertEqual(caso_c["produtos_existentes"], 1)
                self.assertEqual(caso_c["vendas_inseridas"], 1)
                self.assertEqual(caso_c["vendas_existentes"], 1)
                self.assertEqual(caso_c["itens_inseridos"], 1)
                self.assertEqual(caso_c["itens_existentes"], 1)

                contagens_antes_conflito = _contagens(conexao)
                with self.assertRaises(ErroImportacaoRel0024):
                    importar_rel0024(
                        arquivo_d,
                        nome_arquivo="conflito.xlsx",
                        conexao_externa=conexao,
                    )
                self.assertEqual(
                    _contagens(conexao), contagens_antes_conflito
                )

                produto_novo = conexao.execute(
                    select(ProdutoRel0024.__table__).where(
                        ProdutoRel0024.__table__.c.codigo_produto == produto_2
                    )
                ).mappings().one()
                self.assertIsNone(produto_novo["cod_tipo"])
                self.assertIsNone(produto_novo["nom_tipo"])
                self.assertIsNone(produto_novo["origem_classificacao"])
            finally:
                transacao.rollback()

        with self.engine.connect() as verificacao:
            self.assertEqual(_contagens(verificacao), antes)

    def test_e_f_g_validacoes_web(self):
        cliente = app.test_client()
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 50 * 1024 * 1024)

        extensao = cliente.post(
            "/importar-rel0024",
            data={"arquivo": (BytesIO(b"texto"), "relatorio.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(extensao.status_code, 400)

        invalido = cliente.post(
            "/importar-rel0024",
            data={"arquivo": (BytesIO(b"nao e xlsx"), "relatorio.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(invalido.status_code, 400)

        limite_original = app.config["MAX_CONTENT_LENGTH"]
        app.config["MAX_CONTENT_LENGTH"] = 100
        try:
            grande = cliente.post(
                "/importar-rel0024",
                data={"arquivo": (BytesIO(b"x" * 200), "grande.xlsx")},
                content_type="multipart/form-data",
            )
            self.assertEqual(grande.status_code, 413)
        finally:
            app.config["MAX_CONTENT_LENGTH"] = limite_original

    def test_total_liquido_venda_menos_devolucao(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "venda_devol.xlsx"
            movimentos = [
                _linha_movimento(900001, 800001, 5, tipo="Venda"),
                _linha_movimento(900002, 800001, 2, tipo="Devol"),
            ]
            _criar_relatorio(arquivo, movimentos)
            resultado = ler_rel0024(arquivo)
            validacao = validar_rel0024(resultado)

        self.assertEqual(validacao["linhas_venda"], 1)
        self.assertEqual(validacao["linhas_devolucao"], 1)
        self.assertEqual(validacao["quantidade_vendas"], Decimal("5"))
        self.assertEqual(validacao["valor_vendas"], Decimal("50"))
        self.assertEqual(
            validacao["quantidade_devolucoes"], Decimal("2")
        )
        self.assertEqual(validacao["valor_devolucoes"], Decimal("-20"))
        self.assertEqual(
            validacao["quantidade_liquida_calculada"], Decimal("3")
        )
        self.assertEqual(
            validacao["valor_liquido_calculado"], Decimal("30")
        )
        self.assertTrue(validacao["total_geral_quantidade_confere"])
        self.assertTrue(validacao["total_geral_valor_confere"])
        self.assertTrue(
            validacao["totais_produto_quantidade_conferem_total_geral"]
        )
        self.assertTrue(
            validacao["totais_produto_valor_conferem_total_geral"]
        )
        self.assertEqual(len(resultado["itens"]), 1)
        self.assertEqual(resultado["itens"][0]["tipo"], "Venda")

    def test_divergencia_descricao_e_auditada_sem_sobrescrever(self):
        with tempfile.TemporaryDirectory() as pasta, self.engine.connect() as conexao:
            transacao = conexao.begin()
            antes = _contagens(conexao)
            maior_venda = conexao.execute(
                select(func.max(VendaRel0024.__table__.c.numero_venda))
            ).scalar_one() or 0
            maior_produto = conexao.execute(
                select(func.max(ProdutoRel0024.__table__.c.codigo_produto))
            ).scalar_one() or 0
            codigo = maior_produto + 2001
            primeiro = Path(pasta) / "descricao_historica.xlsx"
            segundo = Path(pasta) / "descricao_atualizada.xlsx"
            _criar_relatorio(
                primeiro,
                [
                    _linha_movimento(
                        maior_venda + 2001,
                        codigo,
                        1,
                        descricao="DESCRICAO HISTORICA 100M",
                    )
                ],
            )
            _criar_relatorio(
                segundo,
                [
                    _linha_movimento(
                        maior_venda + 2002,
                        codigo,
                        1,
                        descricao="DESCRICAO ATUALIZADA 100ML",
                    )
                ],
            )
            try:
                importar_rel0024(
                    primeiro,
                    nome_arquivo=primeiro.name,
                    conexao_externa=conexao,
                )
                relatorio = importar_rel0024(
                    segundo,
                    nome_arquivo=segundo.name,
                    conexao_externa=conexao,
                )
                self.assertEqual(len(relatorio["divergencias_descricao"]), 1)
                divergencia = relatorio["divergencias_descricao"][0]
                self.assertEqual(divergencia["codigo_produto"], codigo)
                self.assertEqual(
                    divergencia["descricao_banco"],
                    "DESCRICAO HISTORICA 100M",
                )
                self.assertEqual(
                    divergencia["descricao_arquivo"],
                    "DESCRICAO ATUALIZADA 100ML",
                )
                descricao_preservada = conexao.execute(
                    select(ProdutoRel0024.__table__.c.descricao_original).where(
                        ProdutoRel0024.__table__.c.codigo_produto == codigo
                    )
                ).scalar_one()
                self.assertEqual(
                    descricao_preservada, "DESCRICAO HISTORICA 100M"
                )
                auditoria = conexao.execute(
                    select(DivergenciaDescricaoRel0024.__table__).where(
                        DivergenciaDescricaoRel0024.__table__.c.codigo_produto
                        == codigo
                    )
                ).mappings().one()
                self.assertEqual(
                    auditoria["descricao_arquivo"],
                    "DESCRICAO ATUALIZADA 100ML",
                )
            finally:
                transacao.rollback()

        with self.engine.connect() as verificacao:
            self.assertEqual(_contagens(verificacao), antes)

    def test_estado_real_e_constraints_preservados(self):
        with self.engine.connect() as conexao:
            self.assertEqual(
                _contagens(conexao),
                {
                    "importacoes": 1,
                    "produtos": 4666,
                    "vendas": 27671,
                    "itens": 52005,
                    "quantidade": Decimal("73769.0000"),
                },
            )
            ficticias = conexao.execute(
                text("SELECT COUNT(*) FROM public.vendas")
            ).scalar_one()
            self.assertEqual(ficticias, 30)
            auditoria = conexao.execute(
                select(ImportacaoRel0024.__table__)
            ).mappings().one()
            self.assertEqual(auditoria["produtos_inseridos"], 4666)
            self.assertEqual(auditoria["vendas_inseridas"], 27671)
            self.assertEqual(
                auditoria["soma_quantidades_inseridas"],
                Decimal("73769.0000"),
            )

        inspetor = inspect(self.engine)
        esperadas = {
            "produtos_rel0024": {("filial", "codigo_produto")},
            "vendas_rel0024": {("filial", "numero_venda")},
            "itens_rel0024": {
                ("venda_id", "produto_id", "ocorrencia_produto")
            },
        }
        for tabela, constraints in esperadas.items():
            encontradas = {
                tuple(item["column_names"])
                for item in inspetor.get_unique_constraints(tabela)
            }
            self.assertTrue(constraints.issubset(encontradas))


if __name__ == "__main__":
    unittest.main()
