"""
The ranking as a document to put in front of a team.

The spreadsheet is for the analyst who wants to check the numbers; this is for
the meeting where the shortlist is discussed. It answers four questions in
order — what is recommended, what the recommendation is made of, how far it
survives a change of method or weighting, and how it was produced — because that
is the order in which a room asks them.

Charts are drawn with matplotlib rather than exported from the interactive
figures: plotly needs a browser engine to write a static image, and a report
should not depend on one being installed.
"""

from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from services.scoring import CRITERIA, CRITERIA_BY_KEY
from services.summary import contributions, describe_country

ACCENT = colors.HexColor("#2E7D32")
MUTED = colors.HexColor("#607D8B")
LIGHT = colors.HexColor("#ECEFF1")
RULE = colors.HexColor("#CFD8DC")

ACCENT_HEX = "#2E7D32"
GREY_HEX = "#B0BEC5"

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


# ----------------------------------------------------------------------------
# styles
# ----------------------------------------------------------------------------
def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, textColor=colors.HexColor("#263238"),
            alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=colors.HexColor("#263238"),
            spaceBefore=12, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.8, leading=14, spaceAfter=6,
        ),
        "lead": ParagraphStyle(
            "lead", parent=base["Normal"], fontName="Helvetica",
            fontSize=11.5, leading=16, spaceAfter=8,
            textColor=colors.HexColor("#263238"),
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.2, leading=10.5,
        ),
        "cellhead": ParagraphStyle(
            "cellhead", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8.2, leading=10.5, textColor=colors.white,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=MUTED, spaceAfter=8,
        ),
    }


def _clean(text) -> str:
    """Markdown emphasis out, XML entities escaped, for reportlab paragraphs."""
    s = "" if text is None else str(text)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("**", "")


# ----------------------------------------------------------------------------
# charts
# ----------------------------------------------------------------------------
def _figure_bytes(fig) -> BytesIO:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=200, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def _chart_scores(df_results: pd.DataFrame, top_n: int = 15) -> BytesIO:
    data = df_results.head(top_n).iloc[::-1]
    complete = data["Criteria missing"].eq(0) if "Criteria missing" in data else None

    fig, ax = plt.subplots(figsize=(7.2, 0.30 * len(data) + 0.8))
    bars = ax.barh(
        data["Country"], data["Final Score"],
        color=[ACCENT_HEX if (complete is None or c) else GREY_HEX for c in
               (complete if complete is not None else [True] * len(data))],
        height=0.68,
    )
    for bar, value in zip(bars, data["Final Score"]):
        ax.text(value + 0.012, bar.get_y() + bar.get_height() / 2,
                f"{value:.2f}", va="center", fontsize=7.5, color="#455A64")

    ax.set_xlim(0, min(1.0, float(data["Final Score"].max()) * 1.18))
    ax.set_xlabel("Final score", fontsize=8, color="#455A64")
    ax.tick_params(axis="y", labelsize=8)
    ax.tick_params(axis="x", labelsize=7.5, colors="#607D8B")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#CFD8DC")
    ax.grid(axis="x", color="#ECEFF1", linewidth=0.8)
    ax.set_axisbelow(True)
    return _figure_bytes(fig)


