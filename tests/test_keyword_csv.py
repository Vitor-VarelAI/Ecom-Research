"""
Testes do parser CSV Keyword Planner.

Usa ficheiros CSV temporários como fixture.
Sem chamadas reais à rede.
"""

import codecs
import csv
import os
import tempfile
import textwrap

import pytest

from pipeline.adapters import keyword_csv


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_csv(content: str) -> str:
    """Escreve um CSV temporário e devolve o path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
    )
    tmp.write(textwrap.dedent(content).strip())
    tmp.close()
    return tmp.name


def _write_utf16_tsv(content: str) -> str:
    """Escreve um export UTF-16 LE com BOM, tab-delimitado, com preâmbulo
    (título + intervalo de datas antes do header) — o formato do Google
    Keyword Planner actual.  Devolve o path."""
    payload = textwrap.dedent(content).strip() + "\n"
    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False)
    tmp.write(codecs.BOM_UTF16_LE + payload.encode("utf-16-le"))
    tmp.close()
    return tmp.name


# ── Tests ──────────────────────────────────────────────────────────────────


class TestKeywordCsvParse:
    """Testa o parsing de CSVs válidos."""

    def test_english_headers(self):
        """Headers em inglês (formato standard internacional)."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,0.65,0.42
        mochila escolar,3200,0.80,0.35
        mochila viagem,2100,0.45,0.28
        mochila bebé,1800,0.30,0.22
        mochila desporto,950,0.55,0.18
        mochila gaming,800,0.70,0.15
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 5400
            assert result["cpc"] == 0.42
            assert result["total_keywords"] == 6
            assert len(result["related_keywords"]) == 5
            # Primeira relacionada deve ser a de maior volume
            assert result["related_keywords"][0]["keyword"] == "mochila escolar"
            assert result["related_keywords"][0]["volume"] == 3200
        finally:
            os.unlink(path)

    def test_portuguese_headers(self):
        """Headers em português (Google Ads PT)."""
        csv_content = """
        Palavra-chave,Pesquisas mensais,Concorrência,CPC médio
        caneca personalizada,3200,0.55,0.38
        caneca foto,1800,0.40,0.25
        caneca namorado,1200,0.30,0.20
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 3200
            assert result["cpc"] == 0.38
            assert result["total_keywords"] == 3
            assert len(result["related_keywords"]) == 2
        finally:
            os.unlink(path)

    def test_utf8_bom(self):
        """CSV com BOM (UTF-8-SIG) deve ser lido correctamente."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        vela aromática,8900,0.72,0.55
        vela soja,4500,0.60,0.42
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 8900
        finally:
            os.unlink(path)

    def test_single_keyword(self):
        """CSV com apenas 1 keyword."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        tapete yoga premium,1500,0.35,0.90
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 1500
            assert result["related_keywords"] == []
        finally:
            os.unlink(path)


class TestKeywordCsvErrors:
    """Testa tratamento de erros."""

    def test_file_not_found(self):
        result = keyword_csv.parse("/tmp/nao-existe-mesmo.csv")
        assert "error" in result
        assert "não encontrado" in result["error"].lower()

    def test_empty_csv(self):
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert "error" in result
            assert "vazio" in result["error"].lower()
        finally:
            os.unlink(path)

    def test_unknown_headers(self):
        """Headers completamente irreconhecíveis."""
        csv_content = """
        ColunaX,ColunaY,ColunaZ
        valor1,valor2,valor3
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert "error" in result
            assert "headers não reconhecidos" in result["error"].lower()
        finally:
            os.unlink(path)

    def test_corrupted_csv(self):
        """CSV com formatação inválida."""
        path = "/tmp/corrupt.csv"
        with open(path, "w") as f:
            f.write("Keyword,Volume\n")
            f.write('"unclosed quote,100\n')
        try:
            result = keyword_csv.parse(path)
            # Pode dar erro ou parse parcial — o importante é não crashar
            assert isinstance(result, dict)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_numbers_with_commas(self):
        """Volumes com vírgulas (ex: "5,400")."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        almofada personalizada,"5,400",0.45,0.33
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 5400
        finally:
            os.unlink(path)


class TestKeywordCsvKeywordAware:
    """Regressões: selecção por keyword (matching exacto normalizado).

    Uma keyword pedida com volume baixo deve receber os SEUS métricas — nunca
    os da linha de maior volume — e uma keyword ausente do export deve dar
    erro explícito em vez de herdar outra linha.
    """

    CSV_CONTENT = """
    Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
    mochila personalizada,5400,0.65,0.42
    mochila escolar,3200,0.80,0.35
    mochila viagem,2100,0.45,0.28
    mochila bebé,1800,0.30,0.22
    mochila desporto,950,0.55,0.18
    mochila gaming,800,0.70,0.15
    """

    def test_requested_keyword_gets_its_own_metrics(self):
        """Keyword pedida com volume baixo recebe volume/CPC próprios, não os
        da linha de maior volume; related exclui a keyword pedida."""
        path = _write_csv(self.CSV_CONTENT)
        try:
            result = keyword_csv.parse(path, keyword="mochila viagem")
            assert result["volume"] == 2100
            assert result["cpc"] == 0.28
            assert result["competition"] == "0.45"
            assert result["total_keywords"] == 6
            related = result["related_keywords"]
            assert len(related) == 5
            assert related[0]["keyword"] == "mochila personalizada"
            assert related[0]["volume"] == 5400
            assert "mochila viagem" not in [r["keyword"] for r in related]
        finally:
            os.unlink(path)

    def test_requested_keyword_normalized_exact_match(self):
        """Matching exacto normalizado: diferenças de caso e acentos não
        quebram a selecção."""
        path = _write_csv(self.CSV_CONTENT)
        try:
            result = keyword_csv.parse(path, keyword="Mochila Bébé")
            assert result["volume"] == 1800
            assert result["cpc"] == 0.22
        finally:
            os.unlink(path)

    def test_missing_requested_keyword_returns_explicit_error(self):
        """Keyword pedida ausente -> erro explícito que nomeia a keyword, sem
        nunca devolver métricas de outra linha."""
        path = _write_csv(self.CSV_CONTENT)
        try:
            result = keyword_csv.parse(path, keyword="mochila inexistente")
            assert "error" in result
            assert "mochila inexistente" in result["error"]
            assert "volume" not in result
        finally:
            os.unlink(path)

    def test_keyword_aware_success_reports_matched_keyword(self):
        """Parse keyword-aware bem-sucedido expõe ``matched_keyword`` com o
        texto exacto da linha do CSV que serviu a keyword — prova a ligação
        linha-resultado nos artefactos."""
        path = _write_csv(self.CSV_CONTENT)
        try:
            result = keyword_csv.parse(path, keyword="Mochila Bébé")
            assert result["matched_keyword"] == "mochila bebé"
            assert result["volume"] == 1800
            # Keyword-aware sem ``keyword`` não inventa matched_keyword.
            legacy = keyword_csv.parse(path)
            assert "matched_keyword" not in legacy
        finally:
            os.unlink(path)


class TestKeywordCsvGoogleExportFormat:
    """Formato real do export do Google Keyword Planner (2026): ficheiro
    UTF-16 LE com BOM, delimitado por tabs, com duas linhas de preâmbulo
    (título + intervalo de datas) antes do header.  O parser deve detectar
    encoding, delimiter e a linha do header em vez de assumir a linha 1.

    Fixture sintética pequena — nunca o ficheiro grande de produção.
    """

    EXPORT = """
    Keyword Stats 2026-08-10 at 08_58_37
    "July 1, 2025 - June 30, 2026"
    Keyword	Currency	Avg. monthly searches	Competition	Competition (indexed value)	Top of page bid (low range)	Top of page bid (high range)
    mochila personalizada	EUR	500	High	100	0.33	1.39
    mochila escolar	EUR	3200	High	100	0.35	1.20
    mochila viagem	EUR	2100	High	100	0.28	0.90
    mochila bebé	EUR	1800	Low	100	0.22	0.80
    """

    def test_utf16_tab_preamble_parses(self):
        """Detecta UTF-16 LE + BOM, delimiter tab e o header na 3ª linha."""
        path = _write_utf16_tsv(self.EXPORT)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 3200  # maior volume: mochila escolar
            assert result["cpc"] == 0.35
            assert result["competition"] == "High"
            assert result["total_keywords"] == 4
            assert len(result["related_keywords"]) == 3
            # Depois da maior (escolar), as seguintes por volume: viagem, bebé, personalizada.
            assert result["related_keywords"][0]["keyword"] == "mochila viagem"
            assert result["related_keywords"][0]["volume"] == 2100
        finally:
            os.unlink(path)

    def test_competition_stays_textual_not_indexed(self):
        """Competition (textual: 'High') nunca é sobrescrita por Competition
        (indexed value: '100')."""
        path = _write_utf16_tsv(self.EXPORT)
        try:
            result = keyword_csv.parse(path, keyword="mochila bebé")
            assert result["competition"] == "Low"
            assert result["competition"] != "100"
            assert result["volume"] == 1800
        finally:
            os.unlink(path)

    def test_utf16_keyword_aware_matched_keyword(self):
        """Modo keyword-aware no formato UTF-16/tab: métricas da linha certa e
        matched_keyword com o texto exacto do CSV."""
        path = _write_utf16_tsv(self.EXPORT)
        try:
            result = keyword_csv.parse(path, keyword="Mochila Viagem")
            assert result["matched_keyword"] == "mochila viagem"
            assert result["volume"] == 2100
            assert result["cpc"] == 0.28
            assert result["competition"] == "High"
            related = result["related_keywords"]
            assert "mochila viagem" not in [r["keyword"] for r in related]
        finally:
            os.unlink(path)

    def test_utf16_missing_keyword_explicit_error(self):
        """Keyword ausente no export UTF-16 -> erro explícito."""
        path = _write_utf16_tsv(self.EXPORT)
        try:
            result = keyword_csv.parse(path, keyword="mochila inexistente")
            assert "error" in result
            assert "mochila inexistente" in result["error"]
        finally:
            os.unlink(path)


class TestKeywordCsvHeaderDetection:
    """A detecção do header (e do delimiter) não depende do formato tab: um
    CSV vírgula com linhas de preâmbulo também é resolvido."""

    def test_comma_csv_with_preamble_lines(self):
        csv_content = """
        Google Ads Keyword Planner
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,High,0.42
        mochila escolar,3200,High,0.35
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="mochila escolar")
            assert result["matched_keyword"] == "mochila escolar"
            assert result["volume"] == 3200
            assert result["competition"] == "High"
            assert result["total_keywords"] == 2
        finally:
            os.unlink(path)


