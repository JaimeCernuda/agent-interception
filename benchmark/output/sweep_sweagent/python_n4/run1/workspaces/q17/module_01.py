# module_01.py
def fn_01_0(x):
    if x is None:
        raise KeyError('bad x in fn_01_0')
    return x
def fn_01_1(x):
    if x is None:
        raise PermissionError('bad x in fn_01_1')
    return x
def fn_01_2(x):
    if x is None:
        raise IndexError('bad x in fn_01_2')
    return x
