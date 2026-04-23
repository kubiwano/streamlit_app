import streamlit as st
import requests
import pandas as pd

# 1. PAGE CONFIGURATION
# Essential for WordPress iframes to ensure the app uses the full width available
st.set_page_config(page_title="Clinical Trials Explorer", layout="wide")

# 2. DATA PROCESSING FUNCTION
def process_studies_to_df(studies):
    rows = []
    for study in studies:
        # Accessing different JSON modules
        protocol = study.get("protocolSection", {})
        derived = study.get("derivedSection", {})
        
        id_module = protocol.get("identificationModule", {})
        status_module = protocol.get("statusModule", {})
        design_module = protocol.get("designModule", {})
        eligibility = protocol.get("eligibilityModule", {})
        contacts = protocol.get("contactsLocationsModule", {})
        
        # NCT ID and Link
        nct_id = id_module.get("nctId")
        study_url = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None
        
        # Inclusion/Exclusion Criteria Splitting
        raw_criteria = eligibility.get("eligibilityCriteria", "")
        inclusion, exclusion = "N/A", "N/A"
        if "Exclusion Criteria:" in raw_criteria:
            parts = raw_criteria.split("Exclusion Criteria:")
            inclusion = parts[0].replace("Inclusion Criteria:", "").strip()
            exclusion = parts[1].strip()
        else:
            inclusion = raw_criteria.replace("Inclusion Criteria:", "").strip()

        # Sponsor & MeSH Terms
        sponsor_info = id_module.get("leadSponsor", {})
        mesh_browse = derived.get("conditionBrowseModule", {})
        meshes = [m.get("term") for m in mesh_browse.get("meshes", []) if m.get("term")]

        # Locations and Enrollment
        locations = contacts.get("locations", [])
        countries = list(set([loc.get("country") for loc in locations if loc.get("country")]))
        enrollment = design_module.get("enrollmentInfo", {}).get("count", "N/A")

        row = {
            "Study ID": nct_id,
            "Link": study_url,
            "Study Title": id_module.get("briefTitle", "No title"),
            "Indication": ", ".join(protocol.get("conditionsModule", {}).get("conditions", [])),
            "MeSH Terms": ", ".join(meshes) if meshes else "N/A",
            "Study Type": design_module.get("studyType", "N/A"),
            "Recruitment Status": status_module.get("overallStatus", "Unknown"),
            "Phase": ", ".join(design_module.get("phases", [])),
            "Sponsor": sponsor_info.get("name", "N/A"),
            "Sponsor Type": sponsor_info.get("class", "N/A"),
            "Min Age": eligibility.get("minimumAge", "N/A"),
            "Max Age": eligibility.get("maximumAge", "N/A"),
            "Study Start Date": status_module.get("startDateStruct", {}).get("date", "N/A"),
            "Primary Completion Date": status_module.get("primaryCompletionDateStruct", {}).get("date", "N/A"),
            "Countries": ", ".join(countries) if countries else "Global",
            "Number of Sites": len(locations),
            "Number of Patients": enrollment,
            "Inclusion Criteria": inclusion,
            "Exclusion Criteria": exclusion
        }
        rows.append(row)
    return pd.DataFrame(rows)

# 3. USER INTERFACE
st.title("🧪 Clinical Trials Explorer")
st.markdown("Automated ClinicalTrials.gov Data Extraction")

# 4. SIDEBAR - GLOBAL SEARCH
st.sidebar.header("1. API Search Filters")
condition_query = st.sidebar.text_input("Global Condition (e.g., Cancer)", value="Diabetes")
location_query = st.sidebar.text_input("Country Location (Optional)", value="")
results_limit = st.sidebar.slider("Initial Results Limit", 5, 100, 20)

# Initialize Session State to store downloaded data
if 'raw_df' not in st.session_state:
    st.session_state.raw_df = None

# Button to trigger the API request
if st.sidebar.button("Fetch Data from CT.gov"):
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.cond": condition_query,
        "pageSize": results_limit
    }
    if location_query:
        params["query.locl"] = location_query

    with st.spinner('Accessing ClinicalTrials.gov Database...'):
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            studies = response.json().get("studies", [])
            
            if studies:
                st.session_state.raw_df = process_studies_to_df(studies)
            else:
                st.sidebar.warning("No results found. Try a different query.")
                st.session_state.raw_df = None
        except Exception as e:
            st.error(f"Connection Error: {e}")

# 5. SIDEBAR - DYNAMIC INDICATION FILTERING
if st.session_state.raw_df is not None:
    st.sidebar.markdown("---")
    st.sidebar.header("2. Refine Results")
    
    # Extract unique individual indications for the filter
    all_inds = st.session_state.raw_df['Indication'].str.split(', ').explode().unique()
    all_inds = sorted([x for x in all_inds if x and x != "N/A"])
    
    selected_inds = st.sidebar.multiselect(
        "Filter by Specific Indication:",
        options=all_inds,
        default=all_inds
    )

    # Filter the DataFrame based on multiselect
    filtered_df = st.session_state.raw_df[
        st.session_state.raw_df['Indication'].apply(
            lambda x: any(ind in x for ind in selected_inds)
        )
    ]

    # 6. DISPLAY RESULTS
    st.subheader(f"Search Results: {len(filtered_df)} Trials found")
    
    st.dataframe(
        filtered_df,
        use_container_width=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="Open Study ↗"),
            "Study Title": st.column_config.TextColumn("Study Title", width="medium"),
            "Inclusion Criteria": st.column_config.TextColumn("Inclusion Criteria", width="large"),
            "Exclusion Criteria": st.column_config.TextColumn("Exclusion Criteria", width="large"),
            "MeSH Terms": st.column_config.TextColumn("MeSH Terms", width="medium"),
            "Indication": st.column_config.TextColumn("Indication", width="medium"),
        },
        hide_index=True
    )

    # 7. EXPORT DATA
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download current table as CSV",
        data=csv,
        file_name=f"clinical_trials_export.csv",
        mime='text/csv',
    )
else:
    st.info("💡 Start by entering a condition in the sidebar and clicking 'Fetch Data'.")

# 8. FOOTER (Optional)
st.sidebar.markdown("---")
st.sidebar.caption("Data source: ClinicalTrials.gov API v2")