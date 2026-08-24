"""
The ranking as a file the planner can keep, and read back.

A ranking computed from a live registry is not reproducible on its own: the same
query run a month later returns different trials. What makes it reproducible is
the record of everything that went into it — the query, the weights, the
thresholds, the sample restrictions, the benchmark trials the planner chose and
the moment the data was fetched. Chapter 3.3 requires that record; this module
writes it.

The Scenario sheet is both the audit trail and the way back. It stores API codes
rather than display labels, so reading it restores the exact query rather than
something that looks like it. One file, two directions.
"""

import datetime
from io import BytesIO

import pandas as pd

from services.external_data import INDICATORS_FILE, snapshot_stamp
from services.scoring import CRITERIA

SCENARIO_SHEET = "Scenario"
FONT = "Arial"

# Keys whose value is a list, joined for storage and split on the way back.
LIST_KEYS = {"phases", "status"}
BOOL_KEYS = {
    "remove_past_pcd", "exclude_single_site", "exclude_single_country",
    "exclude_healthy_volunteers", "exclude_undated_competition",
    "competition_reference_auto",
}


def _rows(section: str, mapping: dict) -> list[dict]:
    out = []
    for key, value in mapping.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join("" if v is None else str(v) for v in value)
        elif value is None:
            value = ""
        out.append({"Section": section, "Key": key, "Value": value})
    return out


def build_scenario(session_state, api_query: dict, fetched_at, weights: dict) -> pd.DataFrame:
    """Everything needed to recompute this ranking, as Section/Key/Value rows."""
    hist = api_query.get("historical", {})
    comp = api_query.get("competition", {})
    window = tuple(comp.get("competition_window") or ())

    def iso(index):
        if len(window) > index and window[index] is not None:
            return window[index].strftime("%Y-%m-%d")
        return ""

    rows = []
    rows += _rows("Run", {
        "exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "registry_fetched_at": fetched_at.strftime("%Y-%m-%d %H:%M")
                               if hasattr(fetched_at, "strftime") else str(fetched_at),
        "indicator_file": INDICATORS_FILE.name,
        "indicator_file_modified": datetime.datetime.fromtimestamp(
            snapshot_stamp()).strftime("%Y-%m-%d %H:%M") if snapshot_stamp() else "",
        "ai_model": session_state.get("ai_model_used", ""),
    })
    planned = session_state.get("study_params", {}) or {}
    planned_range = tuple(planned.get("date_range") or ())

    def planned_date(index):
        if len(planned_range) > index and planned_range[index] is not None:
            return planned_range[index].strftime("%Y-%m-%d")
        return ""

    rows += _rows("Planned study", {
        "title": planned.get("title"),
        "indication": planned.get("indication"),
        "sponsor_type": planned.get("sponsor_type"),
        "phase": (planned.get("phases") or [""])[0],
        "planned_start": planned_date(0),
        "planned_end": planned_date(1),
        "num_patients": planned.get("num_patients"),
        "num_sites": planned.get("num_sites"),
        "inclusion_criteria": planned.get("inclusion_criteria"),
        "exclusion_criteria": planned.get("exclusion_criteria"),
    })
    rows += _rows("Historical query", {
        "indication": hist.get("indication"),
        "phases": hist.get("phases"),
        "sponsor": hist.get("sponsor"),
        "status": hist.get("status"),
        "type": hist.get("type"),
        "start_date": hist.get("start_date"),
    })
    rows += _rows("Competition query", {
        "indication": comp.get("indication"),
        "phases": comp.get("phases"),
        "sponsor": comp.get("sponsor"),
        "status": comp.get("status"),
        "type": comp.get("type"),
        "started_after": iso(0),
        "started_before": iso(1),
        "remove_past_pcd": comp.get("remove_past_pcd"),
    })
    rows += _rows("Model settings", {
        "min_trials_for_rate": session_state.get("min_trials_for_rate"),
        "competition_reference": session_state.get("competition_reference"),
        "competition_reference_auto": session_state.get("competition_reference_auto"),
    })
    rows += _rows("Sample restrictions", {
        "exclude_single_site": session_state.get("exclude_single_site"),
        "exclude_single_country": session_state.get("exclude_single_country"),
        "exclude_healthy_volunteers": session_state.get("exclude_healthy_volunteers"),
        "exclude_undated_competition": session_state.get("exclude_undated_competition"),
    })
    rows += _rows("Weights", {c.key: weights.get(c.key, 0) for c in CRITERIA})
    return pd.DataFrame(rows)


