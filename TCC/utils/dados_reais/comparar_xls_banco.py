"""Compara um relatório XLSX hierárquico com o sgfpod1, sem escrever dados."""

from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
import re
import unicodedata
import warnings

from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    PASTA_PROJETO,
    ErroConfiguracaoFarmacia,
    carregar_configuracao_farmacia,
    conectar_farmacia_somente_leitura,
)
from .extrair_vendas import ErroAmostra, confirmar_constraints


ARQUIVO_XLSX = PASTA_PROJETO / "dados" / "teste_F06_abril.xlsx"
COD_REDE = 1
COD_FILIAL = 6
DATA_INICIAL = date(2026, 4, 1)
DATA_FINAL = date(2026, 4, 15)

# Posições confirmadas no relatório. As colunas pessoais AF, AM e AP,
# além do telefone iniciado em K, não são acessadas pelo parser.
COLUNA_VENDA = 0       # A
COLUNA_FILIAL = 3      # D
COLUNA_DATA = 16       # Q
COLUNA_HORA = 21       # V
COLUNA_TOTAL_VENDA = 57  # BF
COLUNA_CODIGO_ITEM = 1   # B
COLUNA_DESCRICAO_ITEM = 6  # G
COLUNA_QUANTIDADE = 18    # S
COLUNA_VALOR_UNITARIO = 22  # W
COLUNA_TOTAL_ITEM = 30       # AE
MAX_COLUNA_LIDA = 58         # BF
COLUNAS_TECNICAS = {
    COLUNA_VENDA,
    COLUNA_FILIAL,
    COLUNA_DATA,
    COLUNA_HORA,
    COLUNA_TOTAL_VENDA,
    COLUNA_CODIGO_ITEM,
    COLUNA_DESCRICAO_ITEM,
    COLUNA_QUANTIDADE,
    COLUNA_VALOR_UNITARIO,
    COLUNA_TOTAL_ITEM,
}


SQL_CONFIRMACAO = text(
    """
    SELECT
        current_database() AS banco,
        current_setting('transaction_read_only') AS somente_leitura
    """
)

SQL_VENDAS = text(
    """
    SELECT
        cod_rede,
        cod_filial,
        num_nota,
        dat_emissao AS data,
        hor_emissao AS hora,
        flg_excluido AS venda_excluida,
        tot_nota AS total_venda
    FROM public.cadcvend
    WHERE cod_rede = :rede
      AND cod_filial = :filial
      AND dat_emissao BETWEEN :data_inicial AND :data_final
    ORDER BY dat_emissao, hor_emissao, num_nota
    """
)

SQL_ITENS = text(
    """
    SELECT
        item.cod_rede,
        item.cod_filial,
        item.num_nota,
        item.num_sequencial,
        venda.dat_emissao AS data,
        item.cod_reduzido,
        produto.cod_produto,
        item.qtd_produto AS quantidade,
        item.vlr_unitario AS valor_unitario,
        item.vlr_total AS total_item,
        venda.flg_excluido AS venda_excluida,
        item.flg_excluido AS item_excluido
    FROM public.cadivend AS item
    INNER JOIN public.cadcvend AS venda
      ON venda.cod_rede = item.cod_rede
     AND venda.cod_filial = item.cod_filial
     AND venda.num_nota = item.num_nota
    LEFT JOIN public.cadprodu AS produto
      ON produto.cod_rede = item.cod_rede
     AND produto.cod_reduzido = item.cod_reduzido
    WHERE venda.cod_rede = :rede
      AND venda.cod_filial = :filial
      AND venda.dat_emissao BETWEEN :data_inicial AND :data_final
    ORDER BY item.cod_rede, item.cod_filial, item.num_nota,
             item.num_sequencial
    """
)

SQL_CRUZAMENTO_EXCLUIDOS = text(
    """
    SELECT
        venda.flg_excluido AS venda_excluida,
        item.flg_excluido AS item_excluido,
        COUNT(*) AS quantidade_itens,
        COUNT(DISTINCT (venda.cod_rede, venda.cod_filial, venda.num_nota))
            AS quantidade_vendas,
        SUM(item.qtd_produto) AS soma_quantidade
    FROM public.cadivend AS item
    INNER JOIN public.cadcvend AS venda
      ON venda.cod_rede = item.cod_rede
     AND venda.cod_filial = item.cod_filial
     AND venda.num_nota = item.num_nota
    WHERE venda.cod_rede = :rede
      AND venda.cod_filial = :filial
      AND venda.dat_emissao BETWEEN :data_inicial AND :data_final
    GROUP BY venda.flg_excluido, item.flg_excluido
    ORDER BY venda.flg_excluido NULLS FIRST,
             item.flg_excluido NULLS FIRST
    """
)

