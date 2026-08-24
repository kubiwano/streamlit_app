"""
Restriction of the registry samples to trials comparable with the planned study.

The registry query selects trials by indication, phase, sponsor and status, but
not by how they were run or in whom. A single-site investigator-initiated study
and a forty-country registration programme both come back from the same query,
and counting them alike inflates the experience criterion with participation
that says little about a country's capacity to run a multicentre trial.

The filters are applied after the download rather than in the query, because the
API exposes no parameter for the number of sites: the count exists only in the
returned record.

The two samples are not restricted alike, and the difference is deliberate:

  historical   describes capacity to run a trial like the planned one, so it is
               narrowed to trials that resemble it in scale and in population;
  competition  measures pressure on the same pool of patients, so trials are
               removed only when they do not draw on that pool at all. A local
               single-site study still competes for the same patients; a
               healthy-volunteer study does not.
"""

EXCLUDE_UNDATED = "exclude_undated_competition"

HISTORICAL_ONLY = ("exclude_single_site", "exclude_single_country")
BOTH_SAMPLES = ("exclude_healthy_volunteers",)
COMPETITION_ONLY = (EXCLUDE_UNDATED,)

_LABELS = {
    "exclude_single_site": "single-site",
    "exclude_single_country": "single-country",
    "exclude_healthy_volunteers": "healthy-volunteer",
    EXCLUDE_UNDATED: "undated",
}


def site_profile(study) -> tuple[int, int]:
    """Number of listed sites and number of distinct countries for one trial."""
    locations = (
        study.get("protocolSection", {})
        .get("contactsLocationsModule", {})
        .get("locations", [])
    )
    countries = {loc.get("country") for loc in locations if loc.get("country")}
    return len(locations), len(countries)


def accepts_healthy_volunteers(study):
    """
    True, False, or None when the registry record does not say.

    The field is optional in the registry, and an absent answer is not a denial,
    so it is returned as unknown rather than folded into False.
    """
    value = (
        study.get("protocolSection", {})
        .get("eligibilityModule", {})
        .get("healthyVolunteers")
    )
    return value if isinstance(value, bool) else None


def current_restrictions(session_state) -> dict:
    """The restrictions as set in the model settings, as plain booleans."""
    return {
        name: bool(session_state.get(name, False))
        for name in HISTORICAL_ONLY + BOTH_SAMPLES + COMPETITION_ONLY
    }


def _as_timestamp(value):
    """
    Registry dates arrive as '2021', '2021-03' or '2021-03-15'. A partial date is
    read as the first day of the period it names, which is the earliest moment it
    could denote — the reading that keeps a trial in the sample rather than out of
    it when the boundary falls inside that period.
    """
    import pandas as pd

    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed


def study_dates(study) -> tuple:
    """Start date and primary completion date as written in the record."""
    status = study.get("protocolSection", {}).get("statusModule", {})
    return (
        status.get("startDateStruct", {}).get("date"),
        status.get("primaryCompletionDateStruct", {}).get("date"),
    )


def filter_competition_dates(
    studies,
    min_primary_completion=None,
    start_from=None,
    start_to=None,
) -> list:
    """
    Apply the competition date rules in Python rather than in the query.

    The registry's own RANGE filter can only match records that carry the field,
    so a competing trial with no date on file is dropped by the API without a
    word. That is exactly the silent treatment of a missing value the rest of
    this model refuses: not knowing when a trial ends is not evidence that it
    ends before the planned study opens.

    A trial the rules cannot judge therefore passes this stage untouched. Whether
    it is counted at all is a separate decision, made in the model settings and
    applied by filter_competition, so that it appears alongside the other
    exclusions instead of hiding inside a date boundary.
    """
    import pandas as pd

    lower_pcd = _as_timestamp(min_primary_completion)
    lower_start = _as_timestamp(start_from)
    upper_start = _as_timestamp(start_to)

    if lower_pcd is None and lower_start is None and upper_start is None:
        return list(studies)

    kept = []
    for study in studies:
        start_raw, pcd_raw = study_dates(study)
        start, pcd = _as_timestamp(start_raw), _as_timestamp(pcd_raw)

        if lower_pcd is not None and pcd is not None and pcd < lower_pcd:
            continue

        if start is not None:
            if lower_start is not None and start < lower_start:
                continue
            if upper_start is not None and start > upper_start:
                continue

        kept.append(study)
    return kept


def count_undated(studies) -> int:
    """Competing trials with no primary completion date on file."""
    return sum(1 for s in studies if not study_dates(s)[1])


def _filter(studies, exclude_single_site, exclude_single_country, exclude_healthy):
    """
    Trials matching the active restrictions, in their original order.

    A trial is dropped on scale only when the count is exactly one. Records with
    no location data at all are kept: an absent list means the registry does not
    say how the trial was run, which is not the same as saying it ran at one
    site. The same reasoning governs the healthy-volunteer flag — an unstated
    answer keeps the trial in the sample. Chapter 3.3 rules out treating a
    missing value as a known one, and that applies to exclusions as much as to
    the criteria themselves.
    """
    if not (exclude_single_site or exclude_single_country or exclude_healthy):
        return list(studies)

    kept = []
    for study in studies:
        if exclude_single_site or exclude_single_country:
            sites, countries = site_profile(study)
            if exclude_single_site and sites == 1:
                continue
            if exclude_single_country and countries == 1:
                continue
        if exclude_healthy and accepts_healthy_volunteers(study) is True:
            continue
        kept.append(study)
    return kept


def filter_historical(studies, restrictions: dict) -> list:
    return _filter(
        studies,
        restrictions.get("exclude_single_site", False),
        restrictions.get("exclude_single_country", False),
        restrictions.get("exclude_healthy_volunteers", False),
    )


def filter_competition(studies, restrictions: dict) -> list:
    """The population restriction and, if asked for, the undated trials."""
    kept = _filter(
        studies, False, False, restrictions.get("exclude_healthy_volunteers", False)
    )
    if restrictions.get(EXCLUDE_UNDATED):
        kept = [s for s in kept if study_dates(s)[1]]
    return kept


def describe_restrictions(restrictions: dict, sample: str = "historical") -> str:
    """Wording for the caption that travels with a restricted sample."""
    names = (
        HISTORICAL_ONLY + BOTH_SAMPLES
        if sample == "historical"
        else BOTH_SAMPLES + COMPETITION_ONLY
    )
    parts = [_LABELS[n] for n in names if restrictions.get(n)]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"{parts[0]} trials excluded"
    return ", ".join(parts[:-1]) + f" and {parts[-1]} trials excluded"
