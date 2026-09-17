const dadosDashboard = JSON.parse(
    document.getElementById("dashboard-data").textContent
);

const paleta = [
    "#176b4d",
    "#2f8f68",
    "#53a77f",
    "#76b99a",
    "#99cbb5",
    "#3f6f8f",
    "#6c79a8",
    "#9a7caf",
];

function obterCores(quantidade) {
    return Array.from(
        { length: quantidade },
        (_, indice) => paleta[indice % paleta.length]
    );
}

function criarGraficoBarras(elemento, serie, opcoesExtras = {}) {
    return new Chart(document.getElementById(elemento), {
        type: "bar",
        data: {
            labels: serie.labels,
            datasets: [{
                label: "Unidades vendidas",
                data: serie.valores,
                backgroundColor: obterCores(serie.valores.length),
                borderRadius: 5,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                },
            },
            ...opcoesExtras,
        },
    });
}

criarGraficoBarras(
    "grafico-medicamentos",
    dadosDashboard.medicamentos,
    {
        indexAxis: "y",
        scales: {
            x: { beginAtZero: true, ticks: { precision: 0 } },
        },
    }
);

new Chart(document.getElementById("grafico-categorias"), {
    type: "doughnut",
    data: {
        labels: dadosDashboard.categorias.labels,
        datasets: [{
            label: "Unidades vendidas",
            data: dadosDashboard.categorias.valores,
            backgroundColor: obterCores(dadosDashboard.categorias.valores.length),
            borderWidth: 2,
        }],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
    },
});

new Chart(document.getElementById("grafico-tempo"), {
    type: "line",
    data: {
        labels: dadosDashboard.tempo.labels,
        datasets: [{
            label: "Unidades vendidas",
            data: dadosDashboard.tempo.valores,
            borderColor: "#176b4d",
            backgroundColor: "rgb(23 107 77 / 15%)",
            fill: true,
            tension: 0.25,
        }],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: { beginAtZero: true, ticks: { precision: 0 } },
        },
    },
});

criarGraficoBarras("grafico-cidades", dadosDashboard.cidades);
