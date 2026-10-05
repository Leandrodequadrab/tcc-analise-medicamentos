const formNovaAnalise = document.getElementById("form-nova-analise");
const estadoProcessamento = document.getElementById("estado-processamento");
const botaoAnalisarArquivo = document.getElementById("botao-analisar-arquivo");

if (formNovaAnalise && estadoProcessamento && botaoAnalisarArquivo) {
    formNovaAnalise.addEventListener("submit", () => {
        estadoProcessamento.classList.add("is-visible");
        estadoProcessamento.setAttribute("aria-hidden", "false");
        botaoAnalisarArquivo.disabled = true;
        botaoAnalisarArquivo.textContent = "Analisando…";
    });
}

// Filtros puramente visuais: operam somente nas linhas já presentes na tabela.
const filtrosAumentos = document.getElementById("filtros-aumentos");
const corpoAumentos = document.getElementById("linhas-aumentos");

if (filtrosAumentos && corpoAumentos) {
    const linhasAumentos = Array.from(corpoAumentos.querySelectorAll("tr[data-date]"));
    const busca = document.getElementById("busca-aumentos");
    const classificacao = document.getElementById("classificacao-aumentos");
    const dataInicial = document.getElementById("data-inicial-aumentos");
    const dataFinal = document.getElementById("data-final-aumentos");
    const ordenacao = document.getElementById("ordenacao-aumentos");
    const itensPorPagina = document.getElementById("itens-pagina-aumentos");
    const limpar = document.getElementById("limpar-filtros-aumentos");
    const estado = document.getElementById("estado-filtros-aumentos");
    const anterior = document.getElementById("pagina-anterior-aumentos");
    const proxima = document.getElementById("proxima-pagina-aumentos");
    const paginaAtual = document.getElementById("pagina-atual-aumentos");
    const totalOriginal = Number(filtrosAumentos.dataset.totalOriginal || linhasAumentos.length);
    let pagina = 1;

    const normalizar = (valor) => valor
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLocaleLowerCase("pt-BR");

    Array.from(new Set(linhasAumentos.map((linha) => linha.dataset.classification).filter(Boolean)))
        .sort((a, b) => a.localeCompare(b, "pt-BR"))
        .forEach((valor) => {
            const opcao = document.createElement("option");
            opcao.value = valor;
            opcao.textContent = valor.replaceAll("_", " ");
            classificacao.appendChild(opcao);
        });

    function atualizarTabela() {
        const termo = normalizar(busca.value.trim());
        const tipo = classificacao.value;
        const de = dataInicial.value;
        const ate = dataFinal.value;
        const criterio = ordenacao.value;

        const correspondentes = linhasAumentos.filter((linha) => {
            const buscaLinha = normalizar(`${linha.dataset.product} ${linha.dataset.code}`);
            return (!termo || buscaLinha.includes(termo))
                && (!tipo || linha.dataset.classification === tipo)
                && (!de || linha.dataset.date >= de)
                && (!ate || linha.dataset.date <= ate);
        });

        correspondentes.sort((a, b) => {
            if (criterio === "mais-recente") return b.dataset.date.localeCompare(a.dataset.date);
            if (criterio === "mais-antigo") return a.dataset.date.localeCompare(b.dataset.date);
            return Number(b.dataset.difference) - Number(a.dataset.difference);
        });

        const tamanho = Number(itensPorPagina.value);
        const totalPaginas = Math.ceil(correspondentes.length / tamanho);
        pagina = Math.min(pagina, Math.max(totalPaginas, 1));
        const inicio = (pagina - 1) * tamanho;
        const visiveis = new Set(correspondentes.slice(inicio, inicio + tamanho));

        linhasAumentos.forEach((linha) => {
            linha.hidden = !visiveis.has(linha);
        });

        const limiteLista = linhasAumentos.length < totalOriginal
            ? ` (${linhasAumentos.length} carregadas na lista; ${totalOriginal} detectadas no total)`
            : ` (${totalOriginal} detectadas no total)`;
        estado.textContent = correspondentes.length
            ? `${correspondentes.length} ocorrência(s) correspondem aos filtros${limiteLista}.`
            : `Nenhuma ocorrência corresponde aos filtros${limiteLista}.`;
        paginaAtual.textContent = totalPaginas
            ? `Página ${pagina} de ${totalPaginas} · exibindo ${inicio + 1}–${Math.min(inicio + tamanho, correspondentes.length)} de ${correspondentes.length}`
            : "Nenhum resultado para exibir";
        anterior.disabled = pagina <= 1 || totalPaginas === 0;
        proxima.disabled = pagina >= totalPaginas || totalPaginas === 0;
    }

    [busca, classificacao, dataInicial, dataFinal, ordenacao, itensPorPagina].forEach((controle) => {
        controle.addEventListener("input", () => {
            pagina = 1;
            atualizarTabela();
        });
        controle.addEventListener("change", () => {
            pagina = 1;
            atualizarTabela();
        });
    });
    anterior.addEventListener("click", () => {
        pagina = Math.max(1, pagina - 1);
        atualizarTabela();
    });
    proxima.addEventListener("click", () => {
        pagina += 1;
        atualizarTabela();
    });
    limpar.addEventListener("click", () => {
        busca.value = "";
        classificacao.value = "";
        dataInicial.value = "";
        dataFinal.value = "";
        ordenacao.value = "maior-aumento";
        itensPorPagina.value = "10";
        pagina = 1;
        atualizarTabela();
        busca.focus();
    });

    atualizarTabela();
}
