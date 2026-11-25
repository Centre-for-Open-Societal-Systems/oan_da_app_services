# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DACertificate(Document):
	"""A certificate's own details: title, issuer, dates (services/profile_lists.py).

	The DA Registry keeps the file and its name on the Education & Professional record;
	these details stay in DA Services. A row is written when a certificate is asked for;
	its status follows its change request, and ``registry_document_id`` is filled when
	the registry applied it.
	"""
