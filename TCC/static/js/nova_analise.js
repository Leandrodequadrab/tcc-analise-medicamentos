const formNovaAnalise = document.getElementById("form-nova-analise");
const estadoProcessamento = document.getElementById("estado-processamento");
const botaoAnalisarArquivo = document.getElementById("botao-analisar-arquivo");
const mensagemProcessamento = document.getElementById("mensagem-processamento");

if (formNovaAnalise && estadoProcessamento && botaoAnalisarArquivo) {
    formNovaAnalise.addEventListener("submit", () => {
        estadoProcessamento.classList.add("is-visible");
        estadoProcessamento.setAttribute("aria-hidden", "false");
        botaoAnalisarArquivo.disabled = true;
        botaoAnalisarArquivo.textContent = "Analisando…";

        if (mensagemProcessamento) {
            const mensagens = [
                "Processando relatório...",
                "Identificando medicamentos...",
                "Organizando histórico de vendas...",
                "Executando análise de padrões...",
                "Preparando resultados...",
            ];
            let indice = 0;
            const intervalo = window.setInterval(() => {
                indice = (indice + 1) % mensagens.length;
                mensagemProcessamento.classList.add("is-changing");
                window.setTimeout(() => {
                    mensagemProcessamento.textContent = mensagens[indice];
                    mensagemProcessamento.classList.remove("is-changing");
                }, 180);
            }, 2200);
            window.addEventListener("pagehide", () => window.clearInterval(intervalo), { once: true });
        }
    });
}

const filtrosAumentos = document.getElementById("filtros-aumentos");
const corpoAumentos = document.getElementById("linhas-aumentos");

if (filtrosAumentos && corpoAumentos) {
    const linhasAumentos = Array.from(corpoAumentos.querySelectorAll("tr[data-date]"));
    const busca = document.getElementById("busca-aumentos");
    const tipoChave = document.getElementById("tipo-chave-aumentos");
    const classificacao = document.getElementById("classificacao-aumentos");
    const dataInicial = document.getElementById("data-inicial-aumentos");
    const dataFinal = document.getElementById("data-final-aumentos");
    const campoOrdenacao = document.getElementById("campo-ordenacao-aumentos");
    const direcaoOrdenacao = document.getElementById("direcao-ordenacao-aumentos");
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

    Array.from(new Set(linhasAumentos.map((linha) => linha.dataset.type).filter(Boolean)))
        .sort((a, b) => a.localeCompare(b, "pt-BR"))
        .forEach((valor) => {
            const opcao = document.createElement("option");
            opcao.value = valor;
            opcao.textContent = valor;
            tipoChave.appendChild(opcao);
        });

    const classificacoes = new Set(linhasAumentos.flatMap((linha) =>
        (linha.dataset.classification || "").split("|").filter(Boolean)
    ));
    Array.from(classificacoes)
        .sort((a, b) => a.localeCompare(b, "pt-BR"))
        .forEach((valor) => {
            const opcao = document.createElement("option");
            opcao.value = valor;
            opcao.textContent = valor.replaceAll("_", " ");
            classificacao.appendChild(opcao);
        });

    function atualizarRotuloDirecao() {
        const eData = campoOrdenacao.value === "date";
        direcaoOrdenacao.options[0].textContent = eData ? "Mais recente" : "Maior → menor";
        direcaoOrdenacao.options[1].textContent = eData ? "Mais antigo" : "Menor → maior";
    }

    function atualizarTabela() {
        const termo = normalizar(busca.value.trim());
        const tipoSelecionado = tipoChave.value;
        const tipo = classificacao.value;
        const de = dataInicial.value;
        const ate = dataFinal.value;
        const campo = campoOrdenacao.value;
        const multiplicador = direcaoOrdenacao.value === "asc" ? 1 : -1;

        const correspondentes = linhasAumentos.filter((linha) => {
            const buscaLinha = normalizar(`${linha.dataset.product} ${linha.dataset.code}`);
            const classificacoesLinha = (linha.dataset.classification || "").split("|");
            return (!termo || buscaLinha.includes(termo))
                && (!tipoSelecionado || linha.dataset.type === tipoSelecionado)
                && (!tipo || classificacoesLinha.includes(tipo))
                && (!de || linha.dataset.date >= de)
                && (!ate || linha.dataset.date <= ate);
        });

        correspondentes.sort((a, b) => {
            if (campo === "date") return a.dataset.date.localeCompare(b.dataset.date) * multiplicador;
            return (Number(a.dataset[campo]) - Number(b.dataset[campo])) * multiplicador;
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

    campoOrdenacao.addEventListener("change", () => {
        atualizarRotuloDirecao();
        direcaoOrdenacao.value = "desc";
        pagina = 1;
        atualizarTabela();
    });
    [busca, tipoChave, classificacao, dataInicial, dataFinal, direcaoOrdenacao, itensPorPagina].forEach((controle) => {
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
        tipoChave.value = "";
        classificacao.value = "";
        dataInicial.value = "";
        dataFinal.value = "";
        campoOrdenacao.value = "quantity";
        direcaoOrdenacao.value = "desc";
        atualizarRotuloDirecao();
        itensPorPagina.value = "10";
        pagina = 1;
        atualizarTabela();
        busca.focus();
    });

    atualizarRotuloDirecao();
    atualizarTabela();
}
