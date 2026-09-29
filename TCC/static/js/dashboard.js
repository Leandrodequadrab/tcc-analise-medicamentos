const dashboardData = JSON.parse(
    document.getElementById("dashboard-data").textContent
);
const numberFormat = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const chartColors = ["#176b4d", "#4c9272", "#79b092", "#c08b42", "#768a9b", "#a3adb5"];

function criarGrafico(elemento, tipo, labels, valores, titulo, extras = {}) {
    const canvas = document.getElementById(elemento);
    if (!canvas || typeof Chart === "undefined" || !labels.length) return;
    return new Chart(canvas, {
        type: tipo,
        data: {
            labels,
            datasets: [{
                label: titulo,
                data: valores,
                backgroundColor: tipo === "doughnut" ? chartColors : "rgb(23 107 77 / 18%)",
                borderColor: tipo === "doughnut" ? "#fff" : "#176b4d",
                borderWidth: tipo === "doughnut" ? 2 : 2,
                borderRadius: tipo === "bar" ? 4 : 0,
                fill: tipo === "line",
                tension: .2,
                pointRadius: tipo === "line" ? 3 : 0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: tipo === "doughnut", position: "bottom" } },
            scales: tipo === "doughnut" ? {} : {
                y: { beginAtZero: true, ticks: { precision: 0 } },
                x: { grid: { display: false } },
            },
            ...extras,
        },
    });
}

criarGrafico(
    "grafico-classificacoes",
    "doughnut",
    dashboardData.classificacoes.labels,
    dashboardData.classificacoes.valores,
    "Produtos",
);
criarGrafico(
    "grafico-vendas-mensais",
    "bar",
    dashboardData.vendas_mensais.labels,
    dashboardData.vendas_mensais.valores,
    "Unidades vendidas",
);

function formatarData(valor) {
    const [ano, mes, dia] = valor.split("-");
    return `${dia}/${mes}/${ano}`;
}

function inserirCelula(linha, valor, classe = "") {
    const celula = document.createElement("td");
    celula.textContent = valor;
    if (classe) celula.className = classe;
    linha.appendChild(celula);
}

function mostrarErroIA(mensagem) {
    const estado = document.getElementById("ia-estado");
    estado.className = "alert alert-warning";
    estado.textContent = mensagem;
}

async function carregarResumoIA() {
    const secao = document.getElementById("resumo-ia");
    if (!secao) return;
    if (dashboardData.sem_dados) {
        mostrarErroIA("O resumo por IA ficará disponível após importar dados Rel_0024.");
        return;
    }
    try {
        const resposta = await fetch(secao.dataset.endpoint, {
            headers: { Accept: "application/json" },
        });
        const dados = await resposta.json();
        if (!resposta.ok || !dados.disponivel) {
            mostrarErroIA(dados.mensagem || "A análise por IA está indisponível no momento.");
            return;
        }

        document.querySelectorAll("[data-metrica]").forEach((elemento) => {
            elemento.textContent = numberFormat.format(dados.metricas[elemento.dataset.metrica]);
        });
        document.querySelectorAll("[data-metrica-percentual]").forEach((elemento) => {
            elemento.textContent = `${numberFormat.format(dados.metricas[elemento.dataset.metricaPercentual])}% das observações`;
        });

        criarGrafico(
            "grafico-aumentos-mensais",
            "line",
            dados.aumentos_mensais.labels,
            dados.aumentos_mensais.valores,
            "Aumentos atípicos",
        );

        const corpo = document.getElementById("ia-destaques");
        corpo.replaceChildren();
        if (!dados.destaques.length) {
            const linha = document.createElement("tr");
            const celula = document.createElement("td");
            celula.colSpan = 7;
            celula.className = "text-center text-secondary";
            celula.textContent = "Nenhum aumento atípico encontrado no período.";
            linha.appendChild(celula);
            corpo.appendChild(linha);
        } else {
            dados.destaques.forEach((item) => {
                const linha = document.createElement("tr");
                inserirCelula(linha, formatarData(item.data));
                inserirCelula(linha, item.codigo_produto);
                inserirCelula(linha, item.descricao, "product-description");
                inserirCelula(linha, item.classificacao_operacional);
                inserirCelula(linha, numberFormat.format(item.quantidade_diaria), "text-end");
                inserirCelula(linha, numberFormat.format(item.referencia_historica), "text-end");
                inserirCelula(linha, Number(item.score_samples).toFixed(5), "text-end");
                corpo.appendChild(linha);
            });
        }
        document.getElementById("ia-conteudo").hidden = false;
        const estado = document.getElementById("ia-estado");
        estado.className = "visually-hidden";
        estado.textContent = "Resumo da análise por IA carregado.";
    } catch (_erro) {
        mostrarErroIA("Não foi possível carregar o resumo por IA. Tente novamente mais tarde.");
    }
}

carregarResumoIA();
