"""Livestock records as returned by the staff read calls.

Field names inside a record are registry-defined and are kept as given.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LivestockSection(BaseModel):
	"""One section of an intake submission."""

	section_id: str | None = None
	section_register_id: str | None = None
	records: list[dict] = Field(default_factory=list)


class LivestockSubmission(BaseModel):
	"""A Livestock intake submission read back from the staff API."""

	submission_id: str
	record_name: str | None = None
	application_reference: str | None = None
	draft_status: str | None = None
	approval_status: str | None = None
	sections: list[LivestockSection] = Field(default_factory=list)
