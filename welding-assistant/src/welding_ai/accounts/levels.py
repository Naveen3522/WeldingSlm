"""Access levels 1 to 10. A person's level comes from their role (User 1, Weld Engineer 5, Admin 10 to begin with; an Admin can add roles at any level 1 to 9, and
several roles may share a level). 10 is the Admin and is never handed out from the Users page (Admins are made with scripts/create_admin.py).

What a level may do is decided by the permissions file (accounts/permissions.py, data/settings/permissions.xml): `allowed` answers the named rights the routes
ask for (`ask`, `edit_formula`, `library`, ...) from it.
"""
ADMIN_LEVEL = 10
MAX_ASSIGNABLE = 9
DEFAULT_LEVEL = 1

# the names for the three starting levels, used only when a person has no role row (an older database)
STARTING_NAMES = [(1, "User"), (5, "Weld Engineer"), (10, "Admin")]


def is_admin(level):
    """True for an Admin (level 10). The Users pages (People, Roles, Permissions) and the mail settings are for this person alone, whatever the permissions file says."""
    return level >= ADMIN_LEVEL


def name(level):
    return next((n for lowest, n in reversed(STARTING_NAMES) if level >= lowest), STARTING_NAMES[0][1])


def allowed(level, right):
    from welding_ai.accounts import permissions
    return permissions.current().allowed(level, right)
