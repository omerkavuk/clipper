from datetime import datetime, timezone

from clipper.collectors import social
from clipper.collectors.social import SocialCollector, search_terms
from clipper.models import Item, RunWindow
from clipper.pipeline import collect
from clipper.queries import Query

WINDOW = RunWindow(datetime(2026, 11, 7, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))
SUBJECT = Query("subject", "Subject", [["Aurora Climate Lab", "Laboratorio Clima Aurora"]], [])
PROG = Query("programme:Green Schools", "P", [["Green Schools"]], [])
COMBINED = Query("subject×initiatives", "S", [["Aurora Climate Lab"], ["VELA"]], [])


class FakeReach:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        for key, out in self.answers.items():
            if args[0] == key or " ".join(args).startswith(key):
                return out
        return {"ok": True, "items": []}


def post(url, date, text="Aurora Climate Lab at COP31", author="someone"):
    return {"url": url, "title": "", "text": text, "author": author, "published_at": date}


def test_search_terms_skip_combined_queries_and_cap():
    assert search_terms([SUBJECT, COMBINED, PROG]) == [
        ("Aurora Climate Lab", "subject"), ("Laboratorio Clima Aurora", "subject"),
        ("Green Schools", "programme:Green Schools")]


def test_linkedin_goes_through_site_search_and_keeps_dated_posts_in_window():
    fake = FakeReach({"web": {"ok": True, "items": [
        post("https://www.linkedin.com/posts/a", "2026-11-10T00:00:00.000Z"),
        post("https://www.linkedin.com/posts/old", "2026-02-12T00:00:00.000Z"),
        post("https://www.linkedin.com/posts/nodate", None)]}})
    items, statuses = SocialCollector(["linkedin"], run=fake).collect([SUBJECT], WINDOW)
    assert fake.calls[0][:3] == ["web", "search", 'site:linkedin.com/posts "Aurora Climate Lab"']
    assert [i.url for i in items] == ["https://www.linkedin.com/posts/a"]
    it = items[0]
    assert it.source == "social:linkedin" and it.outlet == "LinkedIn · someone"
    assert it.outlet_domain == "linkedin.com" and it.matched_queries == ["subject"]
    assert all(s.ok for s in statuses)


def test_login_needed_stops_that_platform_with_plain_reason():
    fake = FakeReach({"twitter": {"ok": False, "items": [], "error": {"code": "NEED_LOGIN"}}})
    items, statuses = SocialCollector(["x"], run=fake).collect([SUBJECT, PROG], WINDOW)
    assert items == [] and len(fake.calls) == 1
    assert statuses[0].error == "needs you to log in once"


def test_youtube_results_are_read_for_their_date():
    fake = FakeReach({
        "youtube search": {"ok": True, "items": [post("https://youtu.be/v1", None)]},
        "youtube read": {"ok": True, "items": [{"published_at": "2026-11-09"}]}})
    items, _ = SocialCollector(["youtube"], run=fake).collect([PROG], WINDOW)
    assert len(items) == 1 and items[0].published_at.date().isoformat() == "2026-11-09"


def test_social_posts_survive_default_social_domain_exclusion(tmp_path):
    from clipper.project import load_project
    from clipper.store import Store
    from pathlib import Path
    p = load_project(Path(__file__).parent.parent / "examples" / "aurora-climate-lab")
    when = datetime(2026, 11, 10, tzinfo=timezone.utc)
    social_item = Item("social:linkedin", "LinkedIn", "linkedin.com", "https://www.linkedin.com/posts/a",
                       "Aurora Climate Lab", when, when, matched_queries=["subject"])
    page = Item("google_news", "LinkedIn", "linkedin.com", "https://www.linkedin.com/company/x",
                "Aurora Climate Lab", when, when, matched_queries=["subject"])

    class Fake:
        name = "fake"

        def collect(self, queries, window):
            return [social_item, page], []

    with Store(tmp_path / "s.db") as store:
        run = collect(p, store, WINDOW, [Fake()])
        assert [i.source for i in store.items(run)] == ["social:linkedin"]


def test_private_copy_uses_its_own_home_and_port(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        class Done:
            stdout, stderr = '{"ok": true, "items": []}', ""
        return Done()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(social.subprocess, "run", fake_run)
    assert social.run_reach(["channels"])["ok"] is True
    assert seen["HOME"] == str(tmp_path / ".clipper" / "reach")
    assert seen["USERPROFILE"] == seen["HOME"] and seen["REACH_BROWSER_PORT"] == "9333"


def test_own_accounts_are_left_out():
    fake = FakeReach({"web": {"ok": True, "items": [
        post("https://www.linkedin.com/posts/aurora-climate-lab_afsic2026-x", "2026-11-10T00:00:00Z"),
        post("https://www.linkedin.com/posts/aurora-in-italy_italy-and-aurora", "2026-11-10T00:00:00Z"),
        post("https://www.linkedin.com/posts/aurora-climate-lab-fan_y", "2026-11-10T00:00:00Z")]}})
    c = SocialCollector(["linkedin"], run=fake, own_accounts=["Aurora-Climate-Lab"])
    items, _ = c.collect([PROG], WINDOW)
    assert sorted(i.url for i in items) == ["https://www.linkedin.com/posts/aurora-climate-lab-fan_y",
                                            "https://www.linkedin.com/posts/aurora-in-italy_italy-and-aurora"]


def test_report_scope_names_social_platforms():
    from pathlib import Path
    from clipper.project import load_project
    from clipper.layout import scope as _scope
    p = load_project(Path(__file__).parent.parent / "examples" / "aurora-climate-lab")
    assert _scope(p) == "online media in Italy"
    p.social = ["linkedin", "youtube"]
    assert _scope(p) == "online media in Italy, LinkedIn, YouTube"
