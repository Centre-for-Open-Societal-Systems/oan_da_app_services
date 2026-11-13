import frappe
from frappe import _
from frappe.model.document import Document


class DAIntegrationEvent(Document):
	def validate(self):
		if not self.is_new() and self.status == "Completed":
			frappe.throw(_("Completed integration events cannot be modified."))

	def on_trash(self):
		frappe.throw(_("Integration events are append-only and cannot be deleted."))
