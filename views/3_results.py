import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import datetime
import html as _html
import re as _re

from services.data_aggregator import aggregate_country_metrics
from services.registry_data import (
    competition_date_rules,
    load_registry_data,
    report_fetch_problems,
)
from services.external_data import (
    PROVISIONAL_STARTUP_BASES,
    external_maxima,
    merge_external_criteria,
    startup_basis_label,
)
from services.sample_filter import (
    EXCLUDE_UNDATED,
    count_undated,
    current_restrictions,
    describe_restrictions,
    filter_competition,
    filter_competition_dates,
    filter_historical,
)
from services.scoring import CRITERIA, CRITERIA_BY_KEY, add_scores, rank_countries
from services.study_table import AUDIT_COLUMNS, build_table
from services.report import build_report
from services.topsis import compare_rankings, topsis_scores
from services.summary import (
    contributions,
    decisiveness,
    describe_country,
    group_profile,
    leadership_sensitivity,
    specialists,
    compare_with,
)

def _inline(text: str) -> str:
    """Country names come from the registry, so escape first, then honour **bold**."""
    return _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _html.escape(text))


def tight_paragraphs(lines, line_height: float = 1.4, gap: str = "0.4rem") -> None:
    """
    Consecutive st.markdown() calls each carry their own block spacing, which
    spreads a few sentences of prose over half a screen. Rendering them as one
    element with explicit margins keeps the narrative readable as a paragraph.
    """
    body = "".join(
        f"<p style='margin:0 0 {gap} 0'>{_inline(line)}</p>" for line in lines if line
    )
    st.markdown(f"<div style='line-height:{line_height}'>{body}</div>", unsafe_allow_html=True)


def tight_bullets(heading: str, lines, line_height: float = 1.4) -> None:
    items = "".join(f"<li style='margin:0 0 0.25rem 0'>{_inline(line)}</li>" for line in lines)
    st.markdown(
        f"<div style='line-height:{line_height}'>"
        f"<p style='margin:0 0 0.25rem 0'><strong>{_html.escape(heading)}</strong></p>"
        f"<ul style='margin:0 0 0.4rem 0;padding-left:1.2rem'>{items}</ul>"
        f"</div>",
        unsafe_allow_html=True,
    )


st.title("Optimization Results & Ranking")
st.markdown("Analyze the scoring of potential countries based on **live ClinicalTrials.gov data**.")

api_query = st.session_state.get("api_query", {})
if not api_query:
    st.warning("No parameters found. Please go back to '2. Search Criteria' and configure your search.")
    st.stop()

# --- METRYKI ---
def render_table_metrics(df, is_historical=False):
    if df.empty:
        return
        
    unique_ids = df["NCT ID"].nunique()
    statuses = [str(s) for s in df["Status"].unique() if s != "N/A"]
    
    phases_set = set()
    for p in df["Phases"]:
        if p != "N/A":
            phases_set.update([x.strip() for x in str(p).split(",")])
            
    valid_starts = pd.to_datetime(df[df["Start Date"] != "N/A"]["Start Date"], errors='coerce').dropna()
    min_start = valid_starts.min().strftime('%Y-%m-%d') if not valid_starts.empty else "N/A"
    max_start = valid_starts.max().strftime('%Y-%m-%d') if not valid_starts.empty else "N/A"

    valid_pcd = pd.to_datetime(df[df["Primary Completion"] != "N/A"]["Primary Completion"], errors='coerce').dropna()
    
    st.caption(f"**Unique Trials:** {unique_ids}")
    st.caption(f"**Start Date Range:** From {min_start} to {max_start}")
    
    if is_historical:
        max_pcd = valid_pcd.max().strftime('%Y-%m-%d') if not valid_pcd.empty else "N/A"
        st.caption(f"**Max Primary Completion Date:** {max_pcd}")
    else:
        min_pcd = valid_pcd.min().strftime('%Y-%m-%d') if not valid_pcd.empty else "N/A"
        st.caption(f"**Min Primary Completion Date:** {min_pcd}")
        
    st.caption(f"**Distinct Statuses:** {', '.join(statuses) if statuses else 'N/A'}")
    st.caption(f"**Distinct Phases:** {', '.join(sorted(phases_set)) if phases_set else 'N/A'}")
    st.markdown("<br>", unsafe_allow_html=True)

# ==========================================
# 2. POBRANIE DANYCH I AGREGACJA W LOCIE
# ==========================================
with st.spinner("Fetching live data from ClinicalTrials.gov and calculating metrics..."):
    hist_result, comp_result, fetched_at = load_registry_data(api_query)
    # Restrictions of scale narrow the historical sample only; the population
    # restriction narrows both. See services/sample_filter.py for why.
    restrictions = current_restrictions(st.session_state)
    hist_all, comp_all = hist_result.studies, comp_result.studies
    hist_studies = filter_historical(hist_all, restrictions)
    comp_dated = filter_competition_dates(comp_all, **competition_date_rules(api_query))
    comp_studies = filter_competition(comp_dated, restrictions)
    selected_benchmarks = st.session_state.get("selected_benchmarks", [])
    df_metrics = aggregate_country_metrics(
        hist_studies, comp_studies, selected_benchmarks,
        min_trials_for_rate=st.session_state.get('min_trials_for_rate', 3),
    )
    # unmatched country names are reported on the Data Quality page
    df_metrics, _unresolved = merge_external_criteria(df_metrics)

report_fetch_problems("Historical studies", hist_result)
report_fetch_problems("Competition", comp_result)
st.caption(f"Registry data retrieved: {fetched_at:%Y-%m-%d %H:%M}")

if df_metrics.empty:
    st.error("No locations found for the provided search criteria. Try broadening your search parameters.")
    st.stop()

