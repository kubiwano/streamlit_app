"""
Reconciling country identifiers between ClinicalTrials.gov and the World Bank.

The registry stores the country as free text entered by the sponsor; the World
Bank uses its own display names. Neither matches the other, so the join runs on
ISO 3166-1 alpha-3 codes, the only stable identifier common to both sources.

Names are resolved in three passes:
  1. normalised World Bank names, read from the indicator workbook
  2. an alias table for spellings the registry uses
  3. failure, reported openly rather than silently dropping the country
"""

import re
import unicodedata

# Spellings that normalisation alone will not reconcile. Left side is what the
# registry (or common usage) writes, right side is the ISO 3166-1 alpha-3 code.
ALIASES = {
    # different word order or abbreviations on the World Bank side
    "korea republic of": "KOR",
    "republic of korea": "KOR",
    "south korea": "KOR",
    "korea democratic peoples republic of": "PRK",
    "north korea": "PRK",
    "iran islamic republic of": "IRN",
    "iran": "IRN",
    "egypt": "EGY",
    "venezuela": "VEN",
    "yemen": "YEM",
    "gambia": "GMB",
    "bahamas": "BHS",
    "micronesia federated states of": "FSM",
    "micronesia": "FSM",
    "somalia": "SOM",
    "hong kong": "HKG",
    "macao": "MAC",
    "macau": "MAC",
    "congo democratic republic of the": "COD",
    "democratic republic of the congo": "COD",
    "congo the democratic republic of the": "COD",
    "congo republic of the": "COG",
    "republic of the congo": "COG",
    # here the bracketed part is the only thing telling the two apart,
    # so both must be matched as whole strings
    "congo kinshasa": "COD",
    "congo brazzaville": "COG",
    "lao peoples democratic republic": "LAO",
    "laos": "LAO",
    # renamed or alternative country names
    "slovakia": "SVK",
    "czech republic": "CZE",
    "czechia": "CZE",
    "turkey": "TUR",
    "turkiye": "TUR",
    "viet nam": "VNM",
    "vietnam": "VNM",
    "russia": "RUS",
    "russian federation": "RUS",
    "moldova republic of": "MDA",
    "republic of moldova": "MDA",
    "macedonia the former yugoslav republic of": "MKD",
    "north macedonia": "MKD",
    "cote divoire": "CIV",
    "ivory coast": "CIV",
    "cape verde": "CPV",
    "cabo verde": "CPV",
    "swaziland": "SWZ",
    "eswatini": "SWZ",
    "burma": "MMR",
    "myanmar": "MMR",
    "kyrgyzstan": "KGZ",
    "kyrgyz republic": "KGZ",
    "syrian arab republic": "SYR",
    "syria": "SYR",
    "tanzania united republic of": "TZA",
    "united republic of tanzania": "TZA",
    "bolivia plurinational state of": "BOL",
    "brunei": "BRN",
    "brunei darussalam": "BRN",
    "saint lucia": "LCA",
    "saint kitts and nevis": "KNA",
    "saint vincent and the grenadines": "VCT",
    "st lucia": "LCA",
    "st kitts and nevis": "KNA",
    "st vincent and the grenadines": "VCT",
    "palestinian territories": "PSE",
    "palestine state of": "PSE",
    "west bank and gaza": "PSE",
    "united states of america": "USA",
    "united states": "USA",
    "united kingdom of great britain and northern ireland": "GBR",
    "united kingdom": "GBR",
    "holy see vatican city state": "VAT",
    "vatican city": "VAT",
    "timor leste": "TLS",
    "east timor": "TLS",
    "curacao": "CUW",
    "sint maarten dutch part": "SXM",
    "saint martin french part": "MAF",
    "virgin islands us": "VIR",
    "virgin islands british": "VGB",
    # Territories that host trials but have no separate World Bank entry.
    # The World Bank folds the French overseas departments into France, so the
    # registry lists them while the indicator snapshot does not. They resolve to
    # a code and keep their place in the ranking with external criteria left
    # undetermined, exactly like Taiwan (chapter 3.3).
    "taiwan": "TWN",
    "taiwan province of china": "TWN",
    "martinique": "MTQ",
    "guadeloupe": "GLP",
    "reunion": "REU",
    "la reunion": "REU",
    "french guiana": "GUF",
    "guyane": "GUF",
    "mayotte": "MYT",
    "saint barthelemy": "BLM",
    "saint pierre and miquelon": "SPM",
    "wallis and futuna": "WLF",
    "aland islands": "ALA",
    "guernsey": "GGY",
    "jersey": "JEY",
    "anguilla": "AIA",
    "montserrat": "MSR",
    "saint helena": "SHN",
    "falkland islands": "FLK",
    "falkland islands malvinas": "FLK",
    "cook islands": "COK",
    "niue": "NIU",
    "western sahara": "ESH",
    "bonaire sint eustatius and saba": "BES",
}


def normalise(name: str) -> str:
    """Lowercase, strip accents and punctuation, drop a leading article."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Apostrophes are removed rather than replaced by a space, so that
    # "People's" collapses to "peoples" instead of splitting into two words.
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def _without_parenthetical(name: str) -> str:
    """"Puerto Rico (US)" -> "Puerto Rico". Empty if nothing was removed."""
    stripped = re.sub(r"\([^)]*\)", " ", str(name))
    return stripped if stripped != str(name) else ""


class CountryResolver:
    """Resolves country names to ISO 3166-1 alpha-3 codes."""

    def __init__(self, reference: dict[str, str] | None = None):
        """`reference` maps a country name to its ISO code, e.g. World Bank names."""
        self._lookup: dict[str, str] = dict(ALIASES)
        if reference:
            for name, iso3 in reference.items():
                # aliases win: they were written for the registry's spellings
                self._lookup.setdefault(normalise(name), iso3)
                # the World Bank qualifies some names, e.g. "Puerto Rico (US)";
                # register the bare form too so the registry spelling matches
                bare = _without_parenthetical(name)
                if bare:
                    self._lookup.setdefault(normalise(bare), iso3)
        self.unresolved: set[str] = set()

    def to_iso3(self, name: str) -> str | None:
        """
        Resolve a country name, trying progressively looser readings.

        The registry sometimes carries two names in one field, as in
        "Turkey (Türkiye)". Normalisation alone turns that into "turkey turkiye",
        which matches nothing, so the bracketed part is tried on its own and
        then removed.
        """
        if not name:
            return None

        candidates = [normalise(name)]

        outside = _without_parenthetical(name)
        if outside:
            candidates.append(normalise(outside))

        inside = re.findall(r"\(([^)]*)\)", str(name))
        candidates.extend(normalise(part) for part in inside)

        for key in candidates:
            if key and key in self._lookup:
                return self._lookup[key]

        self.unresolved.add(str(name))
        return None

    def report(self) -> list[str]:
        """Names that could not be resolved, for the user to see."""
        return sorted(self.unresolved)
