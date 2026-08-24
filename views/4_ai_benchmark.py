import streamlit as st
import pandas as pd

from services.registry_data import load_registry_data, report_fetch_problems
from services.study_table import BENCHMARK_COLUMNS, build_table
from services.sample_filter import (
    current_restrictions,
    describe_restrictions,
    filter_historical,
)

st.title("Comprehensive AI & Operational Benchmarking")
st.markdown("Analyze all historical trials side-by-side with calculated enrollment metrics and AI-driven criteria similarity scoring.")

planned_study = st.session_state.get("study_params", {})
api_query = st.session_state.get("api_query", {})

if not planned_study or not api_query:
    st.warning("Missing data for Analysis. Please define the study and search criteria on the previous pages first.")
    st.stop()

# The historical sample is downloaded here rather than on the results page,
# because selecting benchmark trials precedes the ranking. The download is
# cached, so the results page reuses it without a second request.
with st.spinner("Fetching historical studies from ClinicalTrials.gov..."):
    hist_result, _comp_result, _fetched_at = load_registry_data(api_query)

report_fetch_problems("Historical studies", hist_result)

# The same restrictions the ranking uses, so the trials offered for selection here
# are exactly the ones the experience criterion will count.
restrictions = current_restrictions(st.session_state)
hist_studies = filter_historical(hist_result.studies, restrictions)
restriction = describe_restrictions(restrictions)
if restriction:
    st.caption(
        f"Sample restricted in the model settings on '4. Weightage and Results': "
        f"{restriction} ({len(hist_result.studies) - len(hist_studies)} of "
        f"{len(hist_result.studies)} trials)."
    )
if not hist_studies:
    st.warning("No historical studies found for the given criteria. Broaden the search on '2. Search Criteria'.")
    st.stop()

# ==========================================
# SEKCJA WALIDACJI: OSTRZEŻENIE O LIMICIE AI
# ==========================================
AI_LIMIT = 100


def _start_date(study):
    """Sort key: newest first, records with no start date last."""
    raw = (
        study.get("protocolSection", {})
        .get("statusModule", {})
        .get("startDateStruct", {})
        .get("date")
    )
    # ISO strings sort correctly as text, and a partial date like "2021-03"
    # orders before "2021-03-15", which is the right reading of an unknown day.
    return raw or ""


# The cap used to take the first hundred trials in whatever order the registry
# returned them, while the message below promised the hundred most recent. Sorting
# makes the promise true and the choice defensible: recent protocols describe
# current practice, which is what a benchmark is meant to represent.
scored_sample = sorted(hist_studies, key=_start_date, reverse=True)[:AI_LIMIT]

if len(hist_studies) > AI_LIMIT:
    oldest = _start_date(scored_sample[-1]) or "unknown"
    st.warning(
        f"**Scoring is limited to {AI_LIMIT} trials.** The sample holds "
        f"**{len(hist_studies)}**, so the {AI_LIMIT} most recently started are sent "
        f"for scoring — everything begun before {oldest} is left unscored but stays "
        f"in the table below and can still be ticked by hand. Narrow the historical "
        f"filters on **'2. Search Criteria'** to bring the whole sample within reach."
    )

