"""Investiga evidências para medicamento × não medicamento sem classificar.

Reutiliza a coleta protegida da Etapa 9B.6A. Os dois bancos são consultados em
modo somente leitura e nenhum resultado é persistido.
"""

from collections import Counter, defaultdict
import re
import sys
import unicodedata

from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import ErroConfiguracaoFarmacia
from .investigar_cadastro_produtos import investigar


TABELAS_RELEVANTES = {
    "cadprodu",
    "cadtipro",
    "cadgrpro",
    "caddepto",
    "cadcateg",
    "classe_terapeutica",
    "cadcddcb",
    "cadcdbar",
    "cadlabor",
    "cadloted",
    "vinculo_produto_anvisa",
}

AMOSTRAS_PROVAVEIS = (
    ("medicamento", "Dipirona", r"\bDIPIRONA\b"),
    ("medicamento", "Losartana", r"\bLOSARTANA\b"),
    ("medicamento", "Glifage", r"\bGLIFAGE\b"),
    ("medicamento", "Escitalopram", r"\bESCITALOPRAM\b"),
    ("medicamento", "Quetiapina", r"\bQUETIAPINA\b"),
    ("medicamento", "Puran T4", r"\bPURAN\s+T4\b"),
    ("não medicamento", "Sabonete", r"\bSAB(?:ONETE)?\b"),
    ("não medicamento", "Bebida", r"\b(?:BEBIDA|REFRIG|COCA[- ]?COLA)\b"),
    ("não medicamento", "Fralda", r"\b(?:FRALDA|FRD)"),
    ("não medicamento", "Absorvente", r"\b(?:ABS|ABSORVENTE)\b"),
    ("não medicamento", "Seringa", r"\bSERINGA\b"),
    ("não medicamento", "Higiene/cosmético", r"\b(?:SHAMPOO|DESODORANTE)\b"),
)


