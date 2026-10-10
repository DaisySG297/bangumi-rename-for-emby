# -*- coding: utf-8 -*-
"""布局审计：在真实窗口尺寸下检查各区块位置、尺寸与是否被裁切。

由于无法在无桌面环境截图，本测试通过几何度量替代肉眼检查，
可捕捉「内容超出窗口」「卡片高度不足导致裁切」「按钮溢出」等回归。

运行方式（需已编译 tkinter 的 Python）::

    python tests/test_layout.py
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bangumi_rename_gui as gui  # noqa: E402

failures = []


def check(label, got, want):
    if got == want:
        print(f"  [PASS] {label}: {got}")
    else:
        print(f"  [FAIL] {label}: 期望 {want!r}，实际 {got!r}")
        failures.append(label)


def check_true(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{': ' + str(detail) if detail else ''}")
    if not condition:
        failures.append(label)


SAMPLES = [
    "[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4",
    "[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4",
    "[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv",
    "[Nix-Raws] Reiwa no Dara-san S01E01 [CR WEB-DL 1080p][SC_TC].mp4",
    "[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4",
    "random_video.mp4",
    "cover.jpg",
]


def build_sandbox():
    root = tempfile.mkdtemp(prefix="bangumi_layout_")
    for name in SAMPLES:
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("x")
    return root


def rect(widget):
    """返回控件的屏幕矩形 (top, bottom, left, right)。"""
    widget.update_idletasks()
    top = widget.winfo_rooty()
    left = widget.winfo_rootx()
    return top, top + widget.winfo_height(), left, left + widget.winfo_width()


def main():
    sandbox = build_sandbox()
    app = gui.RenameApp()
    app.geometry("1200x800+40+20")
    app.dir_var.set(sandbox)
    app.update()
    app.do_scan()
    for _ in range(3):
        app.update()

    win_w, win_h = app.winfo_width(), app.winfo_height()
    print(f"窗口尺寸: {win_w}x{win_h}\n")

    print("【1】垂直区块排列")
    blocks = {
        "头部": app.winfo_children()[0],
        "流程指示器": app.steps,
        "设置卡片": app.dir_entry.master.master.master,
        "操作行": app.scan_btn.master,
        "表格卡片": app.table_card,
        "日志卡片": app.log_text.master.master.master,
        "状态栏": app.chips.master,
    }
    positions = []
    for name, widget in blocks.items():
        top, bottom, left, right = rect(widget)
        positions.append((top, bottom, name))
        print(f"  {name}: y={top}~{bottom} (高 {bottom - top}) x={left}~{right}")

    ordered = sorted(positions)
    check_true("区块按名称顺序自上而下排列",
               [n for _t, _b, n in ordered] ==
               ["头部", "流程指示器", "设置卡片", "操作行", "表格卡片", "日志卡片", "状态栏"],
               [n for _t, _b, n in ordered])

    print("\n【2】区块不重叠")
    keys = ["设置卡片", "操作行", "表格卡片", "日志卡片", "状态栏"]
    spans = {n: (t, b) for t, b, n in positions}
    for first, second in zip(keys, keys[1:]):
        check_true(f"{first} 与 {second} 无重叠",
                   spans[first][1] <= spans[second][0] + 1,
                   f"{spans[first][1]} <= {spans[second][0]}")

    print("\n【3】内容不超出窗口")
    status_top = spans["状态栏"][0]
    win_bottom = app.winfo_rooty() + win_h
    check_true("状态栏底边不超出窗口", spans["状态栏"][1] <= win_bottom + 2,
               f"{spans['状态栏'][1]} <= {win_bottom}")
    check_true("日志卡片位于状态栏之上", spans["日志卡片"][1] <= status_top + 1,
               f"{spans['日志卡片'][1]} <= {status_top}")

    print("\n【4】卡片内容未被裁切")
    for name, card in (("设置卡片", blocks["设置卡片"]), ("日志卡片", blocks["日志卡片"])):
        need = card.interior.winfo_reqheight() + 2 * card._pad_y
        check_true(f"{name} 高度满足内容需求",
                   card.winfo_height() >= need - 1,
                   f"实际 {card.winfo_height()} >= 需要 {need}")

    table_card = app.table_card
    check_true("表格卡片高度充裕", table_card.winfo_height() >= 180,
               f"实际 {table_card.winfo_height()}")
    check_true("表格横向空间充裕", table_card.interior.winfo_width() >= 900,
               f"实际 {table_card.interior.winfo_width()}")
    check_true("表格可见高度充裕", app.tree.winfo_height() >= 120,
               f"实际 {app.tree.winfo_height()}")
    check_true("日志可见高度满足 6 行", app.log_text.winfo_height() >= 6 * 14,
               f"实际 {app.log_text.winfo_height()}")

    print("\n【5】控件不溢出所在行")
    action_row = app.scan_btn.master
    row_right = action_row.winfo_rootx() + action_row.winfo_width()
    buttons = [w for w in action_row.winfo_children() if isinstance(w, gui.FlatButton)]
    check_true("操作行按钮数量", len(buttons) == 7, len(buttons))
    overflow = []
    for btn in buttons:
        _t, _b, _l, right = rect(btn)
        if right > row_right + 1:
            overflow.append((btn._text, right - row_right))
    check_true("按钮未溢出操作行", not overflow, overflow)

    header = app.winfo_children()[0]
    header_right = header.winfo_rootx() + header.winfo_width()
    _t, _b, _l, steps_right = rect(app.steps)
    check_true("流程指示器未溢出头部", steps_right <= header_right - 10,
               f"{steps_right} <= {header_right - 10}")

    dir_row_right = app.dir_entry.master.winfo_rootx() + app.dir_entry.master.winfo_width()
    entry_right = app.dir_entry.winfo_rootx() + app.dir_entry.winfo_width()
    check_true("目录输入框有可用宽度", app.dir_entry.winfo_width() >= 300,
               f"实际 {app.dir_entry.winfo_width()}")
    check_true("目录输入框未与右侧按钮重叠", entry_right <= dir_row_right,
               f"{entry_right} <= {dir_row_right}")

    print("\n【6】窗口缩放后仍然可用")
    for size in ("1000x700", "980x680"):
        app.geometry(size)
        for _ in range(3):
            app.update()
        small_w, small_h = app.winfo_width(), app.winfo_height()
        _t, log_bottom, _l, _r = rect(app.log_text.master.master.master)
        _t, status_top, _l, _r = rect(app.chips.master)
        _t, status_bottom, _l, _r = rect(app.chips.master)
        win_bottom = app.winfo_rooty() + small_h
        print(f"  {size} → 实际 {small_w}x{small_h}")
        check_true(f"{size} 日志区未被裁切", log_bottom <= status_top + 1,
                   f"{log_bottom} <= {status_top}")
        check_true(f"{size} 状态栏完整可见", status_bottom <= win_bottom + 2,
                   f"{status_bottom} <= {win_bottom}")
        check_true(f"{size} 表格仍有余量", app.tree.winfo_height() >= 60,
                   f"实际 {app.tree.winfo_height()}")
        check_true(f"{size} 按钮未溢出",
                   all(rect(b)[3] <= app.scan_btn.master.winfo_rootx() +
                       app.scan_btn.master.winfo_width() + 1 for b in buttons))

    app.destroy()
    shutil.rmtree(sandbox, ignore_errors=True)

    print("\n" + "=" * 62)
    if failures:
        print(f"结果：{len(failures)} 项未通过 -> {failures}")
        return 1
    print("结果：全部断言通过（布局无重叠、无裁切、无溢出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
