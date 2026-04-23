import streamlit as st
import requests
import pandas as pd

# Konfiguracja strony
st.set_page_config(page_title="IVCO - Clinical Trials Explorer", layout="wide")

st.title("🧪 Przeglądarka Badań Klinicznych")
st.write("Dane pobierane w czasie rzeczywistym z ClinicalTrials.gov")

# --- SIDEBAR: FILTRY ---
st.sidebar.header("Filtry wyszukiwania")
condition = st.sidebar.text_input("Choroba / Stan", value="Cancer")
location = st.sidebar.text_input("Kraj (np. Poland)", value="")
status = st.sidebar.selectbox("Status badania", ["Dowolny", "RECRUITING", "COMPLETED", "TERMINATED"])

limit = st.sidebar.slider("Liczba wyników", 5, 50, 10)

# --- LOGIKA API ---
def fetch_trials(cond, loc, stat, limit):
    # Budowanie bazowego URL API v2
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    
    params = {
        "query.cond": cond,
        "pageSize": limit
    }
    
    if loc:
        params["query.locl"] = loc  # filtr lokalizacji
    if stat != "Dowolny":
        params["filter.overallStatus"] = stat

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Błąd połączenia z API: {e}")
        return None

# --- WYŚWIETLANIE WYNIKÓW ---
if st.button("Szukaj badań"):
    data = fetch_trials(condition, location, status, limit)
    
    if data and "studies" in data:
        studies = data["studies"]
        st.success(f"Znaleziono {len(studies)} badań dla: {condition}")
        
        for study in studies:
            protocol = study.get("protocolSection", {})
            id_module = protocol.get("identificationModule", {})
            status_module = protocol.get("statusModule", {})
            description_module = protocol.get("descriptionModule", {})
            
            nct_id = id_module.get("nctId")
            title = id_module.get("officialTitle", id_module.get("briefTitle"))
            curr_status = status_module.get("overallStatus")
            brief_summary = description_module.get("briefSummary", "Brak opisu.")

            # Wyświetlanie w "kartach" (expanderach)
            with st.expander(f"📍 {nct_id} | {title}"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**Opis:** {brief_summary}")
                with col2:
                    st.info(f"**Status:** {curr_status}")
                    st.link_button("Szczegóły badania", f"https://clinicaltrials.gov/study/{nct_id}")
    else:
        st.warning("Nie znaleziono badań spełniających kryteria.")