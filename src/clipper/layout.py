"""The leadership report: figures first, then coverage grouped by programme, outlet first.

The layout is built from the tags as a list of blocks, so the Markdown copy and the Word
file always say the same thing. Only the short summary comes from Claude's draft.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from clipper import countries
from clipper.models import Item, Outlet, Project

PROFILE_TIERS = ("national", "broadcaster", "agency")  # most prominent first
OTHER = "other"
SUMMARY_SENTENCES = 3
# Country-code domains that are used as generic ones, so they say nothing about the country.
GENERIC_CCTLDS = {"tv", "io", "co", "me", "ai", "fm", "ly", "cc", "ws", "to", "eu", "int"}
TLD_EXCEPTIONS = {"uk": "GB"}


@dataclass
class Run:
    text: str
    url: str | None = None
    bold: bool = False


@dataclass
class Block:
    kind: str  # title | meta | h2 | h3 | outlet | bullet | para
    runs: list[Run] = field(default_factory=list)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _day(d: datetime) -> str:
    return f"{d:%d %b %Y}"


def _long_day(d: datetime) -> str:
    return f"{d.day} {d:%B %Y}"


def _domain(value: str) -> str:
    """The host of a URL or a bare domain, without www."""
    v = (value or "").strip().lower()
    return (urlsplit(v if "//" in v else "//" + v).hostname or "").removeprefix("www.")


def item_link(item: Item) -> str:
    return item.canonical_url or item.url


_NEXT_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u00C0-\u024F])")


def first_sentence(text: str) -> str:
    """The description shown under an outlet: one sentence only."""
    return _sentence(_NEXT_SENTENCE.split(" ".join((text or "").split()), maxsplit=1)[0])


def _sentence(text: str) -> str:
    text = " ".join((text or "").split()).rstrip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


# ---------------------------------------------------------------- groups

def report_groups(project: Project) -> list[tuple[str, list[str]]]:
    """(heading, programme names) in project order; the last group holds everything else."""
    custom = project.report_groups or {}
    taken: set[str] = set()
    groups: list[tuple[str, list[str]]] = []
    for heading, names in custom.items():
        if str(heading).casefold() == OTHER:
            continue
        members = [p for p in project.programmes if p.casefold() in {str(n).casefold() for n in names or []}]
        taken |= {m.casefold() for m in members}
        groups.append((str(heading), members))
    for p in project.programmes:
        if p.casefold() not in taken:
            groups.append((p, [p]))
    other = next((str(v) for k, v in custom.items() if str(k).casefold() == OTHER and isinstance(v, str)),
                 f"{project.organisation}: other coverage")
    groups.append((other, [OTHER]))
    return groups


# ---------------------------------------------------------------- where an outlet is

def _tier_of(outlets: list[Outlet]) -> dict[str, str]:
    return {o.domain.lower().removeprefix("www."): o.tier for o in outlets}


def _lookup(domain: str, table: dict[str, str]) -> str | None:
    while domain:
        if domain in table:
            return table[domain]
        if "." not in domain:
            return None
        domain = domain.split(".", 1)[1]
    return None


def where_from(item: Item, tags: dict, project: Project, listed: dict[str, str]) -> str:
    """"home", a foreign country code, or "unknown": a .com outlet nobody placed is not proof
    that it is domestic."""
    domain = _domain(item.outlet_domain or item.url)
    if domain and _lookup(domain, listed):
        return "home"
    tagged = str(tags.get("outlet_country") or "").upper()
    if tagged and countries.is_known(tagged):
        return "home" if tagged == project.country else tagged
    tld = domain.rsplit(".", 1)[-1] if domain else ""
    if len(tld) == 2 and tld not in GENERIC_CCTLDS:
        code = TLD_EXCEPTIONS.get(tld, tld.upper())
        if countries.is_known(code):
            return "home" if code == project.country else code
    return "unknown"


def foreign_country(item: Item, tags: dict, project: Project, listed: dict[str, str]) -> str | None:
    """The other country an outlet belongs to (from the tagger, else its country-code domain)."""
    domain = _domain(item.outlet_domain or item.url)
    if domain and _lookup(domain, listed):
        return None
    tagged = str(tags.get("outlet_country") or "").upper()
    if tagged:
        return tagged if tagged != project.country and countries.is_known(tagged) else None
    if not domain:
        return None
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) != 2 or tld in GENERIC_CCTLDS:
        return None
    code = TLD_EXCEPTIONS.get(tld, tld.upper())
    if code == project.country or not countries.is_known(code):
        return None
    return code


# ---------------------------------------------------------------- the report

@dataclass
class ReportData:
    project: Project
    since: datetime
    last_day: datetime
    items: dict[int, Item]
    tags: dict[int, dict]
    stories: dict[int, list[int]]
    outlets: list[Outlet]
    previous_relevant: int | None
    summary: str
    max_items: int


def _rank_key(item: Item, tags: dict, tiers: dict[str, str]):
    tier = _lookup(_domain(item.outlet_domain or item.url), tiers)
    prominence = PROFILE_TIERS.index(tier) if tier in PROFILE_TIERS else len(PROFILE_TIERS)
    return (tags[item.id].get("naming") != "explicit", prominence, -item.published_at.timestamp())


def _at_a_glance(d: ReportData, relevant: list[Item], group_counts: list[tuple[str, int]],
                 tiers: dict[str, str]) -> list[Block]:
    p, tags = d.project, d.tags
    n = len(relevant)
    # Social posts are mentions, not publisher articles: they are counted on their own line.
    social = [i for i in relevant if (i.source or "").startswith("social:")]
    press = [i for i in relevant if i not in social]
    word = "mention" if social else "article"
    outlets = {i.outlet for i in press}
    out: list[Block] = []

    def bullet(label: str, text: str) -> None:
        out.append(Block("bullet", [Run(f"{label}: ", bold=True), Run(text)]))

    bullet("Coverage", f"{_plural(len(press), 'article')} in {_plural(len(outlets), 'outlet')} between "
                       f"{_long_day(d.since)} and {_long_day(d.last_day)}.")
    if social:
        accounts = {i.outlet for i in social}
        bullet("Social media", f"{_plural(len(social), 'post')} by {_plural(len(accounts), 'account')}.")
    tone = Counter(tags[i.id].get("tone") for i in relevant)
    bullet("Tone", f"{tone['positive']} positive, {tone['neutral']} neutral, {tone['negative']} negative.")
    top_name, top_n = max(group_counts, key=lambda g: g[1]) if group_counts else ("", 0)
    if top_n and top_name != group_counts[-1][0]:  # the last group is "other coverage"
        bullet(top_name, f"{top_n} of the {_plural(n, word)}.")
    named = sum(1 for i in relevant if tags[i.id].get("naming") == "explicit")
    parent = sum(1 for i in relevant if tags[i.id].get("naming") == "parent_only")
    text = f"{named} of the {_plural(n, word)}."
    if parent:
        text = f"{named} of the {_plural(n, word)}; {parent} more name only {p.parent_names[0] if p.parent_names else 'its parent body'}."
    bullet(f"{p.organisation} named directly", text)
    prominent = [i for i in press if _lookup(_domain(i.outlet_domain or i.url), tiers) in PROFILE_TIERS]
    if prominent:
        best = min(prominent, key=lambda i: _rank_key(i, tags, tiers))
        out.append(Block("bullet", [Run("Highest-profile coverage: ", bold=True), Run(f"{best.outlet}, "),
                                    Run(" ".join(best.title.split()), url=item_link(best)),
                                    Run(f" ({_day(best.published_at)}).")]))
    inst = [i for i in press if tags[i.id].get("type") == "institutional"]
    news = len(press) - len(inst)
    text = f"{_plural(news, 'news article')}; {len(inst)} from institutions"
    if inst:
        top, c = Counter(i.outlet for i in inst).most_common(1)[0]
        if c >= 2:
            text += f", {c} of them from {top}"
    bullet("Who published", text + ".")
    origin = Counter(where_from(i, tags[i.id], p, tiers) for i in press)
    abroad = Counter({c: k for c, k in origin.items() if c not in ("home", "unknown")})
    unknown = origin.get("unknown", 0)
    if abroad:
        where = ", ".join(countries.name(c) for c, _ in abroad.most_common())
        text = f"{_plural(sum(abroad.values()), 'article')} ({where})"
    else:
        text = f"none confirmed from outlets outside {countries.in_country(p.country)}"
    if unknown:
        text += f"; outlet country unknown for {_plural(unknown, 'article')}"
    bullet("Reach abroad", text + ".")
    if d.previous_relevant is not None:
        bullet("Compared with last period", f"{_plural(n, word)}, against {d.previous_relevant} "
                                             f"in the previous report.")
    return out


def _units(members: list[Item], d: ReportData) -> list[list[Item]]:
    """Stories (several outlets, one bullet) and single articles, within one group."""
    ids = {i.id for i in members}
    seen: set[int] = set()
    units: list[list[Item]] = []
    for story in d.stories.values():
        in_group = [d.items[i] for i in story if i in ids and i not in seen]
        if len({i.outlet for i in in_group}) > 1:
            in_group.sort(key=lambda i: (d.tags[i.id].get("naming") != "explicit", i.published_at))
            units.append(in_group)
            seen |= {i.id for i in in_group}
    units += [[i] for i in members if i.id not in seen]
    return units


def _outlet_heading(unit: list[Item]) -> str:
    names: list[str] = []
    for i in unit:
        if i.outlet not in names:
            names.append(i.outlet)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _bullet(unit: list[Item], d: ReportData, guard) -> Block:
    lead = unit[0]
    runs: list[Run] = []
    desc = guard(first_sentence(d.tags[lead.id].get("summary", "")))
    if desc:
        runs.append(Run(desc + " "))
    for n, it in enumerate(unit):
        if n:
            runs.append(Run("; "))
        runs.append(Run(" ".join(it.title.split()), url=item_link(it)))
        if len(unit) > 1:
            runs.append(Run(f" · {it.outlet}"))
        runs.append(Run(f" · {_day(it.published_at)}"))
        if it.subscribers_only:
            runs.append(Run(" · subscribers only"))
    return Block("bullet", runs)


def build(d: ReportData, guard=lambda s: s) -> list[Block]:
    p = d.project
    blocks = [Block("title", [Run(f"{p.organisation} media coverage")]),
              Block("meta", [Run(f"{p.organisation} · {_day(d.since)} – {_day(d.last_day)}"
                                 f" · Scope: {scope(p)}")])]
    relevant = [d.items[i] for i, t in d.tags.items() if t.get("relevant") is True and i in d.items]
    if not relevant:
        blocks.append(Block("para", [Run(f"No coverage of {p.organisation} was found in this period.")]))
        return blocks

    tiers = _tier_of(d.outlets)
    groups = report_groups(p)
    by_programme = {name.casefold(): heading for heading, names in groups for name in names}
    members: dict[str, list[Item]] = {h: [] for h, _ in groups}
    for i in relevant:
        related = str(d.tags[i.id].get("related_to") or OTHER).casefold()
        members[by_programme.get(related, groups[-1][0])].append(i)
    group_counts = [(h, len(members[h])) for h, _ in groups]

    blocks.append(Block("h2", [Run("At a glance")]))
    blocks += _at_a_glance(d, relevant, group_counts, tiers)
    if d.summary:
        blocks += [Block("h2", [Run("Summary")]), Block("para", [Run(d.summary)])]

    shown = set(i.id for i in sorted(relevant, key=lambda i: _rank_key(i, d.tags, tiers))[:d.max_items])
    blocks.append(Block("h2", [Run("By topic")]))
    order = sorted(groups, key=lambda g: (len(members[g[0]]) == 0, -len(members[g[0]])))
    for heading, _ in order:
        items = members[heading]
        if not items:
            blocks += [Block("h3", [Run(heading)]), Block("para", [Run("No coverage this period.")])]
            continue
        blocks.append(Block("h3", [Run(f"{heading} ({_plural(len(items), 'article')})")]))
        listed = [i for i in items if i.id in shown]
        by_heading: dict[str, list[list[Item]]] = {}
        for unit in _units(listed, d):
            by_heading.setdefault(_outlet_heading(unit), []).append(unit)

        def order_key(entry):
            units = entry[1]
            best = min((_rank_key(u[0], d.tags, tiers) for u in units))
            return (best[1], -sum(len(u) for u in units), best[2])

        for outlet, units in sorted(by_heading.items(), key=order_key):
            blocks.append(Block("outlet", [Run(outlet, bold=True)]))
            units.sort(key=lambda u: u[0].published_at, reverse=True)
            blocks += [_bullet(u, d, guard) for u in units]
        hidden = len(items) - len(listed)
        if hidden:
            blocks.append(Block("para", [Run(f"Plus {_plural(hidden, 'more article')}.")]))
    return blocks


def scope(project: Project) -> str:
    from clipper.collectors.social import PLATFORM_NAMES
    text = f"online media in {countries.in_country(project.country)}"
    platforms = [PLATFORM_NAMES[s] for s in project.social if s in PLATFORM_NAMES]
    return f"{text}, {', '.join(platforms)}" if platforms else text


# ---------------------------------------------------------------- output

def _md_runs(runs: list[Run]) -> str:
    out = []
    for r in runs:
        text = r.text.replace("[", "(").replace("]", ")")
        if r.url:
            url = r.url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            text = f"[{text}]({url})"
        if r.bold and text.strip():
            stripped = text.rstrip()
            text = f"**{stripped}**" + text[len(stripped):]
        out.append(text)
    return "".join(out).rstrip()


def to_markdown(blocks: list[Block]) -> str:
    prefix = {"title": "# ", "h2": "## ", "h3": "### ", "bullet": "- "}
    lines: list[str] = []
    for b in blocks:
        text = _md_runs(b.runs)
        if b.kind == "outlet":
            text = "**" + "".join(r.text for r in b.runs).replace("[", "(").replace("]", ")") + "**"
        line = prefix.get(b.kind, "") + text
        if b.kind == "bullet" and lines and lines[-1].startswith("- "):
            lines.append(line)
        else:
            if lines:
                lines.append("")
            lines.append(line)
    return "\n".join(lines) + "\n"


DARK_BLUE = (0x00, 0x20, 0x60)
LINK_BLUE = (0x05, 0x63, 0xC1)


def _add_link(paragraph, text: str, url: str) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "%02X%02X%02X" % LINK_BLUE)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def to_docx(blocks: list[Block], path) -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    for b in blocks:
        if b.kind in ("title", "h2", "h3"):
            level = {"title": 1, "h2": 2, "h3": 3}[b.kind]
            doc.add_heading("".join(r.text for r in b.runs), level=level)
            continue
        para = doc.add_paragraph(style="List Bullet" if b.kind == "bullet" else None)
        if b.kind == "outlet":
            run = para.add_run("".join(r.text for r in b.runs))
            run.bold = True
            run.font.size = Pt(13)
            run.font.color.rgb = RGBColor(*DARK_BLUE)
            para.paragraph_format.keep_with_next = True
            para.paragraph_format.space_before = Pt(8)
            para.paragraph_format.space_after = Pt(2)
            continue
        for r in b.runs:
            if r.url:
                _add_link(para, r.text, r.url)
            else:
                para.add_run(r.text).bold = r.bold
    doc.save(str(path))
