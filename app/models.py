from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LoginSpec(BaseModel):
    """Filled only when the user says a site needs login."""

    login_url: str
    username_selector: str = "input[type='email'], input[name='username'], input[name='email'], #email, #username"
    password_selector: str = "input[type='password'], input[name='password'], #password"
    submit_selector: str = "button[type='submit'], input[type='submit']"
    # credentials are never stored in the plan file — prompted at run time
    username_env: Optional[str] = None  # optional env var name hint
    password_env: Optional[str] = None


class FieldSpec(BaseModel):
    name: str
    description: str = ""
    selector: Optional[str] = None  # CSS selector relative to item
    attribute: Optional[str] = None  # e.g. href, src; None = text
    required: bool = False


class SiteSpec(BaseModel):
    name: str
    start_url: str
    method: Literal["http", "browser"] = "http"
    notes: str = ""
    list_selector: Optional[str] = None  # container for each result/item
    fields: List[FieldSpec] = Field(default_factory=list)
    pagination_selector: Optional[str] = None  # "next" link
    max_pages: Optional[int] = None
    login: Optional[LoginSpec] = None
    wait_for_selector: Optional[str] = None  # browser only
    extra_urls: List[str] = Field(default_factory=list)


class ScrapePlan(BaseModel):
    """Structured baseline produced by the AI chat, executed by Python."""

    title: str = "Untitled scrape"
    goal: str
    fields: List[FieldSpec] = Field(default_factory=list)  # global field defs
    sites: List[SiteSpec] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    max_items: Optional[int] = None
    notes: str = ""

    def resolved_fields_for(self, site: SiteSpec) -> List[FieldSpec]:
        if site.fields:
            return site.fields
        return self.fields
