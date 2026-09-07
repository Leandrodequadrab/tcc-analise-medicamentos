import os

from dotenv import load_dotenv
from flask import Flask, render_template
from sqlalchemy import URL, text

from database import db, inicializar_banco


load_dotenv()


app = Flask(__name__)

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


@app.route("/teste-conexao")
def teste_conexao():
    db.session.execute(text("SELECT 1"))
    return {"mensagem": "Conexão com PostgreSQL realizada com sucesso."}


if __name__ == "__main__":
    app.run(debug=True)
