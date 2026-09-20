const dadosAnalises = JSON.parse(
    document.getElementById("analises-data").textContent
);

const azul = "#3b789f";
const azulClaro = "rgb(59 120 159 / 18%)";
const cores = ["#244f73", "#3b789f", "#5994b6", "#7cafc8", "#a1c8d8"];

function opcoesPadrao() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
        },
        scales: {
            y: { beginAtZero: true },
        },
    };
}

new Chart(document.getElementById("grafico-mensal"), {
    type: "bar",
    data: {
        labels: dadosAnalises.totais_mensais.labels,
        datasets: [{
            label: "Unidades vendidas",
            data: dadosAnalises.totais_mensais.valores,
            backgroundColor: azul,
            borderRadius: 6,
        }],
    },
    options: opcoesPadrao(),
});

new Chart(document.getElementById("grafico-crescimento"), {
    type: "line",
    data: {
        labels: dadosAnalises.crescimento_mensal.labels,
        datasets: [{
            label: "Crescimento (%)",
            data: dadosAnalises.crescimento_mensal.valores,
            borderColor: azul,
            backgroundColor: azulClaro,
            fill: true,
            tension: 0.2,
            spanGaps: false,
        }],
    },
    options: {
        ...opcoesPadrao(),
        plugins: {
            legend: { display: false },
            tooltip: {
                callbacks: {
                    label: (contexto) => `${contexto.parsed.y.toFixed(2)}%`,
                },
            },
        },
    },
});

new Chart(document.getElementById("grafico-variacao"), {
    type: "bar",
    data: {
        labels: dadosAnalises.variacao_medicamentos.labels,
        datasets: [{
            label: "Desvio-padrão",
            data: dadosAnalises.variacao_medicamentos.valores,
            backgroundColor: cores,
            borderRadius: 6,
        }],
    },
    options: {
        ...opcoesPadrao(),
        indexAxis: "y",
        scales: {
            x: { beginAtZero: true },
        },
    },
});

new Chart(document.getElementById("grafico-cidades"), {
    type: "bar",
    data: {
        labels: dadosAnalises.registros_cidades.labels,
        datasets: [{
            label: "Registros de venda",
            data: dadosAnalises.registros_cidades.valores,
            backgroundColor: cores,
            borderRadius: 6,
        }],
    },
    options: opcoesPadrao(),
});
