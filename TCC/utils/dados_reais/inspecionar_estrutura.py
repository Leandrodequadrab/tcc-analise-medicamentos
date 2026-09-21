"""Inspeciona colunas e chaves declaradas sem consultar dados comerciais."""

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)


TABELAS_ALVO = ("cadcvend", "cadivend", "cadprodu")

SQL_IDENTIFICACAO = text(
    """
    SELECT
        current_database() AS banco,
        current_user AS usuario,
        current_setting('server_version') AS versao,
        current_setting('transaction_read_only') AS somente_leitura
    """
)

SQL_COLUNAS = text(
    """
    SELECT
        table_schema,
        table_name,
        ordinal_position,
        column_name,
        data_type,
        is_nullable
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND lower(table_name) IN :tabelas
    ORDER BY table_schema, table_name, ordinal_position
    """
).bindparams(bindparam("tabelas", expanding=True))

SQL_CHAVES = text(
    """
    SELECT
        namespace_origem.nspname AS table_schema,
        tabela_origem.relname AS table_name,
        restricao.conname AS constraint_name,
        CASE restricao.contype
            WHEN 'p' THEN 'PRIMARY KEY'
            WHEN 'f' THEN 'FOREIGN KEY'
            WHEN 'u' THEN 'UNIQUE'
        END AS constraint_type,
        array_to_string(
            ARRAY(
                SELECT atributo.attname
                FROM unnest(restricao.conkey) WITH ORDINALITY AS coluna(numero, ordem)
                JOIN pg_catalog.pg_attribute AS atributo
                  ON atributo.attrelid = restricao.conrelid
                 AND atributo.attnum = coluna.numero
                ORDER BY coluna.ordem
            ),
            ', '
        ) AS columns,
        namespace_destino.nspname AS foreign_table_schema,
        tabela_destino.relname AS foreign_table_name,
        array_to_string(
            ARRAY(
                SELECT atributo.attname
                FROM unnest(restricao.confkey) WITH ORDINALITY AS coluna(numero, ordem)
                JOIN pg_catalog.pg_attribute AS atributo
                  ON atributo.attrelid = restricao.confrelid
                 AND atributo.attnum = coluna.numero
                ORDER BY coluna.ordem
            ),
            ', '
        ) AS foreign_columns
    FROM pg_catalog.pg_constraint AS restricao
    JOIN pg_catalog.pg_class AS tabela_origem
      ON tabela_origem.oid = restricao.conrelid
    JOIN pg_catalog.pg_namespace AS namespace_origem
      ON namespace_origem.oid = tabela_origem.relnamespace
    LEFT JOIN pg_catalog.pg_class AS tabela_destino
      ON tabela_destino.oid = restricao.confrelid
    LEFT JOIN pg_catalog.pg_namespace AS namespace_destino
      ON namespace_destino.oid = tabela_destino.relnamespace
    WHERE namespace_origem.nspname NOT IN ('pg_catalog', 'information_schema')
      AND lower(tabela_origem.relname) IN :tabelas
      AND restricao.contype IN ('p', 'f', 'u')
    ORDER BY namespace_origem.nspname, tabela_origem.relname, restricao.conname
    """
).bindparams(bindparam("tabelas", expanding=True))


def _consultar_dataframe(conexao, consulta):
    registros = conexao.execute(
        consulta, {"tabelas": list(TABELAS_ALVO)}
    ).mappings().all()
    return pd.DataFrame([dict(registro) for registro in registros])


def inspecionar_estrutura():
    """Executa somente SELECTs de metadados e devolve três DataFrames."""

    with conectar_farmacia_somente_leitura() as conexao:
        identificacao = pd.DataFrame(
            [dict(conexao.execute(SQL_IDENTIFICACAO).mappings().one())]
        )
        colunas = _consultar_dataframe(conexao, SQL_COLUNAS)
        chaves = _consultar_dataframe(conexao, SQL_CHAVES)

    return {
        "identificacao": identificacao,
        "colunas": colunas,
        "chaves": chaves,
    }


def main():
    try:
        resultado = inspecionar_estrutura()
    except ErroConfiguracaoFarmacia as erro:
        print("CONFIGURAÇÃO PENDENTE")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONECTAR AO sgfpod1")
        print("Confirme serviço, host, porta, usuário e senha definidos no .env.")
        print(f"Tipo do erro: {type(erro).__name__}")
        raise SystemExit(1) from None

    print("IDENTIFICAÇÃO DA CONEXÃO")
    print(resultado["identificacao"].to_string(index=False))

    print("\nCOLUNAS CONFIRMADAS")
    if resultado["colunas"].empty:
        print("Nenhuma das três tabelas foi localizada nos schemas visíveis.")
    else:
        print(resultado["colunas"].to_string(index=False))

    print("\nCHAVES E RELACIONAMENTOS DECLARADOS")
    if resultado["chaves"].empty:
        print("Nenhuma chave primária, estrangeira ou única foi declarada.")
    else:
        print(resultado["chaves"].to_string(index=False))

    print(
        "\nA extração foi interrompida após a inspeção. "
        "Nenhum JOIN ou acesso aos registros comerciais foi executado."
    )


if __name__ == "__main__":
    main()
