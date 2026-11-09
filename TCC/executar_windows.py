"""Instalação e abertura no Windows, sem alterar o processamento da aplicação."""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

PASTA = Path(__file__).resolve().parent
VERSAO_PYTHON = (3, 14, 6)
VERSAO_POSTGRESQL = "18.6"
VARIAVEIS_BANCO = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
TABELAS = (
    "vendas", "importacoes_rel0024", "produtos_rel0024", "vendas_rel0024",
    "itens_rel0024", "divergencias_descricao_rel0024", "cadastro_produtos_farmacia",
)
PERFIS = {
    "demonstracao": (".env.demonstracao", "dados/demonstracao/cadastro_dcb_farmacia.csv"),
    "original": (".env", "dados/cadastro_dcb_farmacia.csv"),
}


class ErroInicializacao(RuntimeError):
    """Mensagem que pode ser apresentada sem expor credenciais."""


def validar_python():
    if sys.version_info[:3] != VERSAO_PYTHON or struct.calcsize("P") != 8:
        raise ErroInicializacao("Use Python 3.14.6 de 64 bits. Consulte o passo 1 do README.")


def validar_venv(pasta=PASTA):
    if Path(sys.prefix).resolve() != (pasta / ".venv").resolve():
        raise ErroInicializacao("Use INICIAR_SISTEMA.bat ou o Python da pasta .venv deste projeto.")


def validar_dependencias(pasta=PASTA):
    lock = pasta / "requirements-lock.txt"
    if not lock.is_file():
        raise ErroInicializacao("Falta requirements-lock.txt. Extraia a distribuição completa.")
    for linha in lock.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        nome, esperada = linha.split("==", 1)
        try:
            encontrada = importlib.metadata.version(nome)
        except importlib.metadata.PackageNotFoundError:
            raise ErroInicializacao(f"Falta a biblioteca {nome}. Execute INSTALAR.bat.") from None
        if encontrada != esperada:
            raise ErroInicializacao(f"A biblioteca {nome} precisa da versão {esperada}. Execute INSTALAR.bat.")
    try:
        import flask
        import flask_sqlalchemy
        import numpy
        import openpyxl
        import pandas
        import psycopg
        import scipy
        import sklearn
        import sqlalchemy
        import xlrd
    except (ImportError, OSError):
        raise ErroInicializacao("Uma biblioteca não pôde ser carregada. Execute INSTALAR.bat novamente.") from None


def instalar(pasta=PASTA):
    validar_python()
    if not (pasta / "requirements-lock.txt").is_file():
        raise ErroInicializacao("Falta requirements-lock.txt. Extraia a distribuição completa.")
    ambiente = pasta / ".venv"
    python = ambiente / "Scripts" / "python.exe"
    if ambiente.exists():
        if not python.is_file():
            raise ErroInicializacao("A pasta .venv está incompleta. Peça ajuda para recriá-la; nada foi apagado.")
        teste = subprocess.run(
            [str(python), "-I", "-B", "-c",
             "import sys,struct;sys.exit(0 if sys.version_info[:3]==(3,14,6) and struct.calcsize('P')==8 else 1)"],
            capture_output=True,
        )
        if teste.returncode:
            raise ErroInicializacao("A .venv existente usa outro Python. Peça ajuda para recriá-la; nada foi apagado.")
    else:
        print("Criando o ambiente exclusivo deste projeto...", flush=True)
        subprocess.run([sys.executable, "-I", "-B", "-m", "venv", str(ambiente)], check=True)
    print("Instalando as versões originais. Aguarde; esta etapa precisa de internet...", flush=True)
    subprocess.run(
        [str(python), "-I", "-B", "-m", "pip", "--isolated", "--require-virtualenv",
         "install", "--no-user", "--disable-pip-version-check", "-r", str(pasta / "requirements-lock.txt")],
        cwd=pasta, check=True,
    )
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "check"], cwd=pasta, check=True)
    subprocess.run(
        [str(python), "-I", "-B", str(pasta / "executar_windows.py"), "verificar-dependencias"],
        cwd=pasta, check=True,
    )
    print("\nBibliotecas instaladas e conferidas.")
    print("O banco não foi criado nem modificado. Prepare-o conforme o passo 3 do README.")
    print("Depois, abra INICIAR_SISTEMA.bat.")


