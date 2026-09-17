"""Consultas e preparação dos dados apresentados no dashboard."""

from sqlalchemy import func, select

from database import Venda, db


def _montar_serie(consulta, formatar_rotulo=str):
    """Executa uma consulta agregada e monta a estrutura usada pelo Chart.js."""

    resultados = db.session.execute(consulta).all()
    return {
        "labels": [formatar_rotulo(linha.rotulo) for linha in resultados],
        "valores": [int(linha.total or 0) for linha in resultados],
    }


def _obter_indicadores():
    """Obtém os quatro totais exibidos nos cards."""

    consulta = select(
        func.count(Venda.id).label("total_registros"),
        func.count(func.distinct(Venda.medicamento)).label("medicamentos"),
        func.count(func.distinct(Venda.categoria)).label("categorias"),
        func.count(func.distinct(Venda.cidade)).label("cidades"),
    )
    resultado = db.session.execute(consulta).one()

    return {
        "total_registros": resultado.total_registros,
        "medicamentos": resultado.medicamentos,
        "categorias": resultado.categorias,
        "cidades": resultado.cidades,
    }


def _obter_medicamentos_mais_vendidos():
    """Soma as quantidades e retorna os dez medicamentos com maior volume."""

    total = func.sum(Venda.quantidade).label("total")
    consulta = (
        select(Venda.medicamento.label("rotulo"), total)
        .group_by(Venda.medicamento)
        .order_by(total.desc(), Venda.medicamento)
        .limit(10)
    )
    return _montar_serie(consulta)


def _obter_vendas_por_categoria():
    """Soma as quantidades vendidas de cada categoria."""

    total = func.sum(Venda.quantidade).label("total")
    consulta = (
        select(Venda.categoria.label("rotulo"), total)
        .group_by(Venda.categoria)
        .order_by(total.desc(), Venda.categoria)
    )
    return _montar_serie(consulta)


def _obter_vendas_ao_longo_do_tempo():
    """Soma as quantidades por data em ordem cronológica."""

    total = func.sum(Venda.quantidade).label("total")
    consulta = (
        select(Venda.data.label("rotulo"), total)
        .group_by(Venda.data)
        .order_by(Venda.data)
    )
    return _montar_serie(consulta, lambda data: data.isoformat())


def _obter_vendas_por_cidade():
    """Soma as quantidades vendidas em cada cidade."""

    total = func.sum(Venda.quantidade).label("total")
    consulta = (
        select(Venda.cidade.label("rotulo"), total)
        .group_by(Venda.cidade)
        .order_by(total.desc(), Venda.cidade)
    )
    return _montar_serie(consulta)


def obter_dados_dashboard():
    """Reúne todos os indicadores e séries usados pela página."""

    return {
        "indicadores": _obter_indicadores(),
        "graficos": {
            "medicamentos": _obter_medicamentos_mais_vendidos(),
            "categorias": _obter_vendas_por_categoria(),
            "tempo": _obter_vendas_ao_longo_do_tempo(),
            "cidades": _obter_vendas_por_cidade(),
        },
    }
