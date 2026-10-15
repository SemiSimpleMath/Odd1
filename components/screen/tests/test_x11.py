"""X11 readers: parsing xprop and xwininfo output."""
from odd1_screen import x11


def test_stacking_list_bottom_first():
    text = "_NET_CLIENT_LIST_STACKING: window id # 0x120000c, 0x1400004, 0xe00003"
    assert x11.parse_window_list(text) == ["0x120000c", "0x1400004", "0xe00003"]
    assert x11.parse_window_list("_NET_CLIENT_LIST_STACKING:  not found.") == []


def test_properties_prefer_net_wm_name_and_unescape():
    text = ('_NET_WM_PID = 7326\n_NET_WM_NAME = "Say \\"hi\\" — Firefox"\nWM_NAME = "fallback"\n'
            "_NET_WM_STATE = _NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_HIDDEN\n")
    assert x11.parse_properties(text) == {"pid": 7326, "title": 'Say "hi" — Firefox',
                                          "states": ["maximized_vert", "hidden"]}
    assert x11.parse_properties("_NET_WM_PID:  not found.\nWM_NAME = \"xterm\"")["pid"] is None


def test_geometry_is_absolute_client_area():
    text = """
  Absolute upper-left X:  0
  Absolute upper-left Y:  19
  Relative upper-left X:  0
  Relative upper-left Y:  19
  Width: 1280
  Height: 781
  Map State: IsViewable
"""
    assert x11.parse_geometry(text) == {"x": 0, "y": 19, "width": 1280, "height": 781, "viewable": True}
    assert x11.parse_geometry("Width: 3") is None
