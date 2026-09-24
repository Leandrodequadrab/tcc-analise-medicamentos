"""Leitura e validação do relatório hierárquico Rel_0024."""

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import time
import warnings

from openpyxl import load_workbook


PADRAO_TOTAL_PRODUTO = re.compile(
    r"^\s*Total\s+Produto:\s*(\d+)\s+(.+?)\s*$", re.IGNORECASE
)

# Posições confirmadas no Rel_0024. M, N e O contêm vendedor,
# cliente e telefone; não são acessadas.
COL_VENDA = 0       # A
COL_FILIAL = 1      # B
COL_TIPO = 5        # F
COL_DATA = 6        # G
COL_HORA_BRUTA = 10  # K, valor exportado sem formato temporal confiável
COL_TOTAL_PRODUTO_QUANTIDADE = 15  # P
COL_QUANTIDADE = 16  # Q
COL_VALOR_UNITARIO = 18  # S
COL_PERCENTUAL_DESCONTO = 19  # T
COL_VALOR_DESCONTO = 20  # U
COL_VALOR_ACRESCIMO = 21  # V
COL_TOTAL_ITEM = 24  # Y
MAX_COLUNAS = 25


class ErroRel0024(RuntimeError):
    """Indica estrutura incompatível ou registro grave inválido."""


def _valor(linha, indice):
    if indice >= len(linha):
        return None
    celula = linha[indice]
    return celula.value if hasattr(celula, "value") else celula


def _inteiro(valor):
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def _decimal(valor):
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _texto_bruto(valor):
    if valor is None or valor == "":
        return None
    return str(valor)


def _validar_cabecalho(linha):
    esperados = {
        COL_VENDA: "venda",
        COL_FILIAL: "loja",
        COL_DATA: "emiss",
        COL_QUANTIDADE: "qtd",
        COL_VALOR_UNITARIO: "vlr.unit",
        COL_TOTAL_ITEM: "vlr.total",
    }
    encontrados = {
        indice: str(_valor(linha, indice) or "").lower()
        for indice in esperados
    }
    ausentes = [
        termo
        for indice, termo in esperados.items()
        if termo not in encontrados[indice]
    ]
    if ausentes:
        raise ErroRel0024(
            "Cabeçalho Rel_0024 incompatível; termos ausentes: "
            + ", ".join(ausentes)
        )


