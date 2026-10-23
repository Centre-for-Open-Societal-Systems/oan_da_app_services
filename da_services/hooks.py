app_name = "da_services"
app_title = "DA Services"
app_publisher = "COSS - Centre for Open Societal Systems"
app_description = "OpenAgriNet Ethiopia DA Services (Part 2): Development Agent supervision, KPI, performance, leave and lifecycle management"
app_email = "admin@openagrinet.org"
app_license = "mit"

# Apps
# ------------------

required_apps = ["oan_auth_service"]

# Register Werkzeug REST routes for Frappe API Map
before_request = ["da_services.api.router.ensure_routes_registered"]

# Denied-access audit rows are queued during the request (the failing transaction is
# rolled back) and persisted here in their own commit.
after_request = ["da_services.utils.audit.flush"]

add_to_apps_screen = [
	{
		"name": "da_services",
		"title": "DA Services",
		"route": "/app/da-rbac-assignment",
		"has_permission": "da_services.api.permission.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/da_services/css/da_services.css"
# app_include_js = "/assets/da_services/js/da_services.js"

# include js, css files in header of web template
# web_include_css = "/assets/da_services/css/da_services.css"
# web_include_js = "/assets/da_services/js/da_services.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "da_services/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "da_services/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "da_services.utils.jinja_methods",
# 	"filters": "da_services.utils.jinja_filters"
# }

# Installation
# ------------

# The five DA Services roles (FSD Appendix B) are seeded so a fresh site comes up
# usable; migrate re-runs the seed idempotently.
after_install = "da_services.setup.install.after_install"
after_migrate = "da_services.setup.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "da_services.uninstall.before_uninstall"
# after_uninstall = "da_services.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "da_services.utils.before_app_install"
# after_app_install = "da_services.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "da_services.utils.before_app_uninstall"
# after_app_uninstall = "da_services.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "da_services.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "da_services.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["da_services.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# FSD 3.1.1 deny-by-default RBAC. The query condition filters list views, reports and
# the REST API uniformly; has_permission mirrors it for a single document.
permission_query_conditions = {
	"DA RBAC Assignment": "da_services.permissions.rbac_assignment_query_conditions",
	"DA Task": "da_services.permissions.da_task_query_conditions",
	"DA Visit": "da_services.permissions.da_visit_query_conditions",
	"DA Notification": "da_services.permissions.da_notification_query_conditions",
	"DA Kebele Assignment": "da_services.permissions.da_kebele_assignment_query_conditions",
	"DA Profile Change Request": "da_services.permissions.da_profile_change_request_query_conditions",
	"DA Farmer Link": "da_services.permissions.da_farmer_link_query_conditions",
	"DA Farmer Reference": "da_services.permissions.da_farmer_reference_query_conditions",
}

has_permission = {
	"DA RBAC Assignment": "da_services.permissions.has_rbac_assignment_permission",
	"DA Task": "da_services.permissions.has_da_task_permission",
	"DA Visit": "da_services.permissions.has_da_visit_permission",
	"DA Notification": "da_services.permissions.has_da_notification_permission",
	"DA Kebele Assignment": "da_services.permissions.has_da_kebele_assignment_permission",
	"DA Profile Change Request": "da_services.permissions.has_da_profile_change_request_permission",
	"DA Farmer Link": "da_services.permissions.has_da_farmer_link_permission",
	"DA Farmer Reference": "da_services.permissions.has_da_farmer_reference_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"da_services.integrations.grievance.queue.process_pending",
			"da_services.services.farmer_updates.process_pending_farmer_updates",
		],
	},
}

# Testing
# -------

before_tests = "da_services.setup.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "da_services.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "da_services.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "da_services.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["da_services.utils.before_request"]
# after_request = ["da_services.utils.after_request"]

# Job Events
# ----------
# before_job = ["da_services.utils.before_job"]
# after_job = ["da_services.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication & Profile Resolution
# -----------------------------------
# Integrates with oan_auth_service to enrich user introspection (GET /api/v1/auth/me)
# with DA-specific scope data. Auth itself is handled by oan_auth_service's middleware.

on_user_profile = ["da_services.api.v1.profile.resolve_user_profile_hook"]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