# 1. Globalne statystyki zapytania i audyt danych źródłowych
# Left unnumbered on purpose: this is the sample the ranking is computed from,
# not a step the user works through.
with st.container(border=True):
    st.header(":material/database: Registry Sample", divider="gray")

    # Both metrics count the same thing: trials removed by the restrictions in the
    # model settings. What the search parameters exclude — the historical lookback,
    # the competition date rules — is not reported here for either sample, because
    # that is the query the user wrote, not something the model did to it.
    excluded = len(hist_all) - len(hist_studies)
    comp_restricted = len(comp_dated) - len(comp_studies)
    restriction = describe_restrictions(restrictions)
    restriction_comp = describe_restrictions(restrictions, sample="competition")

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric(
        "Total Historical Studies Analyzed",
        len(hist_studies),
        f"-{excluded} excluded" if excluded else None,
        delta_color="off",
        help=(
            f"{restriction.capitalize()} in the model settings below."
            if restriction
            else "Every trial returned by the historical query."
        ),
    )
    col_m2.metric(
        "Benchmark Trials Selected",
        len(selected_benchmarks),
        help="Feed the specific experience criterion.",
    )
    col_m3.metric(
        "Total Competing Studies Found",
        len(comp_studies),
        f"-{comp_restricted} excluded" if comp_restricted else None,
        delta_color="off",
        help=(
            f"{restriction_comp.capitalize()} in the model settings below."
            if restriction_comp
            else "Every trial matching the competition query."
        ),
    )

    with st.expander(
        "View Raw Data: Historical Studies",
        expanded=st.session_state.get("hist_raw_expanded", False),
    ):
        st.markdown(
            "Verify the exact trials and calculated enrollment baselines fetched by "
            "your historical parameters. The **Benchmark** column is editable here — "
            "ticking a trial has the same effect as selecting it on the AI Benchmark "
            "page, so a set can be corrected without going back."
        )
        df_hist_raw = build_table(hist_studies, AUDIT_COLUMNS, benchmark_ncts=selected_benchmarks)
        render_table_metrics(df_hist_raw, is_historical=True)
        edited_hist = st.data_editor(
            df_hist_raw,
            use_container_width=True,
            hide_index=True,
            key="hist_raw_editor",
            disabled=[c for c in df_hist_raw.columns if c != "Benchmark"],
            column_config={
                "Benchmark": st.column_config.CheckboxColumn(
                    "Benchmark",
                    width="small",
                    help="Editable. Sets the trials behind specific experience and benchmark ER.",
                )
            },
        )

        # The selection is read back rather than written through: the ranking above
        # was computed from the previous set, so a change has to restart the run.
        # Guarded by a comparison, otherwise every rerun would trigger the next one.
        if not edited_hist.empty:
            picked = edited_hist.loc[edited_hist["Benchmark"] == True, "NCT ID"].tolist()
            if set(picked) != set(selected_benchmarks):
                st.session_state.selected_benchmarks = picked
                # Keep the AI Benchmark page's own table in step with the change.
                table = st.session_state.get("comprehensive_table")
                if table is not None and not table.empty:
                    table["Select Benchmark"] = table["NCT ID"].isin(picked)
                    st.session_state.comprehensive_table = table
                st.session_state.hist_raw_expanded = True
                st.rerun()

    with st.expander("View Raw Data: Competition Studies"):
        st.markdown("Verify the active/planned pipelines saturation fetched by your competition parameters.")
        df_comp_raw = build_table(comp_studies, AUDIT_COLUMNS)
        render_table_metrics(df_comp_raw, is_historical=False)
        st.dataframe(df_comp_raw, use_container_width=True, hide_index=True)

