"""JWT auth_hook configuration for this deployment.

The validation logic itself lives in oan_auth_service. This module exists to bind
it to da_services' own API namespace and exempt paths,
so the shared library never needs to know this app exists.
"""

API_NAMESPACE = "/api/method/da_services."

EXEMPT_PATHS: list[str] = []


def register():
	"""Register the da_services RPC namespace with oan_auth_service."""
	try:
		from oan_auth_service.api.middleware import register_namespace

		register_namespace(API_NAMESPACE, EXEMPT_PATHS)
	except ImportError:
		pass


register()
