def can_access(is_admin, is_owner, is_locked):
    # Fixed: proper precedence groups (is_admin or is_owner) and not is_locked
    # so that access is granted only if admin/owner AND not locked.
    return (is_admin or is_owner) and not is_locked
