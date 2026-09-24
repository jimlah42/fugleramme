"""A weekly "birds around home" email from the frame's own BirdNET-Go.

Top-level numbers, the week's collage (the frame's own renderer), the new birds
with their plates, and the regulars. Sent from your own mail account with an
app password - no mail server to run.

    uv run python extras/weekly_email.py --sample --preview /tmp/week.html   # draft, sample data
    uv run python extras/weekly_email.py --preview /tmp/week.html            # this week, real data
    uv run python extras/weekly_email.py --send                              # this week, emailed

Settings live in ~/.config/fugleramme/weekly-email.json (see extras/README.md).
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import smtplib
import ssl
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from PIL import Image

from fugleramme.names import canonical, variants_for
from fugleramme.render.collage import render_collage

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "assets" / "artwork"
STYLE = "classic"
LABELS = REPO / "assets" / "birdnet_labels_v2.4.txt"
SETTINGS = Path.home() / ".config" / "fugleramme" / "weekly-email.json"
PAPER = (242, 237, 226)  # the frame's own paper tone
COLLAGE_BIRDS = 12
REGULARS = 8  # the most-heard leaderboard
BAR = "#5a8a2c"  # leaf green; passes the dataviz lightness/chroma/contrast checks on the paper

# ---------------------------------------------------------------- data


def common_names() -> dict[str, str]:
    out = {}
    for line in LABELS.read_text().splitlines():
        if "_" in line:
            sci, common = line.split("_", 1)
            out[canonical(sci.strip())] = common.strip()
    return out


def sample_week() -> dict:
    """A plausible Brisbane week in late September, for the draft."""
    counts = {
        "Trichoglossus moluccanus": 612,
        "Manorina melanocephala": 488,
        "Gymnorhina tibicen": 301,
        "Cracticus nigrogularis": 244,
        "Corvus orru": 198,
        "Grallina cyanoleuca": 151,
        "Philemon corniculatus": 133,
        "Entomyzon cyanotis": 97,
        "Cacatua galerita": 90,
        "Lichmera indistincta": 74,
        "Trichoglossus chlorolepidotus": 61,
        "Vanellus miles": 52,
        "Sphecotheres vieilloti": 45,
        "Rhipidura leucophrys": 38,
        "Dacelo novaeguineae": 33,
        "Cracticus torquatus": 29,
        "Scythrops novaehollandiae": 12,
        "Alisterus scapularis": 11,
        "Eudynamys orientalis": 9,
        "Todiramphus sanctus": 7,
        "Centropus phasianinus": 3,
        "Alectura lathami": 3,
        "Malurus cyaneus": 2,
    }
    return {
        "start": date(2026, 9, 14),
        "end": date(2026, 9, 20),
        "counts": counts,
        "new": ["Centropus phasianinus"],
        "arrivals": ["Scythrops novaehollandiae", "Eudynamys orientalis"],
        "quiet": ["Zosterops lateralis"],
        "dawn": {"sci": "Cracticus nigrogularis", "time": "5:10am"},
        "busiest_hour": "6-7am",
        "hourly": [
            1,
            1,
            2,
            3,
            6,
            140,
            260,
            245,
            190,
            150,
            138,
            150,
            120,
            150,
            170,
            210,
            120,
            70,
            12,
            4,
            3,
            2,
            2,
            1,
        ],
        "parts": [
            {"label": l, "span": sp, "sci": b}
            for (l, sp, _), b in zip(
                PARTS,
                [
                    "Cracticus nigrogularis",
                    "Trichoglossus moluccanus",
                    "Manorina melanocephala",
                    "Manorina melanocephala",
                    "Trichoglossus moluccanus",
                    "Vanellus miles",
                ],
                strict=True,
            )
        ],
        "last_week_kinds": 20,
        "year": {"kinds": 128, "vs_last_year": 9},
        "once": ["Malurus cyaneus"],
        "sample": True,
    }


PARTS = [  # (label, hours) - the day as a person would split it
    ("Dawn", "5-8am", [5, 6, 7]),
    ("Morning", "8-11am", [8, 9, 10]),
    ("Midday", "11am-2pm", [11, 12, 13]),
    ("Afternoon", "2-5pm", [14, 15, 16]),
    ("Evening", "5-8pm", [17, 18, 19]),
    ("Night", "8pm-5am", [20, 21, 22, 23, 0, 1, 2, 3, 4]),
]
RELIABLE_CONF = 0.9  # one detection counts only if the model was this sure
YOUNG_DAYS = 21  # until then every bird is "new", so the email says so instead


def _get(base: str, path: str, **params) -> object:
    import urllib.parse
    import urllib.request

    url = f"{base.rstrip('/')}/api/v2/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _try(fetch, default=None):
    """A section's data, or its absence: an older BirdNET-Go without an endpoint
    just loses that section."""
    try:
        return fetch()
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return default


def reliable(row: dict) -> bool:
    return row["count"] >= 2 or (row.get("max_confidence") or 0) >= RELIABLE_CONF


def _hour(h: int) -> str:
    """15 -> "3-4pm", 11 -> "11am-12pm"."""
    start, end = h % 24, (h + 1) % 24
    half = lambda x: "am" if x < 12 else "pm"
    clock = lambda x: x % 12 or 12
    if half(start) == half(end):
        return f"{clock(start)}-{clock(end)}{half(end)}"
    return f"{clock(start)}{half(start)}-{clock(end)}{half(end)}"


def day_parts(by_hour: dict[str, list[float]]) -> list[dict]:
    """The most-heard bird in each part of the day, from per-hour calls."""
    out = []
    for label, span, hrs in PARTS:
        calls = {b: sum(v[h] for h in hrs) for b, v in by_hour.items()}
        top = max(calls, key=calls.get) if calls else None
        out.append({"label": label, "span": span, "sci": top if top and calls[top] >= 1 else None})
    return out


def real_week(end: date, base: str) -> dict:
    """The week from the frame's BirdNET-Go, false alarms left out."""
    start = end - timedelta(days=6)
    week = [
        r
        for r in _get(
            base,
            "analytics/species/summary",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        if reliable(r)
    ]
    counts = {canonical(r["scientific_name"]): int(r["count"]) for r in week}
    alltime = _get(base, "analytics/species/summary")
    began = min(
        datetime.fromisoformat(r["first_heard"]).date() for r in alltime if r.get("first_heard")
    )
    young = (end - began).days < YOUNG_DAYS

    firsts = {
        canonical(r["scientific_name"]): datetime.fromisoformat(r["first_heard"]).date()
        for r in alltime
        if r.get("first_heard")
    }
    # a first-ever bird is the week's event, however young the station
    new = [
        s
        for s in sorted(counts, key=counts.get, reverse=True)
        if start <= firsts.get(s, began) <= end
    ]
    data: dict = {
        "start": start,
        "end": end,
        "counts": counts,
        "sample": False,
        "began": began,
        "new": new,
        "young": young,
    }
    if not young:
        mig = _try(lambda: _get(base, "insights/migration"), {})
        data["arrivals"] = [
            canonical(a["scientific_name"])
            for a in mig.get("new_arrivals", [])
            if canonical(a["scientific_name"]) in counts
            and canonical(a["scientific_name"]) not in new
        ]
        data["quiet"] = [canonical(a["scientific_name"]) for a in mig.get("gone_quiet", [])]
        prev = _try(
            lambda: _get(
                base,
                "analytics/species/summary",
                start_date=(start - timedelta(days=7)).isoformat(),
                end_date=(start - timedelta(days=1)).isoformat(),
            )
        )
        if prev is not None:
            data["last_week_kinds"] = len([r for r in prev if reliable(r)])
    hours = _try(
        lambda: _get(
            base,
            "analytics/time/distribution/hourly",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
    )
    if hours:
        best = max(hours, key=lambda h: h["count"])
        data["busiest_hour"] = _hour(int(best["hour"]))
        data["hourly"] = [
            next((int(h["count"]) for h in hours if int(h["hour"]) == i), 0) for i in range(24)
        ]
    who = _try(
        lambda: _get(
            base,
            "analytics/time/distribution/species",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=10,
        )
    )
    if who:
        data["parts"] = day_parts(
            {
                canonical(w["scientificName"]): [b * w["total"] for b in w["buckets"]]
                for w in who
                if canonical(w["scientificName"]) in counts
            }
        )
    dawn = _try(lambda: _get(base, "insights/dawn-chorus").get("species") or [])
    if dawn:
        first = dawn[0]
        when = next((v for k, v in first.items() if "time" in k and isinstance(v, str)), "")
        data["dawn"] = {"sci": canonical(first["scientific_name"]), "time": when}
    kinds_total = len([r for r in alltime if reliable(r)])
    data["year"] = {"kinds": kinds_total, "since": None if not young else began}
    data["once"] = [canonical(r["scientific_name"]) for r in week if r["count"] == 1]
    return data


def wiki_line(sci: str, common: str) -> tuple[str, str]:
    """(one or two plain sentences, link) from Wikipedia's summary API; ("", "")
    when there is nothing, so the section simply shrinks."""
    import re
    import urllib.parse
    import urllib.request

    for title in (common, sci):
        url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(
            title.replace(" ", "_")
        )
        req = urllib.request.Request(url, headers={"User-Agent": "fugleramme-weekly-email/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                page = json.load(r)
        except (OSError, ValueError):
            continue
        text = page.get("extract") or ""
        if page.get("type") == "standard" and text:
            # the first sentences are usually classification; keep the ones a
            # person would say out loud
            dry = re.compile(
                r"famil|genus|monotypic|subspecies|species of|taxon|described by|"
                r"binomial|order |clade|synonym",
                re.IGNORECASE,
            )
            sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if not dry.search(x)]
            line = " ".join(sentences[:2])
            return line, page.get("content_urls", {}).get("desktop", {}).get("page", "")
    return "", ""


# ---------------------------------------------------------------- pictures


def plate_for(sci: str) -> Path | None:
    found = variants_for(sci, IMAGES, STYLE)
    return found[0] if found else None


def on_paper(path: Path, side: int) -> Image.Image:
    """A plate on the frame's paper with the frame's own halo treatment, as a
    JPEG-able image (mail clients and WebP do not get on)."""
    from fugleramme.render.paper import PAD, process_sprite

    art = Image.open(path).convert("RGBA")
    art.thumbnail((side - 2 * PAD, side - 2 * PAD), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (side, side), PAPER)
    at = ((side - art.width) // 2 - PAD, (side - art.height) // 2 - PAD)
    sprite = process_sprite(art, at, textured=False)
    canvas.paste(sprite, at, sprite)
    return canvas


def collage(data: dict, names: dict[str, str]) -> Image.Image:
    top = sorted(data["counts"], key=data["counts"].get, reverse=True)
    picked = list(dict.fromkeys(data.get("new", []) + data.get("arrivals", []) + top))
    entries = [(s, plate_for(s)) for s in picked if plate_for(s)][:COLLAGE_BIRDS]
    # labels a size up from the frame's default: on a phone the collage is ~350px wide
    return render_collage(
        entries, resolution=(1100, 1000), label_size="large", label_text=lambda s: names.get(s, s)
    )


def jpeg(img: Image.Image, q: int = 84) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- the email


def friendly(n: int) -> str:
    if n >= 300:
        return "all week, every day"
    if n >= 100:
        return "most days"
    if n >= 20:
        return "several times"
    return "now and then"


def build(data: dict, household: str = "home") -> tuple[str, str, dict[str, bytes]]:
    """(subject, html with cid: images, {cid: jpeg bytes}). Every section beyond
    the collage is optional: missing data just leaves it out."""
    names = common_names()
    nm = lambda s: names.get(s, s)
    counts = data["counts"]
    kinds = len(counts)
    new = [s for s in data.get("new", []) if s in counts or data["sample"]]
    arrivals = data.get("arrivals", [])
    quiet = data.get("quiet", [])
    top = sorted(counts, key=counts.get, reverse=True)
    regulars = top[:REGULARS]
    span = f"{data['start']:%-d} - {data['end']:%-d %B}"
    spotlight = (arrivals or new or top)[0]

    images: dict[str, bytes] = {"collage": jpeg(collage(data, names))}
    wanted: dict[str, int] = {}
    new_side = 150 if len(new) <= 5 else 110
    for group, side in (
        (regulars, 44),
        (arrivals + quiet, 90),
        (new, new_side),
        ([spotlight], 420),
    ):
        for b in group:
            wanted[b] = max(wanted.get(b, 0), side)
    if data.get("dawn"):
        wanted[data["dawn"]["sci"]] = max(wanted.get(data["dawn"]["sci"], 0), 90)
    for part in data.get("parts", []):
        if part["sci"]:
            wanted[part["sci"]] = max(wanted.get(part["sci"], 0), 70)
    for s, side in wanted.items():
        p = plate_for(s)
        if p:
            images[s.replace(" ", "-")] = jpeg(on_paper(p, max(side * 2, 180)))

    serif = "Georgia,'Times New Roman',serif"
    ink, mute, soft = "#2b2a26", "#8a8375", "#4a463f"

    def img(s: str, width: int) -> str:
        cid = s.replace(" ", "-")
        if cid not in images:
            return ""
        return (
            f'<img src="cid:{cid}" width="{width}" alt="{html.escape(nm(s))}" '
            f'style="display:block;width:100%;max-width:{width}px;height:auto;border:0">'
        )

    def heading(text: str) -> str:
        return (
            f'<div style="font:600 20px/1.3 {serif};color:{ink};border-bottom:1px solid #ddd4c4;'
            f'padding-bottom:6px;margin-bottom:4px">{text}</div>'
        )

    def section(inner: str, top_pad: int = 22) -> str:
        return f'<tr><td class="pad" style="padding:{top_pad}px 28px 4px">{inner}</td></tr>'

    def tile(big: str, small: str) -> str:
        return (
            f'<td align="center" width="33%" style="padding:14px 8px;background:#f7f3ea;border-radius:6px">'
            f'<div class="big" style="font:600 28px/1.1 {serif};color:{ink}">{big}</div>'
            f'<div style="font:13px/1.4 {serif};color:#6d675c">{small}</div></td>'
        )

    # --- headline numbers
    lw = data.get("last_week_kinds")
    diff = (
        ""
        if lw is None
        else (f"{kinds - lw:+d} on last week" if kinds != lw else "same as last week")
    )
    t1 = tile(str(kinds), "kinds of birds" + (f"<br>{diff}" if diff else ""))
    t2 = tile(
        str(len(new) + len(arrivals)), "new to the garden" if not arrivals else "new or returning"
    )
    t3 = (
        tile(html.escape(data["busiest_hour"]), "the busiest hour")
        if data.get("busiest_hour")
        else tile(html.escape(nm(top[0]).split()[-1]), "heard most")
    )
    tiles = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{t1}<td width="10"></td>{t2}<td width="10"></td>{t3}</tr></table>'

    rows = []
    # --- bird of the week
    line, link = wiki_line(spotlight, nm(spotlight))
    why = (
        "back for the season"
        if spotlight in arrivals
        else "new to the garden"
        if spotlight in new
        else "the week's most talkative"
    )
    more = f' <a href="{html.escape(link)}" style="color:#6b5a2e">Read more</a>' if link else ""
    rows.append(
        section(
            heading("Bird of the week")
            + f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td class="stack" width="210" valign="top" style="padding:8px 0"><div class="spot-img">{img(spotlight, 210)}</div></td>
<td class="stack spot-txt" valign="top" style="padding:10px 0 8px 18px">
  <div style="font:600 21px/1.3 {serif};color:{ink}">{html.escape(nm(spotlight))}</div>
  <div style="font:italic 13px/1.4 {serif};color:{mute}">{html.escape(spotlight)} &middot; {why}</div>
  <div style="font:15px/1.55 {serif};color:{soft};padding-top:8px">{html.escape(line)}{more}</div>
</td></tr></table>"""
        )
    )

    # --- new this week: every one, big when few, a grid when many
    shown_new = [b for b in new if b != spotlight]
    if shown_new and len(shown_new) <= 4:
        items = "".join(
            f"""<tr><td width="120" valign="middle" style="padding:8px 0">{img(b, 120)}</td>
<td valign="middle" style="padding:8px 0 8px 16px">
  <div style="font:600 18px/1.3 {serif};color:{ink}">{html.escape(nm(b))}</div>
  <div style="font:15px/1.5 {serif};color:{soft}">First time the frame has heard one.</div></td></tr>"""
            for b in shown_new
        )
        rows.append(
            section(
                heading("New to the garden")
                + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{items}</table>'
            )
        )
    elif shown_new:
        intro = (
            "The frame is just getting started, so everything it hears is a first."
            if data.get("young")
            else f"{len(new)} first-ever visitors this week."
        )
        cells = [
            f"""<td width="25%" align="center" valign="top" style="padding:6px 4px">{img(b, 110)}
<div style="font:13px/1.3 {serif};color:{ink};padding-top:4px">{html.escape(nm(b))}</div></td>"""
            for b in shown_new
        ]
        grid = "".join(
            "<tr>" + "".join(cells[i : i + 4]) + "</tr>" for i in range(0, len(cells), 4)
        )
        rows.append(
            section(
                heading("New to the garden")
                + f'<div style="font:15px/1.5 {serif};color:{soft};padding:6px 0 4px">{intro}</div>'
                + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{grid}</table>'
            )
        )

    # --- comings and goings
    if arrivals or quiet:

        def chips(birds: list[str]) -> str:
            cells = "".join(
                f"""<td width="110" align="center" valign="top" style="padding:6px 4px">
{img(s, 90)}<div style="font:14px/1.3 {serif};color:{ink};padding-top:4px">{html.escape(nm(s))}</div></td>"""
                for s in birds[:5]
            )
            return f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>'

        inner = heading("Comings and goings")
        if arrivals:
            inner += (
                f'<div style="font:15px/1.5 {serif};color:{soft};padding:6px 0 2px">Back for the season:</div>'
                + chips(arrivals)
            )
        if quiet:
            inner += (
                f'<div style="font:15px/1.5 {serif};color:{soft};padding:10px 0 2px">Gone quiet this week: '
                + ", ".join(html.escape(nm(s)) for s in quiet)
                + ".</div>"
            )
        rows.append(section(inner))

    # --- early riser + year so far, side by side
    cards = []
    if data.get("dawn"):
        d = data["dawn"]
        cards.append(f"""<td class="stack" width="50%" valign="top" style="padding:14px;background:#f2ede2;border:1px solid #e0d6c3;border-radius:6px">
<div style="font:600 16px/1.3 {serif};color:{ink}">Early riser</div>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td width="70" style="padding-top:8px">{img(d["sci"], 70)}</td>
<td style="padding:8px 0 0 12px;font:15px/1.45 {serif};color:{soft}">{html.escape(nm(d["sci"]))} started the dawn chorus, usually around <b>{html.escape(d["time"])}</b>.</td></tr></table></td>""")
    if data.get("year"):
        y = data["year"]
        vs = y.get("vs_last_year")
        extra = (
            ""
            if vs is None
            else f" That's <b>{abs(vs)} {'more' if vs > 0 else 'fewer'}</b> than by this time last year."
            if vs
            else " Exactly level with this time last year."
        )
        since = y.get("since")
        title = "So far" if since else "The year so far"
        when = f"since the frame started listening on {since:%-d %B}" if since else "since January"
        cards.append(f"""<td class="stack" width="50%" valign="top" style="padding:14px;background:#f2ede2;border:1px solid #e0d6c3;border-radius:6px">
<div style="font:600 16px/1.3 {serif};color:{ink}">{title}</div>
<div style="font:15px/1.45 {serif};color:{soft};padding-top:8px"><b>{y["kinds"]}</b> kinds of birds {when}.{extra}</div></td>""")
    if cards:
        rows.append(
            section(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                + '<td class="stack-gap" width="12"></td>'.join(cards)
                + "</tr></table>"
            )
        )

    # --- through the day: calls per hour (one colour, only the peak labelled), then who
    hourly = data.get("hourly")
    if hourly and max(hourly) > 0:
        peak = max(hourly)
        top_h = hourly.index(peak)
        cols = "".join(
            f'''<td valign="bottom" align="center" style="padding:0 1px;height:96px">
{f'<div style="font:11px/1 {serif};color:{soft};padding-bottom:3px">{peak:,}</div>' if i == top_h else ""}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td height="{max(1, round(80 * n / peak))}"
 bgcolor="{BAR}" style="background:{BAR};height:{max(1, round(80 * n / peak))}px;border-radius:3px 3px 0 0;font-size:0;line-height:0">&nbsp;</td></tr></table></td>'''
            for i, n in enumerate(hourly)
        )
        ticks = "".join(
            f'<td colspan="6" style="font:11px/1.4 {serif};color:{mute};padding-top:4px">{t}</td>'
            for t in ("12am", "6am", "12pm", "6pm")
        )
        chart = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="table-layout:fixed;border-bottom:1px solid #d9cfbd"><tr>{cols}</tr></table>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed"><tr>{ticks}</tr></table>'
        )
        cards_html = ""
        parts = data.get("parts", [])
        if parts:
            cells = [
                f"""<td width="33%" align="center" valign="top" style="padding:8px 4px;background:#f2ede2;border:1px solid #e0d6c3;border-radius:6px">
<div style="font:600 14px/1.3 {serif};color:{ink}">{html.escape(pt["label"])}</div>
<div style="font:12px/1.3 {serif};color:{mute};padding-bottom:4px">{html.escape(pt["span"])}</div>
{img(pt["sci"], 70) if pt["sci"] else ""}
<div style="font:13px/1.3 {serif};color:{soft};padding-top:4px">{html.escape(nm(pt["sci"])) if pt["sci"] else "quiet"}</div></td>"""
                for pt in parts
            ]
            rows_ = ['<td width="8"></td>'.join(cells[i : i + 3]) for i in range(0, len(cells), 3)]
            cards_html = (
                f'<div style="font:15px/1.5 {serif};color:{soft};padding:14px 0 6px">Who you hear most, and when:</div>'
                + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                + '<tr><td colspan="5" height="8"></td></tr>'.join(f"<tr>{r}</tr>" for r in rows_)
                + "</table>"
            )
        rows.append(
            section(
                heading("Through the day")
                + f'<div style="font:15px/1.5 {serif};color:{soft};padding:6px 0 10px">Calls heard each hour, all week. Busiest around <b style="white-space:nowrap">{html.escape(data.get("busiest_hour", _hour(top_h)))}</b>.</div>'
                + chart
                + cards_html
            )
        )

    # --- most heard: one-colour bars, value at the tip (the rows are their own table)
    most = max(counts[b] for b in regulars) if regulars else 1
    bars = "".join(
        f'''<tr>
<td width="44" style="padding:4px 0">{img(b, 44)}</td>
<td class="name" width="150" style="padding:4px 10px;font:14px/1.3 {serif};color:{ink}">{html.escape(nm(b))}</td>
<td style="padding:4px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
  <td width="{max(2, round(88 * counts[b] / most))}%" height="14" bgcolor="{BAR}"
      style="background:{BAR};height:14px;border-radius:0 4px 4px 0;font-size:0;line-height:0">&nbsp;</td>
  <td style="padding-left:8px;font:13px/1 {serif};color:{soft};white-space:nowrap">{counts[b]:,}</td>
</tr></table></td></tr>'''
        for b in regulars
    )
    once = data.get("once", [])
    tail = (
        (
            f'<div style="font:14px/1.5 {serif};color:{mute};padding-top:8px">Heard just once: '
            + ", ".join(html.escape(nm(b)) for b in once)
            + ".</div>"
        )
        if once
        else ""
    )
    rows.append(
        section(
            heading("Most heard this week")
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{bars}</table>'
            + tail,
            18,
        )
    )

    sample_note = (
        (
            f'<p style="font:12px/1.4 {serif};color:#b3261e;margin:0 0 10px">'
            "DRAFT - sample data, not a real week.</p>"
        )
        if data["sample"]
        else ""
    )
    news = len(new) + len(arrivals)
    fresh = (f", {news} new" if not arrivals else f", {news} new or returning") if news else ""
    subject = f"Birds around {household}: {kinds} kinds{fresh} ({span})"
    head = """<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
@media (max-width:520px) {
  .pad { padding-left:16px !important; padding-right:16px !important; }
  .stack { display:block !important; width:100% !important; box-sizing:border-box; }
  .stack-gap { display:block !important; height:10px !important; width:100% !important; }
  .h1 { font-size:22px !important; }
  .big { font-size:22px !important; }
  .name { width:96px !important; font-size:13px !important; }
  .spot-img { max-width:260px !important; margin:0 auto; }
  .spot-txt { padding-left:0 !important; }
}
</style></head>"""
    body = f"""<!doctype html><html>{head}<body style="margin:0;padding:0;background:#e8e1d4">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e8e1d4">
<tr><td align="center" style="padding:20px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="width:100%;max-width:600px;background:#f2ede2;border-radius:8px">
<tr><td class="pad" style="padding:26px 28px 6px">
  {sample_note}
  <div style="font:13px/1.4 {serif};color:{mute};letter-spacing:.04em;text-transform:uppercase">Birds around {html.escape(household)} &middot; {span}</div>
  <div class="h1" style="font:600 27px/1.25 {serif};color:{ink};padding-top:6px">{kinds} kinds of birds visited this week</div>
</td></tr>
<tr><td class="pad" style="padding:16px 18px 6px">{img("collage", 564) or ""}</td></tr>
<tr><td class="pad" style="padding:14px 28px 4px">{tiles}</td></tr>
{"".join(rows)}
<tr><td class="pad" style="padding:22px 28px 26px;font:12px/1.5 {serif};color:{mute}">
  From the bird frame at {html.escape(household)}. Pictures are hand-coloured plates from
  John Gould's <i>The Birds of Australia</i> (1840s) and other old natural-history books.
</td></tr>
</table></td></tr></table></body></html>"""
    return subject, body, images


def inline_preview(body: str, images: dict[str, bytes]) -> str:
    for cid, data in images.items():
        body = body.replace(
            f"cid:{cid}", "data:image/jpeg;base64," + base64.b64encode(data).decode()
        )
    return body


def send(subject: str, body: str, images: dict[str, bytes], cfg: dict) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["from"], ", ".join(cfg["to"])
    if cfg.get("cc"):
        msg["Cc"] = ", ".join(cfg["cc"])  # send_message delivers to To and Cc alike
    msg.set_content("This week's birds - open in a mail app that shows pictures.")
    cids = {k: make_msgid(domain="fugleramme.local")[1:-1] for k in images}
    for k, cid in cids.items():
        body = body.replace(f"cid:{k}", f"cid:{cid}")
    msg.add_alternative(body, subtype="html")
    html_part = msg.get_payload()[1]
    for k, data in images.items():
        html_part.add_related(data, "image", "jpeg", cid=f"<{cids[k]}>")
    host, port = cfg.get("smtp_host", "smtp.gmail.com"), int(cfg.get("smtp_port", 465))
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context) as s:
            s.login(cfg["username"], cfg["app_password"].replace(" ", ""))
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls(context=context)
            s.login(cfg["username"], cfg["app_password"].replace(" ", ""))
            s.send_message(msg)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sample", action="store_true", help="use made-up data (for a draft)")
    ap.add_argument("--preview", type=Path, help="write a browser preview of the email here")
    ap.add_argument("--send", action="store_true", help="email it (settings file required)")
    ap.add_argument(
        "--end", type=date.fromisoformat, help="last day of the week (default: yesterday)"
    )
    ap.add_argument(
        "--today", action="store_true", help="the week ends today (the Sunday-evening send)"
    )
    ap.add_argument(
        "--birdnet", help="BirdNET-Go address (default: settings, else http://localhost:8080)"
    )
    ap.add_argument("--to", nargs="+", help="send to these instead of the settings' list (a test)")
    args = ap.parse_args()

    cfg = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    today = datetime.now().astimezone().date()
    end = args.end or (today if args.today else today - timedelta(days=1))
    base = args.birdnet or cfg.get("birdnet_url", "http://localhost:8080")
    data = sample_week() if args.sample else real_week(end, base)
    if not data["counts"]:
        print(f"no birds heard {data['start']} to {data['end']} - nothing to send")
        return
    subject, body, images = build(data, cfg.get("household", "home"))
    if args.preview:
        args.preview.write_text(
            f"<title>{html.escape(subject)}</title>" + inline_preview(body, images)
        )
        print(f"{subject}\npreview: {args.preview}")
    if args.send:
        if args.to:
            cfg = {**cfg, "to": args.to, "cc": []}
        send(subject, body, images, cfg)
        print(f"sent: {subject} -> {', '.join(cfg['to'] + cfg.get('cc', []))}")


if __name__ == "__main__":
    main()
