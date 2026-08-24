"""
Client for the ClinicalTrials.gov API v2.

Returns data only — no Streamlit calls. The caller decides how to report
problems to the user, which keeps this module usable outside the app
(tests, scripts, notebooks).
"""

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# Hard ceiling imposed by the API itself.
API_MAX_PAGE_SIZE = 1000


@dataclass
class FetchResult:
    """
    Studies plus an honest account of how the download went.

    complete  - every study matching the query was retrieved
    truncated - stopped because `limit` was reached, more exist server-side
    error     - the request failed; `studies` holds whatever arrived first
    """
    studies: list = field(default_factory=list)
    truncated: bool = False
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None and not self.truncated

    def __len__(self) -> int:
        return len(self.studies)

    def __iter__(self):
        return iter(self.studies)


class CTGovClient:
    BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
    FIELDS = (
        "IdentificationModule,ConditionsModule,StatusModule,DesignModule,"
        "EligibilityModule,ContactsLocationsModule,SponsorCollaboratorsModule"
    )

    @staticmethod
    def fetch_studies(
        query_name: str,
        condition: str,
        phases: list,
        sponsor: str,
        statuses: list,
        start_date_from: str | None = None,
        start_date_to: str | None = None,
        min_primary_completion_date: str | None = None,
        study_type: str | None = None,
        limit: int = 10000,
        timeout: int = 30,
    ) -> FetchResult:
        params = {
            "query.cond": condition,
            "filter.overallStatus": ",".join(statuses) if statuses else None,
            "pageSize": API_MAX_PAGE_SIZE,
            "format": "json",
            "fields": CTGovClient.FIELDS,
        }
        params = {k: v for k, v in params.items() if v is not None}

        advanced_terms = []
        if phases and "ALL" not in phases:
            advanced_terms.append(f"AREA[Phase]({' OR '.join(phases)})")
        if sponsor and sponsor != "ALL":
            advanced_terms.append(f"AREA[LeadSponsorClass]{sponsor}")
        if study_type and study_type != "ALL":
            advanced_terms.append(f"AREA[StudyType]{study_type}")
        # Either side may be left open. MIN and MAX are the registry's own
        # placeholders for an unbounded end of a range, so an absent boundary is
        # expressed as such rather than as an arbitrary far-off date.
        if start_date_from or start_date_to:
            lower = start_date_from or "MIN"
            upper = start_date_to or "MAX"
            advanced_terms.append(f"AREA[StartDate]RANGE[{lower}, {upper}]")
        if min_primary_completion_date:
            advanced_terms.append(
                f"AREA[PrimaryCompletionDate]RANGE[{min_primary_completion_date}, MAX]"
            )
        if advanced_terms:
            params["query.term"] = " AND ".join(advanced_terms)

        result = FetchResult()

        while True:
            try:
                response = requests.get(
                    CTGovClient.BASE_URL, params=params, timeout=timeout
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                result.error = str(exc)
                logger.warning(
                    "[%s] download interrupted after %d studies: %s",
                    query_name, len(result.studies), exc,
                )
                return result

            batch = data.get("studies", [])
            if not batch:
                break
            result.studies.extend(batch)

            next_token = data.get("nextPageToken")
            if not next_token:
                break
            if len(result.studies) >= limit:
                result.truncated = True
                logger.warning(
                    "[%s] reached the limit of %d studies, more are available",
                    query_name, limit,
                )
                break

            params["pageToken"] = next_token

        result.studies = result.studies[:limit]
        logger.info("[%s] retrieved %d studies", query_name, len(result.studies))
        return result
