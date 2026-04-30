def mean(xs):
    # Fixed: handle empty list case
    if not xs:
        return 0.0
    return sum(xs) / len(xs)
