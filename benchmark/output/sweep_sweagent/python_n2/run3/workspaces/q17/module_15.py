# module_15.py
def fn_15_0(x):
    if x is None:
        raise RuntimeError('bad x in fn_15_0')
    return x
def fn_15_1(x):
    if x is None:
        raise TypeError('bad x in fn_15_1')
    return x
def fn_15_2(x):
    if x is None:
        raise IndexError('bad x in fn_15_2')
    return x
