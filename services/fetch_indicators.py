"""
Fetch external country-level indicators for the country ranking model.

Sources
-------
1. World Bank - Worldwide Governance Indicators (WGI), source id 3
2. World Bank - World Development Indicators (WDI), source id 2
3. Literature-based reference table for clinical trial start-up times

Output: a single Excel workbook with separate sheets (raw data + final indicators).

Usage
-----
    pip install requests pandas openpyxl
    python fetch_indicators.py
"""

import time
from datetime import datetime

import requests
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

from pathlib import Path

# Data files live in app/data/, separately from the code that produces them.
# Paths are anchored to this module so the script runs from any directory.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

OUTPUT_FILE = DATA_DIR / "external_indicators.xlsx"
WB_YEAR_RANGE = "2015:2026"      # latest available value within this window is used

# Governance indicators, 0-100 scale, higher = better governance
WGI_INDICATORS = {
    "GOV_WGI_PV.SC": "political_stability",
    "GOV_WGI_VA.SC": "voice_accountability",
    "GOV_WGI_RQ.SC": "regulatory_quality",
    "GOV_WGI_RL.SC": "rule_of_law",
    "GOV_WGI_GE.SC": "government_effectiveness",
    "GOV_WGI_CC.SC": "control_of_corruption",   # fetched as reserve, not used in model
}

# Development indicators
WDI_INDICATORS = {
    "NY.GDP.PCAP.CD":    "gdp_per_capita_usd",       # GDP per capita, current US$
    "NY.GDP.PCAP.PP.CD": "gdp_per_capita_ppp",       # GDP per capita, PPP
    "SH.XPD.CHEX.PP.CD": "health_expenditure_pc",    # Health expenditure per capita, PPP
    "SH.MED.PHYS.ZS":    "physicians_per_1000",      # Physicians per 1,000 people
}

# ─────────────────────────────────────────────────────────────────────────────
# START-UP TIME
# ─────────────────────────────────────────────────────────────────────────────
#
# Definition used throughout: TARGET REVIEW TIME OF THE NATIONAL REGULATORY
# AUTHORITY, expressed in calendar days. It does NOT include ethics committee
# review, site contracting or import permits, which in most jurisdictions run
# in parallel or afterwards.
#
# No global public database of such timelines exists. Its creation for all
# 195 WHO member states was only proposed in Cavaleri et al. (2025),
# Lancet Glob Health 13:e769-e777. Country-level values are therefore compiled
# manually in startup_times.csv from regulatory and industry sources, each row
# carrying a confidence flag:
#     statutory  - legally mandated or officially published review period
#     documented - reported consistently by regulatory or industry sources
#
# Countries absent from the CSV fall back to a published regional median.
# No value is ever inferred from another indicator in this dataset.

STARTUP_TIMES_FILE = DATA_DIR / "startup_times.csv"

# --- Regional fallback -------------------------------------------------------
#
# Median time from regulatory authority submission to approval, reported per
# geographic region for the global phase III APHINITY trial run across 42
# countries: Franzoi M.A.; Procter M.; Twelves C. et al. (2022),
# ecancermedicalscience 16:1379, DOI 10.3332/ecancer.2022.1379, Table 1.
#
# Regions follow the World Bank classification, so they join directly onto the
# country metadata fetched below. Two caveats are documented in the thesis:
#   - the Sub-Saharan Africa median rests on a single participating country;
#   - the study found no statistically significant association between region
#     and approval time (ANOVA p = 0.468), with wide within-region spread.
# The regional median is therefore a source-anchored default, not a predictor.
#
# MENA and South Asia were not covered by the study (the only MENA country,
# Israel, never submitted to a national authority), so they take the overall
# median instead.

STARTUP_TIME_BY_REGION = {
    "Europe & Central Asia":     {"days": 56,  "basis": "APHINITY regional median (n=21)"},
    "East Asia & Pacific":       {"days": 53,  "basis": "APHINITY regional median (n=9)"},
    "Latin America & Caribbean": {"days": 51,  "basis": "APHINITY regional median (n=8)"},
    "North America":             {"days": 31,  "basis": "APHINITY regional median (n=2)"},
    "Sub-Saharan Africa":        {"days": 103, "basis": "APHINITY regional median (n=1)"},
}

