"""
Similarity of historical protocols to the planned study, scored by a language model.

This is an optional aid, not a measurement. The score expresses one model's
subjective reading of how close a protocol is to the planned one; which trials
end up as benchmarks is decided by the planner, who ticks them by hand. Nothing
in the ranking depends on the model agreeing with itself between runs — the
reproducible record is the list of NCT identifiers the planner selected, which
travels with the saved scenario.

The module is deliberately narrow: everything a different provider would need to
replace lives behind score_similarity(). Swapping Gemini for another model means
rewriting this file and nothing else.
"""

import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Pinned rather than discovered. Asking the API for "whatever contains 'flash'"
# meant the model could change under the user without notice, and a score could
# not be attributed to a named tool. The name travels with the results and is
# written into the exported scenario.
#
# Pinning has one cost: providers retire models, and a pinned name eventually
# stops resolving. Rather than leave the user with a 404, resolve_model() falls
# back to whatever the account can still reach and reports the substitution, so
# the run completes and the scenario records the model that actually did the work
# instead of the one we asked for.
MODEL_NAME = "gemini-3.6-flash"

# Preference order for the fallback, most wanted first. Matching is by prefix, so
# a dated build such as "gemini-3.6-flash-002" satisfies "gemini-3.6-flash".
MODEL_PREFERENCE = ("gemini-3.6-flash", "gemini-3", "flash")

# Trials per request. One call for a hundred protocols produced an answer close
# to the output token limit, and an answer cut mid-object is lost in its entirety.
BATCH_SIZE = 25

# Inclusion criteria get the larger share on purpose: they define who can enter,
# and the planner reads them first. Exclusions still get a budget of their own —
# under the previous single 1000-character cut they were dropped entirely, since
# the registry always writes them after the inclusions.
INCLUSION_BUDGET = 1500
EXCLUSION_BUDGET = 900

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "nct_id": {"type": "STRING"},
            "similarity_score": {"type": "INTEGER"},
            "explanation": {"type": "STRING"},
        },
        "required": ["nct_id", "similarity_score", "explanation"],
    },
}


@dataclass
class ScoringResult:
    """Scores plus an account of what the model did with the request."""
    rows: list = field(default_factory=list)
    model_name: str = MODEL_NAME
    unscored: list = field(default_factory=list)   # asked for, not returned
    foreign_ids: list = field(default_factory=list)  # returned, never asked for
    error: str | None = None
    note: str | None = None   # e.g. the pinned model was unavailable

    @property
    def scored_count(self) -> int:
        return len(self.rows)


def _api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY")


def split_criteria(raw: str) -> tuple[str, str]:
    """
    Inclusion and exclusion sections of the registry's eligibility text.

    The two are stored in one free-text field, inclusions first. Splitting before
    any truncation is the whole point: cut the field at a fixed length and the
    exclusions vanish, leaving the model to judge the similarity of inclusion and
    exclusion criteria having seen only the former.
    """
    if not raw:
        return "", ""
    if "Exclusion Criteria:" in raw:
        head, tail = raw.split("Exclusion Criteria:", 1)
        return head.replace("Inclusion Criteria:", "").strip(), tail.strip()
    return raw.replace("Inclusion Criteria:", "").strip(), ""


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget].rstrip() + " […truncated]"


def describe_study(study: dict) -> str:
    """One protocol as the model sees it."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    design = protocol.get("designModule", {})
    status = protocol.get("statusModule", {})

    phases = ", ".join(design.get("phases", [])) or "not stated"
    study_type = design.get("studyType", "not stated")
    enrolment = design.get("enrollmentInfo", {}).get("count", "not stated")
    conditions = ", ".join(protocol.get("conditionsModule", {}).get("conditions", []))
    start = status.get("startDateStruct", {}).get("date", "not stated")

    inclusion, exclusion = split_criteria(
        protocol.get("eligibilityModule", {}).get("eligibilityCriteria", "")
    )

    return (
        f"NCT ID: {ident.get('nctId', 'unknown')}\n"
        f"Title: {ident.get('briefTitle', 'not stated')}\n"
        f"Conditions: {conditions or 'not stated'}\n"
        f"Phase: {phases} | Study type: {study_type} | "
        f"Planned enrolment: {enrolment} | Started: {start}\n"
        f"Inclusion criteria:\n{_clip(inclusion, INCLUSION_BUDGET) or 'not stated'}\n"
        f"Exclusion criteria:\n{_clip(exclusion, EXCLUSION_BUDGET) or 'not stated'}\n"
    )


def describe_planned(planned: dict) -> str:
    phases = planned.get("phases") or []
    return (
        f"Title: {planned.get('title', 'not stated')}\n"
        f"Indication: {planned.get('indication', 'not stated')}\n"
        f"Phase: {', '.join(phases) if phases else 'not stated'}\n"
        f"Planned enrolment: {planned.get('num_patients') or 'not stated'} participants "
        f"across {planned.get('num_sites') or 'an unstated number of'} sites\n"
        f"Inclusion criteria:\n{planned.get('inclusion_criteria') or 'not stated'}\n"
        f"Exclusion criteria:\n{planned.get('exclusion_criteria') or 'not stated'}\n"
    )


def build_prompt(planned: dict, studies: list, instructions: str = "") -> str:
    custom = ""
    if instructions and instructions.strip():
        custom = (
            "\n[ADDITIONAL REQUIREMENT FROM THE USER]\n"
            "Weigh the following when scoring:\n"
            f"{instructions.strip()}\n"
        )

    # The dimensions are named so that explanations stay comparable between
    # trials. Sub-scores are deliberately not requested: a single acknowledged
    # opinion is honest, a table of component figures would dress the same
    # opinion up as a measurement.
    return f"""You are a clinical trial feasibility analyst. Judge how closely each
