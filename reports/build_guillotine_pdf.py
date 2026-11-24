"""Build the guillotine week-N PDF from `python -m nfl.greport` JSON plus a row-count JSON.

    python reports/build_guillotine_pdf.py greport.json counts.json out.pdf
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether)

ET = ZoneInfo("America/New_York")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=3)
B = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=11.5)
SM = ParagraphStyle("sm", parent=B, fontSize=7.5, leading=9)
MONO = ParagraphStyle("mono", parent=SM, fontName="Courier", fontSize=6.5, leading=7.8)


def et(iso):
    if not iso:
        return ""
    d = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
    return d.strftime("%a %b %-d %-I:%M %p") if sys.platform != "win32" else d.strftime("%a %b %#d %#I:%M %p")


def f(x, nd=1):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x))


def table(rows, widths=None, head=1, zebra=True, bold_rows=()):
    t = Table(rows, colWidths=widths, repeatRows=head)
    style = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
             ("FONT", (0, 0), (-1, head - 1), "Helvetica-Bold", 7.5),
             ("BACKGROUND", (0, 0), (-1, head - 1), colors.HexColor("#1f2937")),
             ("TEXTCOLOR", (0, 0), (-1, head - 1), colors.white),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7cbd1")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]
    if zebra:
        for i in range(head, len(rows)):
            if (i - head) % 2:
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f3f4f6")))
    for i in bold_rows:
        style.append(("FONT", (0, i), (-1, i), "Helvetica-Bold", 7.5))
    t.setStyle(TableStyle(style))
    return t


def build(rep, counts, out, headline, deadlines):
    doc = SimpleDocTemplate(out, pagesize=landscape(letter), leftMargin=0.45 * inch, rightMargin=0.45 * inch,
                            topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                            title=f"Guillotine league week {rep['week']} moves", author="fantasy-db")
    w = rep["week"]
    s = []
    now = datetime.now(ET).strftime("%Y-%m-%d %-I:%M %p ET") if sys.platform != "win32" else datetime.now(ET).strftime("%Y-%m-%d %#I:%M %p ET")
    s.append(Paragraph(f"Guillotine league (Yahoo 1642654): week {w} moves for Pirrotine (team 11)", H1))
    s.append(Paragraph(f"Built {now} from guillotine.db on old-will-macbook. Recommendations only: nothing was "
                       f"changed or claimed on Yahoo.", SM))

    s.append(Paragraph("Deadlines", H2))
    s.append(table([["What", "When / rule"]] + deadlines, [2.6 * inch, 7.4 * inch]))

    s.append(Paragraph("Headline moves", H2))
    for i, h in enumerate(headline, 1):
        s.append(Paragraph(f"<b>{i}.</b> {h}", B))

    st = rep["standings_now"]
    s.append(Paragraph(f"Where you stand, week {w}", H2))
    rows = [["Team", "Proj wk " + str(w), "Live", "Season pts", "Pts/wk", "FAB"]]
    bold = []
    for i, t in enumerate(st["alive"], 1):
        rows.append([t["team_name"], f(t["proj_pts"], 2), f(t["pts"], 2), f(t["season_pts"], 2), f(t["pts_per_week"], 2), t["fab"]])
        if t["is_will"]:
            bold.append(i)
    s.append(table(rows, [2.6 * inch, 1 * inch, 0.8 * inch, 1 * inch, 0.8 * inch, 0.7 * inch], bold_rows=bold))
    s.append(Paragraph(f"Your projected margin over the lowest other team: <b>{f(st.get('will_margin_proj'), 2)}</b> "
                       f"({st.get('danger')}).", B))

    # ---------------------------------------------------------------- lineup
    s.append(PageBreak())
    s.append(Paragraph(f"Start / sit, week {w} (risk-adjusted = Yahoo projection minus half the weekly std dev)", H2))
    rows = [["Now", "Rec", "Player", "Pos", "Team", f"Wk{w} opp", "Kickoff (ET)", "Proj", "Mean", "SD", "Floor p25",
             "Low", "Matchup", f"Wk{w+1} opp", "SOS", "Risk-adj", "Last 3"]]
    order = {"QB": 0, "WR": 1, "RB": 2, "TE": 3, "W/R/T": 4, "BN": 5, "IR": 6}
    bold = []
    roster = sorted(rep["roster"], key=lambda m: (m["recommended"] != "START", -(m["risk_adj"] or 0)))
    for i, m in enumerate(roster, 1):
        rows.append([m["slot"], m["recommended"] + (" *" if m["change"] else ""), m["name"], m["pos"], m["team"],
                     m["opp"], et(m["kickoff_utc"]), f(m["proj"], 2), f(m["mean"]), f(m["sd"]), f(m["floor_p25"]),
                     f(m["min"]), f(m["matchup"], 2), m["opp_next"], f(m["sos"], 2), f(m["risk_adj"], 2),
                     ", ".join(f(x) for x in m["last3"])])
        if m["change"]:
            bold.append(i)
    s.append(table(rows, [x * inch for x in (0.42, 0.5, 1.3, 0.42, 0.42, 0.5, 1.05, 0.42, 0.42, 0.38, 0.52, 0.38,
                                             0.52, 0.55, 0.38, 0.52, 0.95)], bold_rows=bold))
    s.append(Paragraph("* = differs from your current Yahoo lineup. Matchup = points the week-"
                       f"{w} opponent allowed per game to that position in this league's half-PPR scoring, weeks 1-{w-1}, "
                       "over the league average (above 1.00 is a soft matchup). SOS = mean of the week "
                       f"{w} and week {w+1} matchup factors (the guillotine ends when one team is left).", SM))

    # ---------------------------------------------------------------- waivers
    s.append(Paragraph("Waiver pool (not on any of the three rosters), best risk-adjusted first", H2))
    rows = [["Player", "Pos", "Team", "Yahoo status", "From chopped team", f"Wk{w} opp", "Proj", "Mean", "SD",
             "Floor p25", "Games", "Matchup", "SOS", "Risk-adj"]]
    for m in rep["waiver_pool"][:14]:
        rows.append([m["name"], m["pos"], m["team"], m.get("yahoo_status") or "", "yes" if m["from_chopped_team"] else "",
                     m["opp"], f(m["proj"], 2), f(m["mean"]), f(m["sd"]), f(m["floor_p25"]), m["games"],
                     f(m["matchup"], 2), f(m["sos"], 2), f(m["risk_adj"], 2)])
    s.append(table(rows, [x * inch for x in (1.5, 0.45, 0.45, 1.3, 0.9, 0.6, 0.5, 0.5, 0.45, 0.6, 0.5, 0.6, 0.45, 0.6)]))

    # ---------------------------------------------------------------- rundown
    s.append(PageBreak())
    s.append(Paragraph("League rundown: starters' points by week (lowest each week is chopped)", H2))
    teams = {}
    for wk in rep["rundown"]:
        for t in wk["teams"]:
            teams.setdefault(t["team_name"], {})[wk["week"]] = t
    weeks = [wk["week"] for wk in rep["rundown"] if wk["week"] < w]
    names = sorted(teams, key=lambda n: (-max(teams[n]), -sum(t["pts"] for t in teams[n].values())))
    rows = [["Team"] + [f"W{x}" for x in weeks] + ["Out"]]
    cell_style = []
    for i, n in enumerate(names, 1):
        row = [n]
        out = ""
        for j, x in enumerate(weeks, 1):
            t = teams[n].get(x)
            row.append(f(t["pts"]) if t else "")
            if t and t["chopped_this_week"]:
                out = f"wk {x}"
                cell_style.append(("BACKGROUND", (j, i), (j, i), colors.HexColor("#fecaca")))
            if t and t["is_will"]:
                cell_style.append(("FONT", (j, i), (j, i), "Helvetica-Bold", 7.5))
        row.append(out or "alive")
        rows.append(row)
    t = table(rows, [1.6 * inch] + [0.62 * inch] * len(weeks) + [0.55 * inch])
    t.setStyle(TableStyle(cell_style))
    s.append(t)
    s.append(Paragraph("Red = that team's lowest-score week, when it was chopped. Your row in bold.", SM))

    rows = [["Week", "Your pts", "Your rank", "Lowest team", "Lowest pts", "Your margin over lowest", "Gap lowest to next",
             "Lowest = chopped?"]]
    for wk in rep["rundown"]:
        if wk["week"] >= w:
            continue
        me = rep["will_by_week"].get(str(wk["week"])) or rep["will_by_week"].get(wk["week"])
        low = wk["teams"][-1]
        rows.append([wk["week"], f(me and me["pts"]), me and f"{me['rank']} of {me['of']}", wk["lowest"], f(low["pts"]),
                     f(me and me["margin_over_lowest"]), f(wk["margin_low_to_next"]),
                     "yes" if wk["lowest_is_chopped"] else ("NO: " + ", ".join(wk["chopped"]))])
    s.append(Paragraph("Your week-by-week", H2))
    s.append(table(rows, [x * inch for x in (0.5, 0.8, 0.8, 2.0, 0.8, 1.5, 1.3, 1.6)]))

    # ---------------------------------------------------------------- verification
    s.append(PageBreak())
    s.append(Paragraph("Verification", H2))
    sc = rep["scoring"]
    s.append(Paragraph("<b>Scoring read from Yahoo league 1642654 /settings:</b> " +
                       "; ".join(f"{k.replace('Offense: ', '')} {v}" for k, v in sc.items()) +
                       f". Roster: {', '.join(rep['roster_slots'])}. No kicker or defense slots. "
                       "The main league (206739) is full PPR (1 per catch) with K, DEF and a superflex, so its "
                       "points are not reused here: every point in this report is Yahoo's own figure for league 1642654.", SM))
    rc = rep["recompute_check"]
    rows = [["Week", "Players with catches", "Match half-PPR recompute", "Match full-PPR recompute"]]
    for r in rc:
        rows.append([r["week"], r["n"], r["match_half_ppr"], r["match_full_ppr"]])
    s.append(Paragraph("Recomputed from nflverse stats with this league's rules vs Yahoo's league-1642654 points "
                       "(within 0.1; return and fumble-return TDs are not in nflverse stats, so a few miss):", SM))
    s.append(table(rows, [0.6 * inch, 1.4 * inch, 1.7 * inch, 1.7 * inch]))
    s.append(Spacer(1, 4))
    rows = [["Week"] + [f"{d} {t}" for d in ("nfl.db", "guillotine.db") for t in ("rosters", "proj", "actuals", "stats")]]
    for r in counts:
        rows.append([r["week"]] + [r[f"{d}_{t}"] for d in ("nfl", "g") for t in ("rosters", "projections", "actuals", "stats")])
    s.append(Paragraph("Rows per week. stats are nflverse (league-independent, copied read-only); rosters are 8 teams "
                       "in nfl.db vs the teams still alive that week here; proj/actuals depth differs because this "
                       "league has no K or DEF.", SM))
    s.append(table(rows, [0.5 * inch] + [0.95 * inch] * 8))
    s.append(Paragraph("<b>Method</b>", SM))
    for line in rep["method"].strip().splitlines():
        s.append(Paragraph(line.replace("<", "&lt;"), MONO))
    doc.build(s)


if __name__ == "__main__":
    rep = json.load(open(sys.argv[1], encoding="utf-8"))
    counts = json.load(open(sys.argv[2], encoding="utf-8"))
    extra = json.load(open(sys.argv[3], encoding="utf-8"))
    build(rep, counts, sys.argv[4], extra["headline"], extra["deadlines"])
