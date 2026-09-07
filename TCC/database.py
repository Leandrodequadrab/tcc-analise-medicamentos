"""Configuração do SQLAlchemy e modelos do banco de dados."""

from datetime import date

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Classe base dos modelos da aplicação."""


db = SQLAlchemy(model_class=Base)


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