# ==========================================
# 3. USTAWIENIA MODELU I WAGI
# ==========================================
with st.container(border=True):
    st.header(":material/tune: 1. Criteria Weights", divider="gray")
    head_left, head_mid, head_right = st.columns([3, 1, 1])
    with head_left:
        st.subheader("Set Criteria Weights")
    # Placed before the inputs on purpose: Streamlit refuses to change a widget's
    # value through session state once that widget has been created in this run.
    with head_mid:
        if st.button("Clear all", use_container_width=True):
            for criterion in CRITERIA:
                st.session_state.criteria_weights[criterion.key] = 0
                st.session_state.pop(f"w_{criterion.key}", None)
            st.rerun()
    with head_right:
        if st.button("Reset to defaults", use_container_width=True):
            for criterion in CRITERIA:
                st.session_state.criteria_weights[criterion.key] = criterion.default_weight
                st.session_state.pop(f"w_{criterion.key}", None)
            st.rerun()

    st.caption(
        "Weights are relative. A criterion left at zero drops out of the calculation."
    )

    # Share of the total is shown in each label rather than in a summary line.
    # Read from the widget key when it exists — that is this run's value, so the
    # percentages refresh as the numbers are typed — and from the durable store
    # otherwise, which is what survives a walk to another page and back.
    store = st.session_state.criteria_weights
    current = {
        c.key: st.session_state.get(f"w_{c.key}", store.get(c.key, c.default_weight))
        for c in CRITERIA
    }
    running_total = sum(current.values())

    # Criteria carrying the footnote about their mutual dependence (chapter 3.4).
    FOOTNOTED = {"idx_political", "idx_legislative"}

    # The two groups sit side by side rather than one above the other: they are the
    # two sources the model draws on, and putting them in parallel columns lets the
    # planner see how much weight rests on the registry against the country
    # environment without scrolling between them.
    weights: dict[str, float] = {}
    group_columns = st.columns(2, gap="large")
    for group_column, (source, heading) in zip(
        group_columns,
        (("registry", "From the trial registry"), ("external", "Country environment")),
    ):
        group = [c for c in CRITERIA if c.source == source]
        group_share = sum(current[c.key] for c in group) / running_total if running_total else 0
        with group_column:
            st.markdown(f"**{heading} · {group_share:.0%}**")
            for criterion in group:
                share = current[criterion.key] / running_total if running_total else 0
                marker = "*" if criterion.key in FOOTNOTED else ""
                # An explicit value, so that a widget whose state Streamlit has
                # discarded comes back at its stored weight instead of falling
                # through to min_value.
                weights[criterion.key] = st.number_input(
                    f"{criterion.label}{marker} · {share:.0%}",
                    min_value=0, step=1,
                    value=int(current[criterion.key]),
                    key=f"w_{criterion.key}",
                )
                store[criterion.key] = weights[criterion.key]

    total_weight = sum(weights.values())
    if total_weight == 0:
        st.error("Total weight cannot be zero. Give at least one criterion a weight above 0.")
        st.stop()

    # Chapter 3.4: the two governance-based criteria move largely together, so
    # weighting both is close to weighting the same dimension twice. The note stays
    # visible whatever the weights are — the asterisks in the labels would otherwise
    # point at nothing — and only its wording follows what is actually in use.
    w_political = weights.get("idx_political", 0)
    w_legislative = weights.get("idx_legislative", 0)
    preamble = (
        "\\* Political risk and legal environment are strongly related "
        "(rank correlation 0.86). "
    )

    if w_political > 0 and w_legislative > 0:
        combined = (w_political + w_legislative) / total_weight
        st.caption(
            preamble
            + f"Together they carry {combined:.0%} of the weight, so the institutional "
            f"dimension counts twice. They are kept apart because they diverge for some "
            f"markets; if that distinction is not relevant here, consider lowering both."
        )
    elif w_political > 0 or w_legislative > 0:
        st.caption(
            preamble
            + "Only one of them carries weight, so the institutional dimension is "
            "counted once."
        )
    else:
        st.caption(
            preamble
            + "Neither carries weight, so the institutional dimension does not affect "
            "the ranking."
        )

    # --- ustawienia modelu, pod wagami ------------------------------------------
    st.markdown("**Model settings**")

    # The reference level defaults to the busiest market in the current sample.
    # Zero and the observed maximum give the same ranking — the scale never shrinks
    # below what was observed — but a figure the user can recognise says what the
    # criterion is measured against, where a zero only says a default was left alone.
    observed_competition = pd.to_numeric(
        df_metrics["competition_raw"], errors="coerce"
    ).max()
    observed_competition = int(observed_competition) if pd.notna(observed_competition) else 0

    if st.session_state.get("competition_reference_auto", True):
        st.session_state["competition_reference"] = observed_competition

    # Two numbers and four switches on one line: they are all one decision — which
    # trials count and how far the scales run — and splitting them across rows made
    # the block read as two unrelated things.
    # Centred vertically, so the switches sit level with the middle of the input
    # boxes rather than riding up beside their labels. The last column is widest
    # because its label is the longest by some way.
    undated_total = count_undated(comp_all)
    set1, set2, restrict1, restrict2, restrict3, restrict4 = st.columns(
        [1.0, 1.0, 1.05, 1.1, 1.1, 1.35], vertical_alignment="center"
    )

    with set1:
        st.number_input(
            "Minimum trials",
            min_value=1, max_value=99, step=1,
            key="min_trials_for_rate",
            help=(
                "Recruitment rate: below this many contributing trials it is left "
                "undetermined rather than set to zero."
            ),
        )
    with set2:
        st.number_input(
            "Competition reference",
            min_value=0, step=1,
            key="competition_reference",
            help=(
                "The number of competing trials you regard as extremely unfavourable. "
                "It sets the minimum span of the scale — not a point of saturation, so "
                "a country at this level is not automatically scored worst."
            ),
        )
        if st.session_state["competition_reference"] != observed_competition:
            st.session_state["competition_reference_auto"] = False

    with restrict1:
        st.checkbox(
            "Exclude single-site trials",
            key="exclude_single_site",
            help="Historical sample only. Trials with no location data are kept.",
        )
    with restrict2:
        st.checkbox(
            "Exclude single-country trials",
            key="exclude_single_country",
            help="Historical sample only — a local trial still competes for patients.",
        )
    with restrict3:
        st.checkbox(
            "Exclude healthy-volunteer trials",
            key="exclude_healthy_volunteers",
            help="Both samples: such a trial neither shows experience nor competes.",
        )
    with restrict4:
        st.checkbox(
            "Exclude competing trials with missing dates",
            key=EXCLUDE_UNDATED,
            help=f"{undated_total} in this sample. A missing end date is not a past one.",
        )

    st.caption(
        f"The busiest market in the current sample has {observed_competition} competing "
        f"trials"
        + (
            "; the reference level follows it automatically."
            if st.session_state.get("competition_reference_auto", True)
            else f", and the reference level is set by hand to "
                 f"{st.session_state['competition_reference']}."
        )
    )

# ==========================================
# 4. SILNIK OBLICZENIOWY
# ==========================================
# Denominators for the external criteria come from the full indicator snapshot,
# not from the countries in this ranking — see services/scoring.add_scores.
FIXED_MAXIMA = external_maxima()

df_results = rank_countries(
    df_metrics, weights,
    reference_level=st.session_state.get("competition_reference", 0),
    fixed_maxima=FIXED_MAXIMA,
)

