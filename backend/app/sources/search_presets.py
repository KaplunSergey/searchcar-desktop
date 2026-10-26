"""Versioned public criteria only. Browser state/headers are never a preset."""
import hashlib
import json
from copy import deepcopy

from pydantic import BaseModel, Field, model_validator


class PublicFields(BaseModel):
    model_config = {'strict': True, 'extra': 'forbid'}


class Facet(PublicFields):
    Group: str = Field(min_length=1, max_length=100)
    Value: str = Field(max_length=2000)
    ForAnalytics: bool | None = None


class LongRange(PublicFields):
    Name: str = Field(min_length=1, max_length=100)
    From: int | None
    To: int | None

    @model_validator(mode='after')
    def ordered(self):
        if self.From is not None and self.To is not None and self.From > self.To:
            raise ValueError('invalid_range')
        return self


class Search(PublicFields):
    Facets: list[Facet] | None = Field(max_length=100)
    FullSearch: str | None = Field(max_length=2000)
    LongRanges: list[LongRange] | None = Field(max_length=100)


class SortEntry(PublicFields):
    IsGeoSort: bool
    SortField: str = Field(min_length=1, max_length=100)
    IsDescending: bool


class SaleStatus(PublicFields):
    SaleStatus: int
    IsSelected: bool


class BidStatus(PublicFields):
    BidStatus: int
    IsSelected: bool


class IAACriteria(PublicFields):
    Searches: list[Search] = Field(max_length=100)
    ZipCode: str = Field(max_length=32)
    miles: int = Field(ge=0, le=1000000)
    PageSize: int = Field(ge=1, le=1000)
    CurrentPage: int = Field(ge=1, le=100000)
    Sort: list[SortEntry] = Field(max_length=20)
    ShowRecommendations: bool
    SaleStatusFilters: list[SaleStatus] = Field(max_length=100)
    BidStatusFilters: list[BidStatus] = Field(max_length=100)


class CopartCriteria(PublicFields):
    query: list[str] = Field(default_factory=list, max_length=100)
    filter: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    searchName: str = Field(default='', max_length=1000)
    watchListOnly: bool = False
    freeFormSearch: bool = False
    vanityKeyword: bool = False
    exclusions: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def public_search(self):
        if self.watchListOnly:
            raise ValueError('account_search_not_supported')
        return self


def preset(source_key, entry_url, criteria, summary, *, creation_url=None):
    # Reject rather than save an unrecognized payload with possible auth fields.
    if len(json.dumps(criteria, ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('search_conditions_too_large')
    # Source-owned schemas validate before this shared envelope is built.
    clean = deepcopy(criteria)
    fingerprint = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = dict(schema_version=1, source_key=source_key, entry_url=entry_url,
                  criteria=clean, summary=summary, creation_url=creation_url, revision=fingerprint)
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('search_conditions_too_large')
    return result
