"""Conexão protegida de leitura com o PostgreSQL da farmácia."""

import os
import re
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, event, text


PASTA_PROJETO = Path(__file__).resolve().parents[2]
ARQUIVO_ENV = PASTA_PROJETO / ".env"
VARIAVEIS_OBRIGATORIAS = (
    "FARM_DB_USER",
    "FARM_DB_HOST",
    "FARM_DB_PORT",
    "FARM_DB_NAME",
)


class ErroConfiguracaoFarmacia(RuntimeError):
    """Indica configuração ausente ou inválida para o banco da farmácia."""


def carregar_configuracao_farmacia():
    """Lê apenas as variáveis destinadas à conexão com o banco real."""

    load_dotenv(ARQUIVO_ENV)
    ausentes = [nome for nome in VARIAVEIS_OBRIGATORIAS if not os.getenv(nome)]
    if ausentes:
        raise ErroConfiguracaoFarmacia(
            "Configure no .env as variáveis: " + ", ".join(ausentes)
        )

    try:
        porta = int(os.environ["FARM_DB_PORT"])
    except ValueError as erro:
        raise ErroConfiguracaoFarmacia(
            "FARM_DB_PORT deve conter somente o número da porta."
        ) from erro

    nome_banco = os.environ["FARM_DB_NAME"]
    if nome_banco != "sgfpod1":
        raise ErroConfiguracaoFarmacia(
            "FARM_DB_NAME deve ser sgfpod1 nesta etapa de inspeção."
        )

    return {
        "usuario": os.environ["FARM_DB_USER"],
        "senha": os.getenv("FARM_DB_PASSWORD") or None,
        "host": os.environ["FARM_DB_HOST"],
        "porta": porta,
        "banco": nome_banco,
    }


def _remover_comentarios_sql(comando):
    sem_blocos = re.sub(r"/\*.*?\*/", "", comando, flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", "", sem_blocos).strip()


def _comando_somente_leitura(comando):
    """Aceita exclusivamente comandos iniciados por SELECT ou SHOW."""

    comando_limpo = _remover_comentarios_sql(comando).upper()
    return bool(re.match(r"^(SELECT|SHOW)\b", comando_limpo))


def criar_engine_farmacia():
    """Cria uma engine separada da aplicação e protegida contra escrita."""

    config = carregar_configuracao_farmacia()
    argumentos_url = {
        "drivername": "postgresql+psycopg2",
        "username": config["usuario"],
        "host": config["host"],
        "port": config["porta"],
        "database": config["banco"],
    }
    if config["senha"] is not None:
        argumentos_url["password"] = config["senha"]

    url = URL.create(
        **argumentos_url
    )
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "application_name": "tcc_inspecao_somente_leitura",
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=30000"
            ),
        },
    )

    @event.listens_for(engine, "before_cursor_execute")
    def bloquear_escrita(
        _conexao, _cursor, comando, _parametros, _contexto, _multiplos
    ):
        if not _comando_somente_leitura(comando):
            raise PermissionError(
                "Comando bloqueado: a conexão com sgfpod1 aceita apenas SELECT e SHOW."
            )

    return engine


@contextmanager
def conectar_farmacia_somente_leitura():
    """Abre a conexão, confirma o modo de leitura e sempre a encerra."""

    engine = criar_engine_farmacia()
    try:
        with engine.connect() as conexao:
            banco_atual = conexao.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if banco_atual != "sgfpod1":
                raise RuntimeError(
                    "A conexão de origem não aponta para o banco sgfpod1."
                )

            somente_leitura = conexao.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
            if somente_leitura != "on":
                raise RuntimeError(
                    "A sessão do sgfpod1 não foi aberta em modo somente leitura."
                )
            yield conexao
    finally:
        engine.dispose()
