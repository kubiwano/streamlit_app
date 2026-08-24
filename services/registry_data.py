"""
Shared access to ClinicalTrials.gov data.

Both the benchmark page and the results page need the same historical sample,
so the download lives here instead of on one of them. The result is cached,
which means whichever page runs first pays for it and the other gets it free.
"""

import datetime

import streamlit as st

from api.ctgov_client import CTGovClient


def resolve_competition_cutoff(comp_params):
    """
    Earliest primary completion date a competing trial must have to still
    compete for the same patients, or None when the rule is switched off.

    Applied after the download rather than in the query — see
    services/sample_filter.filter_competition_dates for why.
    """
    if not comp_params.get("remove_past_pcd"):
        return None

    planned_dates = st.session_state.get("study_params", {}).get("date_range", ())
    if isinstance(planned_dates, (tuple, list)) and len(planned_dates) > 0:
        return planned_dates[0].strftime("%Y-%m-%d")
    if isinstance(planned_dates, datetime.date):
        return planned_dates.strftime("%Y-%m-%d")
    return datetime.date.today().strftime("%Y-%m-%d")


def competition_date_rules(api_query) -> dict:
    """
    The three optional date bounds a competing trial is judged against, ready to
    hand to services.sample_filter.filter_competition_dates.
    """
    comp_params = api_query.get("competition", {})
    start_from, start_to = _window_bounds(comp_params.get("competition_window"))
    return {
        "min_primary_completion": resolve_competition_cutoff(comp_params),
        "start_from": start_from,
        "start_to": start_to,
    }


def _window_bounds(date_range):
    """Two optional ISO dates from a (start, end) pair in which either may be None."""
    values = tuple(date_range or ())
    def iso(index):
        if len(values) > index and values[index] is not None:
            return values[index].strftime("%Y-%m-%d")
        return None
    return iso(0), iso(1)


@st.cache_data(show_spinner=False)
def fetch_api_data(query_params):
    hist_params = query_params["historical"]
    comp_params = query_params["competition"]

    hist_result = CTGovClient.fetch_studies(
        query_name="Historical",
        condition=hist_params["indication"],
        phases=hist_params["phases"],
        sponsor=hist_params["sponsor"],
        statuses=hist_params["status"],
        start_date_from=hist_params.get("start_date"),
        study_type=hist_params.get("type"),
    )

    # No date bounds in the query. The registry can only apply a RANGE to records
    # that carry the field, so filtering here would drop every competing trial with
    # no date on file and never say so. The dates are applied after the download.
    comp_result = CTGovClient.fetch_studies(
        query_name="Competition",
        condition=comp_params["indication"],
        phases=comp_params["phases"],
        sponsor=comp_params["sponsor"],
        statuses=comp_params["status"],
        study_type=comp_params.get("type"),
    )

    return hist_result, comp_result


def load_registry_data(api_query):
    """
    Convenience wrapper: resolve the cutoff, download, stamp the time.
    Returns (hist_result, comp_result, fetched_at).

    The timestamp matters for reproducibility — the registry is live, so the
    same query run weeks apart returns different values. Chapter 3.3 requires
    it to be recorded alongside the ranking.
    """
    hist_result, comp_result = fetch_api_data(api_query)
    return hist_result, comp_result, datetime.datetime.now()


def report_fetch_problems(label, result):
    """Surface an incomplete download instead of passing it off as the full set."""
    if result.error:
        st.error(
            f"**{label}:** download interrupted after {len(result)} studies "
            f"({result.error}). The ranking below is based on an incomplete sample."
        )
    elif result.truncated:
        st.warning(
            f"**{label}:** the query returned more studies than the limit of "
            f"{len(result)}. Narrow the search criteria — the ranking is based on "
            f"a truncated sample."
        )
