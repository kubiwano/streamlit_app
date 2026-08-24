"""
One registry record, read once.

Two views need the same trial-level table: the raw-data audit on the results page
and the benchmark selection on the AI page. They used to parse the record
separately, and the copies drifted — the recruitment rate was computed per site
in one and per trial in the other, and the discrepancy survived for weeks because
each looked right on its own. The parsing lives here now; the views differ only
in which columns they ask for.

Column names follow the four figures of the recruitment calculation in the order
they are computed — Patients, Sites, Enrollment Months, ER — so the last can be
checked against the first three by eye.
"""

import pandas as pd

from services.recruitment import DAYS_PER_MONTH

# Long free-text fields are shortened for the table. The full text is what the
# language model receives; this is only what fits in a cell.
SNIPPET = 120


def split_criteria(raw: str) -> tuple[str, str]:
    """Inclusion and exclusion sections of the registry's eligibility text."""
    if not raw:
        return "", ""
    if "Exclusion Criteria:" in raw:
        head, tail = raw.split("Exclusion Criteria:", 1)
        return head.replace("Inclusion Criteria:", "").strip(), tail.strip()
    return raw.replace("Inclusion Criteria:", "").strip(), ""


def _snippet(text: str) -> str:
    if not text:
        return "N/A"
    return text[:SNIPPET] + "..." if len(text) > SNIPPET else text


def _recruitment(patients, sites, start_date, pcd) -> tuple:
    """
    Recruitment period in months and the rate per site per month.

    The same definition as services/recruitment.py, which feeds the ranking: a
    country is held to what one of its centres enrols in a month, not to the size
    of the programme it took part in.
    """
    start = pd.to_datetime(start_date, errors="coerce")
    completion = pd.to_datetime(pcd, errors="coerce")
    if pd.isna(start) or pd.isna(completion) or completion <= start:
        return None, None

    months = round((completion - start).days / DAYS_PER_MONTH, 1)
    if months <= 0 or not sites or not isinstance(patients, (int, float)):
        return months, None
    return months, round(patients / sites / months, 2)


def parse_study(study: dict) -> dict:
    """Every field either view needs, from one pass over the record."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})

    patients = design.get("enrollmentInfo", {}).get("count")
    locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
    sites = len(locations)
    countries = sorted({loc.get("country") for loc in locations if loc.get("country")})

    start_date = status.get("startDateStruct", {}).get("date")
    pcd = status.get("primaryCompletionDateStruct", {}).get("date")
    months, rate = _recruitment(patients, sites, start_date, pcd)

    inclusion, exclusion = split_criteria(
        protocol.get("eligibilityModule", {}).get("eligibilityCriteria", "")
    )

    return {
        "NCT ID": ident.get("nctId", "N/A"),
        "Sponsor": protocol.get("sponsorCollaboratorsModule", {})
                           .get("leadSponsor", {}).get("name", "N/A"),
        "Title": ident.get("briefTitle", "N/A"),
        "Status": status.get("overallStatus", "N/A"),
        "Phases": ", ".join(design.get("phases", [])) or "N/A",
        "Conditions": ", ".join(protocol.get("conditionsModule", {}).get("conditions", [])),
        "Start Date": start_date or "N/A",
        "Primary Completion": pcd or "N/A",
        "Study End Date": status.get("completionDateStruct", {}).get("date") or "N/A",
        "Countries": ", ".join(countries) if countries else "N/A",
        "Patients": patients if patients is not None else "N/A",
        "Sites": sites,
        "Enrollment Months": months if months is not None else "N/A",
        "ER (Pts/Site/Mon)": rate if rate is not None else "N/A",
        "Inclusion Criteria": _snippet(inclusion),
        "Exclusion Criteria": _snippet(exclusion),
        # Full text, for the table on the benchmark page where the planner reads
        # the criteria rather than glancing at them.
        "Inclusion Criteria (full)": inclusion or "N/A",
        "Exclusion Criteria (full)": exclusion or "N/A",
    }


AUDIT_COLUMNS = [
    "NCT ID", "Sponsor", "Title", "Status", "Phases", "Conditions",
    "Start Date", "Primary Completion", "Study End Date", "Countries",
    "Patients", "Sites", "Enrollment Months", "ER (Pts/Site/Mon)",
    "Inclusion Criteria", "Exclusion Criteria",
]

BENCHMARK_COLUMNS = [
    "NCT ID", "Sponsor", "Title",
    "Inclusion Criteria (full)", "Exclusion Criteria (full)",
    "Patients", "Sites", "Enrollment Months", "ER (Pts/Site/Mon)",
]


def build_table(studies, columns=None, benchmark_ncts=None) -> pd.DataFrame:
    """
    A trial-level table over `studies`.

    benchmark_ncts=None means the sample has no notion of benchmarks (the
    competition table); an empty collection means it has one and nothing is
    selected yet. The distinction decides whether the column appears at all.
    """
    if not studies:
        return pd.DataFrame()

    rows = [parse_study(s) for s in studies]
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]]

    if benchmark_ncts is not None:
        selected = set(benchmark_ncts)
        frame.insert(0, "Benchmark", frame["NCT ID"].isin(selected))

    return frame
