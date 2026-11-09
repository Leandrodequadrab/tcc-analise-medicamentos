"""Copia código e demonstração para entrega, sem credenciais nem vendas reais."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

PASTA = Path(__file__).resolve().parent
PASTAS_CODIGO = {"utils", "models", "tests", "tests_portabilidade", "templates", "static", "documentacao"}
EXTENSOES_CODIGO = {".py", ".html", ".css", ".js", ".md", ".json", ".sha256", ".txt"}
EXEMPLOS_ENV = {".env.demonstracao.example", ".env.original.example"}
CSV_FICTICIOS = {"dados/vendas_ficticias.csv", "dados/vendas_com_problemas.csv"}


def arquivos_para_entrega(pasta=PASTA):
    arquivos = []
    candidatos = []
    for diretorio, pastas, nomes in os.walk(pasta):
        pastas[:] = [nome for nome in pastas if nome not in {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "uploads"}]
        if Path(diretorio) == pasta:
            pastas[:] = [nome for nome in pastas if nome in PASTAS_CODIGO | {"dados", "banco"}]
        candidatos.extend(Path(diretorio) / nome for nome in nomes)
    for arquivo in candidatos:
        relativo = arquivo.relative_to(pasta)
        if any(parte in {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "uploads"} for parte in relativo.parts):
            continue
        nome = relativo.as_posix()
        if len(relativo.parts) == 1:
            incluir = arquivo.name in EXEMPLOS_ENV or arquivo.suffix in {".py", ".bat", ".md"} or arquivo.name in {"requirements.txt", "requirements-lock.txt", ".gitignore"}
        elif nome in CSV_FICTICIOS:
            incluir = True
        elif relativo.parts[:2] == ("dados", "demonstracao"):
            incluir = arquivo.suffix in {".csv", ".xlsx"}
        elif relativo.parts[:2] == ("banco", "demonstracao"):
            incluir = arquivo.name == "tcc_medicamentos_demo.backup"
        else:
            incluir = relativo.parts[0] in PASTAS_CODIGO and (arquivo.suffix in EXTENSOES_CODIGO or arquivo.name in {"LICENSE", "LICENSE.md", ".gitkeep"})
        if incluir:
            arquivos.append(relativo)
    return sorted(arquivos)


def preparar_entrega(destino, pasta=PASTA):
    origem = pasta.resolve()
    destino = Path(destino).resolve()
    if destino == origem or origem in destino.parents:
        raise ValueError("Escolha uma pasta fora do TCC, para não copiar o projeto para dentro dele mesmo.")
    if destino.exists():
        raise ValueError("A pasta de entrega já existe. Nada foi substituído; escolha outro nome.")
    arquivos = arquivos_para_entrega(origem)
    destino.mkdir(parents=True, exist_ok=False)
    for relativo in arquivos:
        alvo = destino / relativo
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem / relativo, alvo)
    return len(arquivos)


def main():
    parser = argparse.ArgumentParser(description="Gera uma cópia segura para entrega; não cria ZIP nem acessa bancos.")
    parser.add_argument("--destino", type=Path, default=PASTA.parent / "TCC_ENTREGA")
    args = parser.parse_args()
    try:
        numero = preparar_entrega(args.destino)
    except (OSError, ValueError) as erro:
        print("Não foi possível preparar a entrega: " + str(erro))
        return 1
    print(f"Cópia de entrega criada: {args.destino}")
    print(f"{numero} arquivos: código, testes e demonstração. Sem .env, .venv, CSV cadastral completo ou relatórios reais.")
    print("Nenhum banco foi acessado e nenhum arquivo do projeto foi excluído. O ZIP não foi criado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
