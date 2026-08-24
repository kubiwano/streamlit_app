import streamlit as st

# 1. Globalna konfiguracja strony (zawsze jako pierwsze wywołanie Streamlit)
st.set_page_config(
    page_title="Clinical Trials Optimizer",
    page_icon=":material/public:",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. Wstępna inicjalizacja stanu aplikacji (Session State)
# Zapobiega to błędom KeyError, gdy użytkownik przeskakuje między stronami
def init_session_state():
    if "study_params" not in st.session_state:
        st.session_state.study_params = {}
    # Ustawienia modelu: czytane na gorze strony wynikow, zanim widgety
    # zdaza sie narysowac, wiec musza istniec od pierwszego uruchomienia.
    if "min_trials_for_rate" not in st.session_state:
        st.session_state.min_trials_for_rate = 3
    if "competition_reference" not in st.session_state:
        st.session_state.competition_reference = 0
    # Dopoki uzytkownik sam nie ruszy suwaka, poziom odniesienia podaza za
    # najwyzsza zaobserwowana liczba badan konkurencyjnych.
    if "competition_reference_auto" not in st.session_state:
        st.session_state.competition_reference_auto = True
    # Ograniczenia proby historycznej. Domyslnie wylaczone: pelna proba jest
    # tym, co zwrocil rejestr, a kazde zawezenie jest decyzja uzytkownika.
    if "exclude_single_site" not in st.session_state:
        st.session_state.exclude_single_site = False
    if "exclude_single_country" not in st.session_state:
        st.session_state.exclude_single_country = False
    if "exclude_healthy_volunteers" not in st.session_state:
        st.session_state.exclude_healthy_volunteers = False
    if "exclude_undated_competition" not in st.session_state:
        st.session_state.exclude_undated_competition = False
    # Wagi trzymane pod wlasnym kluczem, NIE pod kluczami widzetow. Streamlit
    # kasuje stan widzetu, ktorego dany przebieg nie narysowal, wiec przy przejsciu
    # na inna strone klucze "w_*" znikaly, a pole liczbowe bez argumentu value
    # wracalo do min_value, czyli do zera. Zwykly slownik nie podlega temu
    # sprzataniu i to on jest zrodlem prawdy dla wag.
    from services.scoring import CRITERIA
    if "criteria_weights" not in st.session_state:
        st.session_state.criteria_weights = {
            criterion.key: criterion.default_weight for criterion in CRITERIA
        }
    else:
        for criterion in CRITERIA:
            st.session_state.criteria_weights.setdefault(
                criterion.key, criterion.default_weight
            )

init_session_state()

# 3. Definicja stron za pomocą st.Page
page_1 = st.Page(
    page="views/1_study_config.py",
    title="1. Planned Study Definition",
    icon=":material/description:",
    default=True
)

page_2 = st.Page(
    page="views/2_criteria_weights.py",
    title="2. Search Criteria",
    icon=":material/search:"
)

# Wybór badań wzorcowych poprzedza ranking: wskazanie użytkownika jest
# danymi wejściowymi modelu (kryterium doświadczenia szczegółowego),
# a nie komentarzem do gotowego wyniku.
page_3 = st.Page(
    page="views/4_ai_benchmark.py",
    title="3. AI Benchmark",
    icon=":material/psychology:"
)

page_4 = st.Page(
    page="views/3_results.py",
    title="4. Weightage and Results",
    icon=":material/leaderboard:"
)

# Diagnostyka trzymana poza głównym przepływem: nie służy wyborowi kraju,
# lecz sprawdzeniu, czy dane stojące za rankingiem złożono poprawnie.
page_diagnostics = st.Page(
    page="views/5_data_quality.py",
    title="Data Quality",
    icon=":material/fact_check:"
)

# 4. Budowa menu nawigacyjnego
pg = st.navigation(
    {
        "Process": [page_1, page_2, page_3, page_4],
        "Diagnostics": [page_diagnostics],
    }
)

# 5. Dodatki w pasku bocznym widoczne na każdej stronie (opcjonalnie)
with st.sidebar:
    st.markdown("---")
    st.caption("Clinical Trials Optimizer v0.1")

# 6. Uruchomienie routingu
pg.run()