# ==========================================
# 5. TABELA WYNIKÓW
# ==========================================
with st.container(border=True):
    st.header(":material/leaderboard: 2. Country Ranking", divider="gray")
    st.markdown("**Click on any row** to see the detailed profile for that country.")
    # Says where the candidate set comes from. Without it the length of the table
    # looks arbitrary — the honest answer is that a country enters the ranking by
    # having appeared in a trial of this profile, which is itself evidence that
    # one can be run there.
    st.caption(
        f"{len(df_results)} countries are ranked: those appearing in the "
        f"ClinicalTrials.gov records returned by your search. A country with no "
        f"entry in either sample is not a candidate. Scales for the external "
        f"criteria are fixed to the full indicator snapshot, so they do not shift "
        f"with the query."
    )

    # The decision is made on original units — 43 competing trials, 60 days — while
    # the normalised scores are internal to the model (chapter 3.3). They are
    # available on request rather than by default, which halves the column count.
    opt_left, _opt_gap, opt_right = st.columns([2, 4, 2])
    with opt_left:
        show_scores = st.toggle(
            "Show normalised scores",
            value=False,
            help="Adds the 0–1 value behind each criterion.",
        )
    with opt_right:
        # Chapter 3.3 asks for the incompleteness to be visible and filterable, so
        # that a country ranked high on a thin basis can be set aside deliberately
        # rather than taken at face value.
        COMPLETENESS_FILTERS = {
            "Show all countries": len(CRITERIA),
            "At most 1 criterion missing": 1,
            "At most 2 criteria missing": 2,
            "Complete data only": 0,
        }
        filter_choice = st.selectbox(
            "Data completeness",
            options=list(COMPLETENESS_FILTERS),
            help="Hides rows only. Nothing is dropped from the calculation.",
        )
        max_missing = COMPLETENESS_FILTERS[filter_choice]

    active_criteria = [c for c in CRITERIA if weights.get(c.key, 0) > 0 and c.key in df_results.columns]

    BENCHMARK_ER = "recruitment_rate_benchmark"
    BENCHMARK_ER_LABEL = "Benchmark ER (patients per site per month)"

    display = df_results[df_results["Criteria missing"] <= max_missing].copy()
    hidden_count = len(df_results) - len(display)

    ordered = ["Rank", "Country", "Final Score", "Criteria missing", "Evidence coverage"]
    for criterion in active_criteria:
        ordered.append(criterion.key)
        if show_scores:
            ordered.append(criterion.score_column)

    # Recruitment rate observed in the benchmark trials alone. Outside the ranking —
    # too few trials to score on — but this is the figure to set against the rates
    # sites declare in feasibility questionnaires.
    if selected_benchmarks and BENCHMARK_ER in display.columns:
        ordered.append(BENCHMARK_ER)

    display = display[[c for c in ordered if c in display.columns]]
    labels = {c.key: f"{c.label} ({c.unit})" for c in CRITERIA}
    labels.update({c.score_column: f"{c.label} — score" for c in CRITERIA})
    labels[BENCHMARK_ER] = BENCHMARK_ER_LABEL
    display = display.rename(columns=labels)

    column_config = {
        "Country": st.column_config.TextColumn("Country", pinned=True),
        "Final Score": st.column_config.ProgressColumn(
            "Final Score", format="%.2f", min_value=0.0, max_value=1.0
        ),
        "Criteria missing": st.column_config.NumberColumn(
            "Missing Criteria", width="small"
        ),
        "Evidence coverage": st.column_config.NumberColumn(
            "Coverage",
            format="percent",
            width="small",
            help="Share of your declared weight that rests on measurable criteria.",
        ),
        BENCHMARK_ER_LABEL: st.column_config.NumberColumn(
            "Benchmark ER",
            format="%.2f",
            help="Benchmark trials only. Outside the ranking — compare with site declarations.",
        ),
    }

    # Decimals only where the unit has them. Days and trial counts arrive as floats
    # from the source files, but a start-up time of "182.00 days" claims a precision
    # the underlying medians do not carry.
    WHOLE_NUMBER_UNITS = {"Days", "Trials"}
    criterion_by_column = {f"{c.label} ({c.unit})": c for c in CRITERIA}

    for column in display.columns:
        if column in column_config:
            continue
        if not pd.api.types.is_float_dtype(display[column]):
            continue
        criterion = criterion_by_column.get(column)
        whole = criterion is not None and criterion.unit in WHOLE_NUMBER_UNITS
        column_config[column] = st.column_config.NumberColumn(
            column, format="%.0f" if whole else "%.2f"
        )

    if show_scores:
        for criterion in active_criteria:
            column_config[f"{criterion.label} — score"] = st.column_config.ProgressColumn(
                criterion.label, format="%.2f", min_value=0.0, max_value=1.0
            )

    selection_event = st.dataframe(
        display,
        use_container_width=True,
        selection_mode="single-row",
        on_select="rerun",
        hide_index=True,
        column_config=column_config,
    )

    top = df_results.iloc[0]

    incomplete = int((df_results["Criteria missing"] > 0).sum())
    notes = []
    if incomplete:
        notes.append(
            f"{incomplete} of {len(df_results)} countries are scored on an incomplete set "
            f"of criteria."
        )
    if hidden_count:
        notes.append(f"{hidden_count} hidden by the filter above; they remain in the ranking.")
    if notes:
        st.caption(" ".join(notes) + " See the Data Quality page for details.")


