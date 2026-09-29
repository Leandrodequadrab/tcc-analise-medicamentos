"""Modelos isolados para importações oficiais do relatório Rel_0024."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import db


class ImportacaoRel0024(db.Model):
    __tablename__ = "importacoes_rel0024"

    id: Mapped[int] = mapped_column(primary_key=True)
    hash_sha256: Mapped[str] = mapped_column(db.String(64), unique=True, nullable=False)
    nome_arquivo: Mapped[str] = mapped_column(db.String(255), nullable=False)
    importado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    periodo_inicial: Mapped[date] = mapped_column(nullable=False)
    periodo_final: Mapped[date] = mapped_column(nullable=False)
    filial: Mapped[int] = mapped_column(nullable=False)
    quantidade_vendas: Mapped[int] = mapped_column(nullable=False)
    quantidade_itens_arquivo: Mapped[int] = mapped_column(nullable=False)
    quantidade_itens_inseridos: Mapped[int] = mapped_column(nullable=False)
    quantidade_produtos: Mapped[int] = mapped_column(nullable=False)
    soma_quantidades: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)
    soma_valores: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    registros_invalidos: Mapped[int] = mapped_column(nullable=False)
    quantidade_linhas_lidas: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    quantidade_linhas_nao_venda: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    quantidade_linhas_venda: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    quantidade_linhas_devolucao: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    soma_quantidades_devolucao: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    soma_valores_devolucao: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    soma_quantidades_liquida: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    soma_valores_liquido: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    produtos_inseridos: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    produtos_existentes: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    vendas_inseridas: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    vendas_existentes: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    itens_existentes: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    soma_quantidades_inseridas: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    quantidade_conflitos: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    quantidade_divergencias_descricao: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    tempo_processamento_segundos: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, server_default="0"
    )

    itens: Mapped[list["ItemRel0024"]] = relationship(back_populates="importacao")
    divergencias_descricao: Mapped[list["DivergenciaDescricaoRel0024"]] = (
        relationship(back_populates="importacao")
    )


class DivergenciaDescricaoRel0024(db.Model):
    __tablename__ = "divergencias_descricao_rel0024"
    __table_args__ = (
        UniqueConstraint(
            "importacao_id",
            "filial",
            "codigo_produto",
            name="uq_divergencias_rel0024_importacao_produto",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    importacao_id: Mapped[int] = mapped_column(
        ForeignKey("importacoes_rel0024.id"), nullable=False, index=True
    )
    filial: Mapped[int] = mapped_column(nullable=False)
    codigo_produto: Mapped[int] = mapped_column(nullable=False)
    descricao_banco: Mapped[str] = mapped_column(db.String(255), nullable=False)
    descricao_arquivo: Mapped[str] = mapped_column(db.String(255), nullable=False)
    registrada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    importacao: Mapped[ImportacaoRel0024] = relationship(
        back_populates="divergencias_descricao"
    )


class ProdutoRel0024(db.Model):
    __tablename__ = "produtos_rel0024"
    __table_args__ = (
        UniqueConstraint(
            "filial", "codigo_produto", name="uq_produtos_rel0024_filial_codigo"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filial: Mapped[int] = mapped_column(nullable=False)
    codigo_produto: Mapped[int] = mapped_column(nullable=False)
    descricao_original: Mapped[str] = mapped_column(db.String(255), nullable=False)
    cod_tipo: Mapped[int | None] = mapped_column(nullable=True)
    nom_tipo: Mapped[str | None] = mapped_column(db.String(100), nullable=True)
    origem_classificacao: Mapped[str | None] = mapped_column(
        db.String(100), nullable=True
    )
    classificacao_operacional: Mapped[str | None] = mapped_column(
        db.String(40), nullable=True
    )
    elegivel_analise_automedicacao: Mapped[bool | None] = mapped_column(
        db.Boolean, nullable=True
    )

    itens: Mapped[list["ItemRel0024"]] = relationship(back_populates="produto")


class VendaRel0024(db.Model):
    __tablename__ = "vendas_rel0024"
    __table_args__ = (
        UniqueConstraint(
            "filial", "numero_venda", name="uq_vendas_rel0024_filial_numero"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filial: Mapped[int] = mapped_column(nullable=False)
    numero_venda: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[date] = mapped_column(nullable=False)
    hora_original: Mapped[str | None] = mapped_column(db.String(30), nullable=True)
    tipo_original: Mapped[str | None] = mapped_column(db.String(30), nullable=True)

    itens: Mapped[list["ItemRel0024"]] = relationship(back_populates="venda")


class ItemRel0024(db.Model):
    __tablename__ = "itens_rel0024"
    __table_args__ = (
        UniqueConstraint(
            "venda_id",
            "produto_id",
            "ocorrencia_produto",
            name="uq_itens_rel0024_venda_produto_ocorrencia",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    venda_id: Mapped[int] = mapped_column(
        ForeignKey("vendas_rel0024.id"), nullable=False, index=True
    )
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produtos_rel0024.id"), nullable=False, index=True
    )
    importacao_id: Mapped[int] = mapped_column(
        ForeignKey("importacoes_rel0024.id"), nullable=False, index=True
    )
    ocorrencia_produto: Mapped[int] = mapped_column(nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    valor_unitario: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 4), nullable=True
    )
    percentual_desconto: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), nullable=True
    )
    valor_desconto: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 4), nullable=True
    )
    valor_acrescimo: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 4), nullable=True
    )
    valor_total: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 4), nullable=True
    )
    linha_relatorio: Mapped[int] = mapped_column(nullable=False)

    venda: Mapped[VendaRel0024] = relationship(back_populates="itens")
    produto: Mapped[ProdutoRel0024] = relationship(back_populates="itens")
    importacao: Mapped[ImportacaoRel0024] = relationship(back_populates="itens")


class CadastroProdutoFarmacia(db.Model):
    """Espelho local auditável do cadastro mestre da rede farmacêutica."""

    __tablename__ = "cadastro_produtos_farmacia"
    __table_args__ = (
        PrimaryKeyConstraint(
            "cod_rede", "cod_reduzido", name="pk_cadastro_produtos_farmacia"
        ),
    )

    cod_rede: Mapped[int] = mapped_column(nullable=False)
    cod_reduzido: Mapped[int] = mapped_column(nullable=False)
    cod_tipo: Mapped[int | None] = mapped_column(nullable=True)
    nom_tipo: Mapped[str | None] = mapped_column(db.String(100), nullable=True)
    nom_produto: Mapped[str | None] = mapped_column(db.String(255), nullable=True)
    flg_ativo: Mapped[str | None] = mapped_column(db.String(1), nullable=True)
    flg_excluir: Mapped[str | None] = mapped_column(db.String(1), nullable=True)
    origem: Mapped[str] = mapped_column(db.String(100), nullable=False)
    sincronizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


TABELAS_REL0024 = (
    ImportacaoRel0024.__table__,
    DivergenciaDescricaoRel0024.__table__,
    ProdutoRel0024.__table__,
    VendaRel0024.__table__,
    ItemRel0024.__table__,
    CadastroProdutoFarmacia.__table__,
)
