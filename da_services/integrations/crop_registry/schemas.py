"""Crop Sown records as returned by the verified Staff read calls.

The read payload is an intake submission: status fields plus section records.
Tab reads are a separate list. Field names inside a record are registry-defined
and are kept as given.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CropSection(BaseModel):
	"""One section of an intake submission."""

	section_register_id: str | None = None
	records: list[dict] = Field(default_factory=list)


class CropSubmission(BaseModel):
	"""A Crop Sown intake submission read back from the Staff API."""

	submission_id: str
	draft_status: str | None = None
	approval_status: str | None = None
	sections: list[CropSection] = Field(default_factory=list)
