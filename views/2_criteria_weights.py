import streamlit as st
import datetime
from utils.constants import CT_PHASES, CT_STATUSES, CT_SPONSORS, CT_STUDY_TYPES


st.title("ClinicalTrials.gov Search Parameters")
st.markdown("Define separate criteria for analyzing historical experience and tracking active competition. This dual approach ensures precise benchmarking.")

# This page needs somewhere to take its default indication from — either the
# planned study or a query restored from a saved report. Requiring the study
# definition alone left anyone who restored a scenario without one stranded here,
# with a query in hand and no way to edit it.
p = st.session_state.get("study_params") or {}
saved = st.session_state.get("api_query", {})

if not p.get("indication") and not saved:
    st.warning(
        "No parameters found. Define the study on '1. Planned Study Definition', "
        "or restore a saved report there."
    )
    st.stop()

if not p.get("indication"):
    st.info(
        "Working from a restored query. The planned study is not defined, which "
        "only affects the similarity scoring on the next page — fill it in on "
        "'1. Planned Study Definition' if you intend to use that."
    )

# Streamlit discards the state of widgets that a run does not draw, so returning
# to this page from another one wipes every field back to its hard-coded default.
# The saved query is our own dictionary and survives navigation, so the defaults
# are read from it whenever it exists: coming back to correct one filter must not
# silently reset the other eight.
saved_hist = saved.get("historical", {})
saved_comp = saved.get("competition", {})


def labels_of(mapping: dict, codes, fallback: list) -> list:
    """Display labels for stored API codes, falling back when nothing is saved."""
    reverse = {code: label for label, code in mapping.items()}
    restored = [reverse[c] for c in (codes or []) if c in reverse]
    return restored if restored else fallback


def index_of(mapping: dict, code, fallback: str) -> int:
    reverse = {c: label for label, c in mapping.items()}
    label = reverse.get(code, fallback)
    keys = list(mapping.keys())
    return keys.index(label) if label in keys else keys.index(fallback)


default_phases_hist = [ph for ph in p.get("phases", []) if ph in CT_PHASES]

with st.form("api_query_form"):
    
    st.header(":material/history: 1. Historical Studies", divider="gray")
    st.markdown("Filters for assessing country experience, enrollment rates, and finding benchmark studies.")
    
    h_col1, h_col2 = st.columns(2)
    with h_col1:
        h_indication = st.text_input(
            "Indication*",
            value=saved_hist.get("indication", p.get("indication", "")),
            key="h_ind",
        )
        h_phases = st.multiselect(
            "Phases*", options=list(CT_PHASES.keys()),
            default=labels_of(CT_PHASES, saved_hist.get("phases"), default_phases_hist),
            key="h_pha",
        )
        h_sponsor = st.selectbox(
            "Sponsor Type*", options=list(CT_SPONSORS.keys()),
            index=index_of(CT_SPONSORS, saved_hist.get("sponsor"), "Industry"),
            key="h_spo",
        )
        
    with h_col2:
        default_start_date = datetime.date.today().replace(year=datetime.date.today().year - 5)
        saved_lookback = saved_hist.get("start_date")
        if isinstance(saved_lookback, str):
            saved_lookback = datetime.date.fromisoformat(saved_lookback)
        h_start_date = st.date_input(
            "Historical Lookback (Started After)*",
            value=saved_lookback or default_start_date,
            format="YYYY-MM-DD",
            key="h_look",
            help="Trials starting earlier are left out of the historical sample.",
        )
        h_status = st.multiselect(
            "Study Status*", options=list(CT_STATUSES.keys()),
            default=labels_of(CT_STATUSES, saved_hist.get("status"), ["Completed"]),
            key="h_stat",
        )
        h_type = st.selectbox(
            "Study Type*", options=list(CT_STUDY_TYPES.keys()),
            index=index_of(CT_STUDY_TYPES, saved_hist.get("type"), "Interventional"),
            key="h_type",
        )

    st.header(":material/groups: 2. Competition", divider="gray")
    st.markdown("Filters for identifying ongoing and planned trials to evaluate site saturation and competition.")
    
    c_col1, c_col2 = st.columns(2)
    with c_col1:
        c_indication = st.text_input(
            "Indication*",
            value=saved_comp.get("indication", p.get("indication", "")),
            key="c_ind",
        )
        c_phases = st.multiselect(
            "Phases*", options=list(CT_PHASES.keys()),
            default=labels_of(CT_PHASES, saved_comp.get("phases"), list(CT_PHASES.keys())),
            key="c_pha",
        )
        c_sponsor = st.selectbox(
            "Sponsor Type*", options=list(CT_SPONSORS.keys()),
            index=index_of(CT_SPONSORS, saved_comp.get("sponsor"), "All"),
            key="c_spo",
        )
        
    with c_col2:
        # Both boundaries are optional and both start empty. A trial that opened
        # years ago and is still recruiting competes for the same patients today,
        # so restricting competition by start date is a choice the planner makes,
        # not a default the model imposes.
        saved_window = tuple(saved_comp.get("competition_window") or ())
        window_left, window_right = st.columns(2)
        with window_left:
            c_start = st.date_input(
                "Started After",
                value=saved_window[0] if len(saved_window) > 0 else None,
                format="YYYY-MM-DD",
                key="c_start",
                help="Optional. Empty means no lower limit.",
            )
        with window_right:
            c_end = st.date_input(
                "Started Before",
                value=saved_window[1] if len(saved_window) > 1 else None,
                format="YYYY-MM-DD",
                key="c_end",
                help="Optional. Empty means no upper limit.",
            )
        c_status = st.multiselect(
            "Study Status*", options=list(CT_STATUSES.keys()),
            default=labels_of(
                CT_STATUSES, saved_comp.get("status"),
                ["Recruiting", "Not yet recruiting", "Active, not recruiting"],
            ),
            key="c_stat",
        )
        c_type = st.selectbox(
            "Study Type*", options=list(CT_STUDY_TYPES.keys()),
            index=index_of(CT_STUDY_TYPES, saved_comp.get("type"), "All"),
            key="c_type",
        )

    # Full width, below both columns: it applies to the whole competition query and
    # its label is far longer than either column can hold.
    c_remove_past_pcd = st.checkbox(
        "Remove trials with Primary Completion Date in the past (or before planned start)",
        value=saved_comp.get("remove_past_pcd", True),
        key="c_pcd",
    )

    st.divider()
    submitted = st.form_submit_button("Save Search Parameters & Proceed", type="primary")
    
    if submitted:
        errors = []
        if not h_indication.strip(): errors.append("Historical: Indication")
        if not h_phases: errors.append("Historical: Phases")
        if not h_status: errors.append("Historical: Study Status")
        if not c_indication.strip(): errors.append("Competition: Indication")
        if not c_phases: errors.append("Competition: Phases")
        if not c_status: errors.append("Competition: Study Status")
        
        if errors:
            st.error(f"Please fill in the following mandatory fields: **{', '.join(errors)}**")
        else:
            st.session_state.api_query = {
                "historical": {
                    "indication": h_indication,
                    "phases": [CT_PHASES[ph] for ph in h_phases],
                    "sponsor": CT_SPONSORS[h_sponsor],
                    "start_date": h_start_date.strftime("%Y-%m-%d"), 
                    "status": [CT_STATUSES[st] for st in h_status],
                    "type": CT_STUDY_TYPES[h_type]
                },
                "competition": {
                    "indication": c_indication,
                    "phases": [CT_PHASES[ph] for ph in c_phases],
                    "sponsor": CT_SPONSORS[c_sponsor],
                    "competition_window": (c_start, c_end),
                    "status": [CT_STATUSES[st] for st in c_status],
                    "type": CT_STUDY_TYPES[c_type],
                    "remove_past_pcd": c_remove_past_pcd # Zapisujemy wybór
                }
            }
            st.switch_page("views/4_ai_benchmark.py")