def ler_configuracao(pasta=PASTA, modo="demonstracao"):
    from dotenv import dotenv_values

    arquivo = pasta / PERFIS[modo][0]
    if not arquivo.is_file():
        raise ErroInicializacao(f"Falta {arquivo.name}. Configure o modo {modo} conforme o README.")
    valores = dotenv_values(arquivo, interpolate=False)
    ausentes = [nome for nome in VARIAVEIS_BANCO if not valores.get(nome)]
    if ausentes:
        raise ErroInicializacao(f"Complete em {arquivo.name}: " + ", ".join(ausentes) + ". Consulte o README.")
    if valores["DB_NAME"] != "tcc_medicamentos":
        raise ErroInicializacao("DB_NAME deve ser tcc_medicamentos. Confira o destino do modo escolhido.")
    if valores["DB_PASSWORD"] == "GERADA_LOCALMENTE_PELO_PREPARADOR":
        raise ErroInicializacao("O .env ainda contém a senha de exemplo. Configure a senha local, conforme o README.")
    try:
        porta = int(valores["DB_PORT"])
        if not 1 <= porta <= 65535:
            raise ValueError
    except ValueError:
        raise ErroInicializacao("DB_PORT deve ser uma porta válida, entre 1 e 65535.") from None
    # O arquivo desta cópia é a fonte de configuração, inclusive em computadores
    # que já tenham variáveis DB_* de outros projetos na sessão do Windows.
    configuracao = {nome: valores[nome] for nome in VARIAVEIS_BANCO}
    for nome in (
        "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
        "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS", "PGTARGETSESSIONATTRS",
    ):
        os.environ.pop(nome, None)
    os.environ.update(configuracao)
    os.environ["TCC_CADASTRO_DCB"] = str((pasta / PERFIS[modo][1]).resolve())
    return configuracao


def validar_arquivos(pasta=PASTA, modo="demonstracao"):
    essenciais = (
        "app.py", "database.py", "templates/nova_analise.html",
        "static/vendor/bootstrap/bootstrap-5.3.8.min.css",
        "static/vendor/bootstrap/bootstrap-5.3.8.bundle.min.js",
        "static/vendor/chartjs/chart-4.5.0.umd.min.js",
        PERFIS[modo][1],
    )
    for nome in essenciais:
        if not (pasta / nome).is_file():
            raise ErroInicializacao(f"Falta {nome}. Confira os arquivos do modo {modo}.")
    try:
        with (pasta / PERFIS[modo][1]).open(encoding="utf-8-sig", newline="") as arquivo:
            leitor = csv.DictReader(arquivo, delimiter=";")
            obrigatorias = {"cod_reduzido", "nom_produto", "cod_dcb", "nom_dcb", "cod_tipo", "nom_tipo"}
            if not obrigatorias.issubset(leitor.fieldnames or ()) or next(leitor, None) is None:
                raise ValueError
    except (OSError, UnicodeError, ValueError, csv.Error):
        raise ErroInicializacao("O cadastro CSV está vazio ou inválido. Use o arquivo da distribuição.") from None


def verificar_postgresql(configuracao, modo="demonstracao", pasta=PASTA):
    import psycopg

    try:
        with psycopg.connect(
            host=configuracao["DB_HOST"], port=int(configuracao["DB_PORT"]),
            dbname=configuracao["DB_NAME"], user=configuracao["DB_USER"],
            password=configuracao["DB_PASSWORD"], connect_timeout=5, autocommit=True,
            application_name="tcc_banca_verificacao",
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        ) as conexao:
            with conexao.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()[0] != "on":
                    raise ErroInicializacao("A verificação do banco não entrou em somente leitura. Abertura cancelada.")
                cursor.execute("SELECT current_database(), current_setting('server_version')")
                banco, versao = cursor.fetchone()
                if banco != "tcc_medicamentos" or versao.split()[0] != VERSAO_POSTGRESQL:
                    raise ErroInicializacao("Use o banco tcc_medicamentos no PostgreSQL 18.6. Abertura cancelada.")
                for tabela in TABELAS:
                    cursor.execute("SELECT to_regclass(%s)", ("public." + tabela,))
                    if cursor.fetchone()[0] is None:
                        raise ErroInicializacao("O banco está incompleto. Restaure o backup demonstrativo, conforme o README.")
                cursor.execute("SELECT cod_reduzido, cod_tipo, nom_tipo, nom_produto FROM public.cadastro_produtos_farmacia LIMIT 1")
                if cursor.fetchone() is None:
                    raise ErroInicializacao("O catálogo está vazio. Restaure o backup demonstrativo, conforme o README.")
                if modo == "demonstracao":
                    cursor.execute("SELECT count(*), count(*) FILTER (WHERE origem='DEMONSTRACAO_CADASTRO_REAL') FROM public.cadastro_produtos_farmacia")
                    if cursor.fetchone() != (120, 120):
                        raise ErroInicializacao("Este banco não é o demonstrativo de 120 produtos. Abertura cancelada; o banco não foi alterado.")
                    with (pasta / PERFIS[modo][1]).open(encoding="utf-8-sig", newline="") as arquivo:
                        produtos = list(csv.DictReader(arquivo, delimiter=";"))
                    esperado = {int(p["cod_reduzido"]): (int(p["cod_tipo"]) if p["cod_tipo"] else None, p["nom_tipo"] or None, p["nom_produto"]) for p in produtos}
                    cursor.execute("SELECT cod_reduzido,cod_tipo,nom_tipo,nom_produto FROM public.cadastro_produtos_farmacia WHERE cod_rede=1")
                    linhas = cursor.fetchall()
                    if len(linhas) != len(produtos) or {r[0]: r[1:] for r in linhas} != esperado:
                        raise ErroInicializacao("O catálogo demonstrativo e o CSV não correspondem. Confira o modo escolhido.")
    except psycopg.Error as erro:
        if erro.sqlstate == "28P01":
            mensagem = "Usuário ou senha do banco recusados. Confira o .env; não compartilhe a senha."
        elif erro.sqlstate == "3D000":
            mensagem = "O banco informado não existe. Prepare ou restaure a demonstração, conforme o README."
        elif erro.sqlstate == "42501":
            mensagem = "O usuário do banco não tem as permissões necessárias. Peça ajuda ao responsável pelo PostgreSQL."
        else:
            mensagem = "Não foi possível consultar o PostgreSQL. Confira endereço, porta, senha, permissões e se o servidor está ligado."
        raise ErroInicializacao(mensagem) from None


