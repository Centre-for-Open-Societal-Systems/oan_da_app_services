// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("DA Hierarchy", {
	setup(frm) {
		// Only a group can have children, and a node cannot be placed under itself.
		frm.set_query("parent_da_hierarchy", () => ({
			filters: { is_group: 1, name: ["!=", frm.doc.name] },
		}));
	},
});
