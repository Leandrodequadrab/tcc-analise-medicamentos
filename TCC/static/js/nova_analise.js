const formNovaAnalise = document.getElementById("form-nova-analise");
const estadoProcessamento = document.getElementById("estado-processamento");
const botaoAnalisarArquivo = document.getElementById("botao-analisar-arquivo");
const mensagemProcessamento = document.getElementById("mensagem-processamento");

function dataParaTimestampUTC(valor) {
    const correspondencia = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(valor || ""));
    if (!correspondencia) return null;
    const [, ano, mes, dia] = correspondencia;
    const data = new Date(Date.UTC(Number(ano), Number(mes) - 1, Number(dia)));
    if (data.getUTCFullYear() !== Number(ano)
        || data.getUTCMonth() !== Number(mes) - 1
        || data.getUTCDate() !== Number(dia)) return null;
    return data.getTime();
}

function numeroDeAtributo(valor) {
    let texto = String(valor ?? "").trim();
    if (!texto) return null;
    if (texto.includes(",")) {
        texto = texto.includes(".")
            ? texto.replace(/\./g, "").replace(",", ".")
            : texto.replace(",", ".");
    }
    const numero = Number(texto);
    return Number.isFinite(numero) ? numero : null;
}

function dataDentroDoPeriodo(valor, dataInicial, dataFinal) {
    const data = dataParaTimestampUTC(valor);
    if (data === null) return !dataInicial && !dataFinal;
    const inicio = dataInicial ? dataParaTimestampUTC(dataInicial) : null;
    const fim = dataFinal ? dataParaTimestampUTC(dataFinal) : null;
    return (!dataInicial || (inicio !== null && data >= inicio))
        && (!dataFinal || (fim !== null && data <= fim));
}

function compararLinhasOrdenadas(a, b, ordenacaoAtual) {
    const multiplicador = ordenacaoAtual.direcao === "desc" ? -1 : 1;
    if (ordenacaoAtual.campo === "data") {
        const dataA = dataParaTimestampUTC(a.dataset.data);
        const dataB = dataParaTimestampUTC(b.dataset.data);
        if (dataA === null || dataB === null) {
            return dataA === dataB ? 0 : dataA === null ? 1 : -1;
        }
        return (dataA - dataB) * multiplicador;
    }
    const camposInternos = {
        quantidade: "quantidade",
        media: "media",
        diferenca: "diferenca",
    };
    const campoInterno = camposInternos[ordenacaoAtual.campo];
    const valorA = numeroDeAtributo(a.dataset[campoInterno]);
    const valorB = numeroDeAtributo(b.dataset[campoInterno]);
    if (valorA === null || valorB === null) {
        return valorA === valorB ? 0 : valorA === null ? 1 : -1;
    }
    return (valorA - valorB) * multiplicador;
}

function alternarOrdenacao(ordenacaoAtual, campo) {
    if (ordenacaoAtual.campo === campo) {
        return {
            campo,
            direcao: ordenacaoAtual.direcao === "desc" ? "asc" : "desc",
        };
    }
    return { campo, direcao: "desc" };
}

function indicadorOrdenacao(ordenacaoAtual, campo) {
    if (ordenacaoAtual.campo !== campo) {
        return { ariaSort: "none", simbolo: "↕" };
    }
    return ordenacaoAtual.direcao === "desc"
        ? { ariaSort: "descending", simbolo: "▼" }
        : { ariaSort: "ascending", simbolo: "▲" };
}

function normalizarTextoFiltro(valor) {
    return String(valor || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLocaleLowerCase("pt-BR");
}

function filtrarOrdenarPaginar(linhas, filtros, ordenacaoAtual, pagina, tamanho) {
    const termo = normalizarTextoFiltro(filtros.termo);
    const correspondentes = linhas.filter((linha) => {
        const buscaLinha = normalizarTextoFiltro(
            `${linha.dataset.product || ""} ${linha.dataset.code || ""}`
        );
        const classificacoesLinha = (linha.dataset.classification || "").split("|");
        return (!termo || buscaLinha.includes(termo))
            && (!filtros.tipo || linha.dataset.type === filtros.tipo)
            && (!filtros.classificacao || classificacoesLinha.includes(filtros.classificacao))
            && dataDentroDoPeriodo(
                linha.dataset.data,
                filtros.dataInicial,
                filtros.dataFinal
            );
    });

    if (ordenacaoAtual.campo) {
        correspondentes.sort((a, b) => compararLinhasOrdenadas(a, b, ordenacaoAtual));
    }

    const totalPaginas = Math.ceil(correspondentes.length / tamanho);
    const paginaValida = Math.min(Math.max(pagina, 1), Math.max(totalPaginas, 1));
    const inicio = (paginaValida - 1) * tamanho;
    return {
        correspondentes,
        pagina: paginaValida,
        totalPaginas,
        inicio,
        linhasPagina: correspondentes.slice(inicio, inicio + tamanho),
    };
}

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
    const linhasAumentos = Array.from(corpoAumentos.querySelectorAll("tr[data-data]"));
    const busca = document.getElementById("busca-aumentos");
    const tipoChave = document.getElementById("tipo-chave-aumentos");
    const classificacao = document.getElementById("classificacao-aumentos");
    const dataInicial = document.getElementById("data-inicial-aumentos");
    const dataFinal = document.getElementById("data-final-aumentos");
    const itensPorPagina = document.getElementById("itens-pagina-aumentos");
    const limpar = document.getElementById("limpar-filtros-aumentos");
    const estado = document.getElementById("estado-filtros-aumentos");
    const anterior = document.getElementById("pagina-anterior-aumentos");
    const proxima = document.getElementById("proxima-pagina-aumentos");
    const paginaAtual = document.getElementById("pagina-atual-aumentos");
    const totalOriginal = Number(filtrosAumentos.dataset.totalOriginal || linhasAumentos.length);
    const botoesOrdenacao = Array.from(
        document.querySelectorAll("[data-sort-column]")
    );
    let ordenacaoAtual = { campo: null, direcao: "desc" };
    let pagina = 1;

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

    function atualizarIndicadoresOrdenacao() {
        botoesOrdenacao.forEach((botao) => {
            const cabecalho = botao.closest("th");
            const indicador = botao.querySelector(".table-sort-indicator");
            const visual = indicadorOrdenacao(
                ordenacaoAtual,
                botao.dataset.sortColumn
            );
            if (cabecalho) {
                cabecalho.setAttribute("aria-sort", visual.ariaSort);
            }
            if (indicador) {
                indicador.textContent = visual.simbolo;
            }
        });
    }

    function atualizarTabela() {
        const tamanho = Number(itensPorPagina.value);
        const resultado = filtrarOrdenarPaginar(
            linhasAumentos,
            {
                termo: busca.value,
                tipo: tipoChave.value,
                classificacao: classificacao.value,
                dataInicial: dataInicial.value,
                dataFinal: dataFinal.value,
            },
            ordenacaoAtual,
            pagina,
            tamanho
        );
        pagina = resultado.pagina;
        const { correspondentes, totalPaginas, inicio, linhasPagina } = resultado;
        const visiveis = new Set(linhasPagina);

        correspondentes.forEach((linha) => {
            corpoAumentos.appendChild(linha);
        });

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

    botoesOrdenacao.forEach((botao) => {
        botao.addEventListener("click", () => {
            ordenacaoAtual = alternarOrdenacao(
                ordenacaoAtual,
                botao.dataset.sortColumn
            );
            pagina = 1;
            atualizarIndicadoresOrdenacao();
            atualizarTabela();
        });
    });
    [busca, tipoChave, classificacao, dataInicial, dataFinal, itensPorPagina].forEach((controle) => {
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
        itensPorPagina.value = "10";
        ordenacaoAtual = { campo: "data", direcao: "desc" };
        atualizarIndicadoresOrdenacao();
        pagina = 1;
        atualizarTabela();
        busca.focus();
    });

    atualizarIndicadoresOrdenacao();
    atualizarTabela();
}
