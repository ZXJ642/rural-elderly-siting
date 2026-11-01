# -*- coding: utf-8 -*-
"""
keyboard 模块存根（stub）。
仅用于让 SSCFLP_MH31.py 的 `import keyboard` 与 `keyboard.on_press_key(...)` 不报错。
不改动任何第三方算法源码。所有函数为空占位，不实际监听键盘。
"""
def is_pressed(key):
    return False
def read_key():
    return None
def wait(key):
    return None
def press(key):
    pass
def release(key):
    pass
def add_hotkey(*a, **k):
    pass
def remove_hotkey(*a, **k):
    pass
def on_press(*a, **k):
    pass
def on_press_key(key, callback):
    # 空实现：不注册键盘监听，算法按无中断方式正常运行
    pass
def hook(*a, **k):
    pass
def write(text):
    pass
def send(key):
    pass