def _chart_contributions(df_results: pd.DataFrame, weights: dict,
                         total_weight: float, top_n: int = 10) -> BytesIO:
    rows = []
    for _, row in df_results.head(top_n).iterrows():
        parts = contributions(row, weights, total_weight)
        rows.append({"Country": row["Country"],
                     **{CRITERIA_BY_KEY[k].label: v for k, v in parts.items()}})
    frame = pd.DataFrame(rows).set_index("Country").fillna(0.0)
    if frame.empty:
        return None

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    cmap = plt.get_cmap("YlGnBu")
    bottoms = [0.0] * len(frame)
    for index, column in enumerate(frame.columns):
        shade = cmap(0.18 + 0.72 * index / max(len(frame.columns) - 1, 1))
        ax.bar(frame.index, frame[column], bottom=bottoms, label=column,
               color=shade, width=0.66, edgecolor="white", linewidth=0.6)
        bottoms = [b + v for b, v in zip(bottoms, frame[column])]

    ax.set_ylabel("Final score", fontsize=8, color="#455A64")
    ax.tick_params(axis="x", labelrotation=35, labelsize=7.5)
    for label in ax.get_xticklabels():
        label.set_ha("right")
    ax.tick_params(axis="y", labelsize=7.5, colors="#607D8B")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#CFD8DC")
    ax.spines["bottom"].set_color("#CFD8DC")
    ax.grid(axis="y", color="#ECEFF1", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(fontsize=6.6, ncol=3, frameon=False,
              loc="upper center", bbox_to_anchor=(0.5, -0.32))
    return _figure_bytes(fig)


def _chart_recruitment(df_results: pd.DataFrame, has_benchmarks: bool,
                       top_n: int = 10) -> BytesIO:
    data = df_results.head(top_n)
    if "recruitment_rate" not in data or data["recruitment_rate"].isna().all():
        return None
    data = data[data["recruitment_rate"].notna()].iloc[::-1]
    if data.empty:
        return None

    fig, ax = plt.subplots(figsize=(7.2, 0.34 * len(data) + 0.9))
    positions = range(len(data))
    height = 0.36 if has_benchmarks else 0.6

    ax.barh([p + (height / 2 if has_benchmarks else 0) for p in positions],
            data["recruitment_rate"], height=height,
            color=ACCENT_HEX, label="All historical trials")
    if has_benchmarks and "recruitment_rate_benchmark" in data:
        # No fillna: a country without a benchmark rate has none, and a bar of
        # length zero would read as "recruited nobody". matplotlib skips NaN.
        ax.barh([p - height / 2 for p in positions],
                data["recruitment_rate_benchmark"], height=height,
                color="#7E57C2", label="Benchmark trials only")
        ax.legend(fontsize=7, frameon=False, loc="lower right")

    ax.set_yticks(list(positions))
    ax.set_yticklabels(data["Country"], fontsize=8)
    ax.set_xlabel("Patients per site per month", fontsize=8, color="#455A64")
    ax.tick_params(axis="x", labelsize=7.5, colors="#607D8B")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#CFD8DC")
    ax.grid(axis="x", color="#ECEFF1", linewidth=0.8)
    ax.set_axisbelow(True)
    return _figure_bytes(fig)


# ----------------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------------
def _table(data, widths, styles, align_right=()) -> Table:
    head = [Paragraph(_clean(c), styles["cellhead"]) for c in data[0]]
    body = [[Paragraph(_clean(c), styles["cell"]) for c in row] for row in data[1:]]
    table = Table([head] + body, colWidths=widths, repeatRows=1, hAlign="LEFT")

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
    ]
    for column in align_right:
        style.append(("ALIGN", (column, 0), (column, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    return table


def _facts_row(items, styles) -> Table:
    """A row of headline figures, each with its label underneath."""
    cells = []
    for value, label in items:
        cells.append([
            Paragraph(
                f'<font size="17" color="{ACCENT_HEX}"><b>{_clean(value)}</b></font>',
                styles["body"],
            ),
            Paragraph(f'<font size="8" color="#607D8B">{_clean(label)}</font>',
                      styles["body"]),
        ])
    columns = [list(c) for c in zip(*cells)]
    table = Table(columns, colWidths=[CONTENT_W / len(items)] * len(items), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.6, RULE),
    ]))
    return table


# ----------------------------------------------------------------------------
# page furniture
# ----------------------------------------------------------------------------
def _make_decorator(indication: str, stamp: str):
    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, PAGE_H - MARGIN + 6, PAGE_W - MARGIN, PAGE_H - MARGIN + 6)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, PAGE_H - MARGIN + 10,
                          f"Country selection · {indication}")
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 10, stamp)

        canvas.line(MARGIN, MARGIN - 8, PAGE_W - MARGIN, MARGIN - 8)
        canvas.drawString(MARGIN, MARGIN - 18,
                          "Generated from ClinicalTrials.gov and World Bank data")
        canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 18, f"Page {doc.page}")
        canvas.restoreState()
    return decorate