SQL_CAMPOS_CANDIDATOS = text(
    """
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('cadcvend', 'cadivend', 'cadprodu')
      AND (
          LOWER(column_name) LIKE '%devol%'
          OR LOWER(column_name) LIKE '%cancel%'
          OR LOWER(column_name) LIKE '%exclu%'
          OR LOWER(column_name) LIKE '%estorn%'
      )
    ORDER BY table_name, ordinal_position
    """
)


class ErroComparacao(RuntimeError):
    """Indica estrutura inesperada ou comparação insegura."""


def _normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    return texto.encode("ascii", "ignore").decode().lower().strip()


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
    except Exception as erro:
        raise ErroComparacao(f"Valor numérico inválido no XLSX: {valor!r}.") from erro


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _hora(valor):
    if isinstance(valor, datetime):
        return valor.time()
    if isinstance(valor, time):
        return valor
    return None


def _celula(linha, indice):
    return linha[indice].value if indice < len(linha) else None


def ler_relatorio_xlsx(caminho=ARQUIVO_XLSX):
    """Reconstrói vendas e itens usando apenas as colunas permitidas."""

    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroComparacao(f"Arquivo XLSX não encontrado: {caminho}.")

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Workbook contains no default style.*"
        )
        workbook = load_workbook(caminho, read_only=True, data_only=True)

    try:
        abas = workbook.sheetnames
        if not abas:
            raise ErroComparacao("O XLSX não possui abas.")
        planilha = workbook[abas[0]]
        dimensao_declarada = planilha.calculate_dimension()
        planilha.reset_dimensions()

        vendas = []
        itens = []
        venda_atual = None
        cabecalhos_itens = 0
        itens_sem_venda = 0
        ultima_linha_util = 0
        max_coluna_util = 0
        linhas_estrutura = 0
        colunas_estrutura = 0

        for linha in planilha.iter_rows(max_col=MAX_COLUNA_LIDA):
            numero_linha = linha[0].row
            linhas_estrutura = numero_linha
            colunas_estrutura = max(colunas_estrutura, len(linha))
            colunas_preenchidas = [
                indice + 1
                for indice in COLUNAS_TECNICAS
                if _celula(linha, indice) is not None
            ]
            if colunas_preenchidas:
                ultima_linha_util = numero_linha
                max_coluna_util = max(max_coluna_util, max(colunas_preenchidas))

            valor_a = _celula(linha, COLUNA_VENDA)
            filial = _inteiro(_celula(linha, COLUNA_FILIAL))
            data_emissao = _data(_celula(linha, COLUNA_DATA))
            numero_venda = _inteiro(valor_a)

            if numero_venda is not None and filial is not None and data_emissao:
                venda_atual = {
                    "numero_venda": numero_venda,
                    "filial": filial,
                    "data": data_emissao,
                    "hora": _hora(_celula(linha, COLUNA_HORA)),
                    "total_venda": _decimal(
                        _celula(linha, COLUNA_TOTAL_VENDA)
                    ),
                    "linha_xlsx": numero_linha,
                }
                vendas.append(venda_atual)
                continue

            codigo = _celula(linha, COLUNA_CODIGO_ITEM)
            if "codigo" in _normalizar_texto(codigo):
                cabecalhos_itens += 1
                continue

            codigo_item = _inteiro(codigo)
            quantidade = _decimal(_celula(linha, COLUNA_QUANTIDADE))
            if codigo_item is not None and quantidade is not None:
                if venda_atual is None:
                    itens_sem_venda += 1
                    continue
                itens.append(
                    {
                        "numero_venda": venda_atual["numero_venda"],
                        "filial": venda_atual["filial"],
                        "data": venda_atual["data"],
                        "hora": venda_atual["hora"],
                        "codigo_produto": codigo_item,
                        "descricao": _celula(linha, COLUNA_DESCRICAO_ITEM),
                        "quantidade": quantidade,
                        "valor_unitario": _decimal(
                            _celula(linha, COLUNA_VALOR_UNITARIO)
                        ),
                        "total_item": _decimal(
                            _celula(linha, COLUNA_TOTAL_ITEM)
                        ),
                        "linha_xlsx": numero_linha,
                    }
                )

        if ultima_linha_util < 13:
            raise ErroComparacao("A estrutura hierárquica esperada não foi encontrada.")

        vendas_periodo = [
            venda
            for venda in vendas
            if venda["filial"] == COD_FILIAL
            and DATA_INICIAL <= venda["data"] <= DATA_FINAL
        ]
        ids_periodo = {venda["numero_venda"] for venda in vendas_periodo}
        itens_periodo = [
            item
            for item in itens
            if item["numero_venda"] in ids_periodo
            and item["filial"] == COD_FILIAL
        ]

        return {
            "arquivo": caminho,
            "abas": abas,
            "aba_analisada": planilha.title,
            "dimensao_declarada": dimensao_declarada,
            "linhas_estrutura": linhas_estrutura,
            "colunas_estrutura": colunas_estrutura,
            "ultima_linha_util": ultima_linha_util,
            "max_coluna_util": max_coluna_util,
            "cabecalhos_itens": cabecalhos_itens,
            "itens_sem_venda": itens_sem_venda,
            "vendas": vendas_periodo,
            "itens": itens_periodo,
        }
    finally:
        workbook.close()


