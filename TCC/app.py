from flask import Flask, render_template, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import db, inicializar_banco, obter_url_banco_aplicacao
from utils.analises import obter_analises
from utils.dashboard import obter_dados_dashboard
from utils.importar_csv import ErroValidacaoCSV, importar_vendas_csv
from utils.ml.comparar_modelos_relativos import ErroModelo
from utils.ml.diagnosticar_series import ErroDiagnostico
from utils.ml.servico_modelo_c import ErroAnaliseIA, executar_analise_ia


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SQLALCHEMY_DATABASE_URI"] = obter_url_banco_aplicacao()

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


@app.route("/analise-ia")
def analise_ia():
    filtros = {
        "filial": request.args.get("filial", ""),
        "codigo_produto": request.args.get("produto", ""),
        "data_inicial": request.args.get("data_inicial", ""),
        "data_final": request.args.get("data_final", ""),
    }
    try:
        dados = executar_analise_ia(**filtros)
    except ErroAnaliseIA as erro:
        return render_template(
            "analise_ia.html",
            dados=None,
            filtros=filtros,
            erro=str(erro),
            status_erro=erro.status_http,
        ), erro.status_http
    except (ErroDiagnostico, ErroModelo, SQLAlchemyError):
        return render_template(
            "analise_ia.html",
            dados=None,
            filtros=filtros,
            erro=(
                "Não foi possível executar a análise. Verifique a conexão "
                "e a disponibilidade dos dados Rel_0024."
            ),
            status_erro=503,
        ), 503

    return render_template(
        "analise_ia.html",
        dados=dados,
        filtros=dados["filtros"],
        erro=None,
        status_erro=None,
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
