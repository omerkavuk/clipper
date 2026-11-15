from importlib import resources

import pytest

from clipper.collectors.outlet_feeds import load_outlets

TIERS = {"national", "regional", "agency", "broadcaster", "specialist", "trade", "institutional"}


def test_seed_list_covers_baseline_outlets():
    domains = {o.domain for o in load_outlets("it")}
    for d in ["ecodallecitta.it", "protectaweb.it", "teleborsa.it", "primapaginanews.it",
              "canaleenergia.com", "mase.gov.it", "adcgroup.it", "mediakey.it", "polito.it",
              "brand-news.it", "sgi-italia.org"]:
        assert d in domains


def test_seed_list_size():
    assert len(load_outlets("it")) >= 40


def test_entries_are_well_formed():
    outlets = load_outlets("it")
    assert len({o.domain for o in outlets}) == len(outlets)
    for o in outlets:
        assert o.name and o.domain and not o.domain.startswith("www.")
        assert o.tier in TIERS
        assert (o.region is None) == (o.tier != "regional" and o.domain != "polito.it")
        assert o.feed is None or o.feed.startswith("http")


def test_paywall_flags():
    by = {o.domain: o for o in load_outlets("it")}
    for d in ["adcgroup.it", "ilsole24ore.com", "corriere.it"]:
        assert by[d].paywall


def test_regional_coverage():
    regional = [o for o in load_outlets("it") if o.tier == "regional"]
    assert len(regional) >= 8


def test_unknown_code_raises_clear_error():
    with pytest.raises(ValueError, match="unknown outlet list"):
        load_outlets("zz")
    with pytest.raises(ValueError, match="unknown outlet list"):
        load_outlets("../it")


def test_data_file_is_package_resource():
    assert (resources.files("clipper") / "data" / "outlets" / "it.yaml").is_file()
