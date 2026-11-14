// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("DA Kebele Assignment", {
	setup(frm) {
		frm.set_query("kebele", () => ({ filters: { is_active: 1 } }));
	},
});