# ==========================================
# 6. SZCZEGÓŁY KRAJU
# ==========================================
with st.container(border=True):
    # The index returned by the table refers to the rows actually displayed, which
    # the filter above may have narrowed — resolving it against the full ranking
    # would open the wrong country.
    selected_rows = selection_event.selection.rows
    selected_country = (
        display.iloc[selected_rows[0]]["Country"] if selected_rows else top["Country"]
    )
    st.header(
        f":material/travel_explore: 3. Country Detail — {selected_country}",
        divider="gray",
    )

    ranks = df_results.copy()
    for criterion in CRITERIA:
        column = criterion.score_column
        if column in ranks.columns:
            ranks[f"{column}_rank"] = ranks[column].rank(ascending=False, method="min")

    country_data = ranks[ranks["Country"] == selected_country].iloc[0]

    # --- werdykt: cztery liczby, zanim zacznie sie rozbicie ----------------------
    head1, head2, head3, head4 = st.columns(4)
    head1.metric("Rank", f"#{int(country_data['Rank'])} of {len(df_results)}")
    head2.metric("Final score", f"{country_data['Final Score']:.2f}")
    head3.metric("Criteria undetermined", int(country_data["Criteria missing"]))
    head4.metric(
        "Evidence coverage",
        f"{country_data['Evidence coverage']:.0%}",
        help=(
            "Share of your declared weight resting on measurable criteria. The rest is "
            "redistributed, so a lower figure means a narrower basis, not a gap."
        ),
    )

    # --- narracja o wybranym kraju --------------------------------------------------
    profile_country = describe_country(country_data, weights, total_weight)
    sentences = []

    if profile_country.strengths:
        carried = ", ".join(name.lower() for name, _ in profile_country.strengths)
        sentences.append(
            f"**{selected_country}** takes position #{profile_country.rank} of {len(df_results)} "
            f"with a score of {profile_country.score:.2f}, carried mainly by {carried}."
        )
    if profile_country.weakness:
        name, value = profile_country.weakness
        sentences.append(
            f"Its weakest heavily weighted criterion is {name.lower()}, scoring {value:.2f}."
        )

    if selected_country != top["Country"]:
        difference = compare_with(country_data, top, weights)
        if difference["better"]:
            sentences.append(
                f"Against the leader, {selected_country} is stronger on "
                f"{', '.join(c.lower() for c in difference['better'])}."
            )
        if difference["worse"]:
            sentences.append(
                f"It loses ground on {', '.join(c.lower() for c in difference['worse'])}."
            )
        if not difference["better"] and not difference["worse"]:
            sentences.append(
                f"It differs little from the leader on any single criterion; the gap in the "
                f"final score comes from small margins spread across several of them."
            )
    else:
        sentences.append("This is the highest-scoring country in the ranking.")

    if profile_country.missing:
        sentences.append(
            f"Note that {profile_country.missing} criteria could not be determined for it, so "
            f"the score answers {country_data['Evidence coverage']:.0%} of what you said "
            f"matters; the weight of the missing criteria was spread over the rest rather "
            f"than dropped."
        )

    startup_basis = country_data.get("startup_confidence")
    if isinstance(startup_basis, str) and startup_basis in PROVISIONAL_STARTUP_BASES:
        sentences.append(
            f"Its start-up time is not a national figure but a {startup_basis} standing in for "
            f"one, so treat that criterion as provisional and override it if you hold better data."
        )

    tight_paragraphs([" ".join(sentences)])

    col_deep1, col_deep2 = st.columns([3, 2])

    with col_deep1:
        # Contribution = weight share x score. The column matters more than it looks:
        # it turns "this country scored 0.64" into "0.64 came mostly from experience
        # and low competition", which is what a decision-support tool owes its user.
        # Weights are shown as applied to this country, not as declared. Where a
        # criterion is undetermined its weight is redistributed over the rest, so
        # the shares below always add up to 100% and the contributions to the score.
        used_share = float(country_data["Evidence coverage"]) or 1.0

        rows = []
        for criterion in CRITERIA:
            weight = weights.get(criterion.key, 0)
            if weight <= 0:
                continue
            score = country_data.get(criterion.score_column)
            raw = country_data.get(criterion.key)
            determined = pd.notna(score)
            effective = (weight / total_weight) / used_share if determined else 0.0

            # Rendered as text so that counts and days stay whole numbers while the
            # rate and the indices keep two decimals — one numeric column could not
            # hold both formats.
            if pd.isna(raw):
                value_text = "—"
            elif float(raw) == int(raw):
                value_text = f"{int(raw):,}"
            else:
                value_text = f"{raw:,.2f}"

            # Chapter 3.2 requires the basis of the start-up figure to travel with it,
            # so the planner can tell a statutory deadline from a stand-in median and
            # knows which values are worth replacing with their own first.
            unit_text = criterion.unit
            if criterion.key == "startup_days":
                unit_text = f"{unit_text} ({startup_basis_label(country_data.get('startup_confidence'))})"

            rows.append({
                "Criterion": criterion.label,
                "Value": value_text,
                "Unit": unit_text,
                "Rank": (
                    int(country_data[f"{criterion.score_column}_rank"]) if determined else None
                ),
                "Score": float(score) if determined else None,
                "Weight": effective if determined else None,
                "Contribution": float(score) * effective if determined else None,
            })

        breakdown = pd.DataFrame(rows)
        st.dataframe(
            breakdown,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Criterion": st.column_config.TextColumn("Criterion", pinned=True),
                "Value": st.column_config.TextColumn("Value", width="small"),
                "Unit": st.column_config.TextColumn("Unit", width="small"),
                "Rank": st.column_config.NumberColumn("Rank", format="#%d", width="small"),
                "Score": st.column_config.ProgressColumn(
                    "Score", format="%.2f", min_value=0.0, max_value=1.0
                ),
                "Weight": st.column_config.NumberColumn(
                    "Weight",
                    format="percent",
                    width="small",
                    help="Share of the weight actually applied to this country.",
                ),
                "Contribution": st.column_config.NumberColumn(
                    "Contribution",
                    format="%.3f",
                    help="Weight multiplied by score. These add up to the final score.",
                ),
            },
        )
        st.caption(
            f"Contributions add up to the final score of {country_data['Final Score']:.2f}. "
            f"Where a criterion is undetermined its weight is redistributed over the "
            f"remaining ones, so the shares shown are those actually applied to this country."
        )

        if selected_benchmarks and pd.notna(country_data.get(BENCHMARK_ER)):
            st.markdown(
                f"**Benchmark recruitment rate:** {country_data[BENCHMARK_ER]:,.2f} patients "
                f"per site per month, from {int(country_data['recruitment_trials_benchmark'])} "
                f"benchmark trials. Not part of the score — compare it with the rates sites declare."
            )

    with col_deep2:
        # The selected country against the leader: one outline shows a shape,
        # two show whether it is better or worse and on which criterion.
        active = [
            c for c in CRITERIA
            if weights.get(c.key, 0) > 0 and c.score_column in country_data
        ]
        labels_radar = [c.label for c in active]
        values_selected = [country_data[c.score_column] for c in active]

        if labels_radar:
            fig_radar = go.Figure()
            if selected_country != top["Country"]:
                fig_radar.add_trace(go.Scatterpolar(
                    r=[top[c.score_column] for c in active],
                    theta=labels_radar,
                    fill="toself",
                    name=f"{top['Country']} (rank 1)",
                    line_color="#B0BEC5",
                    opacity=0.55,
                ))
            fig_radar.add_trace(go.Scatterpolar(
                r=values_selected,
                theta=labels_radar,
                fill="toself",
                name=selected_country,
                line_color="#4CAF50",
            ))
            fig_radar.update_layout(
                polar=dict(radialaxis=dict(range=[0, 1], showticklabels=True)),
                margin=dict(l=60, r=60, t=30, b=60),
                height=420,
                legend=dict(orientation="h", yanchor="top", y=-0.05, x=0),
            )
            st.plotly_chart(fig_radar, use_container_width=True)


