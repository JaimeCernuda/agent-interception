def mean(xs):
    # Fixed: handle empty list by returning 0.0
    if not xs:
        return 0.0
    return sum(xs) / len(xs)
