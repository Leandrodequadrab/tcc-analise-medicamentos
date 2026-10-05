from pathlib import Path

from flask import Flask, jsonify, render_template, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import db, inicializar_banco, obter_url_banco_aplicacao
from utils.analises import obter_analises
from utils.dashboard import (
    criar_dashboard_vazio,
    obter_dados_dashboard,
    obter_resumo_ia_dashboard,
)
from utils.importar_csv import ErroValidacaoCSV, importar_vendas_csv
from utils.dados_reais.importar_rel0024 import ErroImportacaoRel0024
from utils.dados_reais.servico_upload_rel0024 import (
    preparar_resultado_web,
    processar_upload_rel0024,
)
from utils.dados_reais.servico_nova_analise import (
    ErroNovaAnalise,
    analisar_upload_rel0024,
)
from utils.dados_reais.validar_rel0024 import ErroRel0024
from utils.ml.comparar_modelos_relativos import ErroModelo
from utils.ml.diagnosticar_series import ErroDiagnostico
from utils.ml.servico_modelo_c import ErroAnaliseIA, executar_analise_ia


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(Path(__file__).resolve().parent / "uploads")
app.config["SQLALCHEMY_DATABASE_URI"] = obter_url_banco_aplicacao()

inicializar_banco(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    try:
        dados = obter_dados_dashboard()
    except SQLAlchemyError:
        app.logger.exception("Falha ao consultar as tabelas do Rel_0024 para o dashboard")
        dados = criar_dashboard_vazio(
            "Não foi possível consultar os dados Rel_0024. Verifique a conexão com o banco."
        )
    return render_template("dashboard.html", dados=dados)


@app.get("/api/dashboard/resumo-ia")
def api_dashboard_resumo_ia():
    """Carrega o resumo do Modelo C após a renderização descritiva do dashboard."""

    try:
        return jsonify(obter_resumo_ia_dashboard())
    except ErroAnaliseIA as erro:
        return jsonify({"disponivel": False, "mensagem": str(erro)}), erro.status_http
    except (ErroDiagnostico, ErroModelo, SQLAlchemyError):
        app.logger.exception("Falha ao executar o resumo do Modelo C no dashboard")
        return jsonify(
            {
                "disponivel": False,
                "mensagem": (
                    "Não foi possível carregar o resumo da análise por IA. "
                    "Confira se há dados Rel_0024 disponíveis."
                ),
            }
        ), 503
    except Exception:
        app.logger.exception("Erro inesperado ao montar o resumo do Modelo C")
        return jsonify(
            {
                "disponivel": False,
                "mensagem": "O resumo da análise por IA não pôde ser carregado.",
            }
        ), 503


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


@app.route("/nova-analise", methods=["GET", "POST"])
def nova_analise():
    if request.method == "GET":
        return render_template("nova_analise.html", resultado=None, erro=None)

    try:
        resultado = analisar_upload_rel0024(request.files.get("arquivo"))
    except ErroNovaAnalise as erro:
        return render_template(
            "nova_analise.html", resultado=None, erro=str(erro)
        ), erro.status_http
    except ErroRel0024 as erro:
        return render_template(
            "nova_analise.html",
            resultado=None,
            erro=f"Relatório inválido: {erro}",
        ), 400
    except (ErroDiagnostico, ErroModelo, SQLAlchemyError):
        app.logger.exception("Falha ao executar análise Rel_0024 independente")
        return render_template(
            "nova_analise.html",
            resultado=None,
            erro=(
                "Não foi possível consultar o catálogo local ou executar o modelo. "
                "Nenhum dado de venda foi salvo."
            ),
        ), 503
    except Exception:
        app.logger.exception("Erro inesperado na análise Rel_0024 independente")
        return render_template(
            "nova_analise.html",
            resultado=None,
            erro="O arquivo não pôde ser analisado. Nenhum dado foi salvo.",
        ), 500

    return render_template(
        "nova_analise.html", resultado=resultado, erro=None
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


@app.route("/importar-rel0024", methods=["GET", "POST"])
def importar_rel0024_web():
    if request.method == "GET":
        return render_template(
            "importar_rel0024.html", resultado=None, erro=None
        )

    arquivo = request.files.get("arquivo")
    try:
        relatorio = processar_upload_rel0024(
            arquivo, app.config["UPLOAD_FOLDER"]
        )
        resultado = preparar_resultado_web(relatorio)
    except ErroRel0024 as erro:
        return render_template(
            "importar_rel0024.html", resultado=None, erro=str(erro)
        ), 400
    except ErroImportacaoRel0024 as erro:
        return render_template(
            "importar_rel0024.html", resultado=None, erro=str(erro)
        ), 409
    except (SQLAlchemyError, OSError):
        return render_template(
            "importar_rel0024.html",
            resultado=None,
            erro=(
                "Não foi possível concluir a importação. Nenhum dado parcial "
                "foi confirmado; verifique o arquivo e a conexão com o banco."
            ),
        ), 500
    except Exception:
        app.logger.exception("Falha interna no upload Rel_0024")
        return render_template(
            "importar_rel0024.html",
            resultado=None,
            erro=(
                "O arquivo não pôde ser processado com segurança. Nenhum "
                "dado parcial foi confirmado."
            ),
        ), 500

    return render_template(
        "importar_rel0024.html", resultado=resultado, erro=None
    )


@app.errorhandler(413)
def arquivo_muito_grande(_erro):
    if request.path.startswith("/nova-analise"):
        return render_template(
            "nova_analise.html",
            resultado=None,
            erro="O arquivo ultrapassa o limite permitido de 50 MB.",
        ), 413
    if request.path.startswith("/importar-rel0024"):
        return render_template(
            "importar_rel0024.html",
            resultado=None,
            erro="O arquivo ultrapassa o limite permitido de 50 MB.",
        ), 413
    return render_template(
        "index.html", erro="O arquivo ultrapassa o limite de 50 MB."
    ), 413


@app.route("/teste-conexao")
def teste_conexao():
    db.session.execute(text("SELECT 1"))
    return {"mensagem": "Conexão com PostgreSQL realizada com sucesso."}


if __name__ == "__main__":
    app.run(debug=True)
