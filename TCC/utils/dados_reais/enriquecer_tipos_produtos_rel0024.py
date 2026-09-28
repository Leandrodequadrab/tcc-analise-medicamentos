"""Copia cod_tipo/nom_tipo do cadastro para o catalogo local Rel_0024.

O sgfpod1 e acessado pela conexao protegida, somente leitura. A unica escrita
ocorre no tcc_medicamentos e fica restrita a tres metadados do produto.
"""

from __future__ import annotations

import sys

from sqlalchemy import bindparam, create_engine, text

from database import obter_url_banco_aplicacao
from utils.classificacao_medicamentos import classificar_elegibilidade

from .conexao_farmacia import conectar_farmacia_somente_leitura


BANCO_TCC = "tcc_medicamentos"
TOTAL_ESPERADO = 4_666
ORIGEM = "sgfpod1.cadprodu->cadtipro|regra_primeiro_token_MED_10D4"

SQL_CATALOGO = text(
    """
    SELECT filial, codigo_produto, descricao_original
    FROM public.produtos_rel0024
    ORDER BY codigo_produto
    """
)
SQL_REDESS = text(
    """
    SELECT cod_rede, COUNT(DISTINCT cod_reduzido) AS produtos
    FROM public.cadprodu
    WHERE cod_reduzido IN :codigos
    GROUP BY cod_rede
    ORDER BY cod_rede
    """
).bindparams(bindparam("codigos", expanding=True))
SQL_TIPOS = text(
    """
    SELECT p.cod_reduzido AS codigo_produto, p.cod_tipo, t.nom_tipo
    FROM public.cadprodu AS p
    LEFT JOIN public.cadtipro AS t
      ON t.cod_rede = p.cod_rede AND t.cod_tipo = p.cod_tipo
    WHERE p.cod_rede = :cod_rede
      AND p.cod_reduzido IN :codigos
    ORDER BY p.cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_INTEGRIDADE = text(
    """
    SELECT
        (SELECT COUNT(*) FROM public.produtos_rel0024) AS produtos,
        (SELECT COUNT(*) FROM public.vendas_rel0024) AS vendas,
        (SELECT COUNT(*) FROM public.itens_rel0024) AS itens,
        (SELECT COUNT(*) FROM public.vendas) AS vendas_ficticias,
        (SELECT COALESCE(SUM(quantidade), 0) FROM public.itens_rel0024)
            AS soma_quantidades,
        (SELECT COUNT(*) FROM public.produtos_rel0024
         WHERE cod_tipo IS NOT NULL OR nom_tipo IS NOT NULL) AS produtos_com_tipo,
        (SELECT COUNT(*) FROM public.produtos_rel0024
         WHERE origem_classificacao = :origem) AS produtos_com_origem
    """
)


def _linhas(conexao, consulta, parametros=None):
    return [dict(linha) for linha in conexao.execute(
        consulta, parametros or {}
    ).mappings()]


def _confirmar_tcc(conexao):
    banco = conexao.execute(text("SELECT current_database()" )).scalar_one()
    if banco != BANCO_TCC:
        raise RuntimeError(
            f"Banco de destino recusado: esperado {BANCO_TCC!r}, obtido {banco!r}."
        )


def executar():
    engine = create_engine(obter_url_banco_aplicacao(), pool_pre_ping=True)
    try:
        with engine.connect() as tcc:
            _confirmar_tcc(tcc)
            catalogo = _linhas(tcc, SQL_CATALOGO)
        if len(catalogo) != TOTAL_ESPERADO:
            raise RuntimeError(
                f"Esperados {TOTAL_ESPERADO} produtos; encontrados {len(catalogo)}."
            )
        codigos = sorted({linha["codigo_produto"] for linha in catalogo})
        if len(codigos) != TOTAL_ESPERADO:
            raise RuntimeError("O catalogo local possui codigos de produto duplicados.")

        with conectar_farmacia_somente_leitura() as farmacia:
            modo = farmacia.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
            if modo != "on":
                raise RuntimeError("sgfpod1 nao esta em modo somente leitura.")
            redes = _linhas(farmacia, SQL_REDESS, {"codigos": codigos})
            completas = [
                linha["cod_rede"]
                for linha in redes
                if int(linha["produtos"]) == TOTAL_ESPERADO
            ]
            if len(completas) != 1:
                raise RuntimeError(
                    "Nao foi possivel determinar uma unica rede com cobertura integral."
                )
            cod_rede = completas[0]
            tipos = _linhas(
                farmacia,
                SQL_TIPOS,
                {"cod_rede": cod_rede, "codigos": codigos},
            )

        if len(tipos) != TOTAL_ESPERADO:
            raise RuntimeError(
                f"Cobertura da origem incompleta: {len(tipos)} de {TOTAL_ESPERADO}."
            )
        por_codigo = {linha["codigo_produto"]: linha for linha in tipos}
        if len(por_codigo) != TOTAL_ESPERADO or set(por_codigo) != set(codigos):
            raise RuntimeError("A origem nao produziu correspondencia unica por produto.")

        registros = [
            {
                "filial": produto["filial"],
                "codigo_produto": produto["codigo_produto"],
                "cod_tipo": por_codigo[produto["codigo_produto"]]["cod_tipo"],
                "nom_tipo": por_codigo[produto["codigo_produto"]]["nom_tipo"],
                "origem": ORIGEM,
            }
            for produto in catalogo
        ]

        with engine.begin() as tcc:
            _confirmar_tcc(tcc)
            tcc.execute(text(
                "ALTER TABLE public.produtos_rel0024 "
                "ADD COLUMN IF NOT EXISTS cod_tipo INTEGER"
            ))
            tcc.execute(text(
                "ALTER TABLE public.produtos_rel0024 "
                "ADD COLUMN IF NOT EXISTS nom_tipo VARCHAR(100)"
            ))
            tcc.execute(text(
                "ALTER TABLE public.produtos_rel0024 "
                "ADD COLUMN IF NOT EXISTS origem_classificacao VARCHAR(100)"
            ))
            antes = dict(tcc.execute(
                SQL_INTEGRIDADE, {"origem": ORIGEM}
            ).mappings().one())
            resultado = tcc.execute(
                text(
                    "UPDATE public.produtos_rel0024 "
                    "SET cod_tipo = :cod_tipo, nom_tipo = :nom_tipo, "
                    "origem_classificacao = :origem "
                    "WHERE filial = :filial AND codigo_produto = :codigo_produto"
                ),
                registros,
            )
            depois = dict(tcc.execute(
                SQL_INTEGRIDADE, {"origem": ORIGEM}
            ).mappings().one())
            if resultado.rowcount != TOTAL_ESPERADO:
                raise RuntimeError(
                    f"Atualizacao incompleta: {resultado.rowcount} produtos."
                )
            for campo in (
                "produtos",
                "vendas",
                "itens",
                "vendas_ficticias",
                "soma_quantidades",
            ):
                if antes[campo] != depois[campo]:
                    raise RuntimeError(
                        f"Integridade violada no campo {campo}; transacao revertida."
                    )
            if int(depois["produtos_com_origem"]) != TOTAL_ESPERADO:
                raise RuntimeError("Nem todos os produtos receberam a origem esperada.")

        contagens = {}
        for linha in tipos:
            classificacao, elegivel = classificar_elegibilidade(linha["nom_tipo"])
            contagens[classificacao] = contagens.get(classificacao, 0) + 1
            if elegivel:
                contagens["ELEGIVEIS"] = contagens.get("ELEGIVEIS", 0) + 1
        return {
            "banco_farmacia": "sgfpod1",
            "farmacia_read_only": "on",
            "banco_tcc": BANCO_TCC,
            "cod_rede": cod_rede,
            "origem": ORIGEM,
            "antes": antes,
            "depois": depois,
            "classificacoes": contagens,
        }
    finally:
        engine.dispose()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    resultado = executar()
    print("ENRIQUECIMENTO CONTROLADO CONCLUIDO")
    print(f"sgfpod1 transaction_read_only: {resultado['farmacia_read_only']}")
    print(f"Rede validada: {resultado['cod_rede']}")
    print(f"Origem registrada: {resultado['origem']}")
    print(f"Catalogo: {resultado['depois']['produtos']} produtos")
    print(f"Produtos com tipo: {resultado['depois']['produtos_com_tipo']}")
    for nome, quantidade in sorted(resultado["classificacoes"].items()):
        print(f"{nome}: {quantidade}")
    print("Vendas, itens e quantidades permaneceram inalterados.")


if __name__ == "__main__":
    main()
