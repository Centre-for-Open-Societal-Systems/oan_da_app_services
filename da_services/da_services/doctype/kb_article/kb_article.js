// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

// Audience scope pickers, as on DA RBAC Assignment: active areas only, Zone within the
// row's Region, Woreda within the row's Zone. A value no area code can have: Zone / Woreda
// list nothing until their parent is set on the row.
const NO_MATCH = "__none__";

frappe.ui.form.on("KB Article", {
	setup(frm) {
		frm.set_query("region", "audience_scopes", () => ({ filters: { is_active: 1 } }));
		frm.set_query("zone", "audience_scopes", (doc, cdt, cdn) => ({
			filters: { is_active: 1, region: locals[cdt][cdn].region || NO_MATCH },
		}));
		frm.set_query("woreda", "audience_scopes", (doc, cdt, cdn) => ({
			filters: { is_active: 1, zone: locals[cdt][cdn].zone || NO_MATCH },
		}));
	},
});

frappe.ui.form.on("KB Audience Scope", {
	region(frm, cdt, cdn) {
		clear_if_outside(cdt, cdn, "zone", "DA Zone", "region");
	},

	zone(frm, cdt, cdn) {
		clear_if_outside(cdt, cdn, "woreda", "DA Woreda", "zone");
	},
});

// Empty the row's `child_field` when its value no longer sits under the row's `parent_link`.
function clear_if_outside(cdt, cdn, child_field, child_doctype, parent_link) {
	const row = locals[cdt][cdn];
	const child = row[child_field];
	if (!child) return;
	const parent_value = row[parent_link];
	if (!parent_value) {
		frappe.model.set_value(cdt, cdn, child_field, null);
		return;
	}
	frappe.db.get_value(child_doctype, child, parent_link).then(({ message }) => {
		if ((message && message[parent_link]) !== parent_value) {
			frappe.model.set_value(cdt, cdn, child_field, null);
		}
	});
}
