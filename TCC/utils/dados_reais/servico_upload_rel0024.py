"""Adaptador seguro entre upload Flask e o importador Rel_0024 existente."""

from __future__ import annotations

from pathlib import Path
import tempfile

from werkzeug.utils import secure_filename

from .importar_rel0024 import importar_rel0024
from .validar_rel0024 import ErroRel0024


EXTENSOES_PERMITIDAS = frozenset({".xls", ".xlsx"})


def processar_upload_rel0024(arquivo, pasta_upload):
    """Salva temporariamente, importa e sempre remove o arquivo recebido."""

    if arquivo is None or not arquivo.filename:
        raise ErroRel0024("Selecione um relatório Rel_0024 para importar.")
    nome_seguro = secure_filename(arquivo.filename)
    if not nome_seguro:
        raise ErroRel0024("O nome do arquivo enviado é inválido.")
    extensao = Path(nome_seguro).suffix.lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        raise ErroRel0024("Envie um arquivo com extensão .xlsx ou .xls.")

    pasta = Path(pasta_upload)
    pasta.mkdir(parents=True, exist_ok=True)
    temporario = tempfile.NamedTemporaryFile(
        prefix="rel0024_",
        suffix=extensao,
        dir=pasta,
        delete=False,
    )
    caminho_temporario = Path(temporario.name)
    temporario.close()
    try:
        arquivo.save(caminho_temporario)
        if caminho_temporario.stat().st_size == 0:
            raise ErroRel0024("O arquivo enviado está vazio.")
        return importar_rel0024(
            caminho_temporario,
            nome_arquivo=nome_seguro,
        )
    finally:
        caminho_temporario.unlink(missing_ok=True)


def preparar_resultado_web(relatorio):
    validacao = relatorio["validacao"]
    resultado = relatorio["resultado"]
    return {
        "arquivo": relatorio.get("nome_arquivo") or Path(
            resultado["arquivo"]
        ).name,
        "arquivo_ja_importado": relatorio["arquivo_ja_importado"],
        "filial": validacao["filiais"][0],
        "periodo_inicial": validacao["primeira_data"],
        "periodo_final": validacao["ultima_data"],
        "linhas_lidas": resultado.get("linhas_estrutura", 0),
        "linhas_venda": validacao["linhas_venda"],
        "linhas_devolucao": validacao["linhas_devolucao"],
        "linhas_nao_venda": relatorio["linhas_nao_venda"],
        "vendas_lidas": validacao["vendas_distintas"],
        "vendas_novas": relatorio["vendas_inseridas"],
        "vendas_existentes": relatorio["vendas_existentes"],
        "itens_lidos": validacao["itens"],
        "itens_novos": relatorio["itens_inseridos"],
        "itens_existentes": relatorio["itens_existentes"],
        "produtos_lidos": validacao["produtos_distintos"],
        "produtos_novos": relatorio["produtos_inseridos"],
        "produtos_existentes": relatorio["produtos_existentes"],
        "quantidade_lida": validacao["soma_quantidades"],
        "valor_vendas": validacao["valor_vendas"],
        "quantidade_devolucoes": validacao["quantidade_devolucoes"],
        "valor_devolucoes": validacao["valor_devolucoes"],
        "quantidade_liquida": validacao["quantidade_liquida_calculada"],
        "valor_liquido": validacao["valor_liquido_calculado"],
        "quantidade_total_geral": validacao["quantidade_total_geral"],
        "valor_total_geral": validacao["valor_total_geral"],
        "quantidade_acrescentada": relatorio[
            "soma_quantidades_inseridas"
        ],
        "registros_invalidos": validacao["linhas_invalidas"],
        "divergencias_descricao": relatorio["divergencias_descricao"],
        "tempo_segundos": relatorio["tempo_total_segundos"],
    }