def build_benchmark_table(studies):
    """The shared trial table plus the three columns this page owns."""
    frame = build_table(studies, BENCHMARK_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.rename(columns={
        "Inclusion Criteria (full)": "Inclusion Criteria",
        "Exclusion Criteria (full)": "Exclusion Criteria",
    })
    frame.insert(0, "Select Benchmark", False)
    frame["Similarity Score (%)"] = None
    frame["AI Explanation"] = "Pending AI Analysis..."
    return frame


# Rebuilt whenever the sample itself changes, compared by identifier rather than
# by count. Comparing lengths meant that changing the indication for one with the
# same number of trials left the previous AI scores in place, attached to entirely
# different protocols — a silent mismatch with nothing on screen to reveal it.
# Sorted, because scoring reorders the table by similarity and that reordering
# must not read as a new sample.
current_ids = sorted(
    s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
    for s in hist_studies
)
if (
    "comprehensive_table" not in st.session_state
    or st.session_state.get("benchmark_sample_ids") != current_ids
):
    st.session_state.comprehensive_table = build_benchmark_table(hist_studies)
    st.session_state.benchmark_sample_ids = current_ids

with st.container(border=True):
    st.header(":material/compare_arrows: 1. Match Trials to Your Study", divider="gray")

    # The baseline sits here rather than above the box: it is the reference the
    # scoring is run against, so it belongs to this step and not to the page at large.
    with st.expander("View Your Planned Study Baseline", expanded=False):
        st.markdown(f"**Title:** {planned_study.get('title', 'N/A')}")
        st.markdown(f"**Indication:** {planned_study.get('indication', 'N/A')}")
        st.markdown(f"**Inclusion:** {planned_study.get('inclusion_criteria', 'N/A')}")
        st.markdown(f"**Exclusion:** {planned_study.get('exclusion_criteria', 'N/A')}")

    custom_instructions = st.text_area(
        "Custom AI Instructions (Optional)",
        placeholder="e.g., 'Pay special attention to pediatric trials. Penalize trials that exclude patients with diabetes.'"
    )

    if st.button("Run AI Matrix Scoring", type="primary", use_container_width=True):
        from services.gemini_benchmark import score_similarity

        bar = st.progress(0.0, text="Scoring protocols…")
        result = score_similarity(
            planned_study,
            scored_sample,
            custom_instructions,
            progress=lambda fraction: bar.progress(fraction, text="Scoring protocols…"),
        )
        bar.empty()

        if result.error and not result.rows:
            st.error(result.error)
        else:
            scores = {row["nct_id"]: row for row in result.rows}
            df_updated = st.session_state.comprehensive_table.copy()
            for idx, row in df_updated.iterrows():
                match = scores.get(row["NCT ID"])
                if match:
                    if match["similarity_score"] is not None:
                        df_updated.at[idx, "Similarity Score (%)"] = match["similarity_score"]
                    df_updated.at[idx, "AI Explanation"] = match["explanation"] or "N/A"

            df_updated = df_updated.sort_values(
                by="Similarity Score (%)", ascending=False, na_position="last"
            ).reset_index(drop=True)

            st.session_state.comprehensive_table = df_updated
            # The model name is kept so that it can travel with the exported
            # scenario: a score is attributable only if the tool is named.
            st.session_state.ai_model_used = result.model_name
            # Carried through session state rather than shown here: st.rerun()
            # below wipes the page before anything written now can appear.
            st.session_state.ai_scoring_outcome = {
                "scored": result.scored_count,
                "requested": len(scored_sample),
                "unscored": len(result.unscored),
                "foreign": len(result.foreign_ids),
                "model": result.model_name,
                "error": result.error,
                "note": result.note,
            }
            st.rerun()

    outcome = st.session_state.pop("ai_scoring_outcome", None)
    if outcome:
        st.success(
            f"**Similarity scoring complete.** {outcome['scored']} of "
            f"{outcome['requested']} trials received a score from `{outcome['model']}`; "
            f"the table below is sorted from the most to the least similar protocol. "
            f"The scores are a suggestion — the benchmark set is whatever you tick."
        )
        if outcome.get("note"):
            st.info(outcome["note"])
        if outcome["error"]:
            st.warning(outcome["error"])
        if outcome["unscored"]:
            st.caption(
                f"{outcome['unscored']} trials came back without a score and keep their "
                f"place in the table unranked."
            )
        if outcome["foreign"]:
            st.caption(
                f"{outcome['foreign']} identifiers returned by the model did not belong "
                f"to this sample and were discarded."
            )

with st.container(border=True):
    st.header(":material/checklist: 2. Review and Select Benchmarks", divider="gray")
    st.markdown(f"Review the {len(hist_studies)} historical trials below. Check the boxes in the **'Select Benchmark'** column for trials that perfectly match your target population.")

    # EDYTOR TABELI - zaktualizowana lista blokowanych kolumn i wyjęty overwrite stanu
    edited_df = st.data_editor(
        st.session_state.comprehensive_table, 
        use_container_width=True, 
        hide_index=True,
        key="benchmark_editor", # Bezpieczny klucz edytora!
        disabled=["NCT ID", "Sponsor", "Title", "Inclusion Criteria", "Exclusion Criteria", "Similarity Score (%)", "AI Explanation", "Patients", "Sites", "Enrollment Months", "ER (Pts/Site/Mon)"], 
        column_config={
            "Select Benchmark": st.column_config.CheckboxColumn(
                "Select Benchmark",
                help="Marks a trial as representative of the planned study",
                default=False
            ),
            "Similarity Score (%)": st.column_config.NumberColumn(
                "Similarity Score (%)", format="%d %%"
            ),
            "AI Explanation": st.column_config.TextColumn(
                "AI Explanation",
                width="large", 
                help="Double-click cell to expand text"
            )
        }
    )

    # POPRAWKA BŁĘDU: Usunięto linijkę "st.session_state.comprehensive_table = edited_df", która tworzyła State Loop!
    # Wyciągamy wybrane wartości z tabeli (która jest stanem obecnym w UI) po kliknięciu zapisu.

    if st.button("Save Selected Benchmarks & Continue", type="primary"):
        selected_ncts = edited_df[edited_df["Select Benchmark"] == True]["NCT ID"].tolist()
        st.session_state.selected_benchmarks = selected_ncts
        # Straight on to the ranking. Selecting nothing is a legitimate choice —
        # specific experience then scores zero everywhere and carries no information —
        # and the count is shown as a metric on the results page either way.
        st.switch_page("views/3_results.py")