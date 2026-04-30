# module_04.py
def fn_04_0(x):
    if x is None:
        raise RuntimeError('bad x in fn_04_0')
    return x
def fn_04_1(x):
    if x is None:
        raise IndexError('bad x in fn_04_1')
    return x
