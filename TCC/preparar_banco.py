"""Prepara um catálogo demonstrativo de produtos reais, sem histórico comercial."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
import getpass
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import sys

try:
    import psycopg
    from psycopg import sql
except ModuleNotFoundError:
    if __name__ == "__main__":
        print("Instale primeiro as bibliotecas: .\\.venv\\Scripts\\python.exe -m pip install -r requirements-lock.txt")
        raise SystemExit(1)
    raise

PASTA_PROJETO = Path(__file__).resolve().parent
BANCO = "tcc_medicamentos"
USUARIO = "tcc_banca"
HOST = "127.0.0.1"
VERSAO_POSTGRESQL = "18.6"
SHA256_BACKUP = "33b5d8a3541bc77f8fc33e36fdc94fb7400bae372c6a6b84dd64173a9ace68c9"
SHA256_CSV = "6a38527876a6ceafe7152e5ecf0859cf5083e0c67933b179701a8de2be7a4a4a"
NUMERO_PRODUTOS = 120
ORIGEM_CATALOGO = "DEMONSTRACAO_CADASTRO_REAL"
ARQUIVO_ENV_DEMONSTRACAO = ".env.demonstracao"
TABELAS_SEM_HISTORICO = (
    "vendas", "importacoes_rel0024", "produtos_rel0024", "vendas_rel0024",
    "itens_rel0024", "divergencias_descricao_rel0024",
)


class ErroPreparacao(RuntimeError):
    """Mensagem de preparação que pode ser apresentada sem traceback."""


@dataclass(frozen=True)
class Configuracao:
    porta: int
    administrador: str
    senha_administrador: str = field(repr=False)
    pg_bin: Path
    pasta: Path = PASTA_PROJETO


def validar_ambiente(pasta: Path):
    if sys.version_info[:3] != (3, 14, 6) or struct.calcsize("P") * 8 != 64:
        raise ErroPreparacao("Use Python 3.14.6 de 64 bits, conforme o README.")
    for linha in (pasta / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        if not linha.strip() or linha.startswith("#"):
            continue
        nome, esperada = linha.split("==", 1)
        try:
            instalada = importlib.metadata.version(nome)
        except importlib.metadata.PackageNotFoundError:
            raise ErroPreparacao("Instale primeiro as bibliotecas de requirements-lock.txt.") from None
        if instalada != esperada:
            raise ErroPreparacao(f"A biblioteca {nome} precisa estar na versão {esperada}; reinstale pelo lock.")


def conferir_arquivos(pasta: Path):
    if (pasta / ARQUIVO_ENV_DEMONSTRACAO).exists():
        raise ErroPreparacao("Já existe .env.demonstracao nesta pasta. Nada foi substituído. Se já preparou o banco, abra INICIAR_SISTEMA.bat.")
    backup = pasta / "banco" / "demonstracao" / "tcc_medicamentos_demo.backup"
    if not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest() != SHA256_BACKUP:
        raise ErroPreparacao("O backup demonstrativo está ausente ou foi alterado. Use o arquivo original da distribuição.")
    caminho_csv = pasta / "dados" / "demonstracao" / "cadastro_dcb_farmacia.csv"
    if not caminho_csv.is_file() or hashlib.sha256(caminho_csv.read_bytes()).hexdigest() != SHA256_CSV:
        raise ErroPreparacao("O CSV demonstrativo está ausente ou foi alterado. Use o arquivo da distribuição.")
    with caminho_csv.open(encoding="utf-8-sig", newline="") as arquivo:
        produtos = list(csv.DictReader(arquivo, delimiter=";"))
    if len(produtos) != NUMERO_PRODUTOS or len({p["cod_reduzido"] for p in produtos}) != NUMERO_PRODUTOS:
        raise ErroPreparacao("O CSV precisa conter os 120 produtos do catálogo demonstrativo.")
    return backup, produtos


def localizar_ferramentas(informada: str | None):
    if informada:
        return Path(informada)
    padrao = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL" / "18" / "bin"
    if (padrao / "pg_restore.exe").is_file():
        return padrao
    localizado = shutil.which("pg_restore.exe")
    if localizado:
        return Path(localizado).parent
    resposta = input("Não encontrei o PostgreSQL. Informe a pasta que contém pg_restore.exe: ").strip().strip('"')
    if not resposta:
        raise ErroPreparacao("Instale PostgreSQL 18.6 com as ferramentas de linha de comando.")
    return Path(resposta)


def conferir_ferramentas(pg_bin: Path):
    executavel = pg_bin / "pg_restore.exe"
    if not executavel.is_file():
        raise ErroPreparacao("Não encontrei pg_restore.exe na pasta informada.")
    resultado = subprocess.run([str(executavel), "--version"], capture_output=True, text=True, check=True)
    versao = re.search(r"PostgreSQL\)\s+(\S+)", resultado.stdout)
    if not versao or versao.group(1) != VERSAO_POSTGRESQL:
        raise ErroPreparacao("A ferramenta pg_restore precisa ser da versão 18.6.")


def conectar_admin(config: Configuracao, somente_leitura: bool):
    argumentos = dict(host=HOST, hostaddr=HOST, port=config.porta, dbname="postgres", user=config.administrador,
                      password=config.senha_administrador, connect_timeout=5,
                      application_name="tcc_banca_preparacao", autocommit=True)
    if somente_leitura:
        argumentos["options"] = "-c default_transaction_read_only=on -c statement_timeout=10000"
    return psycopg.connect(**argumentos)


def conferir_destino(cursor, config: Configuracao, somente_leitura: bool):
    cursor.execute("SELECT current_database(), host(inet_server_addr()), inet_server_port(), current_setting('server_version'), current_setting('transaction_read_only')")
    banco, host, porta, versao, leitura = cursor.fetchone()
    if banco != "postgres" or host != HOST or porta != config.porta:
        raise ErroPreparacao("A conexão não corresponde à instância local selecionada.")
    if versao.split()[0] != VERSAO_POSTGRESQL:
        raise ErroPreparacao(f"Servidor recusado: é necessário PostgreSQL 18.6; a versão encontrada foi {versao}.")
    if leitura != ("on" if somente_leitura else "off"):
        raise ErroPreparacao("O modo da conexão não permite esta preparação. Nenhuma configuração do servidor foi alterada.")
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (BANCO,))
    if cursor.fetchone()[0]:
        raise ErroPreparacao("Já existe um banco tcc_medicamentos nesta instância. Operação interrompida; nenhum dado existente foi alterado. Use uma instância exclusiva de demonstração.")
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname NOT IN ('postgres','template0','template1'))")
    if cursor.fetchone()[0]:
        raise ErroPreparacao("Esta instância contém outros bancos. Use uma instalação exclusiva de demonstração; nenhum banco existente foi alterado.")
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=%s)", (USUARIO,))
    if cursor.fetchone()[0]:
        raise ErroPreparacao("Já existe um usuário tcc_banca nesta instância. Operação interrompida; o usuário existente não foi alterado.")
    cursor.execute("SELECT rolsuper OR (rolcreatedb AND rolcreaterole) FROM pg_roles WHERE rolname=current_user")
    if not cursor.fetchone()[0]:
        raise ErroPreparacao("O usuário informado precisa poder criar bancos e usuários. Utilize o administrador escolhido no instalador.")


def restaurar_backup(config: Configuracao, backup: Path, senha_programa: str):
    ambiente = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    ambiente.update(PGPASSWORD=senha_programa, PGCLIENTENCODING="UTF8")
    comando = [str(config.pg_bin / "pg_restore.exe"), "-h", HOST, "-p", str(config.porta),
               "-U", USUARIO, "-d", BANCO, "--no-password", "--no-owner", "--no-privileges",
               "--exit-on-error", "--single-transaction", str(backup)]
    resultado = subprocess.run(comando, env=ambiente, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if resultado.returncode:
        detalhe = resultado.stderr.decode("utf-8", errors="replace").replace(senha_programa, "[senha oculta]")
        raise ErroPreparacao("Não foi possível restaurar o cadastro. " + detalhe[:800])


def conferir_restauracao(config: Configuracao, senha_programa: str, produtos):
    with psycopg.connect(host=HOST, hostaddr=HOST, port=config.porta, dbname=BANCO, user=USUARIO,
                         password=senha_programa, connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=10000") as conexao:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT cod_reduzido,cod_tipo,nom_tipo,nom_produto,cod_rede,origem FROM public.cadastro_produtos_farmacia")
            linhas = cursor.fetchall()
            esperado = {int(p["cod_reduzido"]): (int(p["cod_tipo"]) if p["cod_tipo"] else None,
                        p["nom_tipo"] or None, p["nom_produto"], 1, ORIGEM_CATALOGO) for p in produtos}
            if len(linhas) != NUMERO_PRODUTOS or {r[0]: r[1:] for r in linhas} != esperado:
                raise ErroPreparacao("O catálogo restaurado não corresponde aos 120 produtos demonstrativos do CSV.")
            for tabela in TABELAS_SEM_HISTORICO:
                cursor.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(tabela)))
                if cursor.fetchone()[0] != 0:
                    raise ErroPreparacao("O banco restaurado contém histórico inesperado. A configuração não será gravada.")
        conexao.rollback()


def salvar_configuracao(config: Configuracao, senha_programa: str):
    texto = ("# Gerado localmente pelo preparador; não compartilhe este arquivo.\n"
             f"DB_USER={USUARIO}\nDB_PASSWORD=\"{senha_programa}\"\n"
             f"DB_HOST={HOST}\nDB_PORT={config.porta}\nDB_NAME={BANCO}\n")
    # O modo x impede sobrescrever um arquivo criado durante a preparação.
    with (config.pasta / ARQUIVO_ENV_DEMONSTRACAO).open("x", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(texto)


def preparar(config: Configuracao, confirmar, informar=print):
    if not 1 <= config.porta <= 65535 or not config.administrador or not config.senha_administrador:
        raise ErroPreparacao("Informe uma porta válida, o usuário administrador e sua senha.")
    validar_ambiente(config.pasta)
    backup, produtos = conferir_arquivos(config.pasta)
    conferir_ferramentas(config.pg_bin)
    with conectar_admin(config, somente_leitura=True) as conexao:
        with conexao.cursor() as cursor:
            conferir_destino(cursor, config, somente_leitura=True)
    informar(f"PostgreSQL 18.6 confirmado em {HOST}:{config.porta}.")
    informar("Serão criados um banco novo e um usuário exclusivo, com 120 produtos reais e sem vendas reais.")
    if not confirmar():
        informar("Preparação cancelada. Nenhum banco, usuário ou arquivo foi criado.")
        return False
    senha_programa = secrets.token_urlsafe(32)
    criados = []
    try:
        with conectar_admin(config, somente_leitura=False) as conexao:
            with conexao.cursor() as cursor:
                # Reconfere imediatamente antes das criações, sem reutilizar objetos existentes.
                conferir_destino(cursor, config, somente_leitura=False)
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}").format(sql.Identifier(USUARIO), sql.Literal(senha_programa)))
                criados.append("usuário tcc_banca")
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(BANCO), sql.Identifier(USUARIO)))
                criados.append("banco tcc_medicamentos")
        informar("Banco demonstrativo criado. Carregando o recorte cadastral...")
        restaurar_backup(config, backup, senha_programa)
        conferir_restauracao(config, senha_programa, produtos)
        salvar_configuracao(config, senha_programa)
    except Exception as erro:
        mensagem = str(erro).replace(config.senha_administrador, "[senha oculta]").replace(senha_programa, "[senha oculta]")
        aviso = " Objetos novos criados nesta tentativa: " + ", ".join(criados) + "." if criados else ""
        raise ErroPreparacao("A preparação não foi concluída." + aviso + " Nenhum objeto preexistente foi substituído. Não apague nada; procure ajuda com esta mensagem: " + mensagem) from None
    informar("Cadastro demonstrativo de 120 produtos carregado e conferido.")
    informar("Arquivo .env.demonstracao criado; seu .env original foi preservado e a senha do administrador não foi gravada.")
    informar("Tudo pronto! Siga no README a etapa de abrir o programa.")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepara uma base demonstrativa nova com produtos reais e sem vendas reais.")
    parser.add_argument("--porta", type=int, help="Porta da instância demonstrativa; o padrão sugerido é 5432.")
    parser.add_argument("--usuario", help="Administrador local; o padrão sugerido é postgres.")
    parser.add_argument("--pg-bin", help="Pasta que contém pg_restore.exe do PostgreSQL 18.6.")
    args = parser.parse_args(argv)
    senha = ""
    try:
        if (PASTA_PROJETO / ARQUIVO_ENV_DEMONSTRACAO).exists():
            raise ErroPreparacao("Já existe .env.demonstracao nesta pasta. Nada foi alterado. Abra INICIAR_SISTEMA.bat para usar a demonstração.")
        if any(os.getenv(nome) for nome in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")):
            raise ErroPreparacao("Há variáveis DB_* já definidas nesta sessão. Elas podem fazer a aplicação usar outra conexão. Confira no guia técnico como removê-las somente da sessão antes de continuar.")
        porta = args.porta if args.porta is not None else int(input("Porta do PostgreSQL [5432]: ").strip() or "5432")
        usuario = args.usuario or input("Usuário administrador [postgres]: ").strip() or "postgres"
        pg_bin = localizar_ferramentas(args.pg_bin)
        senha = getpass.getpass("Senha escolhida na instalação do PostgreSQL: ")
        config = Configuracao(porta, usuario, senha, pg_bin, PASTA_PROJETO)
        ok = preparar(config, confirmar=lambda: input("Criar a demonstração nesta instância? Digite SIM para continuar: ").strip().casefold() == "sim")
        return 0 if ok else 1
    except KeyboardInterrupt:
        print("\nPreparação interrompida. Se a criação já havia começado, não repita nem apague objetos; peça ajuda para conferir o estado.")
        return 130
    except Exception as erro:
        mensagem = str(erro)
        if senha:
            mensagem = mensagem.replace(senha, "[senha oculta]")
        print("\nNão foi possível preparar o banco: " + mensagem)
        print("Nenhum serviço PostgreSQL foi parado, reiniciado ou reconfigurado.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
