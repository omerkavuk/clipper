from clipper.models import Project
from clipper.queries import (
    Query, build_queries, matches, matching_ids, to_gdelt, to_google_news,
)
from clipper.textnorm import norm


def test_norm_accents_apostrophes():
    assert norm("Centro di Roma dell’Aurora — CITTÀ") == norm("centro di roma dell'aurora — citta")


def test_programme_standalone_query(example_project):
    ids = [q.id for q in build_queries(example_project)]
    assert "programme:Green Schools" in ids and "subject" in ids
    assert "person:Giulia Marchetti" in ids and "subject×spokesperson" in ids


def test_competitor_and_context_queries(example_project):
    p = example_project
    p.contexts = {"climate": ["climate"]}
    p.competitors = {"FAO": ["FAO"]}
    ids = [q.id for q in build_queries(p)]
    assert "subject×climate" in ids and "subject×FAO" in ids and "FAO" in ids
    assert all(q.exclude == p.exclude_terms for q in build_queries(p))


def test_google_news_syntax():
    q = Query("x", "x", [["Aurora Climate Lab", "Laboratorio Clima Aurora"], ["Green Schools"]], ["tirocinio"])
    assert to_google_news(q, ["aurora.example"]) == '("Aurora Climate Lab" OR "Laboratorio Clima Aurora") "Green Schools" -tirocinio -site:aurora.example'


def test_gdelt_syntax():
    q = Query("x", "x", [["Green Schools"]], [])
    assert to_gdelt(q, "IT", ["it"], ["aurora.example"]) == '"Green Schools" sourcecountry:IT sourcelang:italian -domain:aurora.example'


def test_matches_elision_and_case():
    q = Query("s", "s", [["Centro di Roma dell'Aurora"]], [])
    assert matches(q, "Il CENTRO DI ROMA DELL’Aurora ha premiato…")


def test_boundary_with_non_word_edges():
    q = Query("s", "s", [["dell'Aurora"]], [])
    assert matches(q, "il centro dell'Aurora.")
    q2 = Query("s", "s", [["Aurora"]], [])
    assert not matches(q2, "AuroraX report")
    assert matches(q2, "(Aurora)")


def test_exclude_blocks_match():
    q = Query("s", "s", [["Aurora"]], ["tirocinio"])
    assert not matches(q, "Aurora: tirocinio a Roma")


def test_matching_ids_and_groups():
    qs = [Query("a", "a", [["Aurora"], ["Roma"]], []), Query("b", "b", [["FAO"]], [])]
    assert matching_ids(qs, "Aurora a Roma") == ["a"]


def test_norm_literal_values():
    assert norm("CITTÀ") == "citta"
    assert norm("dell’Aurora") == "dell'aurora"
    assert norm("  a \n b  ") == "a b"


def test_matches_accented_term_vs_unaccented_text():
    q = Query("s", "s", [["Città"]], [])
    assert matches(q, "CITTA di Roma")
    assert matches(q, "la citta eterna")


def test_matches_unaccented_term_vs_accented_text():
    q = Query("s", "s", [["citta"]], [])
    assert matches(q, "La CITTÀ di Roma")


def test_matches_curly_apostrophe_term_vs_straight_text():
    q = Query("s", "s", [["dell’Aurora"]], [])
    assert matches(q, "il centro dell'Aurora")


def test_blank_terms_ignored(example_project):
    q = Query("s", "s", [["", "  "]], [])
    assert not matches(q, "anything")
    assert matches(Query("s", "s", [["Aurora", ""]], [""]), "Aurora")
    p = example_project
    p.programmes = ["", "  ", "VELA"]
    p.spokespeople = [" "]
    ids = [x.id for x in build_queries(p)]
    assert "programme:VELA" in ids
    assert not any(i in ("programme:", "programme:  ", "person: ") for i in ids)
    assert "subject×spokesperson" not in ids


def test_gdelt_drops_long_terms_but_google_news_keeps_them():
    q = Query("subject", "S", [["Aurora Climate Lab", "Aurora Climate Lab for Climate Action"]], [])
    g = to_gdelt(q, "IT", ["it"], [])
    assert '"Aurora Climate Lab"' in g and "Climate Action" not in g
    assert "Climate Action" in to_google_news(q, [])


def test_gdelt_drops_emptied_group_and_returns_empty_when_nothing_left():
    long = "one two three four five six"
    q = Query("x", "X", [["Aurora"], [long]], [])
    g = to_gdelt(q, "IT", ["it"], [])
    assert "Aurora" in g and long not in g
    assert to_gdelt(Query("y", "Y", [[long]], []), "IT", ["it"], []) == ""


def test_gdelt_quotes_terms_with_dashes_or_punctuation():
    q = Query("subject", "x", [["Aurora", "UN-Entwicklungsprogramm", "Green Schools"]], ["E-Mail"])
    g = to_gdelt(q, "GM", ["de"], [])
    assert g == ('(Aurora OR "UN-Entwicklungsprogramm" OR \"Green Schools\") sourcecountry:GM'
                 ' sourcelang:german -"E-Mail"')
