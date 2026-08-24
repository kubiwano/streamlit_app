"""
The "resume a saved analysis" control, shared by the two configuration pages.

Loading a scenario fills in the planned study, the registry query, the weights,
the thresholds and the sample restrictions in one go, so it belongs wherever the
planner might realise they are redoing work they have already done — which is
either of the two pages where that work is entered.

Kept out of the page scripts because it is the same control twice: a copy on each
page would drift, and the two would eventually restore different things.
"""

import streamlit as st


def render_scenario_loader(key_prefix: str, expanded: bool = False) -> None:
    """
    Draw the uploader and apply whatever the user gives it.

    `key_prefix` keeps the widget keys apart between pages. Streamlit never draws
    both at once, but distinct keys mean a file chosen on one page does not
    silently reappear on the other.
    """
    with st.expander(":material/upload_file: Resume a saved analysis", expanded=expanded):
        st.caption(
            "Upload a spreadsheet exported from the results page. Its Scenario "
            "sheet restores the study definition, the registry query, the weights, "
            "the thresholds and the sample restrictions; the Benchmarks sheet "
            "restores the trials you had selected."
        )
        uploaded = st.file_uploader(
            "Exported report (.xlsx)", type=["xlsx"], key=f"{key_prefix}_scenario_file"
        )
        if uploaded is None:
            return

        # Imported here so that a missing openpyxl costs this one control rather
        # than the page it sits on.
        from services.report import apply_scenario, read_benchmarks, read_scenario

        # What the file actually holds, shown before anything is applied. A report
        # written by an older build simply lacks a section, and saying which one is
        # missing beats leaving a page mysteriously blank.
        try:
            uploaded.seek(0)
            preview = read_scenario(uploaded)
        except Exception as exc:
            st.error(f"Could not read a Scenario sheet from that file: {exc}")
            return

        expected = [
            "Planned study", "Historical query", "Competition query",
            "Model settings", "Sample restrictions", "Weights",
        ]
        # Presence, not truthiness: a section of restrictions all switched off is
        # complete, not missing, and marking it absent sent the last diagnosis in
        # the wrong direction.
        missing = [name for name in expected if name not in preview]
        st.caption(
            "This file contains: "
            + " · ".join(f"{'✓' if n in preview else '✗'} {n}" for n in expected)
        )

        study = preview.get("Planned study", {})
        if study.get("indication"):
            st.caption(
                f"Study: **{study.get('title') or '(untitled)'}** — {study['indication']}"
            )
        elif "Planned study" in preview:
            st.warning(
                "This report carries an empty study definition, so page 1 will stay "
                "blank. It was exported from a session where the study had not been "
                "saved on **'1. Planned Study Definition'** — fill that page in, save "
                "it, and export again to make the file self-contained."
            )

        if st.button(
            "Restore these settings", type="primary", key=f"{key_prefix}_scenario_apply"
        ):
            scenario = preview

            restored, skipped = apply_scenario(scenario, st.session_state)

            uploaded.seek(0)
            benchmarks = read_benchmarks(uploaded)
            if benchmarks:
                st.session_state.selected_benchmarks = benchmarks
                # The benchmark table is rebuilt from whatever the registry returns
                # now, so its tick marks are derived from this list rather than
                # restored alongside it.
                st.session_state.pop("comprehensive_table", None)
                st.session_state.pop("benchmark_sample_ids", None)
                restored.append(f"{len(benchmarks)} benchmark trials")

            st.session_state.scenario_outcome = {"restored": restored, "skipped": skipped}
            st.rerun()


def report_scenario_outcome() -> None:
    """Show what the last restore managed, once, after the rerun it triggered."""
    outcome = st.session_state.pop("scenario_outcome", None)
    if not outcome:
        return
    if outcome["restored"]:
        st.success("Restored: " + ", ".join(outcome["restored"]) + ".")
    if outcome["skipped"]:
        st.caption("Not restored: " + "; ".join(outcome["skipped"]) + ".")
