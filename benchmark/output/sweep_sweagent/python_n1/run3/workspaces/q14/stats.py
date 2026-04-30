def mean(xs):
    # Handle empty list case.
    if len(xs) == 0:
        return 0.0
    return sum(xs) / len(xs)
