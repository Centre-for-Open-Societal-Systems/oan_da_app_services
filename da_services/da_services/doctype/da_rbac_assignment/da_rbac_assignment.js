// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

// Scope pickers: active areas only, Zone within the Region, Woreda within the Zone.
// A value no area code can have: Zone / Woreda list nothing until their parent is set.
const NO_MATCH = "__none__";

frappe.ui.form.on("DA RBAC Assignment", {
	setup(frm) {
		frm.set_query("region_scope", () => ({ filters: { is_active: 1 } }));
		// No parent chosen yet: the filter matches nothing, so the list is empty.
		frm.set_query("zone_scope", () => ({
			filters: { is_active: 1, region: frm.doc.region_scope || NO_MATCH },
		}));
		frm.set_query("woreda_scope", () => ({
			filters: { is_active: 1, zone: frm.doc.zone_scope || NO_MATCH },
		}));
	},

	// DA-ID comes from the User's DA Registry ID; a user without one clears it, since the
	// previous value belonged to the previously selected user.
	// user(frm) {
	// 	if (!frm.doc.user) {
	// 		frm.set_value("da_id", null);
	// 		return;
	// 	}
	// 	frappe.db.get_value("User", frm.doc.user, "da_registry_id").then(({ message }) => {
	// 		frm.set_value("da_id", (message && message.da_registry_id) || null);
	// 	});
	// },

	region_scope(frm) {
		clear_if_outside(frm, "zone_scope", "DA Zone", "region", frm.doc.region_scope);
	},

	zone_scope(frm) {
		clear_if_outside(frm, "woreda_scope", "DA Woreda", "zone", frm.doc.zone_scope);
	},
});

// Empty `child_field` when its value no longer sits under `parent_value`.
function clear_if_outside(frm, child_field, child_doctype, parent_link, parent_value) {
	const child = frm.doc[child_field];
	if (!child) return;
	if (!parent_value) {
		frm.set_value(child_field, null);
		return;
	}
	frappe.db.get_value(child_doctype, child, parent_link).then(({ message }) => {
		if ((message && message[parent_link]) !== parent_value) {
			frm.set_value(child_field, null);
		}
	});
}
