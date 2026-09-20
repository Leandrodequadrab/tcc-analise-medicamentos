import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError

from database import db, inicializar_banco
from utils.analises import obter_analises
from utils.dashboard import obter_dados_dashboard
from utils.importar_csv import ErroValidacaoCSV, importar_vendas_csv


PASTA_PROJETO = Path(__file__).resolve().parent
load_dotenv(PASTA_PROJETO / ".env")


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

variaveis_obrigatorias = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")
variaveis_ausentes = [nome for nome in variaveis_obrigatorias if not os.getenv(nome)]

if variaveis_ausentes:
    raise RuntimeError(
        "Configure as seguintes variáveis no arquivo .env: "
        + ", ".join(variaveis_ausentes)
    )

app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
    drivername="postgresql+psycopg",
    username=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT")),
    database=os.getenv("DB_NAME"),
)

inicializar_banco(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    dados = obter_dados_dashboard()
    return render_template(
        "dashboard.html",
        indicadores=dados["indicadores"],
        graficos=dados["graficos"],
    )


@app.route("/analises")
def analises():
    dados = obter_analises()
    return render_template(
        "analises.html",
        resumo=dados["resumo"],
        graficos=dados["graficos"],
        crescimento_mensal=dados["crescimento_mensal"],
    )


@app.post("/importar")
def importar_csv():
    if "arquivo" not in request.files:
        return render_template(
            "index.html", erro="Nenhum arquivo foi enviado."
        ), 400

    arquivo = request.files["arquivo"]

    if not arquivo.filename:
        return render_template(
            "index.html", erro="Selecione um arquivo CSV."
        ), 400

    if not arquivo.filename.lower().endswith(".csv"):
        return render_template(
            "index.html", erro="O arquivo selecionado deve possuir a extensão .csv."
        ), 400

    try:
        quantidade = importar_vendas_csv(arquivo)
    except ErroValidacaoCSV as erro:
        return render_template("index.html", erro=str(erro)), 400
    except SQLAlchemyError:
        return render_template(
            "index.html",
            erro="Não foi possível salvar os registros no banco de dados.",
        ), 500

    return render_template(
        "index.html",
        sucesso=f"Arquivo importado com sucesso. {quantidade} registros adicionados.",
    )


@app.errorhandler(413)
def arquivo_muito_grande(_erro):
    return render_template(
        "index.html", erro="O arquivo ultrapassa o limite de 5 MB."
    ), 413


@app.route("/teste-conexao")
def teste_conexao():
    db.session.execute(text("SELECT 1"))
    return {"mensagem": "Conexão com PostgreSQL realizada com sucesso."}


if __name__ == "__main__":
    app.run(debug=True)
