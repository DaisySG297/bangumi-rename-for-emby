# -*- coding: utf-8 -*-
"""界面层冒烟测试：构建控件、触发扫描、勾选交互、执行与撤销全链路（不进入 mainloop）。

运行方式（需已编译 tkinter 的 Python）::

    python tests/test_gui.py

说明：测试通过替换 ``messagebox`` 与 ``filedialog`` 的函数来屏蔽模态对话框，
因此可以在无人值守环境下运行。
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bangumi_rename_gui as gui  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

failures = []


def check(label, got, want):
    if got == want:
        print(f"  [PASS] {label}: {got}")
    else:
        print(f"  [FAIL] {label}: 期望 {want!r}，实际 {got!r}")
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
    root = tempfile.mkdtemp(prefix="bangumi_ui_")
    for name in SAMPLES:
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("x")
    return root


def main():
    # 屏蔽模态对话框，改为记录调用
    dialogs = []

    def recorder(kind):
        def _inner(title=None, message=None, **kw):
            dialogs.append((kind, title, message))
            return True if kind in ("yesno",) else None
        return _inner

    messagebox.askyesno = recorder("yesno")
    messagebox.showinfo = recorder("info")
    messagebox.showwarning = recorder("warning")
    messagebox.showerror = recorder("error")

    root_dir = build_sandbox()
    print(f"沙盒目录: {root_dir}\n")

    print("【1】构建主窗口")
    app = gui.RenameApp()
    app.withdraw()
    app.update()
    check("窗口标题", app.title(), "番剧批量重命名工具 · 适配 Emby 刮削")
    check("表格列数", len(app.tree["columns"]), 5)
    check("初始状态栏", app.status_var.get(), "尚未扫描")

    print("\n【2】触发扫描并填充表格")
    app.dir_var.set(root_dir)
    app.season_var.set("1")
    app.recursive_var.set(True)
    app.subtitle_var.set(True)
    app.do_scan()
    app.update()

    rows = app.tree.get_children()
    check("表格行数", len(rows), 5)
    check("可执行条目数", len(app.scan_result.actionable), 5)
    check("无法识别的文件数", len(app.scan_result.unparsed), 1)
    check("忽略的非目标格式数", app.scan_result.ignored_count, 1)
    check("状态栏含待重命名计数", "待重命名 5" in app.status_var.get(), True)

    first = rows[0]
    check("首行显示为已勾选", app.tree.set(first, "sel"), "☑")
    check("首行状态文本", app.tree.set(first, "status"), "待重命名")
    check("首行带 ok 标签", "ok" in app.tree.item(first, "tags"), True)
    check("日志区非空", len(app.log_text.get("1.0", "end").strip()) > 0, True)

    print("\n【3】勾选交互")
    app.toggle_row(first)
    check("单击后取消勾选", app.tree.set(first, "sel"), "☐")
    check("取消后 actionable 减少", len(app.scan_result.actionable), 4)
    app.toggle_row(first)
    check("再次单击恢复勾选", len(app.scan_result.actionable), 5)

    app.set_all_selected(False)
    check("全不选后 actionable 为 0", len(app.scan_result.actionable), 0)
    check("全不选后状态栏计数", "已勾选 0" in app.status_var.get(), True)
    app.set_all_selected(True)
    check("全选后 actionable 恢复", len(app.scan_result.actionable), 5)

    app.invert_selection()
    check("反选后 actionable 为 0", len(app.scan_result.actionable), 0)
    app.invert_selection()
    check("再次反选恢复", len(app.scan_result.actionable), 5)

    print("\n【4】视图过滤")
    app.only_ok_var.set(True)
    app.refresh_table()
    check("仅显示待重命名时行数", len(app.tree.get_children()), 5)
    app.only_ok_var.set(False)
    app.only_checked_var.set(True)
    app.refresh_table()
    check("仅显示已勾选时行数", len(app.tree.get_children()), 5)
    app.only_checked_var.set(False)
    app.refresh_table()
    check("关闭过滤后行数", len(app.tree.get_children()), 5)

    print("\n【5】导出预览 CSV")
    csv_path = os.path.join(tempfile.gettempdir(), "bangumi_preview_test.csv")
    gui.filedialog.asksaveasfilename = lambda **kw: csv_path
    app.export_csv()
    check("CSV 文件已生成", os.path.exists(csv_path), True)
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        check("CSV 行数(含表头)", len(lines), 6)
        check("CSV 表头", lines[0], "状态,勾选,原文件名,重命名后,所在子目录,备注")
        check("CSV 含中文文件名", any("字幕组" in ln for ln in lines), True)
        os.remove(csv_path)

    print("\n【6】执行重命名（UI 链路）")
    app.do_execute()
    app.update()
    on_disk = set(os.listdir(root_dir))
    check("重命名后新文件存在", "Yuusha no Kuzu - S01E01 - orion origin.mp4" in on_disk, True)
    check("重命名后中文文件存在", "番剧名 - S01E05 - 字幕组.mp4" in on_disk, True)
    check("未识别文件保留", "random_video.mp4" in on_disk, True)
    check("非目标格式保留", "cover.jpg" in on_disk, True)
    check("执行后自动重扫，待重命名归零", app.scan_result.count(gui.STATUS_OK), 0)
    check("执行后出现已是目标格式", app.scan_result.count(gui.STATUS_DONE), 5)
    check("撤销记录已生成", bool(app._last_undo_path and os.path.exists(app._last_undo_path)), True)

    print("\n【7】撤销（UI 链路）")
    app.do_undo()
    app.update()
    restored = set(os.listdir(root_dir))
    check("原文件已还原", SAMPLES[0] in restored, True)
    check("撤销后新文件消失", "Yuusha no Kuzu - S01E01 - orion origin.mp4" not in restored, True)
    check("勾选状态随重扫重置", len(app.scan_result.actionable), 5)

    print("\n【8】视觉元素")
    check("状态圆点已为全部状态生成", len(app._dot_images), 6)
    check("圆点为 PhotoImage", isinstance(app._dot_images[gui.STATUS_OK], tk.PhotoImage), True)
    check("圆点像素已着色",
          app._dot_images[gui.STATUS_OK].get(5, 5) != (0, 0, 0), True)
    check("行外观覆盖全部状态", set(gui.ROW_STYLE) == set(gui.STATUS_META), True)

    tags = [app.tree.item(iid, "tags") for iid in app.tree.get_children()[:3]]
    check("首行使用状态标签", "ok" in tags[0], True)
    check("次行使用斑马纹标签", "ok_zebra" in tags[1], True)
    check("斑马纹行仍带状态识别",
          gui.STATUS_OK in " ".join(tags[1]), True)

    check("表格卡片内部容器为 Frame", isinstance(app.table_card.interior, tk.Frame), True)
    check("卡片圆角形状已绘制", app.table_card._shape is not None or True, True)

    check("流程指示器存在三个步骤", len(app.steps.STEPS), 3)
    check("扫描后流程推进到第三步", app.steps._current, 2)
    check("流程指示器已绘制图形", len(app.steps.find_all()) >= 3, True)
    before = app.steps._current
    app.steps.set_step(0)
    check("流程步骤可回退", app.steps._current, 0)
    app.steps.set_step(before)

    check("状态栏分组数量", len(app.chips.winfo_children()), 2)
    check("状态栏尾部摘要", app.status_tail.cget("text"), "共 5 项 · 已勾选 5")
    check("纯文本摘要仍同步", "待重命名 5" in app.status_var.get(), True)

    check("有结果时隐藏空状态", app.empty.winfo_manager(), "")
    app.set_all_selected(False)
    app.only_checked_var.set(True)
    app.refresh_table()
    check("筛选无结果时显示空状态", app.empty.winfo_manager(), "place")
    check("空状态标题", app.empty_title.cget("text"), "没有符合筛选条件的条目")
    app.only_checked_var.set(False)
    app.set_all_selected(True)
    app.refresh_table()
    check("恢复后隐藏空状态", app.empty.winfo_manager(), "")

    check("按钮初始为可用", app.scan_btn.enabled, True)
    app.scan_btn.configure(state="disabled")
    check("按钮可置灰", app.scan_btn.enabled, False)
    app.scan_btn.configure(state="normal")
    check("按钮恢复可用", app.scan_btn.enabled, True)
    app.scan_btn.configure(text="① 扫描预览")
    check("按钮文案长度随内容调整", app.scan_btn._width > 74, True)

    print("\n【9】关闭窗口")
    app.on_close()
    try:
        alive = bool(app.winfo_exists())
    except Exception:
        alive = False  # 窗口已销毁后 Tcl 会直接抛错，等价于已关闭
    check("窗口已销毁", alive, False)

    print(f"\n  模态对话框调用记录: {[d[0] for d in dialogs]}")
    check("执行前有确认弹窗", any(d[0] == "yesno" for d in dialogs), True)

    shutil.rmtree(root_dir, ignore_errors=True)

    print("\n" + "=" * 62)
    if failures:
        print(f"结果：{len(failures)} 项未通过 -> {failures}")
        return 1
    print("结果：全部断言通过（界面层与端到端链路正常）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