# ==========================================
# 7. PODSUMOWANIE: RANKING I ROZKŁAD GEOGRAFICZNY
# ==========================================
with st.container(border=True):
    st.header(":material/summarize: 4. Executive Summary", divider="gray")

    # Everything below is derived from the ranking by rule. No language model is
    # involved: a summary that reworded itself on every run would undercut the
    # repeatability the ranking is built to provide.
    verdict = decisiveness(df_results)
    leader = describe_country(df_results.iloc[0], weights, total_weight)

    sum1, sum2, sum3 = st.columns(3)
    sum1.metric("Countries evaluated", len(df_results))
    sum2.metric(
        "Leader",
        leader.country,
        f"{verdict['gap']:+.2f} over the runner-up" if len(df_results) > 1 else None,
    )
    sum3.metric(
        "Outcome",
        verdict["verdict"].capitalize(),
        help=(
            "From the gap to second place: 0.05 or more is a clear leader, under 0.02 "
            "a tie. The narrower the gap, the more the order rests on your weights."
        ),
    )

    strengths = ", ".join(name.lower() for name, _ in leader.strengths)
    headline = (
        f"**{leader.country}** ranks first with a score of {leader.score:.2f}, carried mainly "
        f"by {strengths}."
    )
    if leader.weakness:
        headline += f" Its weakest heavily weighted criterion is {leader.weakness[0].lower()}."
    if verdict["contenders"] > 1:
        headline += (
            f" {verdict['contenders']} countries sit within 0.02 of the top, so the order "
            f"among them is decided by the weights rather than by the data."
        )
    decisive = leadership_sensitivity(
        df_metrics,
        weights,
        st.session_state.get("competition_reference", 0),
        FIXED_MAXIMA,
    )
    if decisive:
        sensitivity = (
            f"Removing **{', '.join(c.lower() for c in decisive)}** from the calculation would "
            f"put a different country first. The recommendation is therefore sensitive to how "
            f"much that criterion matters to you."
        )
    else:
        sensitivity = (
            "No single criterion decides the outcome — dropping any one of them leaves the "
            "same country in first place."
        )

    tight_paragraphs([headline, sensitivity])
    # Collected as plain sentences so the same wording can be written into the
    # exported report — the planner should not have to retype the reasoning.
    summary_lines = [headline, sensitivity]

    # --- what the leading group looks like ------------------------------------------
    GROUP_SIZE = 10
    profile = group_profile(df_results, GROUP_SIZE)
    observations = []

    if "cost" in profile:
        ratio = profile["cost"]["ratio"]
        if ratio > 1.15:
            observations.append(
                f"The top {GROUP_SIZE} are on average {ratio - 1:.0%} more expensive than the "
                f"rest of the field — this shortlist buys quality with money."
            )
        elif ratio < 0.87:
            observations.append(
                f"The top {GROUP_SIZE} are on average {1 - ratio:.0%} cheaper than the rest of "
                f"the field, so the leaders are not simply the wealthy markets."
            )

    if "startup" in profile:
        ratio = profile["startup"]["ratio"]
        if ratio < 0.85:
            observations.append(
                f"They also start faster, needing about {1 - ratio:.0%} fewer days for "
                f"regulatory approval than the countries below them."
            )
        elif ratio > 1.15:
            observations.append(
                f"They are, however, slower to start — roughly {ratio - 1:.0%} more days to "
                f"regulatory approval than the rest of the field."
            )

    niche = specialists(df_results, weights, GROUP_SIZE)
    outside = [item for item in niche if not item["in_top_group"]]
    inside = [item for item in niche if item["in_top_group"]]

    if inside:
        observations.append(
            "Within the leading group, "
            + "; ".join(
                f"**{item['country']}** is the strongest on {item['criterion'].lower()}"
                for item in inside[:3]
            )
            + "."
        )
    if outside:
        item = outside[0]
        observations.append(
            f"Worth noting outside the top {GROUP_SIZE}: **{item['country']}** leads the whole "
            f"field on {item['criterion'].lower()} despite ranking #{item['rank']} overall — "
            f"raise the weight on that criterion and it climbs quickly."
        )

    if observations:
        tight_bullets("How the leading group looks", observations)
        summary_lines.extend(observations)

    # --- shortlist -----------------------------------------------------------------
    st.markdown("**Shortlist**")
    shortlist_rows = []
    for _, row in df_results.head(GROUP_SIZE).iterrows():
        highlight = describe_country(row, weights, total_weight)
        shortlist_rows.append({
            "Rank": highlight.rank,
            "Country": highlight.country,
            "Score": highlight.score,
            "Driven by": ", ".join(name for name, _ in highlight.strengths),
            "Held back by": highlight.weakness[0] if highlight.weakness else "—",
            "Missing": highlight.missing,
        })
    st.dataframe(
        pd.DataFrame(shortlist_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Rank": st.column_config.NumberColumn("Rank", format="#%d", width="small"),
            "Score": st.column_config.ProgressColumn(
                "Score", format="%.2f", min_value=0.0, max_value=1.0
            ),
            "Missing": st.column_config.NumberColumn("Missing Criteria", width="small"),
        },
    )

    # --- ranking bar chart ---------------------------------------------------------
    # The table gives exact figures; the chart shows how far apart the leaders are,
    # which a column of numbers hides — a close field and a runaway winner look alike.
    TOP_N = 25
    chart_data = df_results.head(TOP_N).copy()
    chart_data["Complete"] = chart_data["Criteria missing"].eq(0).map(
        {True: "All criteria", False: "Some criteria undetermined"}
    )

    fig_scores = px.bar(
        chart_data,
        x="Country",
        y="Final Score",
        color="Complete",
        color_discrete_map={
            "All criteria": "#4CAF50",
            "Some criteria undetermined": "#B0BEC5",
        },
        hover_data={"Rank": True, "Criteria missing": True, "Complete": False},
    )
    fig_scores.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title=None,
        yaxis_title="Final score",
        xaxis={"categoryorder": "total descending"},
        legend=dict(title=None, orientation="h", yanchor="bottom", y=1.0, x=0),
        bargap=0.25,
    )
    st.plotly_chart(fig_scores, use_container_width=True)
    if len(df_results) > TOP_N:
        st.caption(f"Chart shows the top {TOP_N} of {len(df_results)} countries.")

    # --- what each score is made of, and the cost trade-off ------------------------
    chart_left, chart_right = st.columns(2)

    with chart_left:
        st.markdown("**What each score is made of**")
        stack_rows = []
        for _, row in df_results.head(10).iterrows():
            for key, value in contributions(row, weights, total_weight).items():
                stack_rows.append({
                    "Country": row["Country"],
                    "Criterion": CRITERIA_BY_KEY[key].label,
                    "Contribution": value,
                })
        if stack_rows:
            fig_stack = px.bar(
                pd.DataFrame(stack_rows),
                x="Country", y="Contribution", color="Criterion",
            )
            fig_stack.update_layout(
                height=380,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title=None,
                yaxis_title="Final score",
                xaxis={"categoryorder": "total descending"},
                legend=dict(title=None, font=dict(size=10)),
            )
            st.plotly_chart(fig_stack, use_container_width=True)
            st.caption("Segments add up to the final score of each country.")

    with chart_right:
        st.markdown("**Recruitment rate**")
        # The figure planners argue about most, and the one sites are asked to
        # forecast. Showing the benchmark rate beside the general one turns it into
        # a check: a country that recruits well in general but poorly in trials like
        # this one is a different proposition from one that does both.
        er_rows = []
        for _, row in df_results.head(GROUP_SIZE).iterrows():
            if pd.notna(row.get("recruitment_rate")):
                er_rows.append({
                    "Country": row["Country"],
                    "Rate": float(row["recruitment_rate"]),
                    "Basis": "All historical trials",
                    "Trials": int(row.get("recruitment_trials", 0)),
                })
            if selected_benchmarks and pd.notna(row.get("recruitment_rate_benchmark")):
                er_rows.append({
                    "Country": row["Country"],
                    "Rate": float(row["recruitment_rate_benchmark"]),
                    "Basis": "Benchmark trials only",
                    "Trials": int(row.get("recruitment_trials_benchmark", 0)),
                })

        if er_rows:
            er_frame = pd.DataFrame(er_rows)
            fig_er = px.bar(
                er_frame,
                x="Rate", y="Country", color="Basis", orientation="h", barmode="group",
                color_discrete_map={
                    "All historical trials": "#4CAF50",
                    "Benchmark trials only": "#7E57C2",
                },
                hover_data={"Trials": True, "Basis": False},
            )
            fig_er.update_layout(
                height=380,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="Patients per site per month",
                yaxis_title=None,
                yaxis={"categoryorder": "total ascending"},
                legend=dict(title=None, orientation="h", yanchor="bottom", y=1.0, x=0),
            )
            st.plotly_chart(fig_er, use_container_width=True)

            no_rate = int(df_results.head(GROUP_SIZE)["recruitment_rate"].isna().sum())
            note = "Hover for the number of trials behind each figure."
            if no_rate:
                note += (
                    f" {no_rate} of the leading {GROUP_SIZE} are missing from the chart: "
                    f"too few trials to determine a rate."
                )
            if not selected_benchmarks:
                note += " Select benchmark trials to compare against a like-for-like rate."
            st.caption(note)
        else:
            st.info(
                "No recruitment rates could be determined for the leading countries. "
                "Lower the minimum number of trials or widen the historical query."
            )

    # --- caveats -------------------------------------------------------------------
    caveats = []
    if incomplete:
        caveats.append(
            f"{incomplete} of {len(df_results)} countries are scored on an incomplete set of "
            f"criteria; their weight was redistributed over the criteria that were available."
        )
    if hist_result.truncated or comp_result.truncated:
        caveats.append(
            "At least one registry query hit the download limit, so the samples behind "
            "experience and competition are truncated."
        )
    undetermined_rate = int(df_results["recruitment_rate"].isna().sum())
    if undetermined_rate:
        caveats.append(
            f"{undetermined_rate} countries have no recruitment rate: fewer than "
            f"{st.session_state.get('min_trials_for_rate', 3)} trials contributed a usable figure."
        )
    if caveats:
        with st.expander("Caveats on this result"):
            st.markdown(
                "<ul style='line-height:1.4;margin:0;padding-left:1.2rem'>"
                + "".join(f"<li style='margin:0 0 0.25rem 0'>{_inline(i)}</li>" for i in caveats)
                + "</ul>",
                unsafe_allow_html=True,
            )

    st.markdown("**Geographical distribution**")
    col_map1, col_map2 = st.columns(2)
    for column, value_column, scale, title in (
        (col_map1, "experience_raw", "Blues", f"Historical experience ({len(hist_studies)} trials)"),
        (col_map2, "competition_raw", "Reds", f"Active competition ({len(comp_studies)} trials)"),
    ):
        with column:
            fig = px.choropleth(
                df_results,
                locations="iso3" if "iso3" in df_results.columns else "Country",
                locationmode=None if "iso3" in df_results.columns else "country names",
                color=value_column,
                hover_name="Country",
                color_continuous_scale=scale,
                title=title,
            )
            fig.update_layout(
                margin={"r": 0, "t": 40, "l": 0, "b": 0},
                geo=dict(showframe=False, showcoastlines=True, projection_type="equirectangular"),
            )
            st.plotly_chart(fig, use_container_width=True)


