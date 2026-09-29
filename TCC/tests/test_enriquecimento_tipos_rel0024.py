import unittest

from utils.classificacao_medicamentos import (
    MED_CONTROLADO,
    MED_OUTRO,
    NAO_MED,
)
from utils.dados_reais.enriquecer_tipos_produtos_rel0024 import (
    ORIGEM,
    planejar_enriquecimento,
    reconciliar_classificacoes,
)


def _produto(codigo=1, cod_tipo=None, nom_tipo=None, origem=None):
    return {
        "id": codigo,
        "filial": 6,
        "codigo_produto": codigo,
        "descricao_original": f"PRODUTO {codigo}",
        "cod_tipo": cod_tipo,
        "nom_tipo": nom_tipo,
        "origem_classificacao": origem,
    }


def _tipo(codigo=1, cod_tipo=10, nom_tipo="MED GENERICO"):
    return {
        "codigo_produto": codigo,
        "cod_tipo": cod_tipo,
        "nom_tipo": nom_tipo,
    }


def _aplicar_plano(catalogo, plano):
    por_id = {produto["id"]: dict(produto) for produto in catalogo}
    for atualizacao in plano["atualizacoes"]:
        produto = por_id[atualizacao["id"]]
        produto["cod_tipo"] = atualizacao["cod_tipo_depois"]
        produto["nom_tipo"] = atualizacao["nom_tipo_depois"]
        produto["origem_classificacao"] = atualizacao["origem_depois"]
    return list(por_id.values())


class EnriquecimentoTiposRel0024TestCase(unittest.TestCase):
    def test_produto_novo_med(self):
        catalogo = [_produto()]
        plano = planejar_enriquecimento(
            catalogo, {1: _tipo(nom_tipo="MED GENERICO")}
        )
        depois = _aplicar_plano(catalogo, plano)
        self.assertEqual(len(plano["atualizacoes"]), 1)
        self.assertEqual(
            reconciliar_classificacoes(depois)[MED_OUTRO], 1
        )

    def test_produto_novo_controlado(self):
        catalogo = [_produto()]
        plano = planejar_enriquecimento(
            catalogo, {1: _tipo(nom_tipo="MED GENERICO CONTROLADO")}
        )
        depois = _aplicar_plano(catalogo, plano)
        classificacoes = reconciliar_classificacoes(depois)
        self.assertEqual(classificacoes[MED_CONTROLADO], 1)
        self.assertEqual(classificacoes["ELEGIVEIS"], 0)

    def test_produto_novo_nao_med(self):
        catalogo = [_produto()]
        plano = planejar_enriquecimento(
            catalogo, {1: _tipo(nom_tipo="PERFUMARIA")}
        )
        depois = _aplicar_plano(catalogo, plano)
        self.assertEqual(
            reconciliar_classificacoes(depois)[NAO_MED], 1
        )

    def test_produto_sem_correspondencia_permanece_sem_classificacao(self):
        catalogo = [_produto()]
        plano = planejar_enriquecimento(catalogo, {})
        self.assertEqual(plano["atualizacoes"], [])
        self.assertEqual(len(plano["nao_encontrados"]), 1)
        self.assertEqual(
            reconciliar_classificacoes(catalogo)["SEM_CLASSIFICACAO"], 1
        )

    def test_produto_ja_enriquecido_nao_e_atualizado(self):
        catalogo = [_produto(1, 10, "MED GENERICO", ORIGEM)]
        plano = planejar_enriquecimento(catalogo, {1: _tipo()})
        self.assertEqual(plano["atualizacoes"], [])
        self.assertEqual(plano["divergencias"], [])

    def test_classificacao_preenchida_e_igual_e_preservada(self):
        produto = _produto(1, 20, "HIGIENE", ORIGEM)
        plano = planejar_enriquecimento(
            [produto], {1: _tipo(1, 20, "HIGIENE")}
        )
        self.assertEqual(plano["atualizacoes"], [])
        self.assertEqual(produto["nom_tipo"], "HIGIENE")

    def test_divergencia_e_auditada_sem_sobrescrita(self):
        catalogo = [_produto(1, 10, "MED GENERICO", ORIGEM)]
        plano = planejar_enriquecimento(
            catalogo, {1: _tipo(1, 99, "PERFUMARIA")}
        )
        self.assertEqual(plano["atualizacoes"], [])
        self.assertEqual(len(plano["divergencias"]), 1)
        self.assertIn("cod_tipo", plano["divergencias"][0]["divergencias"])
        self.assertIn("nom_tipo", plano["divergencias"][0]["divergencias"])

    def test_enriquecimento_e_idempotente(self):
        catalogo = [_produto()]
        origem = {1: _tipo()}
        primeira = planejar_enriquecimento(catalogo, origem)
        catalogo_enriquecido = _aplicar_plano(catalogo, primeira)
        segunda = planejar_enriquecimento(catalogo_enriquecido, origem)
        self.assertEqual(len(primeira["atualizacoes"]), 1)
        self.assertEqual(segunda["atualizacoes"], [])
        self.assertEqual(segunda["divergencias"], [])


if __name__ == "__main__":
    unittest.main()