def read_scenario(source) -> dict:
    """
    Parse a Scenario sheet back into plain values, grouped by section.

    Returns {section: {key: value}} with lists and booleans restored. Anything
    unreadable is left out rather than guessed at, and the caller reports what
    could not be restored.
    """
    frame = pd.read_excel(source, sheet_name=SCENARIO_SHEET)
    parsed: dict[str, dict] = {}
    for _, row in frame.iterrows():
        section, key = str(row["Section"]), str(row["Key"])
        value = row["Value"]
        value = "" if pd.isna(value) else value

        if key in LIST_KEYS:
            value = [v.strip() for v in str(value).split(",") if v.strip()]
        elif key in BOOL_KEYS:
            value = str(value).strip().lower() in {"true", "1", "yes"}
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        parsed.setdefault(section, {})[key] = value
    return parsed


def ranking_sheet(df_results: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """The ranking in original units, with the diagnostics that qualify it."""
    columns = ["Rank", "Country", "iso3", "Final Score", "Criteria missing", "Evidence coverage"]
    columns += [c.key for c in CRITERIA if weights.get(c.key, 0) > 0]
    for extra in ("recruitment_trials", "recruitment_rate_benchmark", "recruitment_trials_benchmark"):
        columns.append(extra)

    present = [c for c in columns if c in df_results.columns]
    out = df_results[present].copy()
    labels = {c.key: f"{c.label} ({c.unit})" for c in CRITERIA}
    labels.update({
        "iso3": "ISO", "recruitment_trials": "Trials behind the rate",
        "recruitment_rate_benchmark": "Benchmark ER (pts/site/month)",
        "recruitment_trials_benchmark": "Benchmark trials behind the rate",
    })
    return out.rename(columns=labels)


def benchmarks_sheet(table, selected_ids) -> pd.DataFrame:
    """
    The trials the planner chose, which is what actually reproduces the ranking.

    Specific experience and the benchmark recruitment rate are computed from this
    list; without it the scenario restores a query but not the result.
    """
    if not selected_ids:
        return pd.DataFrame({"NCT ID": [], "Note": []})
    if table is None or getattr(table, "empty", True):
        return pd.DataFrame({"NCT ID": list(selected_ids)})

    wanted = [c for c in ("NCT ID", "Sponsor", "Title", "Similarity Score (%)",
                          "AI Explanation", "Patients", "Sites",
                          "Enrollment Months", "ER (Pts/Site/Mon)")
              if c in table.columns]
    chosen = table[table["NCT ID"].isin(selected_ids)][wanted]
    return chosen.reset_index(drop=True)


def summary_sheet(lines: list[str]) -> pd.DataFrame:
    """The executive summary as plain sentences, one per row."""
    return pd.DataFrame({"Executive Summary": [line for line in lines if line]})


def comparison_sheet(agreement: dict) -> pd.DataFrame:
    """SAW against TOPSIS, with the headline figures on top."""
    table = agreement.get("table")
    if table is None or table.empty:
        return pd.DataFrame({"Note": ["No country was described completely enough for TOPSIS."]})

    header = pd.DataFrame([
        {"Country": "— countries compared —", "SAW Rank": agreement.get("countries")},
        {"Country": "— Spearman rho —", "SAW Rank": agreement.get("rho")},
        {"Country": f"— shared top {agreement.get('top_n')} —", "SAW Rank": agreement.get("overlap")},
    ])
    body = table[["Country", "SAW Rank", "TOPSIS Rank", "Rank shift",
                  "Final Score", "TOPSIS Score"]]
    return pd.concat([header, body], ignore_index=True)


def _autoformat(writer) -> None:
    """Readable widths, a bold first row and frozen headers on every sheet."""
    from openpyxl.styles import Alignment, Font

    for sheet in writer.book.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(name=FONT, bold=True)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for column in sheet.columns:
            letter = column[0].column_letter
            longest = max((len(str(c.value)) for c in column if c.value is not None), default=8)
            sheet.column_dimensions[letter].width = min(max(longest + 2, 10), 55)
            for cell in column[1:]:
                cell.font = Font(name=FONT)


def build_report(
    df_results: pd.DataFrame,
    weights: dict,
    session_state,
    api_query: dict,
    fetched_at,
    benchmark_table=None,
    summary_lines=None,
    agreement=None,
) -> bytes:
    """The whole report as bytes, ready for st.download_button."""
    buffer = BytesIO()
    selected = session_state.get("selected_benchmarks", [])

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        ranking_sheet(df_results, weights).to_excel(writer, sheet_name="Ranking", index=False)
        build_scenario(session_state, api_query, fetched_at, weights).to_excel(
            writer, sheet_name=SCENARIO_SHEET, index=False
        )
        benchmarks_sheet(benchmark_table, selected).to_excel(
            writer, sheet_name="Benchmarks", index=False
        )
        summary_sheet(summary_lines or []).to_excel(
            writer, sheet_name="Executive Summary", index=False
        )
        comparison_sheet(agreement or {}).to_excel(
            writer, sheet_name="SAW vs TOPSIS", index=False
        )
        _autoformat(writer)

    return buffer.getvalue()


def read_benchmarks(source) -> list[str]:
    """
    NCT identifiers from the Benchmarks sheet, or an empty list if absent.

    Restoring them is worth doing even though the registry is live: an identifier
    that no longer comes back simply stops being counted, which is the same thing
    that would happen if the planner reselected it by hand.
    """
    try:
        frame = pd.read_excel(source, sheet_name="Benchmarks")
    except Exception:
        return []
    if "NCT ID" not in frame.columns:
        return []
    return [str(v) for v in frame["NCT ID"].dropna().tolist() if str(v).strip()]


def apply_scenario(scenario: dict, session_state) -> tuple[list[str], list[str]]:
    """
    Write a parsed scenario back into session state.

    Returns (restored, skipped) as human-readable notes. Widget values are set
    before the widgets are drawn — Streamlit refuses to change them afterwards —
    so the caller must rerun immediately after this.
    """
    restored, skipped = [], []

    # Written to the durable store and out of the widget keys, so the inputs on the
    # results page rebuild themselves from the restored values instead of holding
    # on to whatever was last typed.
    store = session_state.setdefault(
        "criteria_weights", {c.key: c.default_weight for c in CRITERIA}
    )
    weights = scenario.get("Weights", {})
    applied = 0
    for criterion in CRITERIA:
        if criterion.key in weights:
            try:
                store[criterion.key] = int(float(weights[criterion.key]))
                session_state.pop(f"w_{criterion.key}", None)
                applied += 1
            except (TypeError, ValueError):
                skipped.append(f"weight for {criterion.label}")
    if applied:
        restored.append(f"{applied} criteria weights")

    settings = scenario.get("Model settings", {})
    for key in ("min_trials_for_rate", "competition_reference", "competition_reference_auto"):
        if key in settings:
            session_state[key] = settings[key]
    if settings:
        restored.append("model settings")

    limits = scenario.get("Sample restrictions", {})
    for key, value in limits.items():
        session_state[key] = bool(value)
    if limits:
        restored.append("sample restrictions")

    # Widget state on the search-parameters page takes precedence over the
    # defaults those widgets are given, so a restored query would be overwritten
    # by whatever was typed there earlier in the session. Clearing the keys makes
    # the page rebuild its fields from the query we have just restored.
    for key in (
        "h_ind", "h_pha", "h_spo", "h_look", "h_stat", "h_type",
        "c_ind", "c_pha", "c_spo", "c_start", "c_end", "c_stat", "c_type", "c_pcd",
    ):
        session_state.pop(key, None)

    planned = scenario.get("Planned study", {})
    if planned.get("indication"):
        dates = []
        for key in ("planned_start", "planned_end"):
            raw = str(planned.get(key, "")).strip()
            dates.append(datetime.date.fromisoformat(raw[:10]) if raw else None)
        session_state["study_params"] = {
            "title": planned.get("title", ""),
            "indication": planned.get("indication", ""),
            "sponsor_type": planned.get("sponsor_type", ""),
            "phases": [planned.get("phase")] if planned.get("phase") else [],
            "date_range": tuple(d for d in dates if d is not None),
            "num_patients": int(planned.get("num_patients") or 0),
            "num_sites": int(planned.get("num_sites") or 0),
            "inclusion_criteria": planned.get("inclusion_criteria", ""),
            "exclusion_criteria": planned.get("exclusion_criteria", ""),
            "countries": [],
        }
        restored.append(f"planned study “{planned.get('title') or planned['indication']}”")
    elif "Planned study" not in scenario:
        # Reports exported before the study definition was added to the sheet.
        # Saying so is better than leaving the first page blank without a reason.
        skipped.append(
            "planned study definition — this report predates it being saved; "
            "export a fresh one to carry it over"
        )
    else:
        skipped.append("planned study definition (no indication in the file)")

    hist = scenario.get("Historical query", {})
    comp = scenario.get("Competition query", {})
    if hist.get("indication") and comp.get("indication"):
        window = []
        for key in ("started_after", "started_before"):
            raw = str(comp.get(key, "")).strip()
            window.append(datetime.date.fromisoformat(raw[:10]) if raw else None)
        session_state["api_query"] = {
            "historical": {
                "indication": hist.get("indication"),
                "phases": hist.get("phases", []),
                "sponsor": hist.get("sponsor"),
                "status": hist.get("status", []),
                "type": hist.get("type"),
                "start_date": str(hist.get("start_date", ""))[:10],
            },
            "competition": {
                "indication": comp.get("indication"),
                "phases": comp.get("phases", []),
                "sponsor": comp.get("sponsor"),
                "status": comp.get("status", []),
                "type": comp.get("type"),
                "competition_window": tuple(window),
                "remove_past_pcd": bool(comp.get("remove_past_pcd")),
            },
        }
        restored.append("registry query")
    else:
        skipped.append("registry query (indication missing from the file)")

    return restored, skipped
