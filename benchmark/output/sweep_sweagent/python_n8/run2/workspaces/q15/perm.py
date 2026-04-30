def can_access(is_admin, is_owner, is_locked):
    # FIXED: precedence groups (is_admin or is_owner) and not is_locked
    # correctly; admin/owner can access, but only if not locked.
    return (is_admin or is_owner) and not is_locked
