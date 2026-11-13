"""DA Visit Specialization child doctype (DA-211 Phase 3).

Represents one specialization covered by a visit. A visit can have multiple
specializations (e.g., Combined Crop + Livestock survey).
"""

from frappe.model.document import Document


class DAVisitSpecialization(Document):
	"""Child table row for visit specializations."""

	# Child table - no business logic needed
	pass
