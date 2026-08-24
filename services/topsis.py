"""
TOPSIS as a control method for the weighted sum.

Chapter 3.3 asks a narrow question: how much of the recommendation comes from the
data and how much from the decision to aggregate criteria by adding them up? To
answer it, TOPSIS has to differ from the weighted sum in exactly one respect —
the aggregation rule — and in no other. It therefore runs on the same normalised
matrix the weighted sum uses, with the same weights, rather than on the vector
normalisation usually presented with the method. Normalising differently as well
would leave any difference in the ranking impossible to attribute: two things
would have changed at once.

Where the two methods genuinely part company is in what a good country is. The
weighted sum lets a high score on one criterion pay for a low score on another,
without limit. TOPSIS measures distance from the best and the worst attainable
profile, so a candidate that is excellent on one criterion and poor on a second
sits further from the ideal than the arithmetic alone suggests. A country that
holds first place under both is therefore leading for reasons that survive the
choice of rule.

Only countries described completely can take part. TOPSIS locates the ideal and
the anti-ideal on the full vector of criteria; if the vector differed between
countries, the distances would no longer be mutually comparable. Completeness is
judged over the criteria carrying weight — a criterion the planner set to zero is
not part of the model, so its absence cannot disqualify a country from the
comparison.
"""

import numpy as np
import pandas as pd

from services.scoring import CRITERIA_BY_KEY


def active_criteria(weights: dict) -> list[str]:
    """Criteria carrying weight, in the order declared in services/scoring.py."""
    return [key for key in CRITERIA_BY_KEY if weights.get(key, 0) > 0]


def complete_countries(scored: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Rows with a normalised value for every criterion that carries weight."""
    keys = active_criteria(weights)
    if not keys:
        return scored.iloc[0:0]
    columns = [CRITERIA_BY_KEY[k].score_column for k in keys]
    present = [c for c in columns if c in scored.columns]
    if len(present) != len(columns):
        return scored.iloc[0:0]
    return scored[scored[present].notna().all(axis=1)]


def topsis_scores(scored: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """
    Closeness coefficient for every completely described country.

    Returns the input rows with `TOPSIS Score` and `TOPSIS Rank` added, sorted by
    the coefficient. An empty frame comes back when no country is complete or no
    criterion carries weight — a legitimate outcome for a narrow query, not an
    error, and the caller reports it as such.

    The coefficient is the usual one: distance to the anti-ideal divided by the
    sum of the distances to both poles. It runs from zero, for a country matching
    the worst value on every criterion, to one, for a country matching the best.
    """
    keys = active_criteria(weights)
    subset = complete_countries(scored, weights)
    if subset.empty or not keys:
        out = subset.copy()
        out["TOPSIS Score"] = pd.Series(dtype="float64")
        out["TOPSIS Rank"] = pd.Series(dtype="int64")
        return out

    columns = [CRITERIA_BY_KEY[k].score_column for k in keys]
    matrix = subset[columns].to_numpy(dtype="float64")

    # Weights normalised to sum to one, so the coefficient does not depend on
    # whether the planner typed percentages or raw points.
    w = np.array([weights[k] for k in keys], dtype="float64")
    w = w / w.sum()

    # Every column already runs from 0 (worst) to 1 (best) — destimulants were
    # inverted during normalisation — so the ideal is the column maximum and the
    # anti-ideal the column minimum, with no per-criterion direction to track.
    weighted = matrix * w
    ideal = weighted.max(axis=0)
    anti_ideal = weighted.min(axis=0)

    to_ideal = np.sqrt(((weighted - ideal) ** 2).sum(axis=1))
    to_anti = np.sqrt(((weighted - anti_ideal) ** 2).sum(axis=1))

    span = to_ideal + to_anti
    # Every country identical on the weighted criteria: both distances vanish and
    # the coefficient is undefined. They are genuinely tied, so they all get 1.0
    # rather than a division by zero.
    closeness = np.where(span == 0, 1.0, to_anti / np.where(span == 0, 1.0, span))

    out = subset.copy()
    out["TOPSIS Score"] = closeness
    out = out.sort_values("TOPSIS Score", ascending=False).reset_index(drop=True)
    out["TOPSIS Rank"] = range(1, len(out) + 1)
    return out


def compare_rankings(saw: pd.DataFrame, topsis: pd.DataFrame, top_n: int = 10) -> dict:
    """
    How far the two orderings agree, over the countries both could rank.

    Spearman's rho on the shared countries answers the question chapter 3.3 puts
    to the control method; the overlap of the leading group answers the question
    a planner actually has, since nobody acts on rank forty.
    """
    if saw.empty or topsis.empty:
        return {"countries": 0, "rho": None, "overlap": 0, "top_n": top_n,
                "same_leader": None, "table": pd.DataFrame()}

    shared = saw[saw["Country"].isin(topsis["Country"])][["Country", "Rank", "Final Score"]]
    merged = shared.merge(
        topsis[["Country", "TOPSIS Rank", "TOPSIS Score"]], on="Country", how="inner"
    )
    if merged.empty:
        return {"countries": 0, "rho": None, "overlap": 0, "top_n": top_n,
                "same_leader": None, "table": merged}

    # Re-ranked within the shared subset: the SAW rank counts countries TOPSIS
    # could not score, so comparing the two columns as they stand would measure
    # that exclusion rather than the disagreement between the methods.
    merged["SAW Rank"] = merged["Final Score"].rank(ascending=False, method="min").astype(int)
    merged["Rank shift"] = merged["SAW Rank"] - merged["TOPSIS Rank"]

    # Spearman's rho computed as Pearson on the ranks, which is its definition.
    # pandas delegates method="spearman" to scipy, and one correlation is not
    # worth adding scipy to the dependencies of a Streamlit app. Average ranks
    # rather than minimum ranks, because that is the tie-corrected form.
    rho = None
    if len(merged) > 1:
        saw_ranks = merged["Final Score"].rank(ascending=False, method="average")
        topsis_ranks = merged["TOPSIS Score"].rank(ascending=False, method="average")
        rho = saw_ranks.corr(topsis_ranks, method="pearson")

    saw_top = set(merged.nsmallest(top_n, "SAW Rank")["Country"])
    topsis_top = set(merged.nsmallest(top_n, "TOPSIS Rank")["Country"])

    leader_saw = merged.loc[merged["SAW Rank"].idxmin(), "Country"]
    leader_topsis = merged.loc[merged["TOPSIS Rank"].idxmin(), "Country"]

    return {
        "countries": len(merged),
        "rho": None if rho is None or pd.isna(rho) else float(rho),
        "overlap": len(saw_top & topsis_top),
        "top_n": min(top_n, len(merged)),
        "same_leader": leader_saw == leader_topsis,
        "leader_saw": leader_saw,
        "leader_topsis": leader_topsis,
        "table": merged.sort_values("SAW Rank").reset_index(drop=True),
    }
