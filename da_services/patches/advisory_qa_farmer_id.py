"""Fill the new `farmer_id` column on Advisory Q&A rows saved before it existed.

New rows get it from DA Visit.validate; old rows take it from their parent visit.
"""

import frappe


def execute():
	frappe.db.sql(
		"""update `tabDA Visit Advisory QA` qa
		join `tabDA Visit` v on v.`name` = qa.`parent`
		set qa.`farmer_id` = v.`farmer_id`
		where qa.`parenttype` = 'DA Visit' and ifnull(qa.`farmer_id`, '') != ifnull(v.`farmer_id`, '')"""
	)
