def can_access(is_admin, is_owner, is_locked):
    # BUG: precedence groups (is_admin or is_owner) and not is_locked
    # the wrong way; what's intended is admin/owner AND not locked.
    return is_admin or is_owner and not is_locked
