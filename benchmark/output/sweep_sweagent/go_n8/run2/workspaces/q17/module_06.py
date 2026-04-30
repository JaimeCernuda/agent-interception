# module_06.py
def fn_06_0(x):
    if x is None:
        raise KeyError('bad x in fn_06_0')
    return x
def fn_06_1(x):
    if x is None:
        raise TypeError('bad x in fn_06_1')
    return x
