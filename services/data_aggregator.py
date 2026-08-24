"""
Aggregation of registry records to the country level.

The registry returns one record per trial; the model compares countries. This
module performs only that transition and produces raw values — normalisation and
weighting belong to services/scoring.py.
"""

import pandas as pd

from services.recruitment import DEFAULT_MIN_TRIALS, country_recruitment_rates


def aggregate_country_metrics(
    historical_studies,
    competition_studies,
    selected_ncts=None,
    min_trials_for_rate: int = DEFAULT_MIN_TRIALS,
) -> pd.DataFrame:
    """
    One row per country, with the four registry criteria of chapter 3.3:

      experience_raw    trials in the historical sample the country took part in
      specific_exp_raw  of those, the ones marked as benchmarks by the user
      competition_raw   trials in the competition sample
      recruitment_rate  median patients per site per month, or NaN below the
                        minimum number of contributing trials

    A multinational trial increments the counter of every participating country:
    the registry records the fact of participation, not its scale, so splitting
    the credit would introduce information the source does not contain.
    """
    selected_ncts = set(selected_ncts or [])
    country_stats: dict[str, dict] = {}

    def count(studies, metric, mark_benchmarks=False):
        for study in studies:
            protocol = study.get("protocolSection", {})
            nct_id = protocol.get("identificationModule", {}).get("nctId")
            locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
            countries = {loc.get("country") for loc in locations if loc.get("country")}

            for country in countries:
                stats = country_stats.setdefault(
                    country,
                    {"experience_raw": 0, "competition_raw": 0, "specific_exp_raw": 0},
                )
                stats[metric] += 1
                if mark_benchmarks and nct_id in selected_ncts:
                    stats["specific_exp_raw"] += 1

    count(historical_studies, "experience_raw", mark_benchmarks=True)
    count(competition_studies, "competition_raw")

    if not country_stats:
        return pd.DataFrame()

    df = pd.DataFrame.from_dict(country_stats, orient="index").reset_index()
    df.rename(columns={"index": "Country"}, inplace=True)

    rates = country_recruitment_rates(historical_studies, min_trials=min_trials_for_rate)
    df["recruitment_rate"] = df["Country"].map(lambda c: rates.get(c, {}).get("rate"))
    df["recruitment_trials"] = df["Country"].map(lambda c: rates.get(c, {}).get("trials", 0))

    # The same measure restricted to the benchmark trials. It does not enter the
    # ranking — the sample is deliberately small — but it is the figure to set
    # against the rates sites declare in feasibility questionnaires, which is how
    # chapter 1 frames the confrontation of declarations with historical record.
    # No minimum applies here: the planner chose these trials as representative,
    # and the contributing count travels alongside so the basis stays visible.
    benchmark_rates = country_recruitment_rates(
        historical_studies, min_trials=1, only_nct_ids=selected_ncts
    )
    df["recruitment_rate_benchmark"] = df["Country"].map(
        lambda c: benchmark_rates.get(c, {}).get("rate")
    )
    df["recruitment_trials_benchmark"] = df["Country"].map(
        lambda c: benchmark_rates.get(c, {}).get("trials", 0)
    )

    return df[[
        "Country",
        "experience_raw",
        "specific_exp_raw",
        "recruitment_rate",
        "recruitment_trials",
        "recruitment_rate_benchmark",
        "recruitment_trials_benchmark",
        "competition_raw",
    ]]
