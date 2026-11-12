# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DAServicesSettings(Document):
	def validate(self):
		self._apply_field_default("crop_list_sources")
		self._apply_field_default("crop_register_tab_ids")

	def _apply_field_default(self, fieldname: str) -> None:
		if (getattr(self, fieldname, None) or "").strip():
			return
		field = self.meta.get_field(fieldname)
		if field and field.default:
			setattr(self, fieldname, field.default)
