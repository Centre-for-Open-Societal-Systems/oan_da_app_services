// Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
// For license information, please see license.txt

frappe.treeview_settings["DA Hierarchy"] = {
	// Like Department: the tree starts at the one root record, "All DA Hierarchy".
	ignore_fields: ["parent_da_hierarchy"],
	add_tree_node: "da_services.da_services.doctype.da_hierarchy.da_hierarchy.add_node",
	root_label: "All DA Hierarchy",
	get_tree_root: true,
	menu_items: [
		{
			label: __("New DA Hierarchy"),
			action: function () {
				frappe.new_doc("DA Hierarchy", true);
			},
			condition: 'frappe.boot.user.can_create.indexOf("DA Hierarchy") !== -1',
		},
	],
	onload: function (treeview) {
		treeview.make_tree();
	},
};
