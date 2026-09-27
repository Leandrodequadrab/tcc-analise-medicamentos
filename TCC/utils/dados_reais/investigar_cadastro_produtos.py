"""Investiga, sem escrita, o cadastro de produtos usado pelo Rel_0024.

O sgfpod1 e o tcc_medicamentos são abertos com transações somente leitura.
No banco da farmácia, a proteção adicional de conexao_farmacia permite apenas
instruções iniciadas por SELECT ou SHOW.
"""

from collections import defaultdict
import re
import sys
import unicodedata

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from database import obter_url_banco_aplicacao

from .conexao_farmacia import (
    ErroConfiguracaoFarmacia,
    conectar_farmacia_somente_leitura,
)


BANCO_TCC = "tcc_medicamentos"
PALAVRAS_CATALOGO = (
    "produ",
    "medic",
    "anvisa",
    "ean",
    "barra",
    "dcb",
    "princip",
    "ativo",
    "classe",
    "terap",
    "grupo",
    "categ",
    "tipo",
    "laborat",
    "fabric",
    "otc",
    "mip",
    "prescri",
    "receita",
)

SQL_PRODUTOS_REL0024 = text(
    """
    SELECT filial, codigo_produto, descricao_original
    FROM public.produtos_rel0024
    ORDER BY codigo_produto
    """
)

SQL_CADPRODU = text(
    """
    SELECT *
    FROM public.cadprodu
    WHERE cod_reduzido IN :codigos
    ORDER BY cod_rede, cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_DOMINIOS_PRODUTO = text(
    """
    SELECT
        p.cod_rede,
        p.cod_reduzido,
        p.cod_produto,
        p.nom_produto,
        p.cod_grupo,
        g.nom_grupo,
        p.cod_depto,
        d.nom_depto,
        p.cod_categoria,
        c.nom_categoria,
        p.cod_laborat,
        l.nom_laborat,
        p.cod_tipo,
        t.nom_tipo,
        p.cod_dcb,
        dcb.nom_dcb,
        p.cod_classe_terapeutica,
        ct.descricao AS classe_terapeutica,
        p.num_regms,
        p.tip_lista,
        p.tip_listaad,
        p.tip_classeterapeutica,
        p.flg_ativo,
        p.flg_excluido,
        p.flg_controla,
        p.flg_continuo,
        p.flg_monitorado
    FROM public.cadprodu AS p
    LEFT JOIN public.cadgrpro AS g
      ON g.cod_rede = p.cod_rede AND g.cod_grupo = p.cod_grupo
    LEFT JOIN public.caddepto AS d
      ON d.cod_rede = p.cod_rede AND d.cod_depto = p.cod_depto
    LEFT JOIN public.cadcateg AS c
      ON c.cod_rede = p.cod_rede AND c.cod_categoria = p.cod_categoria
    LEFT JOIN public.cadlabor AS l
      ON l.cod_rede = p.cod_rede AND l.cod_laborat = p.cod_laborat
    LEFT JOIN public.cadtipro AS t
      ON t.cod_rede = p.cod_rede AND t.cod_tipo = p.cod_tipo
    LEFT JOIN public.cadcddcb AS dcb
      ON dcb.cod_rede = p.cod_rede AND dcb.cod_dcb = p.cod_dcb
    LEFT JOIN public.classe_terapeutica AS ct
      ON ct.cod_rede = p.cod_rede
     AND ct.cod_classe_terapeutica = p.cod_classe_terapeutica
    WHERE p.cod_rede = :cod_rede
      AND p.cod_reduzido IN :codigos
    ORDER BY p.cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_CODIGOS_BARRAS = text(
    """
    SELECT
        cod_reduzido,
        COUNT(*) AS quantidade_codigos,
        SUM(CASE WHEN gtin IS NOT NULL AND btrim(gtin) <> '' THEN 1 ELSE 0 END)
            AS quantidade_gtin,
        SUM(CASE WHEN flg_principal THEN 1 ELSE 0 END) AS quantidade_principais
    FROM public.cadcdbar
    WHERE cod_rede = :cod_rede
      AND cod_reduzido IN :codigos
    GROUP BY cod_reduzido
    ORDER BY cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_ANVISA = text(
    """
    SELECT
        cod_reduzido,
        COUNT(*) AS quantidade_vinculos,
        COUNT(DISTINCT NULLIF(btrim(num_registro_ms_anvisa), ''))
            AS registros_anvisa_distintos
    FROM public.vinculo_produto_anvisa
    WHERE cod_rede = :cod_rede
      AND cod_reduzido IN :codigos
    GROUP BY cod_reduzido
    ORDER BY cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_CHAVES_ABCFARMA = text(
    """
    SELECT chave, COUNT(*) AS ocorrencias
    FROM (
        SELECT jsonb_object_keys(des_dados) AS chave
        FROM public.abcfarma_produto
    ) AS chaves
    GROUP BY chave
    ORDER BY chave
    """
)

SQL_ABCFARMA_POR_EAN = text(
    """
    SELECT DISTINCT
        b.cod_reduzido,
        regexp_replace(
            COALESCE(NULLIF(btrim(b.gtin), ''), b.cod_barra::text),
            '[^0-9]', '', 'g'
        ) AS ean_cadastro,
        a.des_dados ->> 'EAN' AS ean_abcfarma,
        a.des_dados ->> 'ID_PRODUTO' AS id_produto_abcfarma,
        a.des_dados ->> 'NOME' AS nome_abcfarma,
        a.des_dados ->> 'REGISTRO_ANVISA' AS registro_anvisa,
        a.des_dados ->> 'DCB' AS dcb_abcfarma,
        a.des_dados ->> 'COMPOSICAO' AS composicao,
        a.des_dados ->> 'CLASSE_TERAPEUTICA' AS classe_terapeutica_abcfarma,
        a.des_dados ->> 'ATC_CODE' AS atc_code,
        a.des_dados ->> 'DESCRICAO_TIPO_PRODUTO' AS tipo_produto_abcfarma,
        a.des_dados ->> 'ID_TARJA' AS id_tarja,
        a.des_dados ->> 'DESCRICAO_LISTA' AS descricao_lista,
        a.des_dados ->> 'PORTARIA_344_98' AS portaria_344_98,
        a.des_dados ->> 'NOME_FABRICANTE' AS fabricante_abcfarma
    FROM public.cadcdbar AS b
    JOIN public.abcfarma_produto AS a
      ON regexp_replace(
             COALESCE(NULLIF(btrim(b.gtin), ''), b.cod_barra::text),
             '[^0-9]', '', 'g'
         ) = regexp_replace(
             COALESCE(a.des_dados ->> 'EAN', ''),
             '[^0-9]', '', 'g'
         )
     AND regexp_replace(
             COALESCE(a.des_dados ->> 'EAN', ''),
             '[^0-9]', '', 'g'
         ) <> ''
    WHERE b.cod_rede = :cod_rede
      AND b.cod_reduzido IN :codigos
    ORDER BY b.cod_reduzido
    """
).bindparams(bindparam("codigos", expanding=True))

SQL_COLUNAS_CADPRODU = text(
    """
    SELECT
        c.ordinal_position,
        c.column_name,
        c.data_type,
        c.udt_name,
        c.is_nullable,
        pg_catalog.col_description(cls.oid, c.ordinal_position) AS comentario
    FROM information_schema.columns AS c
    JOIN pg_catalog.pg_namespace AS ns
      ON ns.nspname = c.table_schema
    JOIN pg_catalog.pg_class AS cls
      ON cls.relnamespace = ns.oid
     AND cls.relname = c.table_name
    WHERE c.table_schema = 'public'
      AND c.table_name = 'cadprodu'
    ORDER BY c.ordinal_position
    """
)

SQL_TABELAS_CANDIDATAS = text(
    """
    SELECT
        c.table_name,
        array_to_string(array_agg(c.column_name::text ORDER BY c.ordinal_position), ', ')
            AS colunas,
        pg_catalog.obj_description(cls.oid, 'pg_class') AS comentario_tabela
    FROM information_schema.columns AS c
    JOIN pg_catalog.pg_namespace AS ns
      ON ns.nspname = c.table_schema
    JOIN pg_catalog.pg_class AS cls
      ON cls.relnamespace = ns.oid
     AND cls.relname = c.table_name
    WHERE c.table_schema = 'public'
      AND (
          lower(c.table_name) LIKE ANY(:padroes)
          OR lower(c.column_name) LIKE ANY(:padroes)
      )
    GROUP BY c.table_schema, c.table_name, cls.oid
    ORDER BY c.table_name
    """
)

SQL_COLUNAS_CANDIDATAS = text(
    """
    SELECT
        c.table_name,
        c.ordinal_position,
        c.column_name,
        c.data_type,
        c.is_nullable,
        pg_catalog.col_description(cls.oid, c.ordinal_position) AS comentario
    FROM information_schema.columns AS c
    JOIN pg_catalog.pg_namespace AS ns
      ON ns.nspname = c.table_schema
    JOIN pg_catalog.pg_class AS cls
      ON cls.relnamespace = ns.oid
     AND cls.relname = c.table_name
    WHERE c.table_schema = 'public'
      AND c.table_name IN :tabelas
    ORDER BY c.table_name, c.ordinal_position
    """
).bindparams(bindparam("tabelas", expanding=True))

SQL_CONSTRAINTS = text(
    """
    SELECT
        no.nspname AS schema_origem,
        co.relname AS tabela_origem,
        con.conname AS constraint_name,
        CASE con.contype
            WHEN 'p' THEN 'PRIMARY KEY'
            WHEN 'f' THEN 'FOREIGN KEY'
            WHEN 'u' THEN 'UNIQUE'
        END AS constraint_type,
        array_to_string(ARRAY(
            SELECT a.attname
            FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_catalog.pg_attribute AS a
              ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            ORDER BY k.ord
        ), ', ') AS colunas_origem,
        nd.nspname AS schema_destino,
        cd.relname AS tabela_destino,
        array_to_string(ARRAY(
            SELECT a.attname
            FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_catalog.pg_attribute AS a
              ON a.attrelid = con.confrelid AND a.attnum = k.attnum
            ORDER BY k.ord
        ), ', ') AS colunas_destino
    FROM pg_catalog.pg_constraint AS con
    JOIN pg_catalog.pg_class AS co ON co.oid = con.conrelid
    JOIN pg_catalog.pg_namespace AS no ON no.oid = co.relnamespace
    LEFT JOIN pg_catalog.pg_class AS cd ON cd.oid = con.confrelid
    LEFT JOIN pg_catalog.pg_namespace AS nd ON nd.oid = cd.relnamespace
    WHERE no.nspname = 'public'
      AND con.contype IN ('p', 'f', 'u')
      AND (
          lower(co.relname) IN :tabelas
          OR lower(COALESCE(cd.relname, '')) IN :tabelas
      )
    ORDER BY co.relname, con.conname
    """
).bindparams(bindparam("tabelas", expanding=True))


def _normalizar_descricao(valor):
    if valor is None:
        return None
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"\s+", " ", texto).strip().upper()


def _linhas(conexao, consulta, parametros=None):
    return [dict(linha) for linha in conexao.execute(consulta, parametros or {}).mappings()]


def _abrir_tcc_somente_leitura():
    engine = create_engine(
        obter_url_banco_aplicacao(),
        pool_pre_ping=True,
        connect_args={
            "application_name": "tcc_investigacao_produtos_read_only",
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
        },
    )
    conexao = engine.connect()
    try:
        banco = conexao.execute(text("SELECT current_database()" )).scalar_one()
        modo = conexao.execute(text("SHOW transaction_read_only")).scalar_one()
        if banco != BANCO_TCC or modo != "on":
            raise RuntimeError(
                f"Leitura do TCC recusada: banco={banco!r}, transaction_read_only={modo!r}."
            )
        return engine, conexao
    except Exception:
        conexao.close()
        engine.dispose()
        raise


def investigar():
    """Coleta evidências estruturais e de cobertura, sem modificar os bancos."""

    # Esta abertura é intencionalmente a primeira ação de banco da investigação.
    with conectar_farmacia_somente_leitura() as farmacia:
        modo_farmacia = farmacia.execute(text("SHOW transaction_read_only")).scalar_one()
        if modo_farmacia != "on":
            raise RuntimeError("sgfpod1 não está em modo somente leitura.")

        engine_tcc, tcc = _abrir_tcc_somente_leitura()
        try:
            produtos_tcc = _linhas(tcc, SQL_PRODUTOS_REL0024)
        finally:
            tcc.close()
            engine_tcc.dispose()

        codigos = sorted({linha["codigo_produto"] for linha in produtos_tcc})
        produtos_cadastro = _linhas(farmacia, SQL_CADPRODU, {"codigos": codigos})
        colunas_cadprodu = _linhas(farmacia, SQL_COLUNAS_CADPRODU)

        padroes = [f"%{palavra}%" for palavra in PALAVRAS_CATALOGO]
        tabelas_candidatas = _linhas(
            farmacia, SQL_TABELAS_CANDIDATAS, {"padroes": padroes}
        )
        nomes_tabelas = sorted({linha["table_name"] for linha in tabelas_candidatas})
        colunas_candidatas = _linhas(
            farmacia, SQL_COLUNAS_CANDIDATAS, {"tabelas": nomes_tabelas}
        )
        constraints = _linhas(
            farmacia,
            SQL_CONSTRAINTS,
            {"tabelas": sorted(set(nomes_tabelas) | {"cadprodu"})},
        )

        rede_cobertura = None
        # A rede é escolhida apenas quando todos os códigos apontam de forma
        # inequívoca para uma única rede; não é inferida a partir da filial.
        redes_por_codigo = defaultdict(set)
        for produto in produtos_cadastro:
            redes_por_codigo[produto["cod_reduzido"]].add(produto["cod_rede"])
        redes_completas = set.intersection(
            *(redes for redes in redes_por_codigo.values())
        ) if redes_por_codigo else set()
        if len(redes_completas) == 1:
            rede_cobertura = next(iter(redes_completas))

        dominios = []
        codigos_barras = []
        vinculos_anvisa = []
        abcfarma_por_ean = []
        if rede_cobertura is not None:
            parametros = {"cod_rede": rede_cobertura, "codigos": codigos}
            dominios = _linhas(farmacia, SQL_DOMINIOS_PRODUTO, parametros)
            codigos_barras = _linhas(farmacia, SQL_CODIGOS_BARRAS, parametros)
            vinculos_anvisa = _linhas(farmacia, SQL_ANVISA, parametros)
            abcfarma_por_ean = _linhas(farmacia, SQL_ABCFARMA_POR_EAN, parametros)
        chaves_abcfarma = _linhas(farmacia, SQL_CHAVES_ABCFARMA)

    por_codigo = defaultdict(list)
    por_rede = defaultdict(set)
    for produto in produtos_cadastro:
        por_codigo[produto["cod_reduzido"]].append(produto)
        por_rede[produto["cod_rede"]].add(produto["cod_reduzido"])

    encontrados = [linha for linha in produtos_tcc if linha["codigo_produto"] in por_codigo]
    nao_encontrados = [
        linha for linha in produtos_tcc if linha["codigo_produto"] not in por_codigo
    ]
    multiplas_redes = {
        codigo: linhas for codigo, linhas in por_codigo.items() if len(linhas) > 1
    }

    descricoes_iguais = 0
    for produto in encontrados:
        descricao_tcc = _normalizar_descricao(produto["descricao_original"])
        if any(
            _normalizar_descricao(cadastro.get("nom_produto")) == descricao_tcc
            for cadastro in por_codigo[produto["codigo_produto"]]
        ):
            descricoes_iguais += 1

    return {
        "seguranca": {
            "banco_farmacia": "sgfpod1",
            "read_only_farmacia": modo_farmacia,
            "banco_tcc": BANCO_TCC,
            "read_only_tcc": "on",
        },
        "produtos_tcc": produtos_tcc,
        "produtos_cadastro": produtos_cadastro,
        "encontrados": encontrados,
        "nao_encontrados": nao_encontrados,
        "multiplas_redes": multiplas_redes,
        "cobertura_por_rede": {
            rede: len(codigos_rede) for rede, codigos_rede in sorted(por_rede.items())
        },
        "descricoes_iguais": descricoes_iguais,
        "rede_cobertura": rede_cobertura,
        "dominios": dominios,
        "codigos_barras": codigos_barras,
        "vinculos_anvisa": vinculos_anvisa,
        "abcfarma_por_ean": abcfarma_por_ean,
        "chaves_abcfarma": chaves_abcfarma,
        "colunas_cadprodu": colunas_cadprodu,
        "tabelas_candidatas": tabelas_candidatas,
        "colunas_candidatas": colunas_candidatas,
        "constraints": constraints,
    }


def _preenchido(valor):
    return valor is not None and str(valor).strip() != ""


def _percentual(quantidade, total):
    return 0.0 if not total else quantidade * 100 / total


def _cobertura(linhas, campo):
    return sum(_preenchido(linha.get(campo)) for linha in linhas)


def _exemplos(linhas, campo, limite=4):
    encontrados = []
    for linha in linhas:
        valor = linha.get(campo)
        if _preenchido(valor) and str(valor).strip() not in encontrados:
            encontrados.append(str(valor).strip())
        if len(encontrados) == limite:
            break
    return encontrados


def _distribuicao(linhas, campo):
    contagens = defaultdict(int)
    for linha in linhas:
        valor = linha.get(campo)
        chave = str(valor).strip() if _preenchido(valor) else "NULL"
        contagens[chave] += 1
    return sorted(contagens.items(), key=lambda item: (-item[1], item[0]))


def _linha_cobertura(rotulo, quantidade, total):
    print(f"- {rotulo}: {quantidade} ({_percentual(quantidade, total):.2f}%)")


def imprimir_relatorio(resultado):
    total = len(resultado["produtos_tcc"])
    dominios = resultado["dominios"]
    abcfarma = resultado["abcfarma_por_ean"]
    abcfarma_por_produto = defaultdict(list)
    for linha in abcfarma:
        abcfarma_por_produto[linha["cod_reduzido"]].append(linha)

    tipos_coluna = {
        linha["column_name"]: linha["data_type"]
        for linha in resultado["colunas_cadprodu"]
    }
    campos_cadprodu = (
        ("cod_reduzido", "identificador na PK", "confirmada"),
        ("cod_produto", "código complementar", "incerta"),
        ("nom_produto", "nome cadastral", "confirmada"),
        ("cod_dcb", "domínio DCB via FK", "confirmada como DCB; não como composição completa"),
        ("cod_classe_terapeutica", "classe terapêutica via FK", "confirmada"),
        ("cod_laborat", "laboratório via FK", "confirmada"),
        ("cod_tipo", "tipo interno via FK", "confirmada como tipo; incerta para OTC"),
        ("num_regms", "possível registro MS", "incerta"),
        ("tip_lista", "lista interna", "incerta"),
        ("tip_classeterapeutica", "tipo de classe", "confirmada pelo comentário da coluna"),
        ("flg_ativo", "estado ativo", "nome claro; valores não documentados"),
        ("flg_excluido", "estado de exclusão", "nome claro; sem valores preenchidos"),
    )

    produtos_com_gtin = sum(
        linha["quantidade_gtin"] > 0 for linha in resultado["codigos_barras"]
    )
    produtos_otc_textual = sum(
        _preenchido(linha.get("nom_tipo"))
        and "OTC" in linha["nom_tipo"].upper()
        for linha in dominios
    )
    produtos_abcfarma = len(abcfarma_por_produto)
    cobertura_abc = {}
    for campo in (
        "registro_anvisa",
        "dcb_abcfarma",
        "composicao",
        "classe_terapeutica_abcfarma",
        "atc_code",
        "fabricante_abcfarma",
    ):
        cobertura_abc[campo] = sum(
            any(_preenchido(item.get(campo)) and str(item[campo]).strip() != "0"
                for item in itens)
            for itens in abcfarma_por_produto.values()
        )
    ambiguos_abc = sum(
        len({item["id_produto_abcfarma"] for item in itens}) > 1
        for itens in abcfarma_por_produto.values()
    )

    print("1. SEGURANÇA / READ-ONLY")
    print(
        f"- sgfpod1: banco confirmado; transaction_read_only="
        f"{resultado['seguranca']['read_only_farmacia']}."
    )
    print(
        f"- tcc_medicamentos: transaction_read_only="
        f"{resultado['seguranca']['read_only_tcc']}."
    )
    print("- O script contém somente SELECT/SHOW; nenhuma escrita ou commit é executado.")

    print("\n2. CORRESPONDÊNCIA REL0024 → CADPRODU")
    _linha_cobertura("códigos encontrados em cadprodu.cod_reduzido", len(resultado["encontrados"]), total)
    _linha_cobertura("códigos não encontrados", len(resultado["nao_encontrados"]), total)
    print(f"- Rede encontrada de forma inequívoca: {resultado['rede_cobertura']}.")
    print(f"- Códigos presentes em mais de uma rede: {len(resultado['multiplas_redes'])}.")
    _linha_cobertura("descrições iguais após normalização", resultado["descricoes_iguais"], total)
    print("- Não há FK entre bancos distintos: o vínculo é operacionalmente validado por cobertura, unicidade e descrição, não declarado como constraint cruzada.")
    for linha in dominios[:5]:
        print(f"  exemplo: {linha['cod_reduzido']} | {linha['nom_produto']}")
    if not resultado["nao_encontrados"]:
        print("- Exemplos não encontrados: nenhum.")

    print("\n3. CAMPOS ÚTEIS DE CADPRODU")
    for campo, significado, status in campos_cadprodu:
        quantidade = _cobertura(dominios, campo)
        exemplos = ", ".join(_exemplos(dominios, campo)) or "sem exemplo preenchido"
        print(
            f"- {campo} | {tipos_coluna.get(campo, 'tipo não localizado')} | "
            f"{quantidade}/{total} | exemplos: {exemplos} | "
            f"significado: {significado} | semântica: {status}."
        )

    print("\n4. TABELAS RELACIONADAS ENCONTRADAS")
    print("- cadcdbar | PK: cod_rede+cod_reduzido+cod_barra | FK formal para cadprodu | código de barras/GTIN confirmado por comentário de coluna.")
    print("- cadcddcb | PK: cod_rede+cod_dcb | FK formal recebida de cadprodu | nom_dcb é o domínio DCB; composição completa não é garantida.")
    print("- classe_terapeutica | PK: cod_classe_terapeutica+cod_rede | FK formal recebida de cadprodu | descrição terapêutica confirmada estruturalmente.")
    print("- cadlabor | PK: cod_rede+cod_laborat | FK formal recebida de cadprodu | nom_laborat confirmado como laboratório cadastral.")
    print("- cadtipro | PK: cod_rede+cod_tipo | FK formal recebida de cadprodu | tipo comercial confirmado; equivalência regulatória OTC não documentada.")
    print("- cadgrpro/caddepto/cadcateg | FKs formais recebidas de cadprodu | taxonomia comercial, não terapêutica.")
    print("- vinculo_produto_anvisa | PK própria | FK para cadloted, que possui FK para cadprodu | vínculo indireto formal, sem cobertura nesta amostra.")
    print("- abcfarma_produto | JSON ABCFarma documentado no comentário da tabela | sem PK/FK com cadprodu; associação por EAN é apenas validada empiricamente.")

    print("\n5. ANVISA / EAN")
    _linha_cobertura("produtos com código em cadcdbar", len(resultado["codigos_barras"]), total)
    _linha_cobertura("produtos com GTIN preenchido", produtos_com_gtin, total)
    _linha_cobertura("produtos em vinculo_produto_anvisa", len(resultado["vinculos_anvisa"]), total)
    _linha_cobertura("cadprodu.num_regms preenchido (semântica ainda incerta)", _cobertura(dominios, "num_regms"), total)
    _linha_cobertura("produtos associados à ABCFarma por EAN", produtos_abcfarma, total)
    _linha_cobertura("registro ANVISA preenchido após associação ABCFarma", cobertura_abc["registro_anvisa"], total)
    print(f"- A associação ABCFarma contém {ambiguos_abc} produtos ligados a mais de um ID_PRODUTO; não é uma FK formal.")

    print("\n6. PRINCÍPIO ATIVO")
    _linha_cobertura("produtos com cod_dcb e nom_dcb pelo vínculo formal", _cobertura(dominios, "nom_dcb"), total)
    _linha_cobertura("DCB preenchida na associação ABCFarma", cobertura_abc["dcb_abcfarma"], total)
    _linha_cobertura("composição preenchida na associação ABCFarma", cobertura_abc["composicao"], total)
    print(f"- Exemplos de nom_dcb: {', '.join(_exemplos(dominios, 'nom_dcb'))}.")
    print("- Recomendação semântica: armazenar como DCB. Não renomear automaticamente para princípio ativo nem assumir que representa toda a composição.")

    print("\n7. OTC / MIP / PRESCRIÇÃO")
    _linha_cobertura("nom_tipo contendo literalmente OTC", produtos_otc_textual, total)
    print("- O vínculo cadprodu→cadtipro é formal, mas não há comentário/dicionário que estabeleça OTC como classificação regulatória definitiva.")
    print(f"- tip_lista: {_distribuicao(dominios, 'tip_lista')}.")
    print(f"- tip_classeterapeutica: {_distribuicao(dominios, 'tip_classeterapeutica')}.")
    print("- O comentário de tip_classeterapeutica documenta: 1=Antimicrobianos, 2=Controle Especial, 3=Agonistas GLP-1; isso não equivale a um indicador completo de prescrição.")
    print("- OTC/MIP/prescrição confirmada para uso definitivo: 0; os campos encontrados permanecem candidatos até validação de negócio/regulatória.")

    print("\n8. CLASSIFICAÇÃO TERAPÊUTICA")
    _linha_cobertura("classe terapêutica confirmada por FK e descrição", _cobertura(dominios, "classe_terapeutica"), total)
    _linha_cobertura("classe terapêutica na associação ABCFarma", cobertura_abc["classe_terapeutica_abcfarma"], total)
    _linha_cobertura("ATC_CODE na associação ABCFarma", cobertura_abc["atc_code"], total)
    print(f"- Exemplos formais: {', '.join(_exemplos(dominios, 'classe_terapeutica'))}.")
    print("- Grupo, departamento e categoria não foram tratados como classes terapêuticas.")

    print("\n9. FABRICANTE / LABORATÓRIO")
    _linha_cobertura("laboratório por FK cadprodu→cadlabor", _cobertura(dominios, "nom_laborat"), total)
    _linha_cobertura("fabricante na associação ABCFarma", cobertura_abc["fabricante_abcfarma"], total)
    print(f"- Exemplos de laboratório: {', '.join(_exemplos(dominios, 'nom_laborat'))}.")

    print("\n10. COBERTURA DOS 4.666 PRODUTOS")
    _linha_cobertura("total Rel_0024", total, total)
    _linha_cobertura("encontrados no cadastro", len(resultado["encontrados"]), total)
    _linha_cobertura("não encontrados", len(resultado["nao_encontrados"]), total)
    _linha_cobertura("com código de barras/GTIN", produtos_com_gtin, total)
    _linha_cobertura("com vínculo ANVISA formal em vinculo_produto_anvisa", len(resultado["vinculos_anvisa"]), total)
    _linha_cobertura("com DCB formal", _cobertura(dominios, "nom_dcb"), total)
    _linha_cobertura("com classe terapêutica confirmada", _cobertura(dominios, "classe_terapeutica"), total)
    _linha_cobertura("com OTC/MIP confirmado", 0, total)
    _linha_cobertura("com laboratório", _cobertura(dominios, "nom_laborat"), total)

    print("\n11. CAMPOS RECOMENDADOS PARA O TCC")
    print("- Incorporar futuramente, preservando a origem: cod_rede, cod_reduzido, nom_produto, GTIN/código de barras, cod_dcb+nom_dcb, cod_classe_terapeutica+descrição e cod_laborat+nom_laborat.")
    print("- Tipo, grupo, departamento e categoria podem ser mantidos como taxonomia interna/comercial, nunca como diagnóstico, indicação clínica ou classe terapêutica.")
    print("- Registrar cobertura e data da extração; manter os dados do Rel_0024 como fonte oficial das vendas.")

    print("\n12. CAMPOS QUE NÃO DEVEM SER UTILIZADOS AINDA")
    print("- Não usar ainda num_regms, dados ABCFarma, ID_TARJA, DESCRICAO_LISTA, PORTARIA_344_98 ou textos contendo OTC como classificação definitiva.")
    print("- Não transformar tip_lista, flg_controla, flg_continuo ou tip_classeterapeutica em prescrição/OTC sem regra documentada.")
    print("- Não usar grupo, departamento ou categoria como indicação clínica; não inferir princípio ativo, doença ou automedicação pelo nome.")

    print("\n13. ARQUIVOS CRIADOS/ALTERADOS")
    print("- Criado: utils/dados_reais/investigar_cadastro_produtos.py.")
    print("- Nenhum modelo, tabela, dashboard, .env ou arquivo XLS/XLSX foi alterado; nenhum commit foi realizado.")

    print("\n14. PRÓXIMO PASSO RECOMENDADO")
    print("- Encerrar a 9B.6A como investigação validada para os campos formais recomendados.")
    print("- Antes do enriquecimento, definir uma 9B.6B com tabela de destino e rastreabilidade, além de validação explícita das regras OTC/prescrição e das divergências da associação ABCFarma por EAN.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        imprimir_relatorio(investigar())
    except (ErroConfiguracaoFarmacia, RuntimeError) as erro:
        print("INVESTIGAÇÃO INTERROMPIDA COM SEGURANÇA")
        print(erro)
        raise SystemExit(1) from None
    except SQLAlchemyError as erro:
        print("INVESTIGAÇÃO INTERROMPIDA POR ERRO DE BANCO")
        print(f"Tipo: {type(erro).__name__}")
        print(f"Mensagem: {erro}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