def ler_rel0024(caminho):
    """Reconstrói itens associando vendas ao Total Produto seguinte."""

    inicio = time.perf_counter()
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroRel0024(f"Arquivo não encontrado: {caminho}.")
    extensao = caminho.suffix.lower()
    if extensao not in (".xls", ".xlsx"):
        raise ErroRel0024(
            "O relatório deve possuir extensão .xls ou .xlsx."
        )

    if extensao == ".xlsx":
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Workbook contains no default style.*"
            )
            workbook = load_workbook(caminho, read_only=True, data_only=True)
        abas = workbook.sheetnames
        if not abas:
            workbook.close()
            raise ErroRel0024("O arquivo não possui planilhas.")
        planilha = workbook[abas[0]]
        dimensao_declarada = planilha.calculate_dimension()
        planilha.reset_dimensions()
        iterador_linhas = planilha.iter_rows(max_col=MAX_COLUNAS)
        fechar_workbook = workbook.close
        aba_utilizada = planilha.title
    else:
        try:
            import xlrd
        except ImportError as erro:
            raise ErroRel0024(
                "Instale as dependências de requirements.txt para ler XLS."
            ) from erro
        workbook = xlrd.open_workbook(caminho, on_demand=True)
        abas = workbook.sheet_names()
        if not abas:
            workbook.release_resources()
            raise ErroRel0024("O arquivo não possui planilhas.")
        planilha = workbook.sheet_by_index(0)
        dimensao_declarada = f"{planilha.nrows} linhas x {planilha.ncols} colunas"

        def _linhas_xls():
            for indice_linha in range(planilha.nrows):
                linha = []
                for indice_coluna in range(min(planilha.ncols, MAX_COLUNAS)):
                    celula = planilha.cell(indice_linha, indice_coluna)
                    valor = celula.value
                    if celula.ctype == xlrd.XL_CELL_DATE:
                        valor = xlrd.xldate_as_datetime(
                            valor, workbook.datemode
                        )
                    linha.append(valor)
                yield linha

        iterador_linhas = _linhas_xls()
        fechar_workbook = workbook.release_resources
        aba_utilizada = planilha.name

    try:
        pendentes = []
        itens = []
        produtos = {}
        totais_produto = []
        linhas_ignoradas = 0
        linhas_invalidas = []
        total_produtos = 0
        total_geral_quantidade = None
        total_geral_valor = None
        linhas_estrutura = 0
        ocorrencias_por_chave = Counter()

        for numero_linha, linha in enumerate(iterador_linhas, start=1):
            linhas_estrutura = numero_linha
            valor_a = _valor(linha, COL_VENDA)

            if numero_linha == 1:
                _validar_cabecalho(linha)
                linhas_ignoradas += 1
                continue

            texto_a = str(valor_a or "")
            produto_encontrado = PADRAO_TOTAL_PRODUTO.match(texto_a)
            if produto_encontrado:
                codigo_produto = int(produto_encontrado.group(1))
                descricao = produto_encontrado.group(2)
                total_produtos += 1
                if not pendentes:
                    linhas_invalidas.append(
                        (numero_linha, "Total Produto sem linhas de venda")
                    )
                descricoes = produtos.setdefault(codigo_produto, set())
                descricoes.add(descricao)

                for venda in pendentes:
                    chave_ocorrencia = (
                        venda["filial"],
                        venda["numero_venda"],
                        codigo_produto,
                    )
                    ocorrencias_por_chave[chave_ocorrencia] += 1
                    itens.append(
                        {
                            **venda,
                            "codigo_produto": codigo_produto,
                            "descricao_produto": descricao,
                            "ocorrencia_produto": ocorrencias_por_chave[
                                chave_ocorrencia
                            ],
                            "linha_total_produto": numero_linha,
                        }
                    )

                total_qtd_informado = _decimal(
                    _valor(linha, COL_TOTAL_PRODUTO_QUANTIDADE)
                )
                total_valor_informado = _decimal(
                    _valor(linha, COL_TOTAL_ITEM)
                )
                totais_produto.append(
                    {
                        "codigo_produto": codigo_produto,
                        "quantidade_informada": total_qtd_informado,
                        "quantidade_calculada": sum(
                            (
                                venda["quantidade"] or Decimal("0")
                                for venda in pendentes
                            ),
                            Decimal("0"),
                        ),
                        "valor_informado": total_valor_informado,
                        "valor_calculado": sum(
                            (
                                venda["valor_total"] or Decimal("0")
                                for venda in pendentes
                            ),
                            Decimal("0"),
                        ),
                    }
                )
                pendentes = []
                continue

            if texto_a.strip().lower().startswith("total geral:"):
                total_geral_quantidade = _decimal(
                    _valor(linha, COL_TOTAL_PRODUTO_QUANTIDADE)
                )
                total_geral_valor = _decimal(_valor(linha, COL_TOTAL_ITEM))
                linhas_ignoradas += 1
                continue

            numero_venda = _inteiro(valor_a)
            filial = _inteiro(_valor(linha, COL_FILIAL))
            data_emissao = _data(_valor(linha, COL_DATA))
            quantidade = _decimal(_valor(linha, COL_QUANTIDADE))
            tipo = str(_valor(linha, COL_TIPO) or "").strip()
            if numero_venda is not None:
                if filial is None or data_emissao is None or quantidade is None:
                    linhas_invalidas.append(
                        (numero_linha, "Linha de venda com campo obrigatório inválido")
                    )
                    continue
                pendentes.append(
                    {
                        "numero_venda": numero_venda,
                        "filial": filial,
                        "data": data_emissao,
                        "hora_bruta": _texto_bruto(
                            _valor(linha, COL_HORA_BRUTA)
                        ),
                        "tipo": tipo,
                        "quantidade": quantidade,
                        "valor_unitario": _decimal(
                            _valor(linha, COL_VALOR_UNITARIO)
                        ),
                        "percentual_desconto": _decimal(
                            _valor(linha, COL_PERCENTUAL_DESCONTO)
                        ),
                        "valor_desconto": _decimal(
                            _valor(linha, COL_VALOR_DESCONTO)
                        ),
                        "valor_acrescimo": _decimal(
                            _valor(linha, COL_VALOR_ACRESCIMO)
                        ),
                        "valor_total": _decimal(
                            _valor(linha, COL_TOTAL_ITEM)
                        ),
                        "linha_relatorio": numero_linha,
                    }
                )
                continue

            if any(
                _valor(linha, indice) not in (None, "")
                for indice in (
                    COL_VENDA,
                    COL_FILIAL,
                    COL_TIPO,
                    COL_DATA,
                    COL_QUANTIDADE,
                    COL_TOTAL_ITEM,
                )
            ):
                linhas_invalidas.append((numero_linha, "Linha não reconhecida"))
            else:
                linhas_ignoradas += 1

        if pendentes:
            raise ErroRel0024(
                "O arquivo terminou com vendas sem uma linha Total Produto."
            )

        return {
            "arquivo": caminho,
            "hash_sha256": _calcular_hash(caminho),
            "abas": abas,
            "aba_utilizada": aba_utilizada,
            "dimensao_declarada": dimensao_declarada,
            "linhas_estrutura": linhas_estrutura,
            "total_linhas_produto": total_produtos,
            "linhas_ignoradas": linhas_ignoradas,
            "linhas_invalidas": linhas_invalidas,
            "produtos_descricoes": produtos,
            "totais_produto": totais_produto,
            "total_geral_quantidade": total_geral_quantidade,
            "total_geral_valor": total_geral_valor,
            "itens": itens,
            "tempo_leitura_segundos": time.perf_counter() - inicio,
        }
    finally:
        fechar_workbook()


