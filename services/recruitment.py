"""
Recruitment rate: from registry records to a country-level value.

Chapter 3.3 describes a two-stage transformation, because the registry reports
enrollment for the trial as a whole and only exceptionally per country:

  stage 1 - a rate is computed for the whole trial, as enrolled participants
            divided by the number of sites and by the length of the recruitment
            period, giving patients per site per month;
  stage 2 - that value is assigned to every participating country and the
            country-level figure is the median across the trials it took part in.

The median rather than the mean, because the measure is bounded below by zero
and unbounded above: one unusually successful project lifts a mean far more than
a failed one lowers it.

Below a minimum number of contributing trials the value is left undetermined
rather than set to zero. Zero would mean "trials ran here and recruited nobody",
which is a different statement from "we do not know", and for a country with no
trials at all it would repeat information already carried by the experience
criterion — penalising the same fact twice.
"""

from statistics import median

DEFAULT_MIN_TRIALS = 3
DAYS_PER_MONTH = 30.44


def trial_recruitment_rate(study: dict) -> float | None:
    """Patients per site per month for a single trial, or None if not computable."""
    protocol = study.get("protocolSection", {})

    enrolled = protocol.get("designModule", {}).get("enrollmentInfo", {}).get("count")
    if not isinstance(enrolled, (int, float)) or enrolled <= 0:
        return None

    locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
    site_count = len(locations)
    if site_count == 0:
        return None

    status = protocol.get("statusModule", {})
    start = status.get("startDateStruct", {}).get("date")
    completion = status.get("primaryCompletionDateStruct", {}).get("date")
    months = recruitment_period_months(start, completion)
    if not months:
        return None

    return enrolled / site_count / months


def recruitment_period_months(start_date: str | None, completion_date: str | None) -> float | None:
    """
    Length of recruitment, taken as the interval between the study start and the
    primary completion date, as defined in chapter 3.1.
    """
    import pandas as pd

    start = pd.to_datetime(start_date, errors="coerce")
    completion = pd.to_datetime(completion_date, errors="coerce")
    if pd.isna(start) or pd.isna(completion) or completion <= start:
        return None

    months = (completion - start).days / DAYS_PER_MONTH
    return months if months > 0 else None


def country_recruitment_rates(
    historical_studies: list,
    min_trials: int = DEFAULT_MIN_TRIALS,
    only_nct_ids: set | None = None,
) -> dict[str, dict]:
    """
    Country-level recruitment rate.

    `only_nct_ids` restricts the calculation to a chosen subset of trials. With
    the benchmark selection passed in, the result is the rate observed in the
    very projects the planner considers representative — the figure to hold
    against the forecasts sites return in feasibility questionnaires.

    Returns, per country name as written in the registry:
        rate    - median across contributing trials, or None if below the threshold
        trials  - how many trials contributed a computable rate
    """
    per_country: dict[str, list[float]] = {}

    for study in historical_studies:
        protocol = study.get("protocolSection", {})

        if only_nct_ids is not None:
            nct_id = protocol.get("identificationModule", {}).get("nctId")
            if nct_id not in only_nct_ids:
                continue

        rate = trial_recruitment_rate(study)
        if rate is None:
            continue

        locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
        countries = {loc.get("country") for loc in locations if loc.get("country")}
        for country in countries:
            per_country.setdefault(country, []).append(rate)

    return {
        country: {
            "rate": median(rates) if len(rates) >= min_trials else None,
            "trials": len(rates),
        }
        for country, rates in per_country.items()
    }
