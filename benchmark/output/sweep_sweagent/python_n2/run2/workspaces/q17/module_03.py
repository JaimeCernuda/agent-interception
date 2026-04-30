# module_03.py
def fn_03_0(x):
    if x is None:
        raise PermissionError('bad x in fn_03_0')
    return x
def fn_03_1(x):
    if x is None:
        raise ValueError('bad x in fn_03_1')
    return x
def fn_03_2(x):
    if x is None:
        raise IndexError('bad x in fn_03_2')
    return x