def _metricas_xlsx(resultado):
    vendas = resultado["vendas"]
    itens = resultado["itens"]
    datas = [venda["data"] for venda in vendas]
    return {
        "vendas": len({venda["numero_venda"] for venda in vendas}),
        "itens": len(itens),
        "produtos": len({item["codigo_produto"] for item in itens}),
        "soma_quantidade": sum(
            (item["quantidade"] for item in itens), Decimal("0")
        ),
        "primeira_data": min(datas) if datas else None,
        "ultima_data": max(datas) if datas else None,
        "total_itens_financeiro": sum(
            (item["total_item"] or Decimal("0") for item in itens),
            Decimal("0"),
        ),
        "total_vendas_financeiro": sum(
            (venda["total_venda"] or Decimal("0") for venda in vendas),
            Decimal("0"),
        ),
    }


def consultar_banco():
    """Obtém somente os campos técnicos necessários no mesmo recorte."""

    parametros = {
        "rede": COD_REDE,
        "filial": COD_FILIAL,
        "data_inicial": DATA_INICIAL,
        "data_final": DATA_FINAL,
    }
    with conectar_farmacia_somente_leitura() as conexao:
        confirmacao = dict(conexao.execute(SQL_CONFIRMACAO).mappings().one())
        confirmar_constraints(conexao)
        vendas = [
            dict(linha)
            for linha in conexao.execute(SQL_VENDAS, parametros).mappings()
        ]
        itens = [
            dict(linha)
            for linha in conexao.execute(SQL_ITENS, parametros).mappings()
        ]
        cruzamento = [
            dict(linha)
            for linha in conexao.execute(
                SQL_CRUZAMENTO_EXCLUIDOS, parametros
            ).mappings()
        ]
        campos_candidatos = [
            dict(linha)
            for linha in conexao.execute(SQL_CAMPOS_CANDIDATOS).mappings()
        ]
    return {
        "confirmacao": confirmacao,
        "vendas": vendas,
        "itens": itens,
        "cruzamento_excluidos": cruzamento,
        "campos_candidatos": campos_candidatos,
    }


def _metricas_banco(resultado):
    vendas = resultado["vendas"]
    itens = resultado["itens"]
    datas = [venda["data"] for venda in vendas if venda["data"] is not None]
    return {
        "vendas": len(
            {
                (v["cod_rede"], v["cod_filial"], v["num_nota"])
                for v in vendas
            }
        ),
        "itens": len(itens),
        "produtos": len(
            {
                (item["cod_rede"], item["cod_reduzido"])
                for item in itens
                if item["cod_reduzido"] is not None
            }
        ),
        "soma_quantidade": sum(
            (item["quantidade"] or Decimal("0") for item in itens),
            Decimal("0"),
        ),
        "primeira_data": min(datas) if datas else None,
        "ultima_data": max(datas) if datas else None,
        "total_itens_financeiro": sum(
            (item["total_item"] or Decimal("0") for item in itens),
            Decimal("0"),
        ),
        "total_vendas_financeiro": sum(
            (venda["total_venda"] or Decimal("0") for venda in vendas),
            Decimal("0"),
        ),
    }