# Applied to regions the study did not cover.
STARTUP_TIME_OVERALL_MEDIAN = 53
STARTUP_OVERALL_BASIS = "APHINITY overall median (n=41), region not covered"

# Optional country-level overrides (e.g. company's own historical data).
# Applied last, so they take precedence over both the CSV and the region.
STARTUP_TIME_OVERRIDES: dict[str, int] = {
    # "POL": 45,
}


# ─────────────────────────────────────────────────────────────────────────────
# WORLD BANK API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_json(url: str, params: dict, attempts: int = 4, timeout: int = 120):
    """HTTP GET with retries and increasing backoff."""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt == attempts:
                print(f"      FAILED after {attempts} attempts: {type(exc).__name__}")
                return None
            wait = 5 * attempt
            print(f"      attempt {attempt} failed ({type(exc).__name__}), retrying in {wait}s")
            time.sleep(wait)
    return None


def fetch_country_metadata() -> pd.DataFrame:
    """Fetch the country list with region and income group classification."""
    rows = []
    page = 1
    while True:
        data = fetch_json(
            "https://api.worldbank.org/v2/country",
            {"format": "json", "per_page": 300, "page": page},
        )
        if data is None or len(data) < 2 or not data[1]:
            break
        for item in data[1]:
            region = (item.get("region") or {}).get("value", "")
            if region in ("", "Aggregates"):      # skip regional aggregates
                continue
            rows.append({
                "iso3": item.get("id", ""),
                "country": item.get("name", ""),
                "region": region,
                "income_group": (item.get("incomeLevel") or {}).get("value", ""),
            })
        if page >= data[0].get("pages", 1):
            break
        page += 1
        time.sleep(0.4)
    return pd.DataFrame(rows)


