const formularioRel0024 = document.getElementById("form-rel0024");

if (formularioRel0024) {
    formularioRel0024.addEventListener("submit", () => {
        const botao = document.getElementById("botao-importar-rel0024");
        const estado = document.getElementById("estado-processamento");
        botao.disabled = true;
        botao.textContent = "Processando…";
        estado.hidden = false;
    });
}