class TestKeywordCsvRelatedOverlap:
    """Modo keyword-aware: as related são filtradas por overlap de prefixo de
    token com a keyword pedida ANTES da selecção top-5 por volume — linhas de
    nichos mistos com zero overlap nunca entram na evidência, mesmo com volume
    maior.  Só fixtures; o CSV de produção está gitignored."""

    def test_one_token_related_filter(self):
        """Uma linha sem overlap (caneca) é rejeitada; o plural (mochilas) entra."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila,5400,0.65,0.42
        mochilas,3200,0.80,0.35
        caneca,9000,0.90,0.50
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="mochila")
            assert result["volume"] == 5400
            related = [r["keyword"] for r in result["related_keywords"]]
            assert related == ["mochilas"]
            assert "caneca" not in related  # zero overlap apesar do maior volume
        finally:
            os.unlink(path)

    def test_multi_token_related_filter(self):
        """A política de product head rejeita linhas de outros produtos mesmo
        com modificador partilhado: só entram candidatas que contenham o token
        de produto da keyword pedida (mochila) — nunca por overlap de um
        modificador comum (personalizada) nem de stopwords (de)."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,0.65,0.42
        mochila escolar,3200,0.80,0.35
        caneca personalizada,2100,0.45,0.28
        caderno de notas,9000,0.90,0.50
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="mochila personalizada")
            related = [r["keyword"] for r in result["related_keywords"]]
            assert "mochila escolar" in related
            assert "caneca personalizada" not in related  # outro produto apesar do modificador
            assert "caderno de notas" not in related      # zero overlap, maior volume
        finally:
            os.unlink(path)

    def test_geo_source_drops_stopword_and_geography_only_rows(self):
        """Defeito provado para ``camisola de portugal``: a linha ``caneca de
        natal`` entrava por overlap do stopword ``de``.  A selecção usa agora
        a política de product head de pipeline/suggestions: só candidatas com
        o token de produto (camisola/camisolas) entram; linhas geography-only
        (volta a portugal) e com overlap só por stopwords são rejeitadas."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        camisola de portugal,5400,0.65,0.42
        camisolas de portugal,8000,0.80,0.35
        camisola portugal preta,3200,0.45,0.28
        caneca de natal,9000,0.90,0.50
        volta a portugal 2026,4000,0.55,0.30
        bicicleta de estrada,7000,0.85,0.60
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="camisola de portugal")
            assert result["volume"] == 5400
            related = [r["keyword"] for r in result["related_keywords"]]
            assert "camisolas de portugal" in related
            assert "camisola portugal preta" in related
            assert "caneca de natal" not in related          # só overlap de stopword 'de'
            assert "volta a portugal 2026" not in related    # geography-only / ciclismo
            assert "bicicleta de estrada" not in related
        finally:
            os.unlink(path)

    def test_geo_first_source_anchors_product_head(self):
        """``portugal camisolas`` ancora o product head ``camisolas`` — a ordem
        dos tokens não muda o anchor nem o filtro de related."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        portugal camisolas,5400,0.65,0.42
        camisola de portugal,8000,0.80,0.35
        caneca de natal,9000,0.90,0.50
        volta a portugal,4000,0.55,0.30
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="portugal camisolas")
            assert result["volume"] == 5400
            related = [r["keyword"] for r in result["related_keywords"]]
            assert "camisola de portugal" in related
            assert "caneca de natal" not in related
            assert "volta a portugal" not in related
        finally:
            os.unlink(path)

    def test_accent_and_case_insensitive_overlap(self):
        """Overlap normalizado: acentos e maiúsculas não quebram o filtro."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        Bébé,5400,0.65,0.42
        Bebe Premium,3200,0.80,0.35
        Mochila,9000,0.90,0.50
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="bebe")
            related = [r["keyword"] for r in result["related_keywords"]]
            assert "Bebe Premium" in related
            assert "Mochila" not in related
        finally:
            os.unlink(path)

    def test_singular_plural_prefix_overlap(self):
        """Qualquer dos lados pode ser o prefixo: 'mochila' é prefixo de
        'mochilas', logo o plural entra."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila,5400,0.65,0.42
        mochilas escolares,3200,0.80,0.35
        malas de viagem,9000,0.90,0.50
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="mochila")
            related = [r["keyword"] for r in result["related_keywords"]]
            assert "mochilas escolares" in related
            assert "malas de viagem" not in related
        finally:
            os.unlink(path)

    def test_zero_overlap_rejected_even_as_top_volume(self):
        """A linha de maior volume do ficheiro é rejeitada quando não tem
        overlap — o filtro corre antes do top-5 por volume."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,0.65,0.42
        caneca azul,9900,0.95,0.60
        mochila escolar,3200,0.80,0.35
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path, keyword="mochila personalizada")
            related = [r["keyword"] for r in result["related_keywords"]]
            assert related == ["mochila escolar"]  # caneca azul (9900) rejeitada
        finally:
            os.unlink(path)

    def test_legacy_parse_without_keyword_keeps_top_volume_related(self):
        """Sem ``keyword``, o comportamento histórico mantém-se: linha de maior
        volume e top 5 por volume sem qualquer filtro de overlap."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,0.65,0.42
        caneca azul,9900,0.95,0.60
        mochila escolar,3200,0.80,0.35
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 9900  # maior volume (legacy)
            related = [r["keyword"] for r in result["related_keywords"]]
            assert related == ["mochila personalizada", "mochila escolar"]
            assert "matched_keyword" not in result
        finally:
            os.unlink(path)


