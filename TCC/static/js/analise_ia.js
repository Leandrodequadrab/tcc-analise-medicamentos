const formulario = document.getElementById("form-analise-ia");
const overlay = document.getElementById("loading-analise");
const botaoAnalisar = document.getElementById("botao-analisar");

function mostrarProcessamento() {
    overlay.classList.add("is-visible");
    overlay.setAttribute("aria-hidden", "false");
    if (botaoAnalisar) {
        botaoAnalisar.disabled = true;
        botaoAnalisar.textContent = "Executando...";
    }
}

if (formulario) formulario.addEventListener("submit", mostrarProcessamento);
document.querySelectorAll("[data-executa-analise]").forEach((link) => link.addEventListener("click", mostrarProcessamento));

const elementoDados = document.getElementById("analise-ia-data");
const elementoGrafico = document.getElementById("grafico-serie-produto");

if (elementoDados && elementoGrafico) {
    const serie = JSON.parse(elementoDados.textContent);
    new Chart(elementoGrafico, {
        type: "line",
        data: {
            labels: serie.labels,
            datasets: [
                { label: "Quantidade diária", data: serie.quantidades, borderColor: "#176b4d", backgroundColor: "rgb(23 107 77 / 10%)", borderWidth: 2, pointRadius: 1.5, fill: true, tension: .16, order: 2 },
                { label: "Referência histórica", data: serie.referencias, borderColor: "#3f6f8f", borderDash: [7, 5], borderWidth: 2, pointRadius: 0, spanGaps: true, tension: .16, order: 3 },
                { label: "Aumento atípico", data: serie.aumentos_atipicos, borderColor: "#b83a3a", backgroundColor: "#b83a3a", showLine: false, pointRadius: 6, pointHoverRadius: 8, order: 1 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: (contexto) => contexto.parsed.y === null ? "" : `${contexto.dataset.label}: ${contexto.parsed.y.toFixed(2)}` } } },
            scales: { x: { ticks: { maxTicksLimit: 12, maxRotation: 0 }, grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: "Unidades vendidas" } } },
        },
    });
}
