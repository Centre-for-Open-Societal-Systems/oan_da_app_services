// Shared Desk geography cascades for DA Region → Zone → Woreda → Kebele Links.
// Usage from a DocType .js: da_services.geo_cascade.setup(frm, { region, zone, woreda, kebele })
frappe.provide("da_services.geo_cascade");

(function () {
	const NO_MATCH = "__none__";

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

	/**
	 * field_map keys are form fields; values are { doctype, parent_field?, parent_link? }.
	 * Example Visit: { region: { doctype: "DA Region" }, zone: { doctype: "DA Zone", parent_field: "region", parent_link: "region" }, ... }
	 */
	da_services.geo_cascade.setup = function (frm, field_map) {
		Object.entries(field_map).forEach(([fieldname, cfg]) => {
			frm.set_query(fieldname, () => {
				const filters = { is_active: 1 };
				if (cfg.parent_field) {
					filters[cfg.parent_link || cfg.parent_field] = frm.doc[cfg.parent_field] || NO_MATCH;
				}
				return { filters };
			});
		});
	};

	da_services.geo_cascade.clear_if_outside = clear_if_outside;

	da_services.geo_cascade.visit_map = {
		region: { doctype: "DA Region" },
		zone: { doctype: "DA Zone", parent_field: "region", parent_link: "region" },
		woreda: { doctype: "DA Woreda", parent_field: "zone", parent_link: "zone" },
		kebele: { doctype: "DA Kebele", parent_field: "woreda", parent_link: "woreda" },
	};

	da_services.geo_cascade.rbac_map = {
		region_scope: { doctype: "DA Region" },
		zone_scope: { doctype: "DA Zone", parent_field: "region_scope", parent_link: "region" },
		woreda_scope: { doctype: "DA Woreda", parent_field: "zone_scope", parent_link: "zone" },
	};
})();