class TestKeywordCsvDelimiterSelection:
    """O delimiter é escolhido pelo header reconhecido, nunca por contagem de
    caracteres do ficheiro inteiro: um export tab cujos dados têm muitas
    vírgulas continua a ser tab, e um CSV vírgula com tabs em campos citados
    continua a ser vírgula."""

    def test_comma_heavy_tab_export_parses_as_tab(self):
        """Export tab (como o do Planner) cujos dados têm muitas vírgulas:
        a antiga heurística de contagem escolheria vírgula e falharia; o
        header reconhecido sob tab decide."""
        content = (
            "Keyword\tAvg. monthly searches\tCompetition\tTop of page bid (low range)\n"
            "mochila, premium, leve, impermeável, com rodas\t5400\tHigh\t0.42\n"
            "mochila, escolar, resistente, com tecido, forrada\t3200\tHigh\t0.35\n"
        )
        path = _write_csv(content)
        try:
            result = keyword_csv.parse(path, keyword="mochila, escolar, resistente, com tecido, forrada")
            assert "error" not in result
            assert result["volume"] == 3200
            assert result["cpc"] == 0.35
            assert result["competition"] == "High"
            assert result["total_keywords"] == 2
        finally:
            os.unlink(path)

    def test_tab_containing_comma_csv_parses_as_comma(self):
        """CSV vírgula com um tab dentro de um campo citado: a contagem bruta
        favoreceria tab; o header reconhecido sob vírgula decide."""
        content = (
            "Keyword,Avg. monthly searches,Competition,Top of page bid (low range)\n"
            '"mochila\tescolar premium",5400,High,0.42\n'
            "mochila viagem,3200,High,0.35\n"
        )
        path = _write_csv(content)
        try:
            result = keyword_csv.parse(path, keyword="mochila\tescolar premium")
            assert "error" not in result
            assert result["volume"] == 5400
            assert result["total_keywords"] == 2
            assert result["cpc"] == 0.42
        finally:
            os.unlink(path)

    def test_tab_preamble_export_with_commas_in_dates_stays_tab(self):
        """Preâmbulo com vírgulas nas datas (formato real do Planner) não
        muda o delimiter: tab continua a ganhar pelo header."""
        content = (
            "Keyword Stats 2026-08-10\n"
            '"July 1, 2025 - June 30, 2026"\n'
            "Keyword\tAvg. monthly searches\tCompetition\tTop of page bid (low range)\n"
            "mochila personalizada\t5400\tHigh\t0.42\n"
        )
        path = _write_csv(content)
        try:
            result = keyword_csv.parse(path, keyword="mochila personalizada")
            assert "error" not in result
            assert result["volume"] == 5400
            assert result["matched_keyword"] == "mochila personalizada"
        finally:
            os.unlink(path)


