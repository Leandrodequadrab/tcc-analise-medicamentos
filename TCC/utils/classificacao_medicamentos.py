"""Classificacao operacional dos tipos internos de produtos da farmacia.

A regra usa exclusivamente ``nom_tipo``. Ela nao afirma classificacao
regulatoria, dispensacao sem prescricao ou automedicacao efetiva.
"""

from __future__ import annotations

import re
import unicodedata


NAO_MED = "NAO_MED"
MED_OTC_INTERNO = "MED_OTC_INTERNO"
MED_CONTROLADO = "MED_CONTROLADO"
MED_ANTIMICROBIANO = "MED_ANTIMICROBIANO"
MED_OUTRO = "MED_OUTRO"

CLASSIFICACOES_MED = frozenset(
    {MED_OTC_INTERNO, MED_CONTROLADO, MED_ANTIMICROBIANO, MED_OUTRO}
)
CLASSIFICACOES_ELEGIVEIS = frozenset(
    {MED_OTC_INTERNO, MED_ANTIMICROBIANO, MED_OUTRO}
)


def normalizar_tipo(valor) -> str:
    """Remove acentos, uniformiza caixa e reduz espacos consecutivos."""

    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", texto).strip().upper()


def tokens_tipo(nom_tipo) -> tuple[str, ...]:
    """Separa a descricao normalizada em tokens alfanumericos completos."""

    return tuple(re.findall(r"[A-Z0-9]+", normalizar_tipo(nom_tipo)))


def eh_tipo_med(nom_tipo) -> bool:
    """Retorna verdadeiro somente quando MED e o primeiro token completo."""

    tokens = tokens_tipo(nom_tipo)
    return bool(tokens) and tokens[0] == "MED"


def classificar_tipo_medicamento(nom_tipo) -> str:
    """Classifica o tipo com precedencia CONTROLADO > ANTIMICROBIANO > OTC."""

    tokens = tokens_tipo(nom_tipo)
    if not tokens or tokens[0] != "MED":
        return NAO_MED
    conjunto = set(tokens)
    if "CONTROLADO" in conjunto:
        return MED_CONTROLADO
    if "ANTIMICROBIANO" in conjunto:
        return MED_ANTIMICROBIANO
    if "OTC" in conjunto:
        return MED_OTC_INTERNO
    return MED_OUTRO


def eh_elegivel_analise_automedicacao(classificacao: str) -> bool:
    """Aplica apenas a elegibilidade operacional provisoria da Etapa 10D.4."""

    return classificacao in CLASSIFICACOES_ELEGIVEIS


elegivel_analise_automedicacao = eh_elegivel_analise_automedicacao


def classificar_elegibilidade(nom_tipo) -> tuple[str, bool]:
    classificacao = classificar_tipo_medicamento(nom_tipo)
    return classificacao, eh_elegivel_analise_automedicacao(classificacao)
