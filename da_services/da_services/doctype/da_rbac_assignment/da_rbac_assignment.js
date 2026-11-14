// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("DA RBAC Assignment", {
	setup(frm) {
		da_services.geo_cascade.setup(frm, da_services.geo_cascade.rbac_map);
	},

	region_scope(frm) {
		da_services.geo_cascade.clear_if_outside(
			frm,
			"zone_scope",
			"DA Zone",
			"region",
			frm.doc.region_scope
		);
	},

	zone_scope(frm) {
		da_services.geo_cascade.clear_if_outside(
			frm,
			"woreda_scope",
			"DA Woreda",
			"zone",
			frm.doc.zone_scope
		);
	},
});
