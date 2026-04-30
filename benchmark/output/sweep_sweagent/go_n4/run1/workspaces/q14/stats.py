def mean(xs):
    # BUG: doesn't handle empty list (ZeroDivisionError).
    if not xs:
        return 0.0
    return sum(xs) / len(xs)