# ==========================================
# 8. METODA KONTROLNA: TOPSIS
# ==========================================
with st.container(border=True):
    st.header(":material/balance: 5. Control Method — TOPSIS", divider="gray")

    # The point of this section is not a second recommendation but an answer to
    # the question a weighted sum always invites: how much of the result follows
    # from the data, and how much from the decision to add criteria up? TOPSIS
    # runs on the same normalised matrix and the same weights, so the only thing
    # that differs is the aggregation rule.
    scored_matrix = add_scores(
        df_metrics,
        st.session_state.get("competition_reference", 0),
        FIXED_MAXIMA,
    )
    df_topsis = topsis_scores(scored_matrix, weights)
    agreement = compare_rankings(df_results, df_topsis)

    if df_topsis.empty:
        st.info(
            "No country is described completely on the criteria you gave weight to, "
            "so the two methods cannot be compared. Lower the recruitment-rate "
            "threshold or set the weight of a sparsely covered criterion to zero."
        )
    else:
        ctrl1, ctrl2, ctrl3 = st.columns(3)
        ctrl1.metric(
            "Countries compared",
            agreement["countries"],
            f"of {len(df_results)} ranked",
            delta_color="off",
            help=(
                "TOPSIS measures distance from an ideal defined on the full vector "
                "of criteria, so only completely described countries can take part."
            ),
        )
        ctrl2.metric(
            "Rank correlation",
            "—" if agreement["rho"] is None else f"{agreement['rho']:.3f}",
            help="Spearman's rho between the two orderings over the shared countries.",
        )
        ctrl3.metric(
            f"Shared top {agreement['top_n']}",
            f"{agreement['overlap']} of {agreement['top_n']}",
            help="Countries appearing in the leading group under both methods.",
        )

        verdict_lines = []
        if agreement["same_leader"]:
            verdict_lines.append(
                f"**{agreement['leader_saw']}** comes first under both rules, so the "
                f"recommendation does not rest on the choice of aggregation method."
            )
        else:
            verdict_lines.append(
                f"The methods disagree at the top: the weighted sum puts "
                f"**{agreement['leader_saw']}** first, TOPSIS **{agreement['leader_topsis']}**. "
                f"Both are defensible readings of the same data, and the difference "
                f"is a reason to look at the two profiles side by side rather than to "
                f"prefer one rule."
            )
        verdict_lines.append(
            "The weighted sum lets a strong criterion pay for a weak one without "
            "limit; TOPSIS measures distance from the best and the worst attainable "
            "profile, so an uneven candidate falls further than the arithmetic alone "
            "suggests. Countries that hold their place under both are the ones whose "
            "position survives that difference."
        )
        tight_paragraphs(verdict_lines)

        comparison = agreement["table"].copy()
        comparison["Movement"] = comparison["Rank shift"].map(
            lambda d: "—" if d == 0 else (f"▲ {d}" if d > 0 else f"▼ {abs(d)}")
        )
        st.dataframe(
            comparison[[
                "Country", "SAW Rank", "TOPSIS Rank", "Movement",
                "Final Score", "TOPSIS Score",
            ]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "SAW Rank": st.column_config.NumberColumn("Weighted sum", format="#%d", width="small"),
                "TOPSIS Rank": st.column_config.NumberColumn("TOPSIS", format="#%d", width="small"),
                "Movement": st.column_config.TextColumn(
                    "Movement", width="small",
                    help="How far TOPSIS moves the country against the weighted sum.",
                ),
                "Final Score": st.column_config.ProgressColumn(
                    "Weighted score", format="%.2f", min_value=0.0, max_value=1.0
                ),
                "TOPSIS Score": st.column_config.ProgressColumn(
                    "Closeness", format="%.2f", min_value=0.0, max_value=1.0
                ),
            },
        )
        st.caption(
            "Ranks are recomputed within the compared subset, so the two columns "
            "measure disagreement between the methods rather than the exclusion of "
            "incompletely described countries."
        )