# ----------------------------------------------------------------------------
# the document
# ----------------------------------------------------------------------------
def build_pdf(
    df_results: pd.DataFrame,
    weights: dict,
    session_state,
    api_query: dict,
    fetched_at,
    summary_lines=None,
    agreement=None,
    decisive=None,
    caveats=None,
    sample_counts=None,
) -> bytes:
    styles = _styles()
    indication = api_query.get("historical", {}).get("indication") or "clinical study"
    stamp = (fetched_at.strftime("%d %B %Y")
             if hasattr(fetched_at, "strftime") else str(fetched_at))
    total_weight = sum(weights.values()) or 1
    counts = sample_counts or {}
    selected = session_state.get("selected_benchmarks", []) or []

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 4,
        title=f"Country selection — {indication}",
        author="Clinical Trials Optimizer",
    )
    story = []

    # ---- page 1: the recommendation -------------------------------------
    story.append(Paragraph("Country Selection", styles["title"]))
    story.append(Paragraph(
        f"{_clean(indication)} · registry data retrieved "
        f"{fetched_at:%d %B %Y, %H:%M}" if hasattr(fetched_at, "strftime")
        else _clean(indication),
        styles["subtitle"],
    ))

    story.append(_facts_row([
        (len(df_results), "countries ranked"),
        (counts.get("historical", "—"), "historical trials"),
        (counts.get("competition", "—"), "competing trials"),
        (len(selected), "benchmark trials"),
    ], styles))
    story.append(Spacer(1, 6))

    for line in (summary_lines or [])[:2]:
        story.append(Paragraph(_clean(line), styles["lead"]))

    story.append(Paragraph("Shortlist", styles["h2"]))
    rows = [["#", "Country", "Score", "Driven by", "Held back by", "Missing"]]
    for _, row in df_results.head(10).iterrows():
        highlight = describe_country(row, weights, total_weight)
        rows.append([
            highlight.rank, highlight.country, f"{highlight.score:.2f}",
            ", ".join(name for name, _ in highlight.strengths),
            highlight.weakness[0] if highlight.weakness else "—",
            highlight.missing,
        ])
    story.append(_table(
        rows,
        [10 * mm, 34 * mm, 14 * mm, 52 * mm, 42 * mm, 16 * mm],
        styles, align_right=(0, 2, 5),
    ))
    story.append(Paragraph(
        "“Missing” counts criteria left undetermined for that country; its score "
        "rests on the remainder, with the weights rescaled.", styles["caption"]))

    chart = _chart_scores(df_results)
    story.append(Paragraph("Final scores", styles["h2"]))
    story.append(Image(chart, width=CONTENT_W, height=CONTENT_W * 0.44))
    if "Criteria missing" in df_results:
        story.append(Paragraph(
            "Grey bars mark countries with at least one criterion undetermined.",
            styles["caption"]))

    # ---- page 2: what the scores are made of ----------------------------
    story.append(PageBreak())
    story.append(Paragraph("What the scores are made of", styles["h2"]))
    story.append(Paragraph(
        "Each bar is a country's final score split into the contribution of every "
        "criterion — the weight it carries multiplied by how the country scores on "
        "it. Segments add up to the score in the table on the previous page.",
        styles["body"]))

    stacked = _chart_contributions(df_results, weights, total_weight)
    if stacked:
        story.append(Image(stacked, width=CONTENT_W, height=CONTENT_W * 0.55))

    story.append(Paragraph("Recruitment rate", styles["h2"]))
    story.append(Paragraph(
        "Patients enrolled per site per month, taken as the median across the "
        "trials each country took part in. Where benchmark trials were selected, "
        "their rate is shown beside the general one: a country that recruits well "
        "in general but poorly in trials like this one is a different proposition "
        "from one that does both.", styles["body"]))
    recruitment = _chart_recruitment(df_results, bool(selected))
    if recruitment:
        story.append(Image(recruitment, width=CONTENT_W, height=CONTENT_W * 0.42))
    else:
        story.append(Paragraph(
            "No country in the shortlist reached the minimum number of trials "
            "required for a recruitment rate.", styles["caption"]))

    # ---- page 3: how far the result holds -------------------------------
    story.append(PageBreak())
    story.append(Paragraph("How far the result holds", styles["h2"]))

    if decisive:
        names = [c.lower() for c in decisive]
        shown = names[:3]
        listed = ", ".join(shown)
        if len(names) > 3:
            listed += f" and {len(names) - 3} more"
        story.append(Paragraph(
            f"Dropping {_clean(listed)} from the calculation would put a different "
            f"country first, so the recommendation depends on how much "
            f"{'those criteria matter' if len(names) > 1 else 'that criterion matters'} "
            f"to you. The more criteria appear here, the more the result is a "
            f"statement about your priorities rather than about the countries.",
            styles["body"]))
    else:
        story.append(Paragraph(
            "No single criterion decides the outcome: dropping any one of them "
            "leaves the same country in first place.", styles["body"]))

    if agreement and agreement.get("countries"):
        rho = agreement.get("rho")
        story.append(Paragraph("Control method — TOPSIS", styles["h2"]))
        story.append(_facts_row([
            (agreement["countries"], "countries compared"),
            ("—" if rho is None else f"{rho:.3f}", "rank correlation"),
            (f"{agreement['overlap']}/{agreement['top_n']}", "shared leading group"),
            ("yes" if agreement.get("same_leader") else "no", "same leader"),
        ], styles))
        if agreement.get("same_leader"):
            verdict = (
                f"{_clean(agreement.get('leader_saw', ''))} comes first under both "
                f"rules, so the recommendation does not rest on the choice of "
                f"aggregation method."
            )
        else:
            verdict = (
                f"The two rules disagree at the top: the weighted sum puts "
                f"{_clean(agreement.get('leader_saw', ''))} first, TOPSIS "
                f"{_clean(agreement.get('leader_topsis', ''))}. Both are defensible "
                f"readings of the same data, and the disagreement is a reason to put "
                f"the two profiles side by side rather than to prefer one rule."
            )
        story.append(Paragraph(verdict, styles["body"]))
        story.append(Paragraph(
            "The weighted sum lets a strong criterion pay for a weak one without "
            "limit; TOPSIS measures distance from the best and the worst attainable "
            "profile. Running both on the same normalised data isolates the effect of "
            "that difference. Only completely described countries can take part, "
            "which is why fewer are compared than ranked.", styles["body"]))

        table = agreement.get("table")
        if table is not None and not table.empty:
            rows = [["Country", "Weighted sum", "TOPSIS", "Movement"]]
            for _, row in table.head(12).iterrows():
                shift = int(row["Rank shift"])
                movement = "—" if shift == 0 else (f"up {shift}" if shift > 0
                                                   else f"down {abs(shift)}")
                rows.append([row["Country"], int(row["SAW Rank"]),
                             int(row["TOPSIS Rank"]), movement])
            story.append(_table(
                rows, [60 * mm, 34 * mm, 30 * mm, 34 * mm],
                styles, align_right=(1, 2),
            ))
    else:
        story.append(Paragraph("Control method — TOPSIS", styles["h2"]))
        story.append(Paragraph(
            "No country was described completely enough on the weighted criteria "
            "for the control method to run.", styles["body"]))

    if caveats:
        story.append(Paragraph("Caveats", styles["h2"]))
        for item in caveats:
            story.append(Paragraph(f"• {_clean(item)}", styles["body"]))

    # ---- page 4: how this was produced ----------------------------------
    story.append(PageBreak())
    story.append(Paragraph("How this was produced", styles["h2"]))
    story.append(Paragraph(
        "The ranking is a weighted sum of nine criteria, four read from the trial "
        "registry and five describing the country environment. Values are normalised "
        "against a fixed scale before weighting; a criterion that could not be "
        "determined is left out and the remaining weights rescaled, rather than "
        "filled with an invented number.", styles["body"]))

    story.append(Paragraph("Criteria and weights", styles["h2"]))
    rows = [["Criterion", "Source", "Direction", "Weight", "Share"]]
    for criterion in CRITERIA:
        weight = weights.get(criterion.key, 0)
        rows.append([
            criterion.label,
            "registry" if criterion.source == "registry" else "country environment",
            "higher is better" if criterion.stimulant else "lower is better",
            weight, f"{weight / total_weight:.0%}",
        ])
    story.append(_table(
        rows, [46 * mm, 40 * mm, 38 * mm, 18 * mm, 16 * mm],
        styles, align_right=(3, 4),
    ))

    hist = api_query.get("historical", {})
    comp = api_query.get("competition", {})
    window = tuple(comp.get("competition_window") or ())

    def window_text(index, fallback):
        if len(window) > index and window[index] is not None:
            return window[index].strftime("%Y-%m-%d")
        return fallback

    settings = [
        ["Setting", "Value"],
        ["Indication", hist.get("indication", "—")],
        ["Historical phases", ", ".join(hist.get("phases", [])) or "—"],
        ["Historical sponsor", hist.get("sponsor", "—")],
        ["Historical status", ", ".join(hist.get("status", [])) or "—"],
        ["Historical trials started after", hist.get("start_date", "—")],
        ["Competing trials started after", window_text(0, "no limit")],
        ["Competing trials started before", window_text(1, "no limit")],
        ["Minimum trials for a recruitment rate",
         session_state.get("min_trials_for_rate", "—")],
        ["Competition reference level", session_state.get("competition_reference", "—")],
        ["Sample restrictions", _restrictions_text(session_state)],
        ["Similarity scoring model", session_state.get("ai_model_used") or "not used"],
        ["Registry data retrieved",
         fetched_at.strftime("%Y-%m-%d %H:%M") if hasattr(fetched_at, "strftime")
         else str(fetched_at)],
    ]
    story.append(Paragraph("Settings behind this ranking", styles["h2"]))
    story.append(_table(settings, [70 * mm, 88 * mm], styles))
    story.append(Paragraph(
        "Every setting above is also written to the Scenario sheet of the "
        "accompanying spreadsheet, which the application can read back to reproduce "
        "this ranking. Similarity scores are an optional aid; the benchmark trials "
        "were selected by hand and are listed in that spreadsheet.",
        styles["caption"]))

    doc.build(story, onFirstPage=_make_decorator(indication, stamp),
              onLaterPages=_make_decorator(indication, stamp))
    return buffer.getvalue()


def _restrictions_text(session_state) -> str:
    labels = {
        "exclude_single_site": "single-site excluded",
        "exclude_single_country": "single-country excluded",
        "exclude_healthy_volunteers": "healthy-volunteer excluded",
        "exclude_undated_competition": "undated competing trials excluded",
    }
    active = [text for key, text in labels.items() if session_state.get(key)]
    return "; ".join(active) if active else "none — full samples used"