def _comparar_identificadores(xlsx, banco):
    vendas_xlsx = {v["numero_venda"] for v in xlsx["vendas"]}
    vendas_banco = {v["num_nota"] for v in banco["vendas"]}
    produtos_xlsx = {item["codigo_produto"] for item in xlsx["itens"]}
    produtos_reduzidos = {
        item["cod_reduzido"]
        for item in banco["itens"]
        if item["cod_reduzido"] is not None
    }
    produtos_complementares = {
        str(item["cod_produto"]).strip()
        for item in banco["itens"]
        if item["cod_produto"] is not None
    }

    itens_xlsx = Counter(
        (
            item["numero_venda"],
            item["codigo_produto"],
            item["quantidade"],
        )
        for item in xlsx["itens"]
    )
    itens_banco_reduzido = Counter(
        (
            item["num_nota"],
            item["cod_reduzido"],
            item["quantidade"],
        )
        for item in banco["itens"]
    )
    correspondencias_itens = sum(
        (itens_xlsx & itens_banco_reduzido).values()
    )

    return {
        "vendas_ambos": vendas_xlsx & vendas_banco,
        "vendas_apenas_xlsx": vendas_xlsx - vendas_banco,
        "vendas_apenas_banco": vendas_banco - vendas_xlsx,
        "produtos_xlsx": produtos_xlsx,
        "produtos_reduzidos_banco": produtos_reduzidos,
        "produtos_cod_produto_banco": produtos_complementares,
        "produtos_em_comum_cod_reduzido": (
            produtos_xlsx & produtos_reduzidos
        ),
        "produtos_em_comum_cod_produto": {
            codigo
            for codigo in produtos_xlsx
            if str(codigo) in produtos_complementares
        },
        "itens_correspondentes_venda_codigo_quantidade": correspondencias_itens,
    }


def comparar():
    # Confirma primeiro o acesso protegido à fonte. Assim, uma falha de
    # conexão não obriga a reler e reconstruir todo o XLSX.
    banco = consultar_banco()
    xlsx = ler_relatorio_xlsx()
    metricas_xlsx = _metricas_xlsx(xlsx)
    metricas_banco = _metricas_banco(banco)
    identificadores = _comparar_identificadores(xlsx, banco)
    diferencas = {
        campo: abs(metricas_xlsx[campo] - metricas_banco[campo])
        for campo in ("vendas", "itens", "produtos", "soma_quantidade")
    }
    return {
        "xlsx": xlsx,
        "banco": banco,
        "metricas_xlsx": metricas_xlsx,
        "metricas_banco": metricas_banco,
        "diferencas": diferencas,
        "identificadores": identificadores,
    }


def _exibir_valor_controle(valor):
    if valor is None:
        return "NULL"
    if isinstance(valor, str) and valor.strip() == "":
        return repr(valor)
    return str(valor)


def _formatar_lista(valores, limite=20):
    ordenados = sorted(valores)
    trecho = ", ".join(str(valor) for valor in ordenados[:limite])
    if len(ordenados) > limite:
        trecho += f" ... (+{len(ordenados) - limite})"
    return trecho or "nenhum"


def _mensagem_banco_segura(erro):
    """Remove dados de conexão da mensagem técnica do driver."""

    mensagem = str(getattr(erro, "orig", erro))
    try:
        config = carregar_configuracao_farmacia()
    except ErroConfiguracaoFarmacia:
        config = {}
    for campo in ("usuario", "host", "porta", "banco"):
        valor = config.get(campo)
        if valor is not None:
            mensagem = mensagem.replace(str(valor), f"<{campo}>")
    return re.sub(
        r"(?i)(password|senha)=[^\s]+", r"\1=<oculto>", mensagem
    ).strip()


def _imprimir_metricas(titulo, metricas):
    print(f"\n{titulo}")
    for campo in (
        "vendas",
        "itens",
        "produtos",
        "soma_quantidade",
        "primeira_data",
        "ultima_data",
        "total_itens_financeiro",
        "total_vendas_financeiro",
    ):
        print(f"- {campo}: {metricas[campo]}")


