def can_access(is_admin, is_owner, is_locked):
    # Access granted if (admin or owner) AND not locked
    return (is_admin or is_owner) and not is_locked
