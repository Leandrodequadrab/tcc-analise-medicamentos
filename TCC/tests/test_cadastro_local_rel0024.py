import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, insert, select

from models.rel0024 import CadastroProdutoFarmacia, ProdutoRel0024
from utils.classificacao_medicamentos import (
    MED_CONTROLADO,
    MED_OUTRO,
    MED_OTC_INTERNO,
    NAO_MED,
    SEM_CLASSIFICACAO,
)
from utils.dados_reais import conexao_farmacia
from utils.dados_reais.importar_rel0024 import (
    ErroImportacaoRel0024,
    _sincronizar_produtos,
)


class CadastroLocalRel0024TestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        CadastroProdutoFarmacia.metadata.create_all(
            self.engine,
            tables=[CadastroProdutoFarmacia.__table__, ProdutoRel0024.__table__],
        )
        with self.engine.begin() as conexao:
            conexao.execute(
                insert(CadastroProdutoFarmacia.__table__),
                [
                    {
                        "cod_rede": 1,
                        "cod_reduzido": 101,
                        "cod_tipo": 11,
                        "nom_tipo": "MED PROPAGADO OTC",
                        "nom_produto": "PRODUTO OTC",
                        "origem": "sgfpod1.cadprodu+cadtipro",
                    },
                    {
                        "cod_rede": 1,
                        "cod_reduzido": 102,
                        "cod_tipo": 14,
                        "nom_tipo": "MED PROPAGADO CONTROLADO",
                        "nom_produto": "PRODUTO CONTROLADO",
                        "origem": "sgfpod1.cadprodu+cadtipro",
                    },
                    {
                        "cod_rede": 1,
                        "cod_reduzido": 103,
                        "cod_tipo": 90,
                        "nom_tipo": "HIGIENE",
                        "nom_produto": "PRODUTO NAO MED",
                        "origem": "sgfpod1.cadprodu+cadtipro",
                    },
                    {
                        "cod_rede": 1,
                        "cod_reduzido": 104,
                        "cod_tipo": None,
                        "nom_tipo": None,
                        "nom_produto": "SEM TIPO",
                        "origem": "sgfpod1.cadprodu+cadtipro",
                    },
                    {
                        "cod_rede": 1,
                        "cod_reduzido": 105,
                        "cod_tipo": 50,
                        "nom_tipo": "MED GENERICO",
                        "nom_produto": "PRODUTO GENERICO",
                        "origem": "sgfpod1.cadprodu+cadtipro",
                    },
                ],
            )

    def tearDown(self):
        self.engine.dispose()

    def test_novos_produtos_usam_cadastro_local_e_codigo_desconhecido_fica_pendente(self):
        with patch.object(
            conexao_farmacia,
            "conectar_farmacia_somente_leitura",
            side_effect=AssertionError("o upload não pode abrir sgfpod1"),
        ):
            with self.engine.begin() as conexao:
                mapa, inseridos, existentes, _ = _sincronizar_produtos(
                    conexao,
                    {
                        (6, 101): "OTC",
                        (6, 102): "CONTROLADO",
                        (6, 103): "HIGIENE",
                        (6, 104): "SEM TIPO",
                        (6, 999): "NAO CADASTRADO",
                    },
                )
                self.assertEqual(inseridos, 5)
                self.assertEqual(existentes, 0)
                rows = conexao.execute(
                    select(ProdutoRel0024.__table__).order_by(
                        ProdutoRel0024.codigo_produto
                    )
                ).mappings().all()

        por_codigo = {linha["codigo_produto"]: linha for linha in rows}
        self.assertEqual(por_codigo[101]["classificacao_operacional"], MED_OTC_INTERNO)
        self.assertTrue(por_codigo[101]["elegivel_analise_automedicacao"])
        self.assertEqual(por_codigo[102]["classificacao_operacional"], MED_CONTROLADO)
        self.assertFalse(por_codigo[102]["elegivel_analise_automedicacao"])
        self.assertEqual(por_codigo[103]["classificacao_operacional"], NAO_MED)
        self.assertFalse(por_codigo[103]["elegivel_analise_automedicacao"])
        self.assertEqual(por_codigo[104]["classificacao_operacional"], SEM_CLASSIFICACAO)
        self.assertFalse(por_codigo[104]["elegivel_analise_automedicacao"])
        self.assertEqual(por_codigo[999]["classificacao_operacional"], SEM_CLASSIFICACAO)
        self.assertIsNone(por_codigo[999]["cod_tipo"])
        self.assertEqual(len(mapa), 5)

    def test_produto_existente_pendente_e_enriquecido_sem_duplicar(self):
        tabela = ProdutoRel0024.__table__
        with self.engine.begin() as conexao:
            conexao.execute(
                insert(tabela).values(
                    filial=6,
                    codigo_produto=101,
                    descricao_original="DESCRICAO ANTIGA",
                )
            )
            _sincronizar_produtos(conexao, {(6, 101): "NOVA DESCRICAO"})
            resultado = _sincronizar_produtos(
                conexao, {(6, 101): "NOVA DESCRICAO"}
            )
            produtos = conexao.execute(select(tabela)).mappings().all()

        self.assertEqual(resultado[1:3], (0, 1))
        self.assertEqual(len(produtos), 1)
        self.assertEqual(produtos[0]["cod_tipo"], 11)
        self.assertEqual(produtos[0]["classificacao_operacional"], MED_OTC_INTERNO)
        self.assertEqual(produtos[0]["descricao_original"], "DESCRICAO ANTIGA")

    def test_divergencia_existente_nao_e_sobrescrita(self):
        tabela = ProdutoRel0024.__table__
        with self.engine.begin() as conexao:
            conexao.execute(
                insert(tabela).values(
                    filial=6,
                    codigo_produto=101,
                    descricao_original="PRODUTO",
                    cod_tipo=90,
                    nom_tipo="HIGIENE",
                    classificacao_operacional=NAO_MED,
                    elegivel_analise_automedicacao=False,
                )
            )
            with self.assertRaises(ErroImportacaoRel0024):
                _sincronizar_produtos(conexao, {(6, 101): "PRODUTO"})
            produto = conexao.execute(select(tabela)).mappings().one()

        self.assertEqual(produto["cod_tipo"], 90)
        self.assertEqual(produto["classificacao_operacional"], NAO_MED)

    def test_tipo_med_comum_e_classificado_como_med_outro(self):
        with self.engine.begin() as conexao:
            _sincronizar_produtos(conexao, {(6, 105): "MED"})
            row = conexao.execute(
                select(ProdutoRel0024.classificacao_operacional).where(
                    ProdutoRel0024.codigo_produto == 105
                )
            ).scalar_one()
        self.assertEqual(row, MED_OUTRO)


if __name__ == "__main__":
    unittest.main()
