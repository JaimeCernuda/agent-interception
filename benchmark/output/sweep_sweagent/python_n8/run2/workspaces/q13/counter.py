def is_positive(value):
    # BUG: comparing string '0' to int 0 raises TypeError on int input.
    return value > 0