def fetch_indicator(code: str, source: int | None = None) -> pd.DataFrame:
    """Fetch one indicator for all countries; return the latest available value."""
    records = []
    page = 1
    while True:
        params = {
            "format": "json",
            "per_page": 1000,
            "page": page,
            "date": WB_YEAR_RANGE,
        }
        if source:
            params["source"] = source

        data = fetch_json(
            f"https://api.worldbank.org/v2/country/all/indicator/{code}", params
        )
        if data is None or not isinstance(data, list) or len(data) < 2 or data[1] is None:
            break

        records.extend(data[1])
        if page >= data[0].get("pages", 1):
            break
        page += 1
        time.sleep(0.4)

    rows = []
    for item in records:
        if item.get("value") is None:
            continue
        iso3 = item.get("countryiso3code") or ""
        if len(iso3) != 3:
            continue
        rows.append({
            "iso3": iso3,
            "country": item["country"]["value"],
            "year": int(item["date"]),
            "value": float(item["value"]),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("year").groupby(["iso3", "country"], as_index=False).last()


def fetch_indicator_group(indicators: dict, source: int | None, label: str) -> pd.DataFrame:
    """Fetch a set of indicators and merge them into a single table."""
    result = None
    for code, name in indicators.items():
        print(f"  [{label}] {code} -> {name}")
        try:
            df = fetch_indicator(code, source)
        except Exception as exc:
            print(f"      SKIPPED ({type(exc).__name__})")
            continue
        if df.empty:
            print("      SKIPPED - no data returned")
            continue
        print(f"      OK: {len(df)} countries")
        df = df.rename(columns={"value": name, "year": f"{name}_year"})
        result = df if result is None else result.merge(df, on=["iso3", "country"], how="outer")
        time.sleep(0.4)
    return result if result is not None else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# FINAL INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def ratio_norm(series: pd.Series) -> pd.Series:
    """
    Ratio normalisation to the 0-1 range: value / observed maximum.

    Used instead of min-max because both components of the infrastructure
    criterion are ratio-scale quantities with a meaningful zero (no health
    spending, no physicians). Min-max would anchor 0 to the lowest observed
    value, which is not the same thing and shifts whenever a worse-off
    country enters the dataset.
    """
    high = series.max()
    if pd.isna(high) or high == 0:
        return pd.Series([0.5] * len(series), index=series.index)
    return series / high

def load_startup_table() -> pd.DataFrame:
    """Load the manually compiled country-level start-up time table."""
    try:
        df = pd.read_csv(STARTUP_TIMES_FILE)
        print(f"   loaded {len(df)} country-level start-up entries")
        return df
    except FileNotFoundError:
        print(f"   WARNING: {STARTUP_TIMES_FILE} not found - regional fallback only")
        return pd.DataFrame(columns=["iso3", "startup_days", "authority", "confidence"])


def assign_startup_time(df: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """
    Assign start-up time with a three-level fallback:
        1. company override    (STARTUP_TIME_OVERRIDES)
        2. country-level value (startup_times.csv)
        3. published regional median, or the overall median where the source
           study did not cover the region

    Nothing is inferred from another indicator in this dataset, so start-up
    time carries no constructional dependency on the legislative criterion.
    """
    region = df["region"].astype(str).str.strip()

    # level 3b - overall median (regions the source study did not cover)
    df["startup_days"] = STARTUP_TIME_OVERALL_MEDIAN
    df["startup_confidence"] = "overall median"
    df["startup_authority"] = ""
    df["startup_source"] = STARTUP_OVERALL_BASIS

    # level 3a - regional median
    in_region = region.isin(STARTUP_TIME_BY_REGION)
    df.loc[in_region, "startup_days"] = region[in_region].map(
        lambda r: STARTUP_TIME_BY_REGION[r]["days"]
    )
    df.loc[in_region, "startup_confidence"] = "regional median"
    df.loc[in_region, "startup_source"] = region[in_region].map(
        lambda r: STARTUP_TIME_BY_REGION[r]["basis"]
    )

    # level 2 - country-level table
    if not table.empty:
        lookup = table.set_index("iso3")
        for iso3, row in lookup.iterrows():
            mask = df["iso3"] == iso3
            if not mask.any():
                continue
            df.loc[mask, "startup_days"] = row.get("startup_days")
            df.loc[mask, "startup_confidence"] = row.get("confidence", "")
            df.loc[mask, "startup_authority"] = row.get("authority", "")
            df.loc[mask, "startup_source"] = row.get("source_note", "")

    # level 1 - company overrides
    for iso3, days in STARTUP_TIME_OVERRIDES.items():
        mask = df["iso3"] == iso3
        df.loc[mask, "startup_days"] = days
        df.loc[mask, "startup_confidence"] = "company override"
        df.loc[mask, "startup_source"] = "internal historical data"

    return df


def build_final_indicators(
    meta: pd.DataFrame, wgi: pd.DataFrame, wdi: pd.DataFrame, startup: pd.DataFrame
) -> pd.DataFrame:
    """
    Build four aggregated thematic indicators.

    economic       - general price level          (cost driver, lower is better)
    political      - political stability          (higher is better)
    legislative    - legal and regulatory quality (higher is better)
    infrastructure - medical infrastructure       (higher is better)
    """
    df = meta.merge(wgi.drop(columns=["country"], errors="ignore"), on="iso3", how="left")
    df = df.merge(wdi.drop(columns=["country"], errors="ignore"), on="iso3", how="left")

    # --- economic: price level = nominal GDP per capita / PPP GDP per capita --
    df["price_level"] = df["gdp_per_capita_usd"] / df["gdp_per_capita_ppp"]

    # --- political: both components already on a 0-100 scale -----------------
    df["idx_political"] = df[["political_stability", "voice_accountability"]].mean(
        axis=1, skipna=True
    )

    # --- legislative: all components already on a 0-100 scale ----------------
    df["idx_legislative"] = df[
        ["regulatory_quality", "rule_of_law", "government_effectiveness"]
    ].mean(axis=1, skipna=True)

    # --- infrastructure: DIFFERENT UNITS -> normalise before averaging -------
    df["_norm_health_exp"] = ratio_norm(df["health_expenditure_pc"])
    df["_norm_physicians"] = ratio_norm(df["physicians_per_1000"])
    df["idx_infrastructure"] = (
        df[["_norm_health_exp", "_norm_physicians"]].mean(axis=1, skipna=True) * 100
    )

    df = assign_startup_time(df, startup)

    columns = [
        "iso3", "country", "region", "income_group",
        "price_level", "idx_political", "idx_legislative", "idx_infrastructure",
        "startup_days", "startup_confidence", "startup_authority", "startup_source",
        "gdp_per_capita_usd", "gdp_per_capita_ppp",
        "health_expenditure_pc", "physicians_per_1000",
        "political_stability", "voice_accountability",
        "regulatory_quality", "rule_of_law", "government_effectiveness",
    ]
    columns = [c for c in columns if c in df.columns]
    return df[columns].sort_values("country")


def build_startup_reference() -> pd.DataFrame:
    """Reference sheet documenting the fallback levels."""
    rows = [
        {
            "level": "1 - company override",
            "rule": "iso3 listed in STARTUP_TIME_OVERRIDES",
            "days": "",
            "note": "internal historical data, takes precedence over all levels",
        },
        {
            "level": "2 - country table",
            "rule": "iso3 present in startup_times.csv",
            "days": "",
            "note": "statute or regulator publication; see source_note per row",
        },
    ]
    rows += [
        {
            "level": "3a - regional median",
            "rule": region,
            "days": info["days"],
            "note": info["basis"],
        }
        for region, info in STARTUP_TIME_BY_REGION.items()
    ]
    rows.append(
        {
            "level": "3b - overall median",
            "rule": "Middle East & North Africa; South Asia",
            "days": STARTUP_TIME_OVERALL_MEDIAN,
            "note": STARTUP_OVERALL_BASIS,
        }
    )
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("FETCHING EXTERNAL COUNTRY INDICATORS")
    print("=" * 70)

    print("\n1/4 Country metadata (region, income group)")
    meta = fetch_country_metadata()
    print(f"   -> {len(meta)} countries")

    print("\n2/4 Worldwide Governance Indicators")
    wgi = fetch_indicator_group(WGI_INDICATORS, source=3, label="WGI")
    print(f"   -> {len(wgi)} countries")

    print("\n3/4 World Development Indicators")
    wdi = fetch_indicator_group(WDI_INDICATORS, source=None, label="WDI")
    print(f"   -> {len(wdi)} countries")

    print("\n4/4 Start-up times (local file)")
    startup = load_startup_table()

    print("\nBuilding final indicators...")
    final = build_final_indicators(meta, wgi, wdi, startup)
    startup_ref = build_startup_reference()

    print("   start-up time coverage:")
    for level, count in final["startup_confidence"].value_counts().items():
        print(f"      {level}: {count}")

    info = pd.DataFrame({
        "field": ["generated_at", "wb_year_range", "countries", "startup_source"],
        "value": [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            WB_YEAR_RANGE,
            len(final),
            "statute/regulator table + APHINITY regional medians "
            "(Franzoi et al. 2022; see startup_regional_fallback sheet)",
        ],
    })

    print(f"\nWriting {OUTPUT_FILE} ...")
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        info.to_excel(writer, sheet_name="info", index=False)
        final.to_excel(writer, sheet_name="final_indicators", index=False)
        startup_ref.to_excel(writer, sheet_name="startup_regional_fallback", index=False)
        if not startup.empty:
            startup.to_excel(writer, sheet_name="startup_by_country", index=False)
        wgi.to_excel(writer, sheet_name="raw_WGI", index=False)
        wdi.to_excel(writer, sheet_name="raw_WDI", index=False)
        meta.to_excel(writer, sheet_name="country_metadata", index=False)

    print("Done.")
    print("\nSheets: info | final_indicators | startup_reference | raw_WGI | raw_WDI "
          "| country_metadata")


if __name__ == "__main__":
    main()
