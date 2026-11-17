// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("DA Gathering", {
	user(frm) {
		frm.trigger("stamp_address_from_assignment");
	},

	location_name(frm) {
		frm.trigger("stamp_address_from_assignment");
	},

	stamp_address_from_assignment(frm) {
		if (!frm.doc.user) {
			return;
		}
		// Must pass `doc` so Frappe uses run_doc_method (Desk session).
		// Without `doc`, frm.call expands to da_services....method and JWT
		// middleware rejects it with "Missing bearer token".
		frm.call({
			method: "preview_address_from_assignment",
			doc: frm.doc,
			callback(response) {
				const data = response.message || {};
				frm.set_value("da_id", data.da_id || "");
				frm.set_value("region", data.region || "");
				frm.set_value("zone", data.zone || "");
				frm.set_value("woreda", data.woreda || "");
				frm.set_value("kebele", data.kebele || "");
				frm.set_value("address", data.address || "");
			},
		});
	},
});