def abrir_quando_pronto(url):
    for _ in range(40):
        try:
            with urlopen(url, timeout=1) as resposta:
                if resposta.status == 200:
                    if not webbrowser.open(url, new=2):
                        print("Abra o navegador manualmente no endereço informado acima.")
                    return
        except OSError:
            time.sleep(0.25)
    print("Não consegui abrir o navegador automaticamente. Use o endereço informado acima.")


def iniciar(porta=5000, abrir_navegador=True, pasta=PASTA, modo="demonstracao"):
    validar_python()
    validar_venv(pasta)
    validar_dependencias(pasta)
    validar_arquivos(pasta, modo)
    configuracao = ler_configuracao(pasta, modo)
    print("Conferindo a conexão com PostgreSQL, sem alterar dados...", flush=True)
    verificar_postgresql(configuracao, modo, pasta)
    # Não toma a porta de outro programa nem abre uma página de outro servidor.
    try:
        with socket.socket() as teste:
            teste.bind(("127.0.0.1", porta))
    except OSError:
        raise ErroInicializacao("A porta de abertura está ocupada. Feche somente uma execução anterior deste programa ou consulte o README.") from None
    os.chdir(pasta)
    sys.path.insert(0, str(pasta))
    os.environ["FLASK_DEBUG"] = "0"
    from app import app
    from werkzeug.serving import make_server

    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    servidor = make_server("127.0.0.1", porta, app, threaded=True)
    url = f"http://127.0.0.1:{porta}/nova-analise"
    print("\nSistema iniciado sem debug e sem recarregamento automático.", flush=True)
    print(f"Modo: {modo}.", flush=True)
    print(url, flush=True)
    print("Deixe esta janela aberta. Para encerrar, pressione Ctrl+C.", flush=True)
    if abrir_navegador:
        threading.Thread(target=abrir_quando_pronto, args=(url,), daemon=True).start()
    try:
        servidor.serve_forever()
    finally:
        servidor.server_close()


def main():
    parser = argparse.ArgumentParser(description="Instalação e execução da demonstração no Windows.")
    parser.add_argument("acao", choices=("instalar", "iniciar", "verificar-dependencias"))
    parser.add_argument("--porta", type=int, default=5000, help="Porta web local; padrão: 5000.")
    parser.add_argument("--nao-abrir-navegador", action="store_true", help="Inicia sem abrir uma janela do navegador.")
    parser.add_argument("--modo", choices=tuple(PERFIS), default="demonstracao", help="Demonstração por padrão; original usa o .env e o CSV completos.")
    args = parser.parse_args()
    try:
        if not 1 <= args.porta <= 65535:
            raise ErroInicializacao("A porta web deve estar entre 1 e 65535.")
        if args.acao == "instalar":
            instalar()
        elif args.acao == "verificar-dependencias":
            validar_python()
            validar_venv()
            validar_dependencias()
            print("Python 3.14.6 x64 e todas as bibliotecas conferidos.")
        else:
            iniciar(args.porta, not args.nao_abrir_navegador, modo=args.modo)
    except KeyboardInterrupt:
        print("\nSistema encerrado.")
        return 0
    except ErroInicializacao as erro:
        print("\nERRO: " + str(erro))
        print("O navegador não foi aberto. Consulte o README para corrigir e tente novamente.")
        return 1
    except subprocess.CalledProcessError:
        print("\nERRO: a instalação não terminou. Confira a conexão com a internet e as mensagens acima; execute INSTALAR.bat novamente.")
        return 1
    except Exception:
        print("\nERRO: não foi possível concluir. Confira os arquivos, as permissões e a configuração conforme o README. Nenhuma credencial foi exibida.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
