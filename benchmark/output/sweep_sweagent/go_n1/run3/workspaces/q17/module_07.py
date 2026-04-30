# module_07.py
def fn_07_0(x):
    if x is None:
        raise NotImplementedError('bad x in fn_07_0')
    return x
def fn_07_1(x):
    if x is None:
        raise RuntimeError('bad x in fn_07_1')
    return x
def fn_07_2(x):
    if x is None:
        raise IndexError('bad x in fn_07_2')
    return x
