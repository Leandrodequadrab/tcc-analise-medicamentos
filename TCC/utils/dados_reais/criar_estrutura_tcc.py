"""Cria e verifica somente as tabelas normalizadas no banco do TCC."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from database import obter_url_banco_aplicacao
from models.dados_reais import TABELAS_DADOS_REAIS


BANCO_PERMITIDO = "tcc_medicamentos"
NOMES_TABELAS = tuple(tabela.name for tabela in TABELAS_DADOS_REAIS)

CONSTRAINTS_ESPERADAS = {
    "produtos": {
        "pk": ("id",),
        "unique": {("cod_rede", "cod_reduzido")},
        "fks": set(),
    },
    "vendas_reais": {
        "pk": ("id",),
        "unique": {("cod_rede", "cod_filial", "num_nota")},
        "fks": set(),
    },
    "itens_venda": {
        "pk": ("id",),
        "unique": {("venda_id", "num_sequencial")},
        "fks": {
            (("venda_id",), "vendas_reais", ("id",)),
            (("produto_id",), "produtos", ("id",)),
        },
    },
}


def _confirmar_banco_destino(conexao):
    banco_atual = conexao.execute(text("SELECT current_database()")).scalar_one()
    if banco_atual != BANCO_PERMITIDO:
        raise RuntimeError(
            f"Operação interrompida: banco atual é {banco_atual!r}, "
            f"mas somente {BANCO_PERMITIDO!r} é permitido."
        )
    return banco_atual


def _descrever_tabela(inspetor, nome):
    colunas = inspetor.get_columns(nome)
    pk = tuple(inspetor.get_pk_constraint(nome)["constrained_columns"])
    uniques = {
        tuple(constraint["column_names"])
        for constraint in inspetor.get_unique_constraints(nome)
    }
    fks = {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspetor.get_foreign_keys(nome)
    }
    return {
        "colunas": colunas,
        "pk": pk,
        "unique": uniques,
        "fks": fks,
    }


def _validar_constraints(estrutura):
    for nome, esperado in CONSTRAINTS_ESPERADAS.items():
        encontrado = estrutura[nome]
        for tipo in ("pk", "unique", "fks"):
            if encontrado[tipo] != esperado[tipo]:
                raise RuntimeError(
                    f"Constraint inesperada em {nome} ({tipo}): "
                    f"{encontrado[tipo]!r}."
                )


def criar_e_verificar_estrutura():
    """Confirma o destino, cria apenas as três tabelas e inspeciona o resultado."""

    engine = create_engine(obter_url_banco_aplicacao())
    try:
        with engine.connect() as conexao:
            banco = _confirmar_banco_destino(conexao)

        inspetor = inspect(engine)
        tabelas_antes = set(inspetor.get_table_names())

        # checkfirst evita recriar tabelas que já existam. A lista limita o
        # comando a produtos, vendas_reais e itens_venda.
        TABELAS_DADOS_REAIS[0].metadata.create_all(
            bind=engine,
            tables=list(TABELAS_DADOS_REAIS),
            checkfirst=True,
        )

        inspetor = inspect(engine)
        tabelas_depois = set(inspetor.get_table_names())
        ausentes = set(NOMES_TABELAS) - tabelas_depois
        if ausentes:
            raise RuntimeError(
                "As seguintes tabelas não foram criadas: "
                + ", ".join(sorted(ausentes))
            )

        estrutura = {
            nome: _descrever_tabela(inspetor, nome) for nome in NOMES_TABELAS
        }
        _validar_constraints(estrutura)
        return {
            "banco": banco,
            "criadas": sorted(set(NOMES_TABELAS) - tabelas_antes),
            "ja_existiam": sorted(set(NOMES_TABELAS) & tabelas_antes),
            "estrutura": estrutura,
        }
    finally:
        engine.dispose()


def _formatar_coluna(coluna):
    aceita_nulo = "SIM" if coluna["nullable"] else "NÃO"
    return f"{coluna['name']} | {coluna['type']} | aceita NULL: {aceita_nulo}"


def main():
    try:
        resultado = criar_e_verificar_estrutura()
    except (RuntimeError, SQLAlchemyError) as erro:
        print("CRIAÇÃO/VERIFICAÇÃO INTERROMPIDA")
        print(erro)
        raise SystemExit(1) from None

    print(f"BANCO CONFIRMADO: {resultado['banco']}")
    print("TABELAS CRIADAS:", resultado["criadas"] or "nenhuma")
    print("TABELAS QUE JÁ EXISTIAM:", resultado["ja_existiam"] or "nenhuma")

    for nome, detalhes in resultado["estrutura"].items():
        print(f"\nTABELA: {nome}")
        print("COLUNAS")
        for coluna in detalhes["colunas"]:
            print(" -", _formatar_coluna(coluna))
        print("PK:", detalhes["pk"])
        print("UNIQUE:", sorted(detalhes["unique"]))
        print("FKs:", sorted(detalhes["fks"]))

    print("\nEstrutura criada e validada. Nenhum dado real foi importado.")


if __name__ == "__main__":
    main()
