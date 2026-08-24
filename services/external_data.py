"""
Access to the external criteria produced by services/fetch_indicators.py.

The workbook is a stored snapshot, refreshed on demand, in contrast to the
registry which is queried live. Loading it here keeps the file path in one
place and lets the rest of the app work with a plain DataFrame keyed by ISO code.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from services.country_codes import CountryResolver

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDICATORS_FILE = DATA_DIR / "external_indicators.xlsx"

# Criteria as defined in chapter 3.2, with the direction of preference.
# True  = stimulant  (higher is better)
# False = destimulant (lower is better)
EXTERNAL_CRITERIA = {
    "price_level": False,          # cost level: cheaper is better
    "idx_political": True,         # political risk
    "idx_legislative": True,       # legal and regulatory environment
    "idx_infrastructure": True,    # healthcare infrastructure
    "startup_days": False,         # start-up time: faster is better
}

_METADATA_COLUMNS = [
    "iso3", "country", "region", "income_group",
    "startup_confidence", "startup_authority", "startup_source",
]


def snapshot_stamp() -> float:
    """
    Modification time of the indicator file, used as a cache key.

    Chapter 3.2 treats the regional start-up medians as provisional and expects
    the end user to replace them with their own figures. The natural way to do
    that is to edit this workbook — so a cache that ignores the file would hide
    the correction until the app was restarted, and the user would reasonably
    conclude their edit had not worked.
    """
    return INDICATORS_FILE.stat().st_mtime if INDICATORS_FILE.exists() else 0.0


@st.cache_data(show_spinner=False)
def _read_indicators(stamp: float) -> pd.DataFrame:
    """`stamp` is not used in the body: it exists to key the cache on the file."""
    df = pd.read_excel(INDICATORS_FILE, sheet_name="final_indicators")
    columns = [c for c in _METADATA_COLUMNS + list(EXTERNAL_CRITERIA) if c in df.columns]
    df = df[columns].copy()
    df["region"] = df["region"].astype(str).str.strip()
    return df.set_index("iso3")


def load_external_indicators() -> pd.DataFrame:
    """
    One row per country, indexed by ISO 3166-1 alpha-3 code.
    Raises FileNotFoundError if the snapshot has never been generated.
    """
    if not INDICATORS_FILE.exists():
        raise FileNotFoundError(
            f"{INDICATORS_FILE.name} not found. Run services/fetch_indicators.py "
            f"to download the external indicators first."
        )
    return _read_indicators(snapshot_stamp())


# How the start-up time was arrived at, in the planner's words rather than the
# data file's. Chapter 3.2 requires this to travel with the value: the point is
# not only that a number exists but that its standing is visible, so the user
# knows which figures to replace with their own first.
STARTUP_BASIS_LABELS = {
    "statutory": "statute",
    "documented": "industry sources",
    "regional median": "regional median",
    "overall median": "overall median",
    "company override": "own data",
}


def startup_basis_label(value) -> str:
    if not isinstance(value, str) or not value:
        return "unknown"
    return STARTUP_BASIS_LABELS.get(value, value)


PROVISIONAL_STARTUP_BASES = {"regional median", "overall median"}


@st.cache_data(show_spinner=False)
def _compute_maxima(stamp: float) -> dict:
    """
    Highest value of each external criterion across the whole snapshot.

    These are the denominators the ratio normalisation must use. Taking them from
    the countries in one ranking would tie the scale to the query: the same
    country would score differently on infrastructure depending on the indication
    searched, which is exactly the instability chapter 3.3 rules out. The
    snapshot is a fixed file, so the scale is fixed with it.
    """
    indicators = load_external_indicators()
    maxima = {}
    for key in EXTERNAL_CRITERIA:
        if key in indicators.columns:
            value = pd.to_numeric(indicators[key], errors="coerce").max()
            if pd.notna(value):
                maxima[key] = float(value)
    return maxima


def external_maxima() -> dict:
    """Keyed on the file, so an edited workbook shifts the scales with it."""
    return _compute_maxima(snapshot_stamp())


def build_resolver() -> CountryResolver:
    """
    Resolver seeded with the World Bank names from the snapshot, so every
    country present in the indicators can be matched by name as well as by code.

    Deliberately not cached. It is cheap to build, and a cached instance would
    both survive edits to the alias table and accumulate unresolved names across
    reruns — two ways of quietly reporting the wrong thing.
    """
    indicators = load_external_indicators()
    reference = {row.country: iso3 for iso3, row in indicators.iterrows()}
    return CountryResolver(reference)


def merge_external_criteria(
    df: pd.DataFrame, country_column: str = "Country"
) -> tuple[pd.DataFrame, list[str]]:
    """
    Join the external criteria onto a country-level table from the registry.

    Returns the joined frame and the registry names that could not be mapped to
    an ISO code, so the caller can show them instead of dropping them silently.

    Countries present in the registry but absent from the World Bank snapshot —
    Taiwan and the French overseas departments — stay in the table with the
    external criteria left empty. Dropping them would remove an option from the
    decision-maker without telling them, which chapter 3.3 rules out.
    """
    indicators = load_external_indicators()
    resolver = build_resolver()

    out = df.copy()
    out["iso3"] = out[country_column].map(resolver.to_iso3)

    criteria = list(EXTERNAL_CRITERIA)
    extra = [c for c in ("region", "income_group", "startup_confidence") if c in indicators.columns]
    out = out.merge(
        indicators[criteria + extra],
        left_on="iso3",
        right_index=True,
        how="left",
    )
    return out, resolver.report()
