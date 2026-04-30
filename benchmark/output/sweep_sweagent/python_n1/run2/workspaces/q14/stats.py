def mean(xs):
    # Handle empty list case to avoid ZeroDivisionError.
    if not xs:
        return 0.0
    return sum(xs) / len(xs)
