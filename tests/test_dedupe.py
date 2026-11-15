from datetime import datetime, timezone

from clipper.dedupe import group_stories, is_duplicate, normalize_url
from clipper.models import Item

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def item(title, url="https://a.it/x", domain="a.it"):
    return Item(source="rss", outlet="A", outlet_domain=domain, url=url, title=title,
                published_at=NOW, collected_at=NOW)


def test_normalize_url():
    assert normalize_url("https://WWW.Teleborsa.it/News/x/?utm_source=a#top") == "https://teleborsa.it/News/x"


def test_normalize_url_keeps_other_params_and_drops_tracking():
    assert normalize_url("https://a.it/p?id=3&fbclid=z&gclid=y&oc=5&utm_medium=m") == "https://a.it/p?id=3"


def test_same_url_is_duplicate():
    assert is_duplicate(item("A", "https://www.a.it/x/?utm_source=q"), item("B", "https://a.it/x"))


def test_google_copy_is_duplicate_of_outlet_item():
    a = item("Clima: premiati 40 studenti", "https://a.it/clima")
    b = item("  Clima:  PREMIATI 40 studenti ", "https://news.google.com/rss/articles/abc")
    b.outlet_domain = "a.it"
    assert is_duplicate(a, b)


def test_different_outlet_same_title_not_duplicate():
    assert not is_duplicate(item("Same", "https://a.it/1", "a.it"), item("Same", "https://b.it/2", "b.it"))


def test_reprints_grouped_as_one_story():
    titles = ["Clima, premiati a Roma 40 studenti ricercatori di oltre 12 regioni con Green Schools",
              "Green Schools, premiati 40 studenti ricercatori da oltre 12 regioni",
              "Green Schools, concluso a Roma lo Open Day 2026: premiati 40 studenti ricercatori da oltre 12 regioni",
              "Italy-Kenya: cooperation and investment in sustainable energy"]
    groups = group_stories([item(t) for t in titles])
    assert sorted(len(g) for g in groups) == [1, 3]
    assert [3] in groups


def test_normalize_url_malformed_does_not_raise():
    assert normalize_url("http://a.it:notaport/x#frag") == "http://a.it:notaport/x"


def test_title_match_uses_token_jaccard():
    from clipper.dedupe import title_match
    assert title_match("Green Schools, premiati 40 studenti ricercatori da oltre 12 regioni",
                       "Green Schools premiati 40 studenti ricercatori da oltre 12 regioni - Eco")
    assert not title_match("Green Schools a Lumio", "Bilancio del Comune di Roma")
    assert not title_match("", "")


def test_title_match_threshold_boundary():
    from clipper.dedupe import title_match
    # 3 shared tokens of 5 in the union = exactly 0.6: passes
    assert title_match("alpha bravo charlie delta", "alpha bravo charlie echo")
    # 3 shared of 6 = 0.5: fails
    assert not title_match("alpha bravo charlie delta", "alpha bravo charlie echo foxtrot")