def _normalizar(valor):
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(
        caractere for caractere in texto
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", texto).strip().upper()


def _preenchido(valor):
    return valor is not None and str(valor).strip() not in ("", "0")


def _percentual(quantidade, total):
    return 0.0 if total == 0 else quantidade * 100 / total


def _por_codigo(linhas, campo="cod_reduzido"):
    resultado = defaultdict(list)
    for linha in linhas:
        resultado[linha[campo]].append(linha)
    return resultado


def _primeiro_preenchido(linhas, campo):
    for linha in linhas:
        if _preenchido(linha.get(campo)):
            return linha[campo]
    return None


def _enriquecer(resultado):
    gtin = {
        linha["cod_reduzido"]: linha
        for linha in resultado["codigos_barras"]
    }
    anvisa_formal = {
        linha["cod_reduzido"]: linha
        for linha in resultado["vinculos_anvisa"]
    }
    abc = _por_codigo(resultado["abcfarma_por_ean"])
    produtos = []
    for original in resultado["dominios"]:
        linha = dict(original)
        codigo = linha["cod_reduzido"]
        barras = gtin.get(codigo, {})
        registros_abc = abc.get(codigo, [])
        linha["possui_gtin"] = barras.get("quantidade_gtin", 0) > 0
        linha["registro_anvisa_abcfarma"] = _primeiro_preenchido(
            registros_abc, "registro_anvisa"
        )
        linha["tipo_abcfarma"] = _primeiro_preenchido(
            registros_abc, "tipo_produto_abcfarma"
        )
        linha["fabricante_abcfarma"] = _primeiro_preenchido(
            registros_abc, "fabricante_abcfarma"
        )
        linha["dcb_abcfarma"] = _primeiro_preenchido(
            registros_abc, "dcb_abcfarma"
        )
        linha["possui_vinculo_anvisa_formal"] = codigo in anvisa_formal
        linha["possui_registro_candidato"] = (
            _preenchido(linha.get("num_regms"))
            or _preenchido(linha["registro_anvisa_abcfarma"])
            or linha["possui_vinculo_anvisa_formal"]
        )
        produtos.append(linha)
    return produtos


def _distribuicao(produtos, codigo, descricao):
    contador = Counter(
        (
            str(linha.get(codigo)).strip()
            if _preenchido(linha.get(codigo)) else "NULL",
            str(linha.get(descricao)).strip()
            if _preenchido(linha.get(descricao)) else "NULL",
        )
        for linha in produtos
    )
    return [
        {"codigo": chave[0], "descricao": chave[1], "quantidade": quantidade}
        for chave, quantidade in sorted(
            contador.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _amostras_manuais(produtos):
    amostras = []
    usados = set()
    for classe, rotulo, padrao in AMOSTRAS_PROVAVEIS:
        campos_busca = ["nom_produto"]
        if rotulo in {"Fralda", "Higiene/cosmético"}:
            campos_busca.append("nom_tipo")
        encontrado = next(
            (
                produto for produto in produtos
                if produto["cod_reduzido"] not in usados
                and any(
                    re.search(padrao, _normalizar(produto.get(campo)))
                    for campo in campos_busca
                )
            ),
            None,
        )
        if encontrado is not None:
            amostras.append({"classe_manual": classe, "rotulo": rotulo, **encontrado})
            usados.add(encontrado["cod_reduzido"])
    return amostras


def _cobertura_indicadores(produtos):
    testes = {
        "DCB formal resolvida": lambda p: _preenchido(p.get("nom_dcb")),
        "classe terapêutica formal": lambda p: _preenchido(p.get("classe_terapeutica")),
        "num_regms cadastral (semântica incerta)": lambda p: _preenchido(p.get("num_regms")),
        "registro ANVISA via ABCFarma/EAN": lambda p: _preenchido(p.get("registro_anvisa_abcfarma")),
        "vínculo ANVISA formal por lote": lambda p: p["possui_vinculo_anvisa_formal"],
        "algum registro candidato": lambda p: p["possui_registro_candidato"],
        "GTIN": lambda p: p["possui_gtin"],
        "laboratório": lambda p: _preenchido(p.get("nom_laborat")),
        "tipo comercial": lambda p: _preenchido(p.get("nom_tipo")),
        "grupo": lambda p: _preenchido(p.get("nom_grupo")),
        "departamento": lambda p: _preenchido(p.get("nom_depto")),
        "categoria": lambda p: _preenchido(p.get("nom_categoria")),
        "associação ABCFarma por EAN": lambda p: any(
            _preenchido(p.get(campo))
            for campo in ("tipo_abcfarma", "registro_anvisa_abcfarma", "dcb_abcfarma")
        ),
    }
    return {
        nome: sum(teste(produto) for produto in produtos)
        for nome, teste in testes.items()
    }


def _sobreposicoes(produtos):
    def dcb(produto):
        return _preenchido(produto.get("nom_dcb"))

    def classe(produto):
        return _preenchido(produto.get("classe_terapeutica"))

    def registro(produto):
        return produto["possui_registro_candidato"]

    return {
        "DCB OU classe terapêutica": sum(dcb(p) or classe(p) for p in produtos),
        "DCB OU registro candidato": sum(dcb(p) or registro(p) for p in produtos),
        "classe terapêutica OU registro candidato": sum(classe(p) or registro(p) for p in produtos),
        "algum dos três": sum(dcb(p) or classe(p) or registro(p) for p in produtos),
        "todos os três": sum(dcb(p) and classe(p) and registro(p) for p in produtos),
        "nenhum dos três": sum(not (dcb(p) or classe(p) or registro(p)) for p in produtos),
    }


def _hipoteses(produtos, amostras):
    def tipo_explicito(produto):
        tipo = _normalizar(produto.get("nom_tipo"))
        return (
            tipo.startswith("MED ")
            or tipo.startswith("ANTICONCEPCIONAL ")
            or tipo == "OFICINAIS"
        )

    def grupo_farmaceutico(produto):
        return _normalizar(produto.get("nom_grupo")) in {
            "GENERICOS", "REFERENCIA", "SIMILAR", "OL REFERENCIA"
        }

    def metadado_farmacologico(produto):
        return (
            _preenchido(produto.get("nom_dcb"))
            or _preenchido(produto.get("classe_terapeutica"))
            or produto["possui_registro_candidato"]
        )

    estrategias = {
        "tipo comercial explicitamente medicamentoso": tipo_explicito,
        "grupo comercial farmacêutico": grupo_farmaceutico,
        "algum metadado farmacológico/registro": metadado_farmacologico,
        "tipo explícito E algum metadado farmacológico": (
            lambda p: tipo_explicito(p) and metadado_farmacologico(p)
        ),
        "tipo explícito OU algum metadado farmacológico": (
            lambda p: tipo_explicito(p) or metadado_farmacologico(p)
        ),
    }
    resultado = []
    for nome, teste in estrategias.items():
        incluidos = [produto for produto in produtos if teste(produto)]
        manuais_incluidos = [a for a in amostras if teste(a)]
        resultado.append(
            {
                "nome": nome,
                "quantidade": len(incluidos),
                "medicamentos_manuais_fora": [
                    a["rotulo"] for a in amostras
                    if a["classe_manual"] == "medicamento" and not teste(a)
                ],
                "nao_medicamentos_manuais_dentro": [
                    a["rotulo"] for a in manuais_incluidos
                    if a["classe_manual"] == "não medicamento"
                ],
                "exemplos_incluidos": [
                    f"{p['cod_reduzido']} {p['nom_produto']}"
                    for p in incluidos[:5]
                ],
                "exemplos_excluidos": [
                    f"{p['cod_reduzido']} {p['nom_produto']}"
                    for p in produtos if not teste(p)
                ][:5],
            }
        )
    return resultado


def _conflitos_indicadores(produtos):
    def tipo_explicito(produto):
        tipo = _normalizar(produto.get("nom_tipo"))
        return (
            tipo.startswith("MED ")
            or tipo.startswith("ANTICONCEPCIONAL ")
            or tipo == "OFICINAIS"
        )

    def metadado(produto):
        return (
            _preenchido(produto.get("nom_dcb"))
            or _preenchido(produto.get("classe_terapeutica"))
            or produto["possui_registro_candidato"]
        )

    tipo_sem_metadado = [
        p for p in produtos if tipo_explicito(p) and not metadado(p)
    ]
    metadado_sem_tipo = [
        p for p in produtos if metadado(p) and not tipo_explicito(p)
    ]
    return {
        "tipo explícito sem metadado farmacológico": {
            "quantidade": len(tipo_sem_metadado),
            "exemplos": [
                f"{p['cod_reduzido']} {p['nom_produto']} | {p['nom_tipo']}"
                for p in tipo_sem_metadado[:10]
            ],
        },
        "metadado farmacológico sem tipo explícito": {
            "quantidade": len(metadado_sem_tipo),
            "exemplos": [
                f"{p['cod_reduzido']} {p['nom_produto']} | {p['nom_tipo']}"
                for p in metadado_sem_tipo[:10]
            ],
        },
    }


def executar_investigacao():
    """Coleta evidências e monta o relatório reproduzível em memória."""

    base = investigar()
    produtos = _enriquecer(base)
    total = len(base["produtos_tcc"])
    if len(produtos) != total:
        raise RuntimeError(
            "A cobertura enriquecida não corresponde aos produtos Rel_0024."
        )
    amostras = _amostras_manuais(produtos)
    constraints = [
        linha for linha in base["constraints"]
        if linha["tabela_origem"] in TABELAS_RELEVANTES
        and (
            linha["tabela_destino"] is None
            or linha["tabela_destino"] in TABELAS_RELEVANTES
        )
    ]
    return {
        "seguranca": base["seguranca"],
        "total": total,
        "encontrados": len(base["encontrados"]),
        "rede": base["rede_cobertura"],
        "produtos": produtos,
        "constraints": constraints,
        "distribuicoes": {
            "TIPOS": _distribuicao(produtos, "cod_tipo", "nom_tipo"),
            "DEPARTAMENTOS": _distribuicao(produtos, "cod_depto", "nom_depto"),
            "GRUPOS": _distribuicao(produtos, "cod_grupo", "nom_grupo"),
            "CATEGORIAS": _distribuicao(produtos, "cod_categoria", "nom_categoria"),
        },
        "cobertura": _cobertura_indicadores(produtos),
        "sobreposicoes": _sobreposicoes(produtos),
        "amostras": amostras,
        "hipoteses": _hipoteses(produtos, amostras),
        "conflitos": _conflitos_indicadores(produtos),
    }


def _imprimir_cobertura(nome, quantidade, total):
    print(f"{nome} | {quantidade} | {_percentual(quantidade, total):.2f}%")


def imprimir_relatorio(relatorio):
    total = relatorio["total"]
    print("1. SEGURANÇA E ESCOPO")
    print(f"- sgfpod1 transaction_read_only: {relatorio['seguranca']['read_only_farmacia']}")
    print(f"- tcc_medicamentos transaction_read_only: {relatorio['seguranca']['read_only_tcc']}")
    print("- Comandos permitidos no sgfpod1: SELECT e SHOW; nenhuma persistência ou commit.")

    print("\n2. COBERTURA REL_0024 → CADPRODU")
    _imprimir_cobertura("Produtos Rel_0024", total, total)
    _imprimir_cobertura("Encontrados por cod_reduzido", relatorio["encontrados"], total)
    print(f"- cod_rede inequívoco: {relatorio['rede']}")
    print("- O vínculo entre bancos não é FK: é a correspondência operacional previamente validada de codigo_produto para cod_reduzido.")

    print("\n3. RELACIONAMENTOS DECLARADOS RELEVANTES")
    for item in relatorio["constraints"]:
        destino = (
            f"{item['tabela_destino']}({item['colunas_destino']})"
            if item["tabela_destino"] else "—"
        )
        print(
            f"- {item['constraint_type']} | {item['tabela_origem']}"
            f"({item['colunas_origem']}) → {destino} | {item['constraint_name']}"
        )
    print("- abcfarma_produto não possui FK com cadprodu; a associação por EAN é empírica e deve permanecer identificada como tal.")

    print("\n4. CLASSIFICAÇÕES INTERNAS")
    for titulo, linhas in relatorio["distribuicoes"].items():
        print(f"\n{titulo}: código | descrição | produtos Rel_0024")
        for linha in linhas:
            print(f"{linha['codigo']} | {linha['descricao']} | {linha['quantidade']}")

    print("\n5. MATRIZ DE COBERTURA DOS INDICADORES")
    print("Indicador | Produtos com informação | % dos 4.666")
    for nome, quantidade in relatorio["cobertura"].items():
        _imprimir_cobertura(nome, quantidade, total)

    print("\n6. SOBREPOSIÇÃO FARMACOLÓGICA")
    print("- Registro candidato combina num_regms, vínculo formal por lote ou registro ABCFarma; a semântica e a ligação ABCFarma continuam explicitamente incertas.")
    for nome, quantidade in relatorio["sobreposicoes"].items():
        _imprimir_cobertura(nome, quantidade, total)

    print("\n7. AMOSTRAS MANUAIS — APENAS VALIDAÇÃO, NUNCA REGRA")
    campos = (
        "cod_reduzido", "nom_produto", "nom_tipo", "nom_depto",
        "nom_grupo", "nom_categoria", "nom_dcb", "classe_terapeutica",
        "num_regms", "registro_anvisa_abcfarma", "nom_laborat",
        "possui_gtin", "tipo_abcfarma",
    )
    for amostra in relatorio["amostras"]:
        print(f"\n- provável {amostra['classe_manual']}: {amostra['rotulo']}")
        for campo in campos:
            print(f"  {campo}: {amostra.get(campo)}")

    print("\n8. HIPÓTESES TESTADAS, NÃO IMPLEMENTADAS")
    for hipotese in relatorio["hipoteses"]:
        _imprimir_cobertura(hipotese["nome"], hipotese["quantidade"], total)
        print(f"  medicamentos manuais aparentemente fora: {hipotese['medicamentos_manuais_fora'] or 'nenhum na amostra'}")
        print(f"  não medicamentos manuais aparentemente dentro: {hipotese['nao_medicamentos_manuais_dentro'] or 'nenhum na amostra'}")
        print(f"  exemplos incluídos: {hipotese['exemplos_incluidos']}")
        print(f"  exemplos excluídos: {hipotese['exemplos_excluidos']}")

    print("\n9. CONFLITOS ENTRE AS DUAS PRINCIPAIS EVIDÊNCIAS")
    for nome, dados in relatorio["conflitos"].items():
        _imprimir_cobertura(nome, dados["quantidade"], total)
        print(f"  exemplos: {dados['exemplos']}")

    print("\n10. INTERPRETAÇÃO E RECOMENDAÇÃO")
    print("- cadtipro.nom_tipo é o candidato interno mais promissor porque contém rótulos explícitos de medicamentos e de linhas não medicamentosas; ainda é taxonomia comercial, não classificação regulatória.")
    print("- caddepto não representa medicamento: nesta rede suas descrições são faixas de preço.")
    print("- Grupo e categoria ajudam a revisar coerência, mas são taxonomias comerciais e possuem classes ambíguas como LIBERADOS, PRIMEIROS SOCORROS e CONVENIENCIA.")
    print("- DCB, classe terapêutica e registro fornecem evidência positiva forte, porém a ausência desses campos não comprova que o produto não seja medicamento.")
    print("- Estratégia futura mais defensável: classificação em três estados, combinando tipo explícito e metadados farmacológicos, mantendo casos conflitantes/ausentes como indeterminados e validando-os contra fonte regulatória externa.")
    print("- Nenhuma regra de medicamento, não medicamento ou OTC/MIP foi implementada nesta etapa.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(executar_investigacao())
    except (ErroConfiguracaoFarmacia, RuntimeError) as erro:
        print("INVESTIGAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("INVESTIGAÇÃO INTERROMPIDA POR ERRO DE BANCO")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
