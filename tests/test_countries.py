import pytest

from clipper import countries
from clipper.collectors.gdelt import GdeltCollector
from clipper.collectors.outlet_feeds import load_outlets
from clipper.queries import Query, to_gdelt


@pytest.mark.parametrize("iso, fips", [("IT", "IT"), ("DE", "GM"), ("GB", "UK"), ("ES", "SP"),
                                       ("AT", "AU"), ("AU", "AS"), ("KE", "KE"), ("GN", "GV"),
                                       ("NO", "NO"), ("ZA", "SF")])
def test_gdelt_codes_differ_from_iso(iso, fips):
    assert countries.gdelt_code(iso) == fips


def test_names_and_article():
    assert countries.name("de") == "Germany"
    assert countries.in_country("IT") == "Italy"
    assert countries.in_country("GB") == "the United Kingdom"
    assert countries.in_country("NL") == "the Netherlands"
    assert countries.name("ZZ") == "ZZ" and not countries.is_known("ZZ")


def test_every_entry_is_well_formed():
    table = countries._table()
    assert len(table) > 240
    assert all(isinstance(k, str) and len(k) == 2 and k.isupper() for k in table)
    codes = [v["gdelt"] for v in table.values() if v["gdelt"]]
    assert all(isinstance(c, str) and len(c) == 2 for c in codes)
    assert len(codes) == len(set(codes))  # no two countries share a GDELT code


def test_outlet_list_only_when_bundled():
    assert countries.outlet_list("IT") == "it"
    assert countries.outlet_list("FR") == ""
    assert load_outlets("") == []


def test_gdelt_collector_uses_gdelt_code_and_skips_uncovered_country():
    q = Query("subject", "x", [["Acme"]], [])
    assert "sourcecountry:GM" in to_gdelt(q, GdeltCollector("DE", ["de"], []).country, ["de"], [])
    items, statuses = GdeltCollector("PS", ["ar"], []).collect([q], window=None)
    assert items == [] and statuses[0].ok is False
    assert statuses[0].error == "country not covered by GDELT"


def test_unknown_language_is_left_out_of_gdelt_query():
    q = Query("subject", "x", [["Acme"]], [])
    assert to_gdelt(q, "KE", ["sw", "xx"], []) == "Acme sourcecountry:KE sourcelang:swahili"
