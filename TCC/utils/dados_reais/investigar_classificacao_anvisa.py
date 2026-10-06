"""Cruza o catálogo Rel_0024 com fontes oficiais da Anvisa.

Etapa investigativa e integralmente somente leitura. Os arquivos oficiais são
lidos do disco, os dois bancos são consultados com as proteções já existentes e
o resultado permanece em memória. Nenhuma classificação é aplicada ao Modelo C.

Fontes esperadas (download direto, sem scraping):
* DADOS_ABERTOS_MEDICAMENTOS.csv;
* TA_PRECO_MEDICAMENTO.csv (lista CMED);
* TA_RESTRICAO_MEDICAMENTO.csv.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from .conexao_farmacia import (
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)
from .investigar_classificacao_medicamentos import executar_investigacao


TOTAL_ESPERADO = 4_666
DATA_ACESSO = date.today().isoformat()

URL_MEDICAMENTOS = (
    "https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.csv"
)
URL_CMED = "https://dados.anvisa.gov.br/dados/TA_PRECO_MEDICAMENTO.csv"
URL_RESTRICOES = (
    "https://dados.anvisa.gov.br/dados/TA_RESTRICAO_MEDICAMENTO.csv"
)
URL_INDICE = "https://dados.anvisa.gov.br/dados/"
URL_LMIP = (
    "https://www.gov.br/anvisa/pt-br/setorregulado/regularizacao/medicamentos/"
    "medicamentos-isentos-de-prescricao/"
    "lista-de-medicamentos-isentos-de-prescricao-lmip"
)

FONTES = (
    {
        "nome": "Dados Abertos — Medicamentos registrados no Brasil",
        "url": URL_MEDICAMENTOS,
        "data_arquivo": "2026-09-25 17:00",
        "finalidade": "registro de produto (9 dígitos), situação e metadados",
    },
    {
        "nome": "Lista de preços de medicamentos CMED",
        "url": URL_CMED,
        "data_arquivo": "2026-07-21 17:30",
        "finalidade": "registro de apresentação (13 dígitos), EAN e tarja",
    },
    {
        "nome": "Restrições de medicamentos",
        "url": URL_RESTRICOES,
        "data_arquivo": "2026-09-25 17:00",
        "finalidade": "avaliar campos de restrição; não usado no vínculo por nome",
    },
    {
        "nome": "Lista de Medicamentos Isentos de Prescrição — LMIP",
        "url": URL_LMIP,
        "data_arquivo": "página modificada em 2026-04-15; IN 285/2024 vigente",
        "finalidade": "critério regulatório de MIP; não usada como chave textual",
    },
)

PASTA_TEMPORARIA = Path(tempfile.gettempdir())
ARQUIVO_MEDICAMENTOS_PADRAO = (
    PASTA_TEMPORARIA / "DADOS_ABERTOS_MEDICAMENTOS_2026-09-25.csv"
)
ARQUIVO_CMED_PADRAO = (
    PASTA_TEMPORARIA / "TA_PRECO_MEDICAMENTO_2026-07-21.csv"
)
ARQUIVO_RESTRICOES_PADRAO = (
    PASTA_TEMPORARIA / "TA_RESTRICAO_MEDICAMENTO_2026-09-25.csv"
)


SQL_GTINS = text(
    """
    SELECT
        cod_reduzido,
        COALESCE(NULLIF(btrim(gtin), ''), cod_barra::text) AS gtin_original,
        flg_principal
    FROM public.cadcdbar
    WHERE cod_rede = :cod_rede
      AND cod_reduzido IN :codigos
    ORDER BY cod_reduzido, flg_principal DESC, cod_barra
    """
).bindparams(bindparam("codigos", expanding=True))


class ErroFonteOficial(RuntimeError):
    """Indica ausência ou esquema inesperado de uma fonte oficial."""


@dataclass(frozen=True)
class RegistroNormalizado:
    original: str
    normalizado: str | None
    registro_base: str | None
    metodo: str
    valido: bool


@dataclass(frozen=True)
class GtinNormalizado:
    original: str
    normalizado: str | None
    metodo: str
    valido: bool


def _texto(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return str(valor).strip()


def _normalizar_texto(valor) -> str:
    texto = unicodedata.normalize("NFKD", _texto(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


def _preenchido(valor) -> bool:
    return _texto(valor) not in {"", "0"}


def _percentual(quantidade: int, total: int) -> float:
    return 0.0 if total == 0 else quantidade * 100 / total


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def normalizar_registro(valor) -> RegistroNormalizado:
    """Normaliza apenas formatos documentados; nunca completa dígitos ausentes."""

    original = _texto(valor)
    if not original:
        return RegistroNormalizado(original, None, None, "ausente", False)

    digitos = re.sub(r"\D", "", original)
    metodo = "somente_digitos" if digitos != original else "ja_numerico"
    if len(digitos) == 13 and digitos.startswith("1"):
        return RegistroNormalizado(
            original, digitos, digitos[:9], f"{metodo}; apresentacao_13", True
        )
    if len(digitos) == 9 and digitos.startswith("1"):
        return RegistroNormalizado(
            original, digitos, digitos, f"{metodo}; produto_9", True
        )

    motivo = f"invalido_{len(digitos)}_digitos"
    if digitos and not digitos.startswith("1"):
        motivo += "_nao_inicia_1"
    return RegistroNormalizado(original, None, None, motivo, False)


def _digito_verificador_gtin_valido(digitos: str) -> bool:
    corpo = digitos[:-1]
    soma = 0
    for indice, caractere in enumerate(reversed(corpo)):
        soma += int(caractere) * (3 if indice % 2 == 0 else 1)
    esperado = (10 - soma % 10) % 10
    return esperado == int(digitos[-1])


def normalizar_gtin(valor) -> GtinNormalizado:
    original = _texto(valor)
    if not original:
        return GtinNormalizado(original, None, "ausente", False)
    digitos = re.sub(r"\D", "", original)
    metodo = "somente_digitos" if digitos != original else "ja_numerico"
    if len(digitos) not in {8, 12, 13, 14}:
        return GtinNormalizado(
            original, None, f"invalido_{len(digitos)}_digitos", False
        )
    if set(digitos) == {"0"}:
        return GtinNormalizado(original, None, "invalido_todos_zeros", False)
    if not _digito_verificador_gtin_valido(digitos):
        return GtinNormalizado(
            original, None, f"{metodo}; digito_verificador_invalido", False
        )
    return GtinNormalizado(
        original, digitos, f"{metodo}; gtin_{len(digitos)}", True
    )


def _validar_arquivo(caminho: Path, nome: str, url: str) -> None:
    if not caminho.is_file() or caminho.stat().st_size == 0:
        raise ErroFonteOficial(
            f"Fonte oficial ausente: {nome}. Baixe manualmente {url} e informe "
            f"o caminho pelo argumento correspondente. Caminho verificado: {caminho}"
        )


def _ler_medicamentos(caminho: Path) -> pd.DataFrame:
    _validar_arquivo(caminho, "DADOS_ABERTOS_MEDICAMENTOS.csv", URL_MEDICAMENTOS)
    dados = pd.read_csv(caminho, sep=";", encoding="cp1252", dtype=str)
    esperadas = {
        "TIPO_PRODUTO",
        "NOME_PRODUTO",
        "NUMERO_REGISTRO_PRODUTO",
        "SITUACAO_REGISTRO",
        "PRINCIPIO_ATIVO",
    }
    ausentes = esperadas - set(dados.columns)
    if ausentes:
        raise ErroFonteOficial(
            "Esquema inesperado em DADOS_ABERTOS_MEDICAMENTOS.csv; faltam: "
            + ", ".join(sorted(ausentes))
        )
    return dados


def _localizar_cabecalho_cmed(caminho: Path) -> int:
    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.reader(arquivo, delimiter=";")
        for indice, linha in enumerate(leitor):
            if linha and _normalizar_texto(linha[0]) == "SUBSTANCIA":
                return indice
            if indice > 100:
                break
    raise ErroFonteOficial("Cabeçalho SUBSTÂNCIA não encontrado na lista CMED.")


def _ler_cmed(caminho: Path) -> pd.DataFrame:
    _validar_arquivo(caminho, "TA_PRECO_MEDICAMENTO.csv", URL_CMED)
    cabecalho = _localizar_cabecalho_cmed(caminho)
    dados = pd.read_csv(
        caminho,
        sep=";",
        encoding="utf-8-sig",
        dtype=str,
        header=cabecalho,
    )
    mapa = {_normalizar_texto(coluna): coluna for coluna in dados.columns}
    esperadas = {
        "SUBSTANCIA",
        "REGISTRO",
        "EAN 1",
        "EAN 2",
        "EAN 3",
        "PRODUTO",
        "APRESENTACAO",
        "TARJA",
    }
    ausentes = esperadas - set(mapa)
    if ausentes:
        raise ErroFonteOficial(
            "Esquema inesperado em TA_PRECO_MEDICAMENTO.csv; faltam: "
            + ", ".join(sorted(ausentes))
        )
    return dados.rename(columns={original: normal for normal, original in mapa.items()})


def _ler_restricoes(caminho: Path) -> pd.DataFrame:
    _validar_arquivo(caminho, "TA_RESTRICAO_MEDICAMENTO.csv", URL_RESTRICOES)
    linhas_validas = []
    linhas_irregulares = []
    with caminho.open("r", encoding="cp1252", newline="") as arquivo:
        leitor = csv.reader(arquivo, delimiter=";")
        cabecalho = next(leitor)
        for numero_linha, linha in enumerate(leitor, start=2):
            if len(linha) == len(cabecalho):
                linhas_validas.append(linha)
            else:
                linhas_irregulares.append(
                    {"linha": numero_linha, "quantidade_campos": len(linha)}
                )
    dados = pd.DataFrame(linhas_validas, columns=cabecalho, dtype=str)
    dados.attrs["linhas_irregulares"] = linhas_irregulares
    esperadas = {
        "NO_PRODUTO",
        "NO_PRINICIPIO_ATIVO",
        "DS_CONCENTRACAO",
        "DS_FORMA_FISICA",
        "DS_RESTRICAO_PRESCRICAO",
    }
    ausentes = esperadas - set(dados.columns)
    if ausentes:
        raise ErroFonteOficial(
            "Esquema inesperado em TA_RESTRICAO_MEDICAMENTO.csv; faltam: "
            + ", ".join(sorted(ausentes))
        )
    return dados


def carregar_fontes_oficiais(
    arquivo_medicamentos: Path,
    arquivo_cmed: Path,
    arquivo_restricoes: Path,
) -> dict:
    """Carrega e valida as três fontes antes de acessar qualquer banco."""

    medicamentos = _ler_medicamentos(arquivo_medicamentos)
    cmed = _ler_cmed(arquivo_cmed)
    restricoes = _ler_restricoes(arquivo_restricoes)

    return {
        "medicamentos": medicamentos,
        "cmed": cmed,
        "restricoes": restricoes,
        "arquivos": {
            "medicamentos": {
                "caminho": str(arquivo_medicamentos),
                "sha256": _sha256(arquivo_medicamentos),
                "linhas": len(medicamentos),
            },
            "cmed": {
                "caminho": str(arquivo_cmed),
                "sha256": _sha256(arquivo_cmed),
                "linhas": len(cmed),
            },
            "restricoes": {
                "caminho": str(arquivo_restricoes),
                "sha256": _sha256(arquivo_restricoes),
                "linhas": len(restricoes),
                "linhas_irregulares": restricoes.attrs.get(
                    "linhas_irregulares", []
                ),
            },
        },
    }


def _coletar_gtins_locais(produtos: list[dict], cod_rede: int) -> tuple[list[dict], str]:
    codigos = sorted({produto["cod_reduzido"] for produto in produtos})
    with conectar_farmacia_somente_leitura() as farmacia:
        modo = farmacia.execute(text("SHOW transaction_read_only")).scalar_one()
        if modo != "on":
            raise RuntimeError("sgfpod1 não está em modo somente leitura.")
        linhas = farmacia.execute(
            SQL_GTINS, {"cod_rede": cod_rede, "codigos": codigos}
        ).mappings().all()
        return [dict(linha) for linha in linhas], modo


def _preparar_indices_oficiais(fontes: dict) -> dict:
    registros_base = defaultdict(list)
    registros_apresentacao = defaultdict(list)
    gtins = defaultdict(list)

    for linha in fontes["medicamentos"].fillna("").to_dict("records"):
        registro = normalizar_registro(linha["NUMERO_REGISTRO_PRODUTO"])
        if registro.valido and registro.registro_base:
            registros_base[registro.registro_base].append(
                {
                    "registro_base": registro.registro_base,
                    "produto": _texto(linha["NOME_PRODUTO"]),
                    "situacao": _texto(linha["SITUACAO_REGISTRO"]),
                    "principio_ativo": _texto(linha["PRINCIPIO_ATIVO"]),
                    "tipo_produto": _texto(linha["TIPO_PRODUTO"]),
                }
            )

    for linha in fontes["cmed"].fillna("").to_dict("records"):
        registro = normalizar_registro(linha["REGISTRO"])
        if not registro.valido or len(registro.normalizado or "") != 13:
            continue
        oficial = {
            "registro_apresentacao": registro.normalizado,
            "registro_base": registro.registro_base,
            "produto": _texto(linha["PRODUTO"]),
            "apresentacao": _texto(linha["APRESENTACAO"]),
            "substancia": _texto(linha["SUBSTANCIA"]),
            "tarja": _texto(linha["TARJA"]),
        }
        registros_apresentacao[registro.normalizado].append(oficial)
        for coluna in ("EAN 1", "EAN 2", "EAN 3"):
            gtin = normalizar_gtin(linha[coluna])
            if gtin.valido and gtin.normalizado:
                item = dict(oficial)
                item["gtin_oficial"] = gtin.normalizado
                gtins[gtin.normalizado].append(item)

    return {
        "registros_base": registros_base,
        "registros_apresentacao": registros_apresentacao,
        "gtins": gtins,
    }


def _deduplicar(linhas: list[dict], campos: tuple[str, ...]) -> list[dict]:
    vistos = set()
    unicos = []
    for linha in linhas:
        chave = tuple(_texto(linha.get(campo)) for campo in campos)
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(linha)
    return unicos


def _tipo_interno_explicito_medicamento(produto: dict) -> bool:
    tipo = _normalizar_texto(produto.get("nom_tipo"))
    return (
        tipo.startswith("MED ")
        or tipo.startswith("ANTICONCEPCIONAL ")
        or tipo == "OFICINAIS"
    )


def _tipo_interno_explicito_nao_medicamento(produto: dict) -> bool:
    return "NAO MEDICAMENTO" in _normalizar_texto(produto.get("nom_tipo"))


def _status_mip(linhas_cmed: list[dict]) -> tuple[str, str]:
    if not linhas_cmed:
        return "MIP INDETERMINADO", "sem apresentação oficial inequívoca"

    tarjadas = {
        _normalizar_texto(linha.get("tarja")) for linha in linhas_cmed
        if _texto(linha.get("tarja"))
    }
    sem_tarja = {"TARJA SEM TARJA"}
    sob_prescricao = {
        "TARJA VERMELHA",
        "TARJA VERMELHA SOB RESTRICAO",
        "TARJA PRETA",
    }
    if tarjadas and tarjadas <= sem_tarja:
        return "MIP CONFIRMADO", "CMED: todas as apresentações vinculadas sem tarja"
    if tarjadas and tarjadas <= sob_prescricao:
        return (
            "SUJEITO A PRESCRIÇÃO",
            "CMED: todas as apresentações vinculadas com tarja de prescrição",
        )
    return (
        "MIP INDETERMINADO",
        "tarja ausente, não documentada ou conflitante entre apresentações",
    )


def _classificar_produtos(
    produtos: list[dict],
    gtins_locais: list[dict],
    indices: dict,
) -> list[dict]:
    gtins_por_produto = defaultdict(list)
    for linha in gtins_locais:
        gtins_por_produto[linha["cod_reduzido"]].append(linha)

    resultados = []
    for produto in produtos:
        codigo = produto["cod_reduzido"]
        registro = normalizar_registro(produto.get("num_regms"))
        base_matches = (
            indices["registros_base"].get(registro.registro_base, [])
            if registro.valido else []
        )
        apresentacao_matches = (
            indices["registros_apresentacao"].get(registro.normalizado, [])
            if registro.valido and len(registro.normalizado or "") == 13 else []
        )

        gtins_normalizados = [
            normalizar_gtin(linha.get("gtin_original"))
            for linha in gtins_por_produto.get(codigo, [])
        ]
        gtins_validos = sorted(
            {gtin.normalizado for gtin in gtins_normalizados if gtin.valido}
        )
        gtin_matches = []
        for gtin in gtins_validos:
            gtin_matches.extend(indices["gtins"].get(gtin, []))
        gtin_matches = _deduplicar(
            gtin_matches,
            ("gtin_oficial", "registro_apresentacao", "produto", "apresentacao"),
        )
        apresentacao_matches = _deduplicar(
            apresentacao_matches,
            ("registro_apresentacao", "produto", "apresentacao"),
        )
        base_matches = _deduplicar(
            base_matches,
            ("registro_base", "produto", "situacao", "principio_ativo"),
        )

        bases_gtin = {
            linha["registro_base"] for linha in gtin_matches
            if linha.get("registro_base")
        }
        bases_registro = {
            linha["registro_base"] for linha in base_matches
            if linha.get("registro_base")
        }
        conflito_identificadores = bool(
            bases_gtin and bases_registro and bases_gtin.isdisjoint(bases_registro)
        )
        conflito_interno = (
            _tipo_interno_explicito_nao_medicamento(produto)
            and bool(base_matches)
            and not bool(gtin_matches)
        )

        if conflito_identificadores:
            classificacao = "INDETERMINADO"
            fonte_principal = "conflito entre registro e GTIN oficiais"
            tipo_correspondencia = "conflitante"
        elif gtin_matches:
            classificacao = "MEDICAMENTO"
            fonte_principal = "ANVISA/CMED"
            tipo_correspondencia = "GTIN exato"
        elif (
            base_matches
            and not conflito_interno
            and (
                apresentacao_matches
                or _tipo_interno_explicito_medicamento(produto)
            )
        ):
            classificacao = "MEDICAMENTO"
            fonte_principal = "ANVISA — medicamentos registrados"
            tipo_correspondencia = (
                "registro completo de apresentação e registro-base"
                if apresentacao_matches else "registro-base de 9 dígitos"
            )
        else:
            classificacao = "INDETERMINADO"
            fonte_principal = "sem evidência regulatória conclusiva"
            tipo_correspondencia = (
                "registro oficial em conflito com tipo interno explícito"
                if conflito_interno else "ausente"
            )
            if base_matches and not conflito_interno:
                tipo_correspondencia = (
                    "registro-base oficial sem segunda evidência estruturada"
                )

        correspondencias_apresentacao = _deduplicar(
            gtin_matches + apresentacao_matches,
            ("registro_apresentacao", "produto", "apresentacao", "tarja"),
        )
        if classificacao == "MEDICAMENTO":
            mip, justificativa_mip = _status_mip(correspondencias_apresentacao)
        else:
            mip, justificativa_mip = (
                "MIP INDETERMINADO",
                "produto não confirmado como medicamento",
            )

        registros_oficiais = sorted(
            {
                linha.get("registro_apresentacao") or linha.get("registro_base")
                for linha in gtin_matches + apresentacao_matches + base_matches
                if linha.get("registro_apresentacao") or linha.get("registro_base")
            }
        )
        registros_oficiais_gtin = sorted(
            {
                linha.get("registro_apresentacao")
                for linha in gtin_matches
                if linha.get("registro_apresentacao")
            }
        )
        registros_oficiais_registro = sorted(
            {
                linha.get("registro_apresentacao") or linha.get("registro_base")
                for linha in apresentacao_matches + base_matches
                if linha.get("registro_apresentacao") or linha.get("registro_base")
            }
        )
        situacoes = sorted(
            {_texto(linha.get("situacao")) for linha in base_matches if linha.get("situacao")}
        )
        tarjadas = sorted(
            {_texto(linha.get("tarja")) for linha in correspondencias_apresentacao if linha.get("tarja")}
        )
        evidencias = []
        if gtin_matches:
            evidencias.append(f"{len(gtin_matches)} apresentação(ões) CMED por GTIN")
        if base_matches:
            evidencias.append(f"{len(base_matches)} registro(s) na base de medicamentos")
        if apresentacao_matches:
            evidencias.append(
                f"{len(apresentacao_matches)} apresentação(ões) CMED por registro completo"
            )

        resultados.append(
            {
                "codigo_produto": codigo,
                "descricao": produto.get("nom_produto"),
                "classificacao_experimental": classificacao,
                "fonte_principal": fonte_principal,
                "identificador_utilizado": (
                    "GTIN" if gtin_matches else "num_regms" if base_matches else None
                ),
                "valor_identificador": (
                    ", ".join(gtins_validos) if gtin_matches else registro.original
                ),
                "tipo_correspondencia": tipo_correspondencia,
                "evidencia_interna": {
                    "tipo": produto.get("nom_tipo"),
                    "dcb": produto.get("nom_dcb"),
                    "classe_terapeutica": produto.get("classe_terapeutica"),
                    "laboratorio": produto.get("nom_laborat"),
                    "registro_abcfarma": produto.get("registro_anvisa_abcfarma"),
                },
                "evidencia_anvisa": evidencias,
                "observacao": (
                    "conflito entre identificadores estruturados"
                    if conflito_identificadores
                    else "registro isolado conflita com tipo interno explícito"
                    if conflito_interno
                    else ""
                ),
                "registro_original": registro.original,
                "registro_normalizado": registro.normalizado,
                "registro_base": registro.registro_base,
                "metodo_normalizacao_registro": registro.metodo,
                "registro_valido": registro.valido,
                "registros_oficiais": registros_oficiais,
                "registros_oficiais_gtin": registros_oficiais_gtin,
                "registros_oficiais_registro": registros_oficiais_registro,
                "situacoes_registro": situacoes,
                "gtins_originais": [gtin.original for gtin in gtins_normalizados],
                "gtins_validos": gtins_validos,
                "metodos_normalizacao_gtin": sorted(
                    {gtin.metodo for gtin in gtins_normalizados}
                ),
                "quantidade_matches_gtin": len(gtin_matches),
                "quantidade_matches_registro_base": len(base_matches),
                "quantidade_matches_registro_apresentacao": len(apresentacao_matches),
                "conflito_identificadores": conflito_identificadores,
                "classificacao_mip": mip,
                "justificativa_mip": justificativa_mip,
                "tarjas_oficiais": tarjadas,
                "nom_tipo": produto.get("nom_tipo"),
                "nom_dcb": produto.get("nom_dcb"),
                "classe_terapeutica": produto.get("classe_terapeutica"),
                "num_regms": produto.get("num_regms"),
                "registro_anvisa_abcfarma": produto.get("registro_anvisa_abcfarma"),
                "possui_gtin": produto.get("possui_gtin"),
            }
        )
    return resultados


def _situacao_correspondencia_registro(item: dict) -> str:
    if not _preenchido(item["registro_original"]):
        return "ausente"
    if not item["registro_valido"]:
        return "invalido"
    quantidade = item["quantidade_matches_registro_base"]
    if quantidade == 0:
        return "sem correspondencia"
    if quantidade == 1:
        return "unica"
    return "multipla"


def _situacao_correspondencia_gtin(item: dict) -> str:
    if not item["gtins_originais"]:
        return "ausente"
    if not item["gtins_validos"]:
        return "invalido"
    registros = set(item["registros_oficiais_gtin"])
    if item["quantidade_matches_gtin"] == 0:
        return "sem correspondencia"
    if len(registros) <= 1:
        return "unica"
    return "multipla"


def _localizar_casos(resultados: list[dict]) -> list[dict]:
    especificacoes = (
        ("Dipirona", r"\bDIPIRONA\b", None),
        ("Losartana", r"\bLOSARTANA\b", None),
        ("Glifage", r"\bGLIFAGE\b", None),
        ("Escitalopram", r"\bESCITALOPRAM\b", None),
        ("Quetiapina", r"\bQUETIAPINA\b", None),
        ("Puran T4", r"\bPURAN\s+T4\b", None),
        ("Lubrificante KY", r"\bKY\b", None),
        ("Teste de gravidez", r"\bTESTE\b.*\bGRAVIDEZ\b", None),
        ("Fralda com registro", r".*", "fralda_registro"),
        ("Creme dental com DCB", r".*", "dental_dcb"),
        ("Sabonete com DCB", r".*", "sabonete_dcb"),
        ("Talco com metadados", r".*", "talco_metadados"),
        ("Tipo NÃO MEDICAMENTO com metadados", r".*", "nao_med_com_metadados"),
    )
    casos = []
    usados = set()
    for rotulo, padrao, filtro in especificacoes:
        candidatos = []
        for item in resultados:
            if item["codigo_produto"] in usados:
                continue
            if not re.search(padrao, _normalizar_texto(item["descricao"])):
                continue
            if filtro == "registro" and not _preenchido(item["num_regms"]):
                continue
            if filtro == "dcb" and not _preenchido(item["nom_dcb"]):
                continue
            if filtro == "metadados" and not (
                _preenchido(item["nom_dcb"])
                or _preenchido(item["classe_terapeutica"])
                or _preenchido(item["num_regms"])
            ):
                continue
            if filtro == "nao_med_com_metadados" and not (
                "NAO MEDICAMENTO" in _normalizar_texto(item["nom_tipo"])
                and (
                    _preenchido(item["nom_dcb"])
                    or _preenchido(item["classe_terapeutica"])
                    or _preenchido(item["num_regms"])
                )
            ):
                continue
            tipo = _normalizar_texto(item["nom_tipo"])
            if filtro == "fralda_registro" and not (
                "FRALDA" in tipo and _preenchido(item["num_regms"])
            ):
                continue
            if filtro == "dental_dcb" and not (
                "DENTAL" in tipo and _preenchido(item["nom_dcb"])
            ):
                continue
            if filtro == "sabonete_dcb" and not (
                "SABONETE" in tipo and _preenchido(item["nom_dcb"])
            ):
                continue
            if filtro == "talco_metadados" and not (
                "TALCO" in tipo
                and (
                    _preenchido(item["nom_dcb"])
                    or _preenchido(item["classe_terapeutica"])
                    or _preenchido(item["num_regms"])
                )
            ):
                continue
            candidatos.append(item)
        if candidatos:
            escolhido = candidatos[0]
            casos.append({"rotulo_controle": rotulo, **escolhido})
            usados.add(escolhido["codigo_produto"])
    return casos


def _digest_resultados(resultados: list[dict]) -> str:
    campos = [
        (
            str(item["codigo_produto"]),
            item["classificacao_experimental"],
            item["classificacao_mip"],
            item["tipo_correspondencia"],
            tuple(item["registros_oficiais"]),
        )
        for item in resultados
    ]
    serializado = json.dumps(campos, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def executar(
    arquivo_medicamentos: Path = ARQUIVO_MEDICAMENTOS_PADRAO,
    arquivo_cmed: Path = ARQUIVO_CMED_PADRAO,
    arquivo_restricoes: Path = ARQUIVO_RESTRICOES_PADRAO,
) -> dict:
    """Executa toda a investigação e devolve estruturas auditáveis em memória."""

    fontes = carregar_fontes_oficiais(
        arquivo_medicamentos, arquivo_cmed, arquivo_restricoes
    )
    base = executar_investigacao()
    if base["total"] != TOTAL_ESPERADO:
        raise RuntimeError(
            f"Esperados {TOTAL_ESPERADO} produtos Rel_0024; encontrados {base['total']}."
        )
    if base["rede"] is None:
        raise RuntimeError("Não foi possível determinar cod_rede de forma inequívoca.")

    gtins_locais, modo_farmacia = _coletar_gtins_locais(
        base["produtos"], base["rede"]
    )
    indices = _preparar_indices_oficiais(fontes)
    resultados = _classificar_produtos(base["produtos"], gtins_locais, indices)
    repeticao = _classificar_produtos(base["produtos"], gtins_locais, indices)

    registro_contagem = Counter(
        _situacao_correspondencia_registro(item) for item in resultados
    )
    gtin_contagem = Counter(_situacao_correspondencia_gtin(item) for item in resultados)
    classificacoes = Counter(
        item["classificacao_experimental"] for item in resultados
    )
    mip = Counter(
        item["classificacao_mip"]
        for item in resultados
        if item["classificacao_experimental"] == "MEDICAMENTO"
    )

    matriz = defaultdict(Counter)
    for item in resultados:
        tipo = _texto(item["nom_tipo"]) or "NULL"
        matriz[tipo][item["classificacao_experimental"]] += 1

    abcfarma_com_registro = sum(
        _preenchido(item["registro_anvisa_abcfarma"]) for item in resultados
    )
    dcb = sum(_preenchido(item["nom_dcb"]) for item in resultados)
    classe = sum(
        _preenchido(item["classe_terapeutica"]) for item in resultados
    )
    laboratorio = sum(
        _preenchido(produto.get("nom_laborat")) for produto in base["produtos"]
    )

    return {
        "fontes": fontes,
        "seguranca": {
            **base["seguranca"],
            "read_only_farmacia_gtin": modo_farmacia,
        },
        "total": len(resultados),
        "resultados": resultados,
        "registro_contagem": registro_contagem,
        "gtin_contagem": gtin_contagem,
        "classificacoes": classificacoes,
        "mip": mip,
        "matriz": matriz,
        "casos": _localizar_casos(resultados),
        "identificadores": {
            "cod_reduzido": len(resultados),
            "gtin": sum(bool(item["gtins_originais"]) for item in resultados),
            "num_regms": sum(
                _preenchido(item["registro_original"]) for item in resultados
            ),
            "abcfarma_registro": abcfarma_com_registro,
            "dcb": dcb,
            "classe_terapeutica": classe,
            "laboratorio": laboratorio,
            "descricao": sum(_preenchido(item["descricao"]) for item in resultados),
        },
        "conflitos_registro_gtin": sum(
            item["conflito_identificadores"] for item in resultados
        ),
        "digest_primeira": _digest_resultados(resultados),
        "digest_repeticao": _digest_resultados(repeticao),
    }


def _linha_quantidade(rotulo: str, quantidade: int, total: int) -> None:
    print(f"{rotulo} | {quantidade} | {_percentual(quantidade, total):.2f}%")


def imprimir_relatorio(relatorio: dict) -> None:
    total = relatorio["total"]

    print("A. FONTES OFICIAIS UTILIZADAS")
    print(f"Data de acesso: {DATA_ACESSO}")
    for fonte in FONTES:
        print(
            f"- {fonte['nome']} | {fonte['url']} | versão/data: "
            f"{fonte['data_arquivo']} | finalidade: {fonte['finalidade']}"
        )
    print(f"- Índice oficial: {URL_INDICE}")
    for nome, dados in relatorio["fontes"]["arquivos"].items():
        print(
            f"  {nome}: {dados['linhas']} linhas | SHA-256 {dados['sha256']} | "
            f"arquivo temporário {dados['caminho']}"
        )
        if dados.get("linhas_irregulares"):
            print(
                f"    linhas estruturais irregulares isoladas: "
                f"{len(dados['linhas_irregulares'])} | "
                f"detalhes: {dados['linhas_irregulares']}"
            )
    print(
        "- TA_RESTRICAO_MEDICAMENTO possui princípio ativo, concentração, forma e "
        "restrição, mas não registro/GTIN; não foi ligada por nome."
    )

    print("\nB. SEGURANÇA E IDENTIFICADORES LOCAIS")
    print(
        f"- sgfpod1 transaction_read_only: "
        f"{relatorio['seguranca']['read_only_farmacia']} / "
        f"{relatorio['seguranca']['read_only_farmacia_gtin']}"
    )
    print(
        f"- tcc_medicamentos transaction_read_only: "
        f"{relatorio['seguranca']['read_only_tcc']}"
    )
    print("Identificador | cobertura | % dos 4.666 | avaliação")
    avaliacoes = {
        "cod_reduzido": "100%; chave interna, inexistente na Anvisa",
        "gtin": "chave forte de apresentação quando coincide exatamente com CMED",
        "num_regms": "chave regulatória; requer validação de 9/13 dígitos",
        "abcfarma_registro": "ponte comercial auxiliar; nunca autoridade",
        "dcb": "evidência farmacológica parcial; não é chave de produto",
        "classe_terapeutica": "evidência parcial; não é chave de produto",
        "laboratorio": "auxiliar; nomes/razões sociais podem variar",
        "descricao": "somente auditoria; nunca chave primária",
    }
    for nome, quantidade in relatorio["identificadores"].items():
        print(
            f"{nome} | {quantidade} | {_percentual(quantidade, total):.2f}% | "
            f"{avaliacoes[nome]}"
        )

    print("\nC. NORMALIZAÇÃO")
    print(
        "- Registro: remove somente pontuação; aceita 9 dígitos (registro do produto) "
        "ou 13 (apresentação), sempre iniciados por 1. Para 13 dígitos, deriva-se "
        "explicitamente a base pelos primeiros 9. Não há padding, correção ou "
        "sobrescrita do original."
    )
    comprimentos = Counter(
        len(re.sub(r"\D", "", item["registro_original"]))
        for item in relatorio["resultados"] if item["registro_original"]
    )
    print(f"- Comprimentos observados em num_regms: {dict(sorted(comprimentos.items()))}")
    metodos_registro = Counter(
        item["metodo_normalizacao_registro"] for item in relatorio["resultados"]
        if item["registro_original"]
    )
    print(f"- Métodos/resultados de registro: {dict(metodos_registro)}")
    print(
        "- GTIN: remove somente pontuação, aceita 8/12/13/14 dígitos e valida o "
        "dígito verificador. Original, normalizado e método permanecem na auditoria."
    )

    print("\nD. CORRESPONDÊNCIA COM A ANVISA")
    print("Registro local | produtos | % dos 4.666")
    for chave in ("unica", "multipla", "sem correspondencia", "invalido", "ausente"):
        _linha_quantidade(chave, relatorio["registro_contagem"][chave], total)
    print("GTIN local | produtos | % dos 4.666")
    for chave in ("unica", "multipla", "sem correspondencia", "invalido", "ausente"):
        _linha_quantidade(chave, relatorio["gtin_contagem"][chave], total)
    _linha_quantidade(
        "Conflito entre registro e GTIN oficiais",
        relatorio["conflitos_registro_gtin"],
        total,
    )
    print(
        "- Única/múltipla no registro conta registros oficiais distintos; no GTIN, "
        "conta apresentações regulatórias distintas alcançadas pelos GTINs locais."
    )

    print("\nE. CLASSIFICAÇÃO EXPERIMENTAL")
    print("Classificação | quantidade | % dos 4.666")
    for classe in ("MEDICAMENTO", "NÃO MEDICAMENTO", "INDETERMINADO"):
        _linha_quantidade(classe, relatorio["classificacoes"][classe], total)
    print(
        "- NÃO MEDICAMENTO permanece zero: as fontes carregadas são catálogos de "
        "medicamentos e a ausência neles não prova uma categoria regulatória externa."
    )
    print(
        "- Registro-base isolado foi mantido indeterminado quando não havia registro "
        "completo na CMED nem tipo interno explicitamente medicamentoso. Registro em "
        "conflito com tipo interno NÃO MEDICAMENTO também permaneceu indeterminado."
    )

    print("\nF. COMPARAÇÃO COM CADTIPRO.NOM_TIPO")
    print("Tipo interno | Medicamento | Não medicamento | Indeterminado")
    itens_matriz = sorted(
        relatorio["matriz"].items(),
        key=lambda item: (-sum(item[1].values()), item[0]),
    )
    for tipo, contagem in itens_matriz:
        destaque = any(
            termo in _normalizar_texto(tipo)
            for termo in ("MED", "OTC", "TARJA", "CONTROLADO", "CONTINUO")
        )
        if destaque or sum(contagem.values()) >= 20:
            print(
                f"{tipo} | {contagem['MEDICAMENTO']} | "
                f"{contagem['NÃO MEDICAMENTO']} | {contagem['INDETERMINADO']}"
            )
    print(
        "- Esta matriz mede concordância; cadtipro continua sendo taxonomia interna, "
        "não autoridade regulatória."
    )

    print("\nG. CASOS DE CONTROLE DA ETAPA 10D.1")
    campos = (
        "codigo_produto",
        "descricao",
        "nom_tipo",
        "num_regms",
        "registro_normalizado",
        "gtins_validos",
        "tipo_correspondencia",
        "registros_oficiais",
        "situacoes_registro",
        "classificacao_experimental",
        "classificacao_mip",
        "tarjas_oficiais",
        "observacao",
    )
    for caso in relatorio["casos"]:
        print(f"\n- {caso['rotulo_controle']}")
        for campo in campos:
            print(f"  {campo}: {caso.get(campo)}")

    print("\nH. MIP / OTC")
    medicamentos = relatorio["classificacoes"]["MEDICAMENTO"]
    print("Situação | quantidade | % dos medicamentos confirmados")
    for classe in (
        "MIP CONFIRMADO",
        "SUJEITO A PRESCRIÇÃO",
        "MIP INDETERMINADO",
    ):
        _linha_quantidade(classe, relatorio["mip"][classe], medicamentos)
    print(
        "- A regra não procura princípio ativo pelo nome na LMIP. MIP/prescrição só "
        "é informado quando registro completo ou GTIN chega à apresentação CMED e "
        "todas as tarjas vinculadas são coerentes."
    )
    print(
        "- A LMIP exige fármaco/associação, forma, concentração e indicação. O "
        "cadastro local não fornece indicação regulatória estruturada e a tabela de "
        "restrições não possui registro/GTIN; por isso não foi feito join textual."
    )

    print("\nI. RECOMENDAÇÃO PARA A PRÓXIMA ETAPA — NÃO IMPLEMENTADA")
    print(
        "- Regra candidata: confirmar medicamento por GTIN exato na CMED, por registro "
        "completo de apresentação sem conflito ou por registro-base acompanhado de "
        "tipo interno explicitamente medicamentoso; manter ausência/conflito como "
        "indeterminado. Somente classificar não medicamento após incorporar uma base "
        "oficial da respectiva categoria com identificador estruturado."
    )
    print(
        "- Para MIP, preferir a restrição/tarja da apresentação oficial identificada; "
        "usar a LMIP apenas quando forma, concentração e indicação puderem ser "
        "comparadas integralmente."
    )

    print("\nJ. REPRODUTIBILIDADE E INTEGRIDADE")
    print(f"- Digest execução 1: {relatorio['digest_primeira']}")
    print(f"- Digest execução 2: {relatorio['digest_repeticao']}")
    print(
        f"- Resultados idênticos em memória: "
        f"{relatorio['digest_primeira'] == relatorio['digest_repeticao']}"
    )
    print("- Nenhum banco alterado; nenhuma transação de escrita ou commit executado.")
    print("- Modelo C, Flask, dashboard e tabelas da aplicação não foram alterados.")
    print("- Nenhuma classificação foi aplicada ao pipeline de análise.")


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Investiga classificação regulatória com fontes oficiais da Anvisa."
    )
    parser.add_argument(
        "--medicamentos",
        type=Path,
        default=ARQUIVO_MEDICAMENTOS_PADRAO,
        help="Caminho para DADOS_ABERTOS_MEDICAMENTOS.csv oficial.",
    )
    parser.add_argument(
        "--cmed",
        type=Path,
        default=ARQUIVO_CMED_PADRAO,
        help="Caminho para TA_PRECO_MEDICAMENTO.csv oficial.",
    )
    parser.add_argument(
        "--restricoes",
        type=Path,
        default=ARQUIVO_RESTRICOES_PADRAO,
        help="Caminho para TA_RESTRICAO_MEDICAMENTO.csv oficial.",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argumentos = _argumentos()
    try:
        relatorio = executar(
            argumentos.medicamentos,
            argumentos.cmed,
            argumentos.restricoes,
        )
        imprimir_relatorio(relatorio)
    except ErroFonteOficial as erro:
        print("INVESTIGAÇÃO INTERROMPIDA — FONTE OFICIAL INDISPONÍVEL OU INVÁLIDA")
        print(erro)
        raise SystemExit(1) from None
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
