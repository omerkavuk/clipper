from pathlib import Path

import pytest
import responses
import yaml

from clipper.project import ProjectError, load_project, save_project
from clipper.sources import (SourcesError, build_sources, check_site, normalize_domain,
                             project_outlets)

EXAMPLE = Path(__file__).parent.parent / "examples" / "aurora-climate-lab"


@pytest.fixture
def p(tmp_path):
    proj = load_project(EXAMPLE)
    proj.folder = tmp_path / "proj"
    save_project(proj)
    return proj


def fake_check(table):
    return lambda domain: table.get(domain, (True, None))


def test_without_own_list_the_bundled_country_list_is_used(p):
    assert len(project_outlets(p)) >= 54


def test_first_build_keeps_chosen_types_adds_checked_sites(p):
    add = [{"name": "ISPI", "domain": "https://www.ispionline.it/it", "tier": "think_tank"},
           {"name": "Gone", "domain": "gone.example", "tier": "think_tank"},
           {"name": "Bad tier", "domain": "x.example", "tier": "blog"}]
    out = build_sources(p, add=add, tiers=["national", "think_tank"],
                        check=fake_check({"ispionline.it": (True, "https://www.ispionline.it/feed"),
                                          "gone.example": (False, None)}))
    assert out["added"] == ["ispionline.it"] and out["unreachable"] == ["gone.example"]
    assert out["skipped"] == ["Bad tier"]
    assert set(out["by_tier"]) == {"national", "think_tank"}
    outlets = project_outlets(p)
    ispi = next(o for o in outlets if o.domain == "ispionline.it")
    assert ispi.feed == "https://www.ispionline.it/feed" and ispi.tier == "think_tank"
    assert (Path(p.folder) / "sources.yaml").is_file()


def test_later_build_keeps_list_and_removes(p):
    build_sources(p, tiers=["national"], check=fake_check({}))
    n = len(project_outlets(p))
    first = project_outlets(p)[0].domain
    out = build_sources(p, remove=[f"www.{first}"], tiers=["agency"], check=fake_check({}))
    assert out["removed"] == [first] and out["sources"] == n - 1  # tiers ignored after the first build


def test_unknown_tier_rejected(p):
    with pytest.raises(SourcesError, match="unknown source type"):
        build_sources(p, tiers=["blogs"])


def test_normalize_domain():
    assert normalize_domain("https://WWW.Ansa.it/sito/notizie") == "ansa.it"
    assert normalize_domain("iai.it") == "iai.it"


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>a</title><link>https://ex.org/a</link></item></channel></rss>"""


@responses.activate
def test_check_site_finds_feed_from_page_link():
    responses.get("https://ex.org/", body='<html><head><link rel="alternate" '
                  'type="application/rss+xml" href="/news.xml"></head></html>')
    responses.get("https://ex.org/news.xml", body=RSS)
    assert check_site("ex.org") == (True, "https://ex.org/news.xml")


@responses.activate
def test_check_site_reachable_without_feed_and_unreachable():
    responses.get("https://nofeed.org/", body="<html></html>")
    for path in ("/feed", "/rss", "/rss.xml", "/feed.xml", "/feeds/all.rss", "/index.xml"):
        responses.get("https://nofeed.org" + path, status=404)
    assert check_site("nofeed.org") == (True, None)
    responses.get("https://down.org/", status=500)
    assert check_site("down.org") == (False, None)


def test_social_platforms_validated(p, tmp_path):
    raw = yaml.safe_load((Path(p.folder) / "project.yaml").read_text(encoding="utf-8"))
    for social, ok in [(["x", "linkedin"], True), (["myspace"], False)]:
        raw["social"] = social
        (Path(p.folder) / "project.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        if ok:
            assert load_project(p.folder).social == social
        else:
            with pytest.raises(ProjectError, match="social"):
                load_project(p.folder)
