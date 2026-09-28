"""Valida a taxonomia operacional MED nos produtos do Rel_0024.

Esta etapa não implementa filtro nem persiste classificação. Os dois bancos são
acessados somente pelas rotinas de leitura já protegidas das etapas 10D.1/10D.2.
A evidência ANVISA é apresentada separadamente e nunca remove um tipo MED.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
import sys

from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import ErroConfiguracaoFarmacia
from .investigar_classificacao_anvisa import (
    ErroFonteOficial,
    executar as executar_anvisa,
)
from .investigar_classificacao_medicamentos import (
    _normalizar,
    executar_investigacao,
)


TOTAL_ESPERADO = 4_666
QUALIFICADORES = ("OTC", "TARJA", "CONTROLADO", "CONTINUO")

# Somente auditoria por descrição. Estes padrões não incluem, excluem ou
# reclassificam nenhum produto.
PADROES_PRODUTOS_INCOMUNS = (
    ("lubrificante", r"\b(?:LUB|LUBRIFICANTE|KY)\b"),
    ("teste", r"\bTESTE\b"),
    ("fralda", r"\b(?:FRALDA|FRD)\b"),
    (
        "higiene/cosmético",
        r"\b(?:SABONETE|SHAMPOO|CONDICIONADOR|DESODORANTE|CREME DENTAL)\b",
    ),
    ("dispositivo", r"\b(?:SERINGA|AGULHA|TERMOMETRO)\b"),
)

# Categorias fora da regra MED que merecem revisão humana por serem próximas ou
# confundíveis no contexto de uma farmácia. Não são classificadas como MED.
PADROES_TIPOS_AMBIGUOS = (
    "ANTICONCEPCIONAL",
    "OFICINAIS",
    "PRODUTOS NATURAIS",
    "SUPLEMENTO",
    "VITAMIN",
    "PRIMEIROS SOCORROS",
    "DERMOCOSMETICOS",
    "APARELHOS / TESTES / BALANCAS",
    "NAO MEDICAMENTO",
)


def _preenchido(valor) -> bool:
    return valor is not None and str(valor).strip() != ""


def _percentual(quantidade: int, total: int) -> float:
    return 0.0 if total == 0 else quantidade * 100 / total


def _eh_tipo_med(descricao) -> bool:
    """Reconhece MED somente como primeiro token completo da descrição."""

    normalizada = _normalizar(descricao)
    tokens = normalizada.split()
    return bool(tokens) and tokens[0] == "MED"


def _chave_ordenacao_tipo(item: dict):
    codigo = item["cod_tipo"]
    try:
        return (0, int(codigo), item["nom_tipo"])
    except (TypeError, ValueError):
        return (1, str(codigo), item["nom_tipo"])


def _agrupar_tipos(produtos: list[dict]) -> list[dict]:
    grupos = defaultdict(list)
    for produto in produtos:
        chave = (produto.get("cod_tipo"), produto.get("nom_tipo"))
        grupos[chave].append(produto)

    tipos = []
    for (codigo, descricao), itens in grupos.items():
        tipos.append(
            {
                "cod_tipo": codigo,
                "nom_tipo": descricao,
                "quantidade": len(itens),
                "produtos": sorted(
                    itens, key=lambda produto: produto["cod_reduzido"]
                ),
            }
        )
    return sorted(tipos, key=_chave_ordenacao_tipo)


def _auditar_descricoes(tipos: list[dict]) -> dict:
    inesperadas = []
    ambiguas = []
    qualificadores = defaultdict(list)

    for tipo in tipos:
        descricao = _normalizar(tipo["nom_tipo"])
        if "MED" in descricao and not _eh_tipo_med(descricao):
            inesperadas.append(tipo)
        if not _eh_tipo_med(descricao) and any(
            termo in descricao for termo in PADROES_TIPOS_AMBIGUOS
        ):
            ambiguas.append(tipo)
        for qualificador in QUALIFICADORES:
            if re.search(rf"\b{qualificador}\b", descricao):
                qualificadores[qualificador].append(tipo)

    return {
        "med_posicao_inesperada": inesperadas,
        "ambiguas": ambiguas,
        "qualificadores": dict(qualificadores),
    }


def _produtos_incomuns(produtos_med: list[dict]) -> list[dict]:
    encontrados = []
    vistos = set()
    for rotulo, padrao in PADROES_PRODUTOS_INCOMUNS:
        for produto in produtos_med:
            codigo = produto["cod_reduzido"]
            if codigo in vistos:
                continue
            if re.search(padrao, _normalizar(produto.get("nom_produto"))):
                encontrados.append(
                    {
                        "motivo_auditoria": rotulo,
                        "cod_reduzido": codigo,
                        "nom_produto": produto.get("nom_produto"),
                        "cod_tipo": produto.get("cod_tipo"),
                        "nom_tipo": produto.get("nom_tipo"),
                    }
                )
                vistos.add(codigo)
    return encontrados


def executar() -> dict:
    """Monta o diagnóstico MED e o complemento regulatório, tudo em memória."""

    interno = executar_investigacao()
    produtos = interno["produtos"]
    if len(produtos) != TOTAL_ESPERADO:
        raise RuntimeError(
            f"Esperados {TOTAL_ESPERADO} produtos Rel_0024; encontrados {len(produtos)}."
        )

    tipos = _agrupar_tipos(produtos)
    tipos_med = [tipo for tipo in tipos if _eh_tipo_med(tipo["nom_tipo"])]
    codigos_tipos_med = {tipo["cod_tipo"] for tipo in tipos_med}
    produtos_med = [
        produto for produto in produtos
        if produto.get("cod_tipo") in codigos_tipos_med
    ]
    produtos_sem_tipo = [
        produto for produto in produtos
        if not _preenchido(produto.get("cod_tipo"))
    ]
    produtos_nao_med = [
        produto for produto in produtos
        if _preenchido(produto.get("cod_tipo"))
        and produto.get("cod_tipo") not in codigos_tipos_med
    ]

    # A etapa 10D.2 é reexecutada sem persistência para obter exatamente a mesma
    # evidência regulatória, preservada como complemento e não como filtro.
    anvisa = executar_anvisa()
    classificacao_por_codigo = {
        item["codigo_produto"]: item["classificacao_experimental"]
        for item in anvisa["resultados"]
    }
    codigos_internos = {produto["cod_reduzido"] for produto in produtos}
    if set(classificacao_por_codigo) != codigos_internos:
        raise RuntimeError(
            "Os conjuntos de produtos das etapas 10D.2 e 10D.3 não coincidem."
        )

    confirmados_anvisa = sum(
        classificacao_por_codigo[produto["cod_reduzido"]] == "MEDICAMENTO"
        for produto in produtos_med
    )

    return {
        "seguranca": {
            "interno": interno["seguranca"],
            "anvisa": anvisa["seguranca"],
        },
        "total": len(produtos),
        "total_tipos": len(tipos),
        "tipos_med": tipos_med,
        "quantidade_tipos_med": len(tipos_med),
        "produtos_med": produtos_med,
        "quantidade_produtos_med": len(produtos_med),
        "produtos_sem_tipo": produtos_sem_tipo,
        "produtos_nao_med": produtos_nao_med,
        "auditoria_descricoes": _auditar_descricoes(tipos),
        "produtos_incomuns": _produtos_incomuns(produtos_med),
        "confirmados_anvisa": confirmados_anvisa,
        "sem_confirmacao_anvisa": len(produtos_med) - confirmados_anvisa,
        "situacao_anvisa_med": Counter(
            classificacao_por_codigo[produto["cod_reduzido"]]
            for produto in produtos_med
        ),
    }


def _imprimir_tipos(tipos: list[dict], exemplos: bool = False) -> None:
    if not tipos:
        print("- nenhum")
        return
    for tipo in tipos:
        print(
            f"{tipo['cod_tipo']} | {tipo['nom_tipo']} | {tipo['quantidade']}"
        )
        if exemplos:
            for produto in tipo["produtos"][:3]:
                print(
                    f"  exemplo: {produto['cod_reduzido']} | "
                    f"{produto['nom_produto']}"
                )


def imprimir_relatorio(relatorio: dict) -> None:
    total = relatorio["total"]

    print("1. SEGURANÇA E ESCOPO")
    print(
        "- sgfpod1 transaction_read_only: "
        f"{relatorio['seguranca']['interno']['read_only_farmacia']} / "
        f"{relatorio['seguranca']['anvisa']['read_only_farmacia_gtin']}"
    )
    print(
        "- tcc_medicamentos transaction_read_only: "
        f"{relatorio['seguranca']['interno']['read_only_tcc']} / "
        f"{relatorio['seguranca']['anvisa']['read_only_tcc']}"
    )
    print("- Apenas SELECT/SHOW; nenhuma persistência ou commit.")

    print("\n2. REGRA OPERACIONAL AUDITADA")
    print(
        "- Foram listados todos os tipos antes da decisão. Nos dados observados, "
        "os tipos MED têm MED como primeiro token completo da descrição."
    )
    print(
        "- Regra desta investigação: primeiro token normalizado exatamente igual a "
        "MED. Isso impede falsos positivos por substring, como UMEDECIDO e "
        "NAO MEDICAMENTO."
    )
    print(
        "- A regra representa a confirmação operacional da farmácia; não substitui "
        "a classificação regulatória ANVISA."
    )

    print("\n3. LISTA COMPLETA DOS TIPOS MED")
    print("cod_tipo | nom_tipo | quantidade de produtos Rel_0024")
    _imprimir_tipos(relatorio["tipos_med"], exemplos=True)

    print("\n4. TOTAIS")
    print(f"- Tipos comerciais existentes: {relatorio['total_tipos']}")
    print(f"- Tipos MED encontrados: {relatorio['quantidade_tipos_med']}")
    print(
        f"- Produtos nos tipos MED: {relatorio['quantidade_produtos_med']} "
        f"({_percentual(relatorio['quantidade_produtos_med'], total):.2f}%)"
    )
    print(f"- Produtos sem cod_tipo: {len(relatorio['produtos_sem_tipo'])}")
    print(
        f"- Produtos com tipo preenchido e não MED: "
        f"{len(relatorio['produtos_nao_med'])}"
    )
    if (
        relatorio["quantidade_produtos_med"]
        + len(relatorio["produtos_sem_tipo"])
        + len(relatorio["produtos_nao_med"])
        != total
    ):
        raise RuntimeError("A reconciliação dos 4.666 produtos falhou.")
    print(f"- Reconciliação: {total}/{total} produtos.")

    auditoria = relatorio["auditoria_descricoes"]
    print("\n5. MED EM POSIÇÃO INESPERADA — NÃO CLASSIFICADOS COMO MED")
    print("cod_tipo | nom_tipo | quantidade")
    _imprimir_tipos(auditoria["med_posicao_inesperada"])

    print("\n6. TIPOS AMBÍGUOS OU CONFUNDÍVEIS — NÃO CLASSIFICADOS COMO MED")
    print("cod_tipo | nom_tipo | quantidade")
    _imprimir_tipos(auditoria["ambiguas"])

    print("\n7. QUALIFICADORES INTERNOS")
    for qualificador in QUALIFICADORES:
        tipos = auditoria["qualificadores"].get(qualificador, [])
        quantidade = sum(tipo["quantidade"] for tipo in tipos)
        print(
            f"\n{qualificador}: {len(tipos)} tipos; {quantidade} produtos"
        )
        print("cod_tipo | nom_tipo | quantidade")
        _imprimir_tipos(tipos)
    print(
        "- OTC é preservado como informação interna da farmácia; não foi convertido "
        "em MIP regulatório."
    )

    print("\n8. PRODUTOS APARENTEMENTE INCOMUNS EM TIPOS MED")
    print("motivo | codigo | produto | cod_tipo | nom_tipo")
    if not relatorio["produtos_incomuns"]:
        print("- nenhum pelos padrões de auditoria documentados")
    for item in relatorio["produtos_incomuns"]:
        print(
            f"{item['motivo_auditoria']} | {item['cod_reduzido']} | "
            f"{item['nom_produto']} | {item['cod_tipo']} | {item['nom_tipo']}"
        )
    print(
        "- A busca por descrição é somente uma triagem de qualidade. Nenhum item foi "
        "retirado ou reclassificado por seu nome."
    )

    print("\n9. COMPLEMENTO REGULATÓRIO ANVISA DA 10D.2")
    quantidade_med = relatorio["quantidade_produtos_med"]
    print(
        f"- Tipos MED com confirmação regulatória encontrada: "
        f"{relatorio['confirmados_anvisa']} "
        f"({_percentual(relatorio['confirmados_anvisa'], quantidade_med):.2f}%)"
    )
    print(
        f"- Tipos MED sem confirmação regulatória conclusiva: "
        f"{relatorio['sem_confirmacao_anvisa']} "
        f"({_percentual(relatorio['sem_confirmacao_anvisa'], quantidade_med):.2f}%)"
    )
    print(
        "- Sem confirmação não significa não medicamento; corresponde a resultado "
        "INDETERMINADO na metodologia conservadora da 10D.2."
    )
    print(
        "- Nenhum tipo MED foi excluído por ausência ou conflito regulatório."
    )

    print("\n10. CONCLUSÃO")
    print(
        f"- Se a taxonomia operacional fosse aplicada futuramente, o conjunto teria "
        f"exatamente {relatorio['quantidade_produtos_med']} produtos em "
        f"{relatorio['quantidade_tipos_med']} tipos MED."
    )
    print("- O filtro não foi implementado nesta etapa.")
    print("- Modelo C, Flask, /analise-ia, templates e bancos não foram alterados.")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar())
    except ErroFonteOficial as erro:
        print("VALIDAÇÃO INTERROMPIDA — FONTE OFICIAL DA 10D.2 INDISPONÍVEL")
        print(erro)
        raise SystemExit(1) from None
    except (ErroConfiguracaoFarmacia, RuntimeError) as erro:
        print("VALIDAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("VALIDAÇÃO INTERROMPIDA POR ERRO DE BANCO")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
