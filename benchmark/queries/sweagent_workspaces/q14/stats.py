def mean(xs):
    # BUG: doesn't handle empty list (ZeroDivisionError).
    return sum(xs) / len(xs)