# ==========================================
# 9. RAPORT I SCENARIUSZ
# ==========================================
with st.container(border=True):
    st.header(":material/download: 6. Report and Scenario", divider="gray")

    # A ranking read off a live registry cannot be reproduced from the ranking
    # alone. What reproduces it is the record of how it was made: the query, the
    # weights, the thresholds, the restrictions, the benchmark trials and the
    # moment the data was fetched. That record is the Scenario sheet, and the same
    # sheet is what the app reads to put those settings back.
    st.markdown(
        "Both files hold the same ranking. The spreadsheet is for checking the "
        "numbers and carries a **Scenario** sheet with every setting behind them; "
        "the PDF is for putting in front of a team. To reopen a saved analysis, "
        "load the spreadsheet at the top of **'1. Planned Study Definition'**."
    )

    export_left, export_pdf = st.columns(2)

    with export_left:
        report_bytes = build_report(
            df_results=df_results,
            weights=weights,
            session_state=st.session_state,
            api_query=api_query,
            fetched_at=fetched_at,
            benchmark_table=st.session_state.get("comprehensive_table"),
            summary_lines=summary_lines,
            agreement=agreement,
        )
        indication = (api_query.get("historical", {}).get("indication") or "ranking")
        safe = "".join(ch if ch.isalnum() else "_" for ch in indication)[:40]
        st.download_button(
            "Download the report",
            data=report_bytes,
            file_name=f"country_ranking_{safe}_{fetched_at:%Y%m%d_%H%M}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
        st.caption(
            f"Five sheets · {len(df_results)} countries · "
            f"{len(selected_benchmarks)} benchmark trials."
        )
        # A report exported without a study definition restores a query but leaves
        # the first page blank, and the next export inherits the same gap. Saying so
        # here is the only place the loop can be broken.
        if not (st.session_state.get("study_params") or {}).get("indication"):
            st.warning(
                "The planned study is not defined, so this report will not restore "
                "page 1. Fill in **'1. Planned Study Definition'**, save it, and "
                "export again for a self-contained file."
            )

    with export_pdf:
        # The spreadsheet is for the analyst checking the numbers; this is for the
        # meeting where the shortlist is argued over, so it leads with the
        # recommendation and keeps the method at the back.
        #
        # Imported here rather than at the top of the file: the PDF needs reportlab
        # and matplotlib, and an installation missing either should lose one button
        # rather than the whole results page.
        try:
            from services.pdf_report import build_pdf
        except ImportError as missing:
            st.warning(
                f"The PDF report needs a package that is not installed ({missing.name}). "
                f"Run `pip install -r requirements.txt` to enable it."
            )
            build_pdf = None

        pdf_bytes = None if build_pdf is None else build_pdf(
            df_results=df_results,
            weights=weights,
            session_state=st.session_state,
            api_query=api_query,
            fetched_at=fetched_at,
            summary_lines=summary_lines,
            agreement=agreement,
            decisive=decisive,
            caveats=caveats,
            sample_counts={
                "historical": len(hist_studies),
                "competition": len(comp_studies),
            },
        )
        if pdf_bytes is not None:
            st.download_button(
                "Download the presentation",
                data=pdf_bytes,
                file_name=f"country_selection_{safe}_{fetched_at:%Y%m%d_%H%M}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
            st.caption(
                "Four pages · recommendation, how the scores are built, how far the "
                "result holds, and the method behind it."
            )
