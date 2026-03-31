import os
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


def load_simple_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


load_simple_env()


class SuggestionItem(BaseModel):
    label: str


class AutocompleteResponse(BaseModel):
    suggestions: List[SuggestionItem]


class GeoapifyAutocompleteClient:
    BASE_URL = "https://api.geoapify.com/v1/geocode/autocomplete"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.timeout = httpx.Timeout(5.0, connect=2.0)

    def fetch(self, query: str, limit: int = 5) -> List[SuggestionItem]:
        params = {
            "text": query,
            "limit": limit,
            "format": "json",
            "apiKey": self.api_key,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self.BASE_URL, params=params)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Autocomplete provider timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail="Autocomplete provider returned an error.") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="Autocomplete provider is unavailable.") from exc

        payload = response.json()
        features = payload.get("results", [])

        suggestions: List[SuggestionItem] = []
        seen = set()
        for item in features:
            label = str(item.get("formatted") or item.get("address_line1") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            suggestions.append(SuggestionItem(label=label))

        return suggestions[:limit]


class AutocompleteService:
    def __init__(self, client: GeoapifyAutocompleteClient) -> None:
        self.client = client

    def autocomplete(self, query: str) -> AutocompleteResponse:
        normalized_query = query.strip()
        if len(normalized_query) < 3:
            return AutocompleteResponse(suggestions=[])
        return AutocompleteResponse(suggestions=self.client.fetch(normalized_query, limit=5))


api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("GEOAPIFY_API_KEY environment variable is required.")

autocomplete_service = AutocompleteService(GeoapifyAutocompleteClient(api_key))

app = FastAPI(title="Dispatch Autocomplete Service", version="1.0.0")


@app.get("/autocomplete", response_model=AutocompleteResponse)
def autocomplete(q: str = Query(..., min_length=1, description="User input for address autocomplete")) -> AutocompleteResponse:
    return autocomplete_service.autocomplete(q)