historical trial resembles a planned study, so that a human planner can decide
which of them to treat as reference trials.

[PLANNED STUDY]
{describe_planned(planned)}
[HISTORICAL TRIALS]
{"".join(describe_study(s) for s in studies)}{custom}
[TASK]
For every historical trial listed above, give a similarity score from 0 to 100
and one or two sentences of justification in English.

Weigh these, in this order:
1. the eligible patient population, above all the inclusion criteria;
2. phase and study type;
3. exclusion criteria that would materially change who can be enrolled;
4. the scale of the trial, in participants and sites.

Score every trial you were given and no others. Use its exact NCT ID.
"""


def available_models() -> list[str]:
    """Model names this account can call for text generation, without the prefix."""
    import google.generativeai as genai

    genai.configure(api_key=_api_key())
    return [
        m.name.split("models/")[-1]
        for m in genai.list_models()
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    ]


def resolve_model(preferred: str = MODEL_NAME) -> tuple[str, str | None]:
    """
    (name to use, note explaining a substitution or None).

    Only called after the preferred model has already failed, so it costs one
    extra request in the rare case and none in the ordinary one.
    """
    try:
        names = available_models()
    except Exception as exc:
        return preferred, f"Could not list available models ({exc})."

    if preferred in names:
        return preferred, None

    for wanted in MODEL_PREFERENCE:
        matches = sorted(n for n in names if n.startswith(wanted))
        if matches:
            chosen = matches[0]
            return chosen, (
                f"`{preferred}` is no longer available; scored with `{chosen}` instead. "
                f"Update MODEL_NAME in services/gemini_benchmark.py to pin it."
            )

    if names:
        return names[0], (
            f"`{preferred}` is no longer available and no comparable model was found; "
            f"scored with `{names[0]}`."
        )
    return preferred, "The API reports no models available for this key."


def _call_model(prompt: str, model_name: str):
    import google.generativeai as genai

    genai.configure(api_key=_api_key())
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(
        prompt,
        generation_config={
            # Zero temperature so that two runs over the same sample put the same
            # trials in front of the planner. The ranking does not depend on it —
            # the selection does, and re-reading a reshuffled list is wasted work.
            "temperature": 0,
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
        },
    )
    return json.loads(response.text)


def score_similarity(
    planned: dict,
    studies: list,
    instructions: str = "",
    progress=None,
) -> ScoringResult:
    """
    Score every trial in `studies`. `progress` is an optional callable taking a
    fraction between 0 and 1, so the caller can draw a progress bar without this
    module knowing anything about the interface it is drawn in.
    """
    if not _api_key():
        return ScoringResult(
            error="No GEMINI_API_KEY found. Add it to the .env file to use similarity scoring."
        )
    if not studies:
        return ScoringResult(error="No historical trials to score.")

    requested = {
        s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
        for s in studies
    }
    requested.discard(None)

    collected: dict[str, dict] = {}
    foreign: set[str] = set()
    model_name, note = MODEL_NAME, None

    batches = [studies[i:i + BATCH_SIZE] for i in range(0, len(studies), BATCH_SIZE)]
    for index, batch in enumerate(batches):
        prompt = build_prompt(planned, batch, instructions)
        try:
            answer = _call_model(prompt, model_name)
        except Exception as exc:
            # A retired model looks like any other failure until it is asked
            # about, so resolve once and retry this same batch before giving up.
            if note is None:
                model_name, note = resolve_model(model_name)
            retried = False
            if note and model_name:
                try:
                    answer = _call_model(prompt, model_name)
                    retried = True
                except Exception as second:
                    exc = second
            if not retried:
                # Whatever arrived so far is kept: a planner with 75 of 100 trials
                # scored is better off than one with an error and nothing to read.
                return ScoringResult(
                    rows=list(collected.values()),
                    model_name=model_name,
                    unscored=sorted(requested - set(collected)),
                    foreign_ids=sorted(foreign),
                    error=f"Scoring stopped after {index} of {len(batches)} batches: {exc}",
                    note=note,
                )

        for item in answer or []:
            nct = item.get("nct_id")
            if nct not in requested:
                # A trial the model invented or carried over from an earlier
                # batch. Silently mapping it would attach a score to a protocol
                # nobody asked about.
                if nct:
                    foreign.add(nct)
                continue
            score = item.get("similarity_score")
            collected[nct] = {
                "nct_id": nct,
                "similarity_score": float(score) if score is not None else None,
                "explanation": item.get("explanation", ""),
            }

        if progress:
            progress((index + 1) / len(batches))

    return ScoringResult(
        rows=list(collected.values()),
        model_name=model_name,
        unscored=sorted(requested - set(collected)),
        foreign_ids=sorted(foreign),
        note=note,
    )
