"""
Findings drawn from a finished ranking, for the executive summary.

Everything here is computed by rule from the ranking itself. Nothing is written
by a language model, and that is a deliberate constraint rather than a
limitation: the repeatability the thesis claims for the ranking would be worth
little if the summary explaining it changed wording on every run. The same
reasoning already governs the benchmark step, where the model only orders
candidates and the planner's selection is what enters the calculation.
"""

from dataclasses import dataclass, field

import pandas as pd

from services.scoring import CRITERIA, CRITERIA_BY_KEY, rank_countries


@dataclass
class CountryHighlight:
    country: str
    rank: int
    score: float
    strengths: list[tuple[str, float]] = field(default_factory=list)
    weakness: tuple[str, float] | None = None
    missing: int = 0


def _effective_weights(row: pd.Series, weights: dict, total: float) -> dict:
    used = float(row.get("Evidence coverage", 1.0)) or 1.0
    return {
        key: (weight / total) / used
        for key, weight in weights.items()
        if weight > 0 and pd.notna(row.get(CRITERIA_BY_KEY[key].score_column))
    }


def contributions(row: pd.Series, weights: dict, total: float) -> dict[str, float]:
    """How many points of the final score each criterion actually delivered."""
    effective = _effective_weights(row, weights, total)
    return {
        key: float(row[CRITERIA_BY_KEY[key].score_column]) * share
        for key, share in effective.items()
    }


def describe_country(row: pd.Series, weights: dict, total: float, top_n: int = 2) -> CountryHighlight:
    """Rank, score, the criteria that carried it and the one that held it back."""
    parts = contributions(row, weights, total)
    ordered = sorted(parts.items(), key=lambda kv: kv[1], reverse=True)

    # The weakness is the criterion with real weight where the score is lowest,
    # not simply the smallest contribution — a criterion weighted at 5% cannot
    # be called a weakness merely for contributing little.
    effective = _effective_weights(row, weights, total)
    scored = [
        (key, float(row[CRITERIA_BY_KEY[key].score_column]))
        for key in effective
        if effective[key] >= 0.10
    ]
    weakest = min(scored, key=lambda kv: kv[1]) if scored else None

    return CountryHighlight(
        country=row["Country"],
        rank=int(row["Rank"]),
        score=float(row["Final Score"]),
        strengths=[(CRITERIA_BY_KEY[k].label, v) for k, v in ordered[:top_n]],
        weakness=(CRITERIA_BY_KEY[weakest[0]].label, weakest[1]) if weakest else None,
        missing=int(row.get("Criteria missing", 0)),
    )


def decisiveness(ranked: pd.DataFrame) -> dict:
    """
    How clear-cut the outcome is.

    A narrow gap at the top is not a flaw in the model but information for the
    planner: it means the choice rests on preferences rather than on evidence,
    and that a small change of weights would reshuffle the leaders.
    """
    if len(ranked) < 2:
        return {"gap": 0.0, "verdict": "single candidate", "contenders": len(ranked)}

    scores = ranked["Final Score"].tolist()
    gap = scores[0] - scores[1]
    within_two_percent = sum(1 for s in scores if scores[0] - s <= 0.02)

    if gap >= 0.05:
        verdict = "clear leader"
    elif gap >= 0.02:
        verdict = "narrow lead"
    else:
        verdict = "effectively tied"

    return {"gap": gap, "verdict": verdict, "contenders": within_two_percent}


def leadership_sensitivity(
    df_metrics: pd.DataFrame,
    weights: dict,
    reference_level: float = 0,
    fixed_maxima: dict | None = None,
) -> list[str]:
    """
    Criteria whose removal changes who comes first.

    Answers the question a weighted sum always invites: how much of this result
    is the data and how much is the weighting? Bears directly on the hypothesis
    about the stability of recommendations under moderate changes of preference.
    """
    baseline = rank_countries(df_metrics, weights, reference_level, fixed_maxima)
    if baseline.empty:
        return []
    leader = baseline.iloc[0]["Country"]

    decisive = []
    for key, weight in weights.items():
        if weight <= 0:
            continue
        probe = dict(weights)
        probe[key] = 0
        if sum(probe.values()) == 0:
            continue
        alternative = rank_countries(df_metrics, probe, reference_level, fixed_maxima)
        if not alternative.empty and alternative.iloc[0]["Country"] != leader:
            decisive.append(CRITERIA_BY_KEY[key].label)
    return decisive


def criterion_leaders(ranked: pd.DataFrame, weights: dict) -> dict[str, str]:
    """Best country on each weighted criterion, by name."""
    leaders = {}
    for key, weight in weights.items():
        if weight <= 0:
            continue
        column = CRITERIA_BY_KEY[key].score_column
        if column not in ranked.columns:
            continue
        valid = ranked[ranked[column].notna()]
        if valid.empty:
            continue
        best = valid.loc[valid[column].idxmax()]
        leaders[CRITERIA_BY_KEY[key].label] = best["Country"]
    return leaders


def specialists(ranked: pd.DataFrame, weights: dict, group_size: int = 10) -> list[dict]:
    """
    Countries that lead a criterion without leading the ranking.

    Worth surfacing because a weighted sum flattens exactly this: a country that
    is outstanding on one thing and mediocre elsewhere lands mid-table, and the
    planner never learns it was the best available on the criterion they may
    care about most.
    """
    found = []
    for label, country in criterion_leaders(ranked, weights).items():
        row = ranked[ranked["Country"] == country].iloc[0]
        overall = int(row["Rank"])
        if overall > 1:
            found.append({
                "country": country,
                "criterion": label,
                "rank": overall,
                "in_top_group": overall <= group_size,
            })
    return sorted(found, key=lambda item: item["rank"])


def group_profile(ranked: pd.DataFrame, group_size: int = 10) -> dict:
    """
    How the leading group differs from the field, on cost and on start-up time.

    Puts a number on the trade-off the model is built to expose: whether this
    particular shortlist bought quality with money and time, or avoided doing so.
    """
    if ranked.empty:
        return {}

    head = ranked.head(group_size)
    rest = ranked.iloc[group_size:]
    if rest.empty:
        return {}

    profile = {}
    for column, name in (("price_level", "cost"), ("startup_days", "startup")):
        if column not in ranked.columns:
            continue
        top_mean = head[column].mean(skipna=True)
        rest_mean = rest[column].mean(skipna=True)
        if pd.notna(top_mean) and pd.notna(rest_mean) and rest_mean:
            profile[name] = {
                "top": float(top_mean),
                "rest": float(rest_mean),
                "ratio": float(top_mean / rest_mean),
            }
    return profile


def compare_with(row: pd.Series, other: pd.Series, weights: dict) -> dict[str, list[str]]:
    """Criteria on which one country beats another, and those where it loses."""
    better, worse = [], []
    for key, weight in weights.items():
        if weight <= 0:
            continue
        column = CRITERIA_BY_KEY[key].score_column
        mine, theirs = row.get(column), other.get(column)
        if pd.isna(mine) or pd.isna(theirs):
            continue
        difference = float(mine) - float(theirs)
        if difference > 0.05:
            better.append(CRITERIA_BY_KEY[key].label)
        elif difference < -0.05:
            worse.append(CRITERIA_BY_KEY[key].label)
    return {"better": better, "worse": worse}
