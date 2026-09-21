"""Configuração do SQLAlchemy e modelos do banco de dados."""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


PASTA_PROJETO = Path(__file__).resolve().parent
ARQUIVO_ENV = PASTA_PROJETO / ".env"


class Base(DeclarativeBase):
    """Classe base dos modelos da aplicação."""


db = SQLAlchemy(model_class=Base)


def obter_url_banco_aplicacao():
    """Monta a URL do banco do TCC usando somente variáveis de ambiente."""

    load_dotenv(ARQUIVO_ENV)
    nomes = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")
    ausentes = [nome for nome in nomes if not os.getenv(nome)]
    if ausentes:
        raise RuntimeError(
            "Configure as seguintes variáveis no arquivo .env: "
            + ", ".join(ausentes)
        )

    try:
        porta = int(os.environ["DB_PORT"])
    except ValueError as erro:
        raise RuntimeError("DB_PORT deve ser um número inteiro.") from erro

    return URL.create(
        drivername="postgresql+psycopg",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=porta,
        database=os.environ["DB_NAME"],
    )


class Venda(db.Model):
    """Representa um registro de venda de medicamento."""

    __tablename__ = "vendas"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[date] = mapped_column(nullable=False)
    medicamento: Mapped[str] = mapped_column(db.String(150), nullable=False)
    categoria: Mapped[str] = mapped_column(db.String(100), nullable=False)
    quantidade: Mapped[int] = mapped_column(nullable=False)
    cidade: Mapped[str] = mapped_column(db.String(100), nullable=False)
    prescricao: Mapped[bool] = mapped_column(nullable=False)


def inicializar_banco(app):
    """Conecta o SQLAlchemy ao Flask e cria as tabelas ausentes."""

    db.init_app(app)

    with app.app_context():
        db.create_all()