class TestKeywordCsvMalformedNulInput:
    """Input NUL-bearing sem BOM e malformado (comprimento ímpar, surrogates
    órfãos) nunca levanta UnicodeDecodeError: `_read_text` degrada para
    latin-1 e `parse` preserva o contrato gracioso {"error": ...}."""

    def test_odd_length_nul_bearing_no_bom_never_raises(self, tmp_path):
        path = str(tmp_path / "odd-nul.csv")
        with open(path, "wb") as f:
            f.write(b"kw\x00")  # ímpar: utf-16-le truncado
        result = keyword_csv.parse(path)
        assert isinstance(result, dict)
        assert "error" in result  # contrato gracioso preservado, nunca exceção

    def test_lone_surrogate_nul_bearing_no_bom_never_raises(self, tmp_path):
        path = str(tmp_path / "surrogate-nul.csv")
        with open(path, "wb") as f:
            f.write(b"\x00\xd8\x00\x00")  # high surrogate órfão em UTF-16LE
        result = keyword_csv.parse(path)
        assert isinstance(result, dict)
        assert "error" in result

    def test_read_text_odd_nul_bytes_never_raises(self, tmp_path):
        path = str(tmp_path / "odd.bin")
        with open(path, "wb") as f:
            f.write(b"a\x00b")
        assert isinstance(keyword_csv._read_text(path), str)

    def test_read_text_lone_surrogate_never_raises(self, tmp_path):
        path = str(tmp_path / "surrogate.bin")
        with open(path, "wb") as f:
            f.write(b"\x00\xd8\x00")
        assert isinstance(keyword_csv._read_text(path), str)

    def test_valid_utf16_no_bom_still_parses(self, tmp_path):
        """UTF-16 LE sem BOM bem-formado continua a ser detectado e parseado
        (o fallback NUL não quebrou o caminho feliz)."""
        payload = ("keyword\tvolume\tcpc\tcompetition\n"
                   "mochila personalizada\t5400\t0.35\tHigh\n").encode("utf-16-le")
        path = str(tmp_path / "valid-utf16.csv")
        with open(path, "wb") as f:
            f.write(payload)
        result = keyword_csv.parse(path)
        assert "error" not in result
        assert result["volume"] == 5400
