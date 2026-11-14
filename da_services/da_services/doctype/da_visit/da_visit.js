// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("DA Visit", {
	setup(frm) {
		da_services.geo_cascade.setup(frm, da_services.geo_cascade.visit_map);
	},

	region(frm) {
		da_services.geo_cascade.clear_if_outside(frm, "zone", "DA Zone", "region", frm.doc.region);
	},

	zone(frm) {
		da_services.geo_cascade.clear_if_outside(frm, "woreda", "DA Woreda", "zone", frm.doc.zone);
	},

	woreda(frm) {
		da_services.geo_cascade.clear_if_outside(frm, "kebele", "DA Kebele", "woreda", frm.doc.woreda);
	},
});