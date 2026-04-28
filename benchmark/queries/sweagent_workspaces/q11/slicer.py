def first_n(seq, n):
    # BUG: should be seq[:n], not seq[:n-1]
    return seq[:n - 1]
