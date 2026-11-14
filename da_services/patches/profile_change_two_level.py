"""Move DA Profile Change Requests to the two-level approval states (12 Nov 2026).

Before: Pending -> Approved -> Applied, one approval (Supervisor or Administrator), the
registry's own decision in registry_approval. After: Pending Supervisor -> Pending
Administrator -> Sent to registry -> Applied, or Registry failed.

    Pending                                   -> Pending Supervisor
    Approved (the push failed)                -> Registry failed (Unavailable or Refused)
    Applied, registry_approval Pending/blank  -> Sent to registry
    Applied, registry_approval Approved       -> Applied
    Applied, registry_approval Rejected       -> Registry failed (Refused)
    Rejected / Changes requested / Cancelled  -> unchanged

The one approval given so far is kept as level 1 (approver, decided_at, decision_note).
Idempotent: a second run finds nothing in the old states.
"""

import frappe

DOCTYPE = "DA Profile Change Request"


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return
	frappe.reload_doc("da_services", "doctype", "da_profile_change_request")
	t = f"`tab{DOCTYPE}`"

	# The decision already given becomes level 1.
	frappe.db.sql(
		f"""update {t} set l1_by = approver, l1_at = decided_at, l1_note = decision_note
		where approver is not null and l1_by is null"""
	)
	frappe.db.sql(
		f"""update {t} set initiated_by_role = 'Development Agent'
		where initiated_by_role is null or initiated_by_role = ''"""
	)
	frappe.db.sql(f"update {t} set status = 'Pending Supervisor' where status = 'Pending'")
	frappe.db.sql(
		f"""update {t} set status = 'Registry failed',
			registry_sync_state = if(registry_error like 'DARegistryUnavailable%%', 'Unavailable', 'Refused')
		where status = 'Approved'"""
	)
	frappe.db.sql(
		f"""update {t} set status = 'Sent to registry'
		where status = 'Applied' and ifnull(registry_approval, '') in ('', 'Pending')"""
	)
	frappe.db.sql(
		f"""update {t} set status = 'Registry failed', registry_sync_state = 'Refused',
			registry_error = ifnull(registry_error, 'The DA Registry rejected the change request')
		where status = 'Applied' and registry_approval = 'Rejected'"""
	)
	frappe.db.sql(
		f"""update {t} set registry_sync_state = if(registry_error like 'DARegistryUnavailable%%', 'Unavailable', 'Refused')
		where registry_sync_state = 'Failed'"""
	)
	# applied_at used to be the push time; keep it as the last push.
	frappe.db.sql(
		f"update {t} set last_push_at = applied_at where last_push_at is null and applied_at is not null"
	)
	frappe.db.sql(
		f"""update {t} set applied_at = null
		where status in ('Sent to registry', 'Registry failed')"""
	)
	frappe.db.sql(
		f"""update {t} set push_attempts = 1
		where ifnull(push_attempts, 0) = 0 and registry_sync_state in ('Accepted', 'Refused', 'Unavailable')"""
	)
