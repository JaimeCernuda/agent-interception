def can_access(is_admin, is_owner, is_locked):
    # FIXED: correct precedence with parentheses ensures (admin OR owner) AND NOT locked
    return (is_admin or is_owner) and not is_locked
