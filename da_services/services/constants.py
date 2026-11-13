"""Names the rest of the app agrees on.

Role names follow the five actors in FSD 2.2 / HLD 3. A "Woreda Officer" or "Senior DA"
is not a role: HLD 3 and 10.1 are explicit that they gain approval authority only by
being granted the Supervisor role through RBAC, so the title never appears here.
"""

# Roles (FSD Appendix B, HLD 3). Desk access is granted to officer-type roles only; the
# DA reaches the system through the mobile app and the v1 API.
ROLE_DA = "Development Agent"
ROLE_SUPERVISOR = "Supervisor"
ROLE_EXECUTIVE = "Executive"
ROLE_ADMIN = "OAN Administrator"
ROLE_COMMS = "Communications Officer"

DA_SERVICES_ROLES = (ROLE_DA, ROLE_SUPERVISOR, ROLE_EXECUTIVE, ROLE_ADMIN, ROLE_COMMS)

# Grievance access: Comms is excluded — FSD Appendix D forbids Comms seeing DA identity.
GRIEVANCE_ROLES = (ROLE_DA, ROLE_SUPERVISOR, ROLE_EXECUTIVE, ROLE_ADMIN)

# Roles whose scope is the whole country. Everyone else is bounded by the Region /
# Woreda on their DA RBAC Assignment rows.
UNRESTRICTED_ROLES = frozenset({ROLE_ADMIN, "System Manager", "Administrator"})

# Roles that may read DA records at all. Communications Officer is deliberately absent:
# FSD Appendix D gives it no access to DA identity.
DA_READ_ROLES = frozenset(
	{ROLE_DA, ROLE_SUPERVISOR, ROLE_EXECUTIVE, ROLE_ADMIN, "System Manager", "Administrator"}
)

# Registry-owned vocabulary mirrored here so code does not scatter string literals.
# Active Status (FSD 3.2.1 column 5) is set by Part 2 lifecycle outcomes.
ACTIVE_STATUS_ACTIVE = "Active"
ACTIVE_STATUS_ON_LEAVE = "On-leave"
ACTIVE_STATUS_SEPARATED = "Separated"
ACTIVE_STATUSES = (ACTIVE_STATUS_ACTIVE, ACTIVE_STATUS_ON_LEAVE, ACTIVE_STATUS_SEPARATED)

# Approval Status (FSD 3.2.1 column 9).
APPROVAL_STATUSES = ("Draft", "Awaiting review", "Approved", "Published")
