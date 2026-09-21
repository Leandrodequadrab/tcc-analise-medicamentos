"""Modelos normalizados dos dados reais usados pelo TCC."""

from datetime import date, time
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import db


class Produto(db.Model):
    """Produto identificado pela chave formal do cadastro de origem."""

    __tablename__ = "produtos"
    __table_args__ = (
        UniqueConstraint(
            "cod_rede",
            "cod_reduzido",
            name="uq_produtos_cod_rede_cod_reduzido",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cod_rede: Mapped[int] = mapped_column(nullable=False)
    cod_reduzido: Mapped[int] = mapped_column(nullable=False)
    cod_produto: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    nom_produto: Mapped[str | None] = mapped_column(db.String(30), nullable=True)
    produto_ativo: Mapped[str | None] = mapped_column(db.String(1), nullable=True)
    produto_excluido: Mapped[str | None] = mapped_column(db.String(1), nullable=True)

    itens: Mapped[list["ItemVenda"]] = relationship(back_populates="produto")


class VendaReal(db.Model):
    """Cabeçalho de venda real sem dados pessoais."""

    __tablename__ = "vendas_reais"
    __table_args__ = (
        UniqueConstraint(
            "cod_rede",
            "cod_filial",
            "num_nota",
            name="uq_vendas_reais_chave_origem",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cod_rede: Mapped[int] = mapped_column(nullable=False)
    cod_filial: Mapped[int] = mapped_column(nullable=False)
    num_nota: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[date | None] = mapped_column(nullable=True)
    hora: Mapped[time | None] = mapped_column(nullable=True)
    venda_excluida: Mapped[str | None] = mapped_column(db.String(1), nullable=True)

    itens: Mapped[list["ItemVenda"]] = relationship(back_populates="venda")


class ItemVenda(db.Model):
    """Item ligado à venda e, quando localizado, ao produto cadastrado."""

    __tablename__ = "itens_venda"
    __table_args__ = (
        UniqueConstraint(
            "venda_id",
            "num_sequencial",
            name="uq_itens_venda_venda_sequencial",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    venda_id: Mapped[int] = mapped_column(
        ForeignKey("vendas_reais.id"), nullable=False, index=True
    )
    produto_id: Mapped[int | None] = mapped_column(
        ForeignKey("produtos.id"), nullable=True, index=True
    )
    num_sequencial: Mapped[int] = mapped_column(nullable=False)
    quantidade: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    valor_unitario: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    valor_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    promocao: Mapped[str | None] = mapped_column(db.String(1), nullable=True)
    prescrito: Mapped[str | None] = mapped_column(db.String(1), nullable=True)
    item_excluido: Mapped[str | None] = mapped_column(db.String(1), nullable=True)

    venda: Mapped[VendaReal] = relationship(back_populates="itens")
    produto: Mapped[Produto | None] = relationship(back_populates="itens")


TABELAS_DADOS_REAIS = (
    Produto.__table__,
    VendaReal.__table__,
    ItemVenda.__table__,
)
