def can_access(is_admin, is_owner, is_locked):
    # FIXED: precedence groups (is_admin or is_owner) and not is_locked
    # the right way; admin/owner must not be locked to access.
    return (is_admin or is_owner) and not is_locked