def imprimir_relatorio(resultado):
    xlsx = resultado["xlsx"]
    banco = resultado["banco"]
    ids = resultado["identificadores"]

    print("=== VALIDAÇÃO XLSX x SGFPOD1 ===")
    print(f"\nArquivo: {xlsx['arquivo']}")
    print(f"Abas: {xlsx['abas']}")
    print(f"Aba analisada: {xlsx['aba_analisada']}")
    print(f"Dimensão declarada no XLSX: {xlsx['dimensao_declarada']}")
    print(
        "Estrutura real: "
        f"{xlsx['linhas_estrutura']} linhas x "
        f"{xlsx['colunas_estrutura']} colunas; "
        f"última linha técnica útil: {xlsx['ultima_linha_util']}"
    )
    print(f"Filial: rede {COD_REDE}, filial {COD_FILIAL}")
    print(f"Período: {DATA_INICIAL} a {DATA_FINAL}")
    print(f"Banco confirmado: {banco['confirmacao']['banco']}")
    print(
        "transaction_read_only: "
        f"{banco['confirmacao']['somente_leitura']}"
    )

    print("\nESTRUTURA DOCUMENTADA")
    print("Venda: A=número, D=filial, Q=emissão, V=hora, BF=total líquido")
    print("Item: B=código, G=descrição, S=quantidade, W=valor unitário, AE=total")
    print("Colunas pessoais ignoradas: telefone, cliente e vendedores")
    print(f"Cabeçalhos hierárquicos de itens: {xlsx['cabecalhos_itens']}")
    print(f"Itens encontrados sem venda anterior: {xlsx['itens_sem_venda']}")

    print("\nPRIMEIRAS LINHAS ÚTEIS RECONSTRUÍDAS")
    for venda in xlsx["vendas"][:3]:
        print(
            f"Venda {venda['numero_venda']} | filial {venda['filial']} | "
            f"{venda['data']} {venda['hora']}"
        )
        itens_venda = [
            item
            for item in xlsx["itens"]
            if item["numero_venda"] == venda["numero_venda"]
        ]
        for item in itens_venda[:3]:
            print(
                f"  -> código {item['codigo_produto']} | "
                f"{item['descricao']} | qtd {item['quantidade']} | "
                f"unitário {item['valor_unitario']} | total {item['total_item']}"
            )

    _imprimir_metricas("XLSX", resultado["metricas_xlsx"])
    _imprimir_metricas("BANCO (dados brutos, sem filtros de controle)", resultado["metricas_banco"])

    print("\nDIFERENÇAS ABSOLUTAS")
    for campo, valor in resultado["diferencas"].items():
        print(f"- {campo}: {valor}")

    print("\nCORRESPONDÊNCIA DE IDENTIFICADORES")
    print(f"Vendas presentes nos dois: {len(ids['vendas_ambos'])}")
    print(f"Vendas apenas no XLSX: {len(ids['vendas_apenas_xlsx'])}")
    print("  números:", _formatar_lista(ids["vendas_apenas_xlsx"]))
    print(f"Vendas apenas no banco: {len(ids['vendas_apenas_banco'])}")
    print("  números:", _formatar_lista(ids["vendas_apenas_banco"]))
    print(
        "Produtos XLSX encontrados como cod_reduzido: "
        f"{len(ids['produtos_em_comum_cod_reduzido'])}/"
        f"{len(ids['produtos_xlsx'])}"
    )
    print(
        "Produtos XLSX encontrados como cod_produto: "
        f"{len(ids['produtos_em_comum_cod_produto'])}/"
        f"{len(ids['produtos_xlsx'])}"
    )
    print(
        "Itens coincidentes por venda+código+quantidade usando cod_reduzido: "
        f"{ids['itens_correspondentes_venda_codigo_quantidade']}"
    )

    print("\nEVIDÊNCIAS NOS CAMPOS DE EXCLUSÃO")
    print("venda_excluida | item_excluido | itens | vendas | soma_quantidade")
    for linha in banco["cruzamento_excluidos"]:
        print(
            f"{_exibir_valor_controle(linha['venda_excluida'])} | "
            f"{_exibir_valor_controle(linha['item_excluido'])} | "
            f"{linha['quantidade_itens']} | {linha['quantidade_vendas']} | "
            f"{linha['soma_quantidade']}"
        )

    print("\nCAMPOS DE SCHEMA CANDIDATOS A EXCLUSÃO/DEVOLUÇÃO/CANCELAMENTO")
    if banco["campos_candidatos"]:
        for campo in banco["campos_candidatos"]:
            print(
                f"- {campo['table_name']}.{campo['column_name']} "
                f"({campo['data_type']})"
            )
    else:
        print("- nenhum candidato encontrado pelos nomes das colunas")

    print("\nNenhum registro foi inserido, atualizado ou removido.")


def main():
    try:
        resultado = comparar()
    except (ErroComparacao, ErroConfiguracaoFarmacia, ErroAmostra) as erro:
        print("COMPARAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("NÃO FOI POSSÍVEL CONSULTAR O sgfpod1")
        print(f"Tipo do erro: {type(erro).__name__}")
        print(f"Mensagem técnica: {_mensagem_banco_segura(erro)}")
        raise SystemExit(1) from None

    imprimir_relatorio(resultado)


if __name__ == "__main__":
    main()
