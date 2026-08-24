"""
The ranking model: normalisation, weighting and the weighted sum.

Implements the rules set out in chapter 3.3.

Normalisation is a single operation — a value divided by a reference — and the
whole design lies in where that reference comes from. It is never the lowest
observed value, because that is simply whichever country happened to come last;
its arrival or departure would shift everyone else without anything about them
having changed.

  RATIO     x / max   for quantities with a true zero: counts of trials, the
                      recruitment rate, start-up days, the price level and the
                      infrastructure composite. Ratios are meaningful here —
                      sixty days really is twice thirty.

  SCALE_100 x / 100   for the governance-based criteria, whose scale is defined
                      as running from zero to one hundred regardless of the data.

Destimulants are inverted after normalisation so that in every column a higher
value means a better prospect.
"""

from dataclasses import dataclass

import pandas as pd

RATIO = "ratio"
SCALE_100 = "scale100"


@dataclass(frozen=True)
class Criterion:
    key: str            # column in the country table
    label: str          # shown to the user
    source: str         # "registry" or "external"
    stimulant: bool     # True if higher is better
    method: str         # RATIO or SCALE_100
    unit: str           # for the raw value shown alongside the score
    default_weight: int

    @property
    def score_column(self) -> str:
        return f"score_{self.key}"


CRITERIA: list[Criterion] = [
    Criterion("experience_raw", "General experience", "registry", True, RATIO,
              "Trials", 15),
    Criterion("specific_exp_raw", "Specific experience", "registry", True, RATIO,
              "Trials", 20),
    Criterion("recruitment_rate", "Recruitment rate", "registry", True, RATIO,
              "pts/site/month", 15),
    Criterion("competition_raw", "Competition", "registry", False, RATIO,
              "Trials", 15),
    Criterion("startup_days", "Start-up time", "external", False, RATIO,
              "Days", 15),
    Criterion("price_level", "Cost level", "external", False, RATIO,
              "Price Level Index", 5),
    Criterion("idx_political", "Political risk", "external", True, SCALE_100,
              "Points (0-100)", 5),
    Criterion("idx_legislative", "Legal environment", "external", True, SCALE_100,
              "Points (0-100)", 5),
    Criterion("idx_infrastructure", "Healthcare infrastructure", "external", True, RATIO,
              "Points (0-100)", 5),
]

CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}

# Competition is the only criterion whose values depend on the query rather than
# being a fixed property of a country, so it is the only one whose range can
# collapse. A reference level guards against that: the denominator becomes the
# greater of the level declared by the planner and the highest observed value.
# Left at zero the mechanism is inert.
REFERENCE_LEVEL_CRITERION = "competition_raw"


def normalise_column(
    values: pd.Series,
    criterion: Criterion,
    reference: float = 0,
    fixed_max: float | None = None,
) -> pd.Series:
    if criterion.method == SCALE_100:
        scaled = values / 100.0
    else:
        observed = float(values.max(skipna=True) or 0)
        denominator = max(observed, float(reference or 0), float(fixed_max or 0))
        if not denominator:
            # every country sits at zero: the criterion cannot separate them
            scaled = values * 0.0
        else:
            scaled = values / denominator

    scaled = scaled.clip(lower=0.0, upper=1.0)
    return scaled if criterion.stimulant else 1.0 - scaled


def add_scores(
    df: pd.DataFrame,
    reference_level: float = 0,
    fixed_maxima: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Add one normalised column per criterion, leaving missing values missing.

    fixed_maxima supplies denominators taken from the whole indicator snapshot
    rather than from the countries in this particular ranking. Chapter 3.3
    requires it for the external criteria: they are fixed properties of a
    country, so the scale they are read against must not shift when the query
    changes. Without it the same country scores differently on infrastructure
    depending on which indication was searched, and the ranking stops being
    reproducible. Registry criteria are deliberately left out — their values do
    come from the query, and the collapse of their range is what the competition
    reference level exists to guard against.
    """
    fixed_maxima = fixed_maxima or {}
    out = df.copy()
    for criterion in CRITERIA:
        if criterion.key not in out.columns:
            out[criterion.score_column] = pd.NA
            continue
        reference = reference_level if criterion.key == REFERENCE_LEVEL_CRITERION else 0
        out[criterion.score_column] = normalise_column(
            pd.to_numeric(out[criterion.key], errors="coerce"),
            criterion,
            reference,
            fixed_maxima.get(criterion.key),
        )
    return out


def weighted_score(df: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """
    Weighted sum over the criteria that are available for each country.

    A missing value is not replaced by a number the model invented. The weights
    of the criteria that are present are rescaled to sum to one, so the result
    keeps its meaning — a weighted average of normalised scores — but rests on a
    narrower base.

    Two diagnostics travel with it. "Criteria missing" counts what could not be
    determined. "Evidence coverage" is the share of the declared weight that
    falls on criteria the data could actually support: at 0.85, the score
    reflects 85% of what the planner said matters, with the rest redistributed
    over the criteria that remained.
    """
    out = df.copy()
    active = {k: w for k, w in weights.items() if w > 0}
    declared_total = sum(active.values())

    if not declared_total:
        out["Final Score"] = 0.0
        out["Criteria missing"] = len(CRITERIA)
        out["Evidence coverage"] = 0.0
        return out

    weighted_sum = pd.Series(0.0, index=out.index)
    applied_weight = pd.Series(0.0, index=out.index)
    missing_count = pd.Series(0, index=out.index)

    for key, weight in active.items():
        column = CRITERIA_BY_KEY[key].score_column
        values = pd.to_numeric(out.get(column), errors="coerce")
        available = values.notna()
        weighted_sum += values.fillna(0.0) * weight
        applied_weight += available * weight
        missing_count += (~available).astype(int)

    out["Final Score"] = (weighted_sum / applied_weight.replace(0, pd.NA)).fillna(0.0)
    out["Criteria missing"] = missing_count
    out["Evidence coverage"] = applied_weight / declared_total
    return out


def rank_countries(
    df: pd.DataFrame,
    weights: dict[str, float],
    reference_level: float = 0,
    fixed_maxima: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Normalise, score, sort. Returns the table with a Rank column in front."""
    scored = weighted_score(add_scores(df, reference_level, fixed_maxima), weights)
    scored = scored.sort_values("Final Score", ascending=False).reset_index(drop=True)
    scored.insert(0, "Rank", range(1, len(scored) + 1))
    return scored
