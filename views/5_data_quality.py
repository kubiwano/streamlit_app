"""
Data quality and linkage diagnostics.

Kept apart from the ranking on purpose: nothing here helps choose a country,
it only shows whether the numbers behind that choice were assembled correctly.
"""

import streamlit as st

from services.data_aggregator import aggregate_country_metrics
from services.external_data import EXTERNAL_CRITERIA, merge_external_criteria
from services.registry_data import competition_date_rules, load_registry_data
from services.sample_filter import (
    EXCLUDE_UNDATED,
    count_undated,
    current_restrictions,
    describe_restrictions,
    filter_competition,
    filter_competition_dates,
    filter_historical,
)

st.title("Data Quality")
st.markdown(
    "Verification of how registry records were matched to external indicators. "
    "These figures do not affect the ranking; they show how much of it rests on "
    "complete data."
)

api_query = st.session_state.get("api_query", {})
if not api_query:
    st.warning("No parameters found. Please configure the search on '2. Search Criteria' first.")
    st.stop()

with st.spinner("Recomputing the country table..."):
    hist_result, comp_result, fetched_at = load_registry_data(api_query)
    selected_benchmarks = st.session_state.get("selected_benchmarks", [])
    restrictions = current_restrictions(st.session_state)
    hist_studies = filter_historical(hist_result.studies, restrictions)
    comp_dated = filter_competition_dates(
        comp_result.studies, **competition_date_rules(api_query)
    )
    comp_studies = filter_competition(comp_dated, restrictions)
    df_metrics = aggregate_country_metrics(
        hist_studies, comp_studies, selected_benchmarks,
        min_trials_for_rate=st.session_state.get('min_trials_for_rate', 3),
    )
    df_metrics, unresolved = merge_external_criteria(df_metrics)

st.caption(f"Registry data retrieved: {fetched_at:%Y-%m-%d %H:%M}")

with st.container(border=True):
    st.header(":material/cloud_download: 1. Registry Download", divider="gray")
    col1, col2 = st.columns(2)
    for column, label, result in (
        (col1, "Historical studies", hist_result),
        (col2, "Competition", comp_result),
    ):
        if result.error:
            state = "interrupted"
        elif result.truncated:
            state = "truncated at the limit"
        else:
            state = "complete"
        column.metric(label, len(result), state)

    undated = count_undated(comp_result.studies)
    if undated:
        st.caption(
            f"{undated} competing trials carry no primary completion date. They are "
            + ("left out" if restrictions[EXCLUDE_UNDATED] else "counted")
            + " — the registry's own date filter would have dropped them silently."
        )

    restriction = describe_restrictions(restrictions)
    restriction_comp = describe_restrictions(restrictions, sample="competition")
    if restriction:
        st.caption(
            f"Historical sample restricted in the model settings: {restriction}. "
            f"{len(hist_studies)} of {len(hist_result.studies)} trials feed the "
            f"experience and recruitment criteria."
        )
    if restriction_comp:
        st.caption(
            f"Competition sample restricted in the model settings: {restriction_comp}. "
            f"{len(comp_studies)} of {len(comp_result.studies)} trials feed the "
            f"competition criterion."
        )

with st.container(border=True):
    st.header(":material/link: 2. Linkage to External Indicators", divider="gray")
    total = len(df_metrics)
    matched = int(df_metrics["iso3"].notna().sum())
    with_external = int(df_metrics["idx_legislative"].notna().sum())

    col1, col2, col3 = st.columns(3)
    col1.metric("Countries in the registry sample", total)
    col2.metric("Matched to an ISO code", matched)
    col3.metric("Covered by external indicators", with_external)

    if unresolved:
        st.warning(
            "**Country names not matched to an ISO code.** No external criteria were "
            "attached to these records. Report them so the translation table can be "
            "extended: " + ", ".join(unresolved)
        )
    else:
        st.success("Every country name in the registry sample was matched to an ISO code.")

    no_external = df_metrics[df_metrics["iso3"].notna() & df_metrics["idx_legislative"].isna()]
    if not no_external.empty:
        st.info(
            "**Present in the registry, absent from the World Bank snapshot.** These "
            "countries keep their place in the ranking with the external criteria left "
            "undetermined, rather than being dropped without notice: "
            + ", ".join(sorted(no_external["Country"]))
        )

with st.container(border=True):
    st.header(":material/speed: 3. Recruitment Rate", divider="gray")
    min_trials = st.session_state.get("min_trials_for_rate", 3)
    determined = int(df_metrics["recruitment_rate"].notna().sum())
    contributing = df_metrics["recruitment_trials"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Countries with a determined rate", determined)
    col2.metric("Left undetermined", len(df_metrics) - determined)
    col3.metric("Minimum trials required", min_trials)

    st.markdown(
        "A rate is computed per trial as enrolled participants divided by the number "
        "of sites and by the recruitment period, then taken as the **median** across "
        "the trials a country took part in. Countries below the threshold are left "
        "**undetermined** rather than scored as zero — for a country with no trials, "
        "zero would repeat what the experience criterion already says."
    )

    rate_view = df_metrics[["Country", "iso3", "recruitment_rate", "recruitment_trials"]].copy()
    rate_view.columns = ["Country", "ISO", "Rate (pts/site/month)", "Contributing trials"]
    st.dataframe(
        rate_view.sort_values("Rate (pts/site/month)", ascending=False, na_position="last"),
        use_container_width=True,
        hide_index=True,
    )

    if contributing.max() and contributing.max() < min_trials:
        st.warning(
            f"No country reached the threshold of {min_trials} trials — the highest is "
            f"{int(contributing.max())}. Either lower the threshold in the model settings on "
            f"'4. Weightage and Results' or widen the historical query."
        )

with st.container(border=True):
    st.header(":material/fact_check: 4. External Criteria Coverage", divider="gray")
    show_incomplete_only = st.checkbox("Show only countries with missing external criteria")

    criteria = list(EXTERNAL_CRITERIA)
    detail = df_metrics[["Country", "iso3", "region"] + criteria].copy()
    detail.insert(3, "Missing criteria", detail[criteria].isna().sum(axis=1))

    if show_incomplete_only:
        detail = detail[detail["Missing criteria"] > 0]

    st.dataframe(
        detail.sort_values(["Missing criteria", "Country"], ascending=[False, True]),
        use_container_width=True,
        hide_index=True,
    )