def _calcular_hash(caminho):
    import hashlib

    resumo = hashlib.sha256()
    with Path(caminho).open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def validar_rel0024(resultado):
    """Calcula métricas e inconsistências sem modificar os itens."""

    itens = resultado["itens"]
    vendas = {
        (item["filial"], item["numero_venda"]) for item in itens
    }
    produtos = {item["codigo_produto"] for item in itens}
    filiais = sorted({item["filial"] for item in itens})
    datas = [item["data"] for item in itens]
    chaves = [
        (item["filial"], item["numero_venda"], item["codigo_produto"])
        for item in itens
    ]
    contagem_chaves = Counter(chaves)
    chaves_repetidas = {
        chave: quantidade
        for chave, quantidade in contagem_chaves.items()
        if quantidade > 1
    }

    dados_venda = defaultdict(set)
    for item in itens:
        dados_venda[(item["filial"], item["numero_venda"])].add(
            (item["data"], item["hora_bruta"], item["tipo"])
        )
    vendas_conflitantes = {
        chave: valores
        for chave, valores in dados_venda.items()
        if len(valores) > 1
    }
    produtos_conflitantes = {
        codigo: descricoes
        for codigo, descricoes in resultado["produtos_descricoes"].items()
        if len(descricoes) > 1
    }

    divergencias_qtd = [
        total
        for total in resultado["totais_produto"]
        if total["quantidade_informada"] != total["quantidade_calculada"]
    ]
    divergencias_valor = [
        total
        for total in resultado["totais_produto"]
        if total["valor_informado"] is not None
        and abs(total["valor_informado"] - total["valor_calculado"])
        > Decimal("0.02")
    ]

    soma_quantidade = sum(
        (item["quantidade"] for item in itens), Decimal("0")
    )
    soma_valores = sum(
        (item["valor_total"] or Decimal("0") for item in itens), Decimal("0")
    )
    return {
        "filiais": filiais,
        "primeira_data": min(datas) if datas else None,
        "ultima_data": max(datas) if datas else None,
        "vendas_distintas": len(vendas),
        "itens": len(itens),
        "produtos_distintos": len(produtos),
        "soma_quantidades": soma_quantidade,
        "soma_valores": soma_valores,
        "linhas_invalidas": len(resultado["linhas_invalidas"]),
        "chaves_repetidas": chaves_repetidas,
        "vendas_conflitantes": vendas_conflitantes,
        "produtos_conflitantes": produtos_conflitantes,
        "divergencias_totais_quantidade": divergencias_qtd,
        "divergencias_totais_valor": divergencias_valor,
        "total_geral_quantidade_confere": (
            resultado["total_geral_quantidade"] == soma_quantidade
        ),
        "total_geral_valor_confere": (
            resultado["total_geral_valor"] == soma_valores
        ),
    }
