"""Read-only Credit page services backed by A2C.

The A2C response schema is not documented in this repository, so every
function returns the upstream JSON body unchanged. There is no local Credit
model to fall back on and no Farmer-ID filter to invent.

Authorization is handled by A2C. DA Services forwards the caller's Authorization header and does not apply a separate local role gate.
"""

from __future__ import annotations

from da_services.integrations.a2c import (
	get_a2c_client,
	validate_application_id,
	validate_page,
	validate_status,
)


def get_loan_application_summary() -> dict | list:
	"""Read the A2C loan application summary unchanged."""
	return get_a2c_client().get_loan_application_summary()


def list_loan_applications(*, status: object = None, page: object = None) -> dict | list:
	"""Read one page of A2C loan applications, forwarding only status and page.

	Validation happens here as well as in the HTTP client so an invalid value
	is a caller error even when the configured backend cannot serve reads.
	"""
	return get_a2c_client().list_loan_applications(
		status=validate_status(status),
		page=validate_page(page),
	)


def get_loan_application_documents(application_id: object) -> dict | list:
	"""Read the documents of one A2C application unchanged."""
	return get_a2c_client().get_loan_application_documents(validate_application_id(application_id))
