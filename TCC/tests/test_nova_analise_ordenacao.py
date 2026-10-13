from pathlib import Path
import subprocess
import unittest


class OrdenacaoNovaAnaliseTestCase(unittest.TestCase):
    def test_fluxo_real_de_filtro_ordenacao_estado_visual_e_paginacao(self):
        raiz = Path(__file__).resolve().parents[1]
        codigo_front = (raiz / "static" / "js" / "nova_analise.js").read_text(
            encoding="utf-8"
        )
        testes_js = r"""
const assert = require("node:assert/strict");
const linha = (data, diferenca, quantidade = 1, media = 1, extras = {}) => ({
    dataset: {
        data,
        diferenca: String(diferenca),
        quantidade: String(quantidade),
        media: String(media),
        product: extras.product || "Produto teste",
        code: extras.code || "123",
        type: extras.type || "DCB",
        classification: extras.classification || "MED_OUTRO",
    },
});
const ordenar = (rows, campo, direcao) => filtrarOrdenarPaginar(
    rows,
    { termo: "", tipo: "", classificacao: "", dataInicial: "", dataFinal: "" },
    { campo, direcao },
    1,
    rows.length || 1
).correspondentes;

const datas = [
    linha("2026-09-19", 1), linha("2026-09-19", 2),
    linha("2026-10-06", 3), linha("2026-09-26", 4),
];
assert.deepEqual(ordenar(datas, "data", "desc").map(x => x.dataset.data), [
    "2026-10-06", "2026-09-26", "2026-09-19", "2026-09-19",
]);
assert.deepEqual(ordenar(datas, "data", "asc").map(x => x.dataset.data), [
    "2026-09-19", "2026-09-19", "2026-09-26", "2026-10-06",
]);

const diferencas = [1.20, 1.86, 9.49, 12.33].map(x => linha("2026-09-19", x));
assert.deepEqual(ordenar(diferencas, "diferenca", "desc").map(x => Number(x.dataset.diferenca)), [12.33, 9.49, 1.86, 1.20]);
assert.deepEqual(ordenar(diferencas, "diferenca", "asc").map(x => Number(x.dataset.diferenca)), [1.20, 1.86, 9.49, 12.33]);

const quantidades = [3, 4, 12, 20].map(x => linha("2026-09-19", 1, x));
assert.deepEqual(ordenar(quantidades, "quantidade", "desc").map(x => Number(x.dataset.quantidade)), [20, 12, 4, 3]);
const medias = [2.5, 1.5, 10].map(x => linha("2026-09-19", 1, 1, x));
assert.deepEqual(ordenar(medias, "media", "desc").map(x => Number(x.dataset.media)), [10, 2.5, 1.5]);

let estado = { campo: null, direcao: "desc" };
estado = alternarOrdenacao(estado, "data");
assert.deepEqual(estado, { campo: "data", direcao: "desc" });
assert.deepEqual(indicadorOrdenacao(estado, "data"), { ariaSort: "descending", simbolo: "▼" });
estado = alternarOrdenacao(estado, "data");
assert.deepEqual(estado, { campo: "data", direcao: "asc" });
assert.deepEqual(indicadorOrdenacao(estado, "data"), { ariaSort: "ascending", simbolo: "▲" });
estado = alternarOrdenacao(estado, "diferenca");
assert.deepEqual(estado, { campo: "diferenca", direcao: "desc" });
assert.deepEqual(indicadorOrdenacao(estado, "diferenca"), { ariaSort: "descending", simbolo: "▼" });
assert.deepEqual(indicadorOrdenacao(estado, "data"), { ariaSort: "none", simbolo: "↕" });

const vinte = Array.from({length: 20}, (_, i) => linha("2026-09-19", i + 1));
const primeiraPagina = filtrarOrdenarPaginar(
    vinte,
    { termo: "", tipo: "", classificacao: "MED_OUTRO", dataInicial: "", dataFinal: "" },
    { campo: "diferenca", direcao: "desc" },
    1,
    10
);
assert.deepEqual(primeiraPagina.linhasPagina.map(x => Number(x.dataset.diferenca)), [20,19,18,17,16,15,14,13,12,11]);
assert.equal(primeiraPagina.correspondentes.length, 20);
assert.equal(primeiraPagina.totalPaginas, 2);
console.log("pipeline, ordenação, indicadores e paginação OK");
"""
        entrada = (
            "globalThis.document = { getElementById: () => null };\n"
            + codigo_front
            + "\n"
            + testes_js
        )
        resultado = subprocess.run(
            ["node", "-"],
            input=entrada.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        stdout = resultado.stdout.decode("utf-8", errors="replace")
        stderr = resultado.stderr.decode("utf-8", errors="replace")
        self.assertEqual(resultado.returncode, 0, stderr or stdout)
        self.assertIn("pipeline, ordenação, indicadores e paginação OK", stdout)


if __name__ == "__main__":
    unittest.main()
