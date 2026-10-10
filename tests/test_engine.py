# -*- coding: utf-8 -*-
"""引擎层端到端验证：扫描 -> 执行 -> 撤销。

运行方式（需已编译 tkinter 的 Python）::

    python tests/test_engine.py

期望值说明（按 ``sorted(files)`` 的实际字典序推导）：
支持扩展名的样本文件共 12 个 → 11 行（1 个因无方括号发布组而无法识别）。
其中 8 个待重命名、1 个目标重名、1 个目标已存在、1 个已是目标格式。
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from bangumi_rename_gui import (  # noqa: E402
    STATUS_CONFLICT, STATUS_DONE, STATUS_EXISTS, STATUS_OK, STATUS_SAME,
    _assign_status, execute_plan, load_undo, save_undo, scan_directory, undo_records,
)

SAMPLES = [
    "[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4",
    "[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4",
    "[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv",
    "[Nix-Raws] Reiwa no Dara-san S01E01 [CR WEB-DL 1080p][SC_TC].mp4",
    "[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4",
    "[orion origin] Yuusha no Kuzu [01] [1080p] [CHT_JPN].srt",
    "[A] Title - 01 - [1080p].mp4",
    "[A] Title [01][1080p].mp4",
    "[A] Title2 [01][720p].mp4",
    "random_video.mp4",
    "cover.jpg",
]
SUBDIR_SAMPLE = os.path.join("Season 2", "[A] Title4 [03][1080p].mkv")
PRECREATE = ["Title2 - S01E01 - A.mp4"]

# 会被执行的样本 -> 期望的新文件名
EXPECTED_RENAMES = {
    "[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4":
        "Yuusha no Kuzu - S01E01 - orion origin.mp4",
    "[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4":
        "Tensei Shitara Slime Datta Ken - S04E02 - BeanSub.mp4",
    "[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv":
        "LV999 no Murabito - S01E02 - smzase.mkv",
    "[Nix-Raws] Reiwa no Dara-san S01E01 [CR WEB-DL 1080p][SC_TC].mp4":
        "Reiwa no Dara-san - S01E01 - Nix-Raws.mp4",
    "[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4":
        "番剧名 - S01E05 - 字幕组.mp4",
    "[orion origin] Yuusha no Kuzu [01] [1080p] [CHT_JPN].srt":
        "Yuusha no Kuzu - S01E01 - orion origin.srt",
    # 字典序中 "- 01 -" 变体先于 "[01]" 变体，因此前者被重命名、后者被判为重名冲突
    "[A] Title - 01 - [1080p].mp4":
        "Title - S01E01 - A.mp4",
    SUBDIR_SAMPLE:
        os.path.join("Season 2", "Title4 - S01E03 - A.mkv"),
}

failures = []


def check(label, got, want):
    if got == want:
        print(f"  [PASS] {label}: {got}")
    else:
        print(f"  [FAIL] {label}: 期望 {want!r}，实际 {got!r}")
        failures.append(label)


def build_sandbox():
    root = tempfile.mkdtemp(prefix="bangumi_test_")
    for name in SAMPLES:
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("x")
    os.makedirs(os.path.join(root, "Season 2"), exist_ok=True)
    with open(os.path.join(root, SUBDIR_SAMPLE), "w", encoding="utf-8") as fh:
        fh.write("x")
    for name in PRECREATE:
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("x")
    return root


def list_files(root):
    found = set()
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            found.add(os.path.relpath(os.path.join(dirpath, fn), root))
    return found


def main():
    root = build_sandbox()
    print(f"沙盒目录: {root}\n")

    # ---------------- 1. 递归扫描（含字幕） ----------------
    print("【1】递归扫描 + 含字幕")
    res = scan_directory(root, recursive=True, default_season=1, include_subtitles=True)
    check("表格行数", len(res.items), 11)
    check("待重命名数", res.count(STATUS_OK), 8)
    check("目标重名数", res.count(STATUS_CONFLICT), 1)
    check("目标已存在数", res.count(STATUS_EXISTS), 1)
    check("已是目标格式数", res.count(STATUS_DONE), 1)
    check("无需修改数", res.count(STATUS_SAME), 0)
    check("无法识别数", len(res.unparsed), 1)
    check("无法识别文件名", res.unparsed, ["random_video.mp4"])
    check("忽略非目标格式数", res.ignored_count, 1)

    print("\n  逐条核对文件名映射：")
    mapping = {it.old_rel: it.new_rel for it in res.items}
    for old_name, want in EXPECTED_RENAMES.items():
        check(old_name, mapping.get(old_name), want)

    print("\n  冲突 / 已存在判定：")
    conflict = next(it for it in res.items if it.status == STATUS_CONFLICT)
    check("被判定为重名的是 [01] 变体", conflict.old_rel, "[A] Title [01][1080p].mp4")
    exists = next(it for it in res.items if it.status == STATUS_EXISTS)
    check("被判定为已存在的是 Title2 源文件", exists.old_rel, "[A] Title2 [01][720p].mp4")
    done = next(it for it in res.items if it.status == STATUS_DONE)
    check("已是目标格式的条目", done.old_rel, "Title2 - S01E01 - A.mp4")

    print("\n  勾选状态：")
    check("冲突条目默认不勾选", conflict.selected, False)
    check("已存在条目默认不勾选", exists.selected, False)
    check("已是目标格式条目默认不勾选", done.selected, False)
    check("待重命名条目默认勾选", all(it.selected for it in res.items if it.status == STATUS_OK), True)

    # 回归测试：_assign_status 不得重置用户勾选
    first_ok = next(it for it in res.items if it.status == STATUS_OK)
    first_ok.selected = False
    _assign_status(res.items)
    check("重复校验后用户取消的勾选被保留", first_ok.selected, False)
    check("actionable 只含已勾选的待重命名条目",
          len(res.actionable), res.count(STATUS_OK) - 1)
    first_ok.selected = True

    # ---------------- 2. 参数开关 ----------------
    print("\n【2】处理选项开关")
    res_nosub = scan_directory(root, recursive=True, include_subtitles=False)
    check("关闭字幕后表格行数", len(res_nosub.items), 10)
    check("关闭字幕后忽略数", res_nosub.ignored_count, 2)

    res_top = scan_directory(root, recursive=False)
    check("非递归表格行数", len(res_top.items), 10)

    res_season = scan_directory(root, recursive=False, default_season=3, include_subtitles=False)
    check("默认季数=3 生效于无季数标识文件",
          any(it.new_rel.startswith("Yuusha no Kuzu - S03E01") for it in res_season.items), True)
    check("默认季数=3 不覆盖自带季数标识",
          any(it.new_rel == "Tensei Shitara Slime Datta Ken - S04E02 - BeanSub.mp4"
              for it in res_season.items), True)

    # ---------------- 3. 执行重命名 ----------------
    print("\n【3】执行重命名")
    plan = scan_directory(root, recursive=True, default_season=1, include_subtitles=True)
    records, errs = execute_plan(plan.items)
    check("成功重命名数", len(records), 8)
    check("失败数", len(errs), 0)

    on_disk = list_files(root)
    for want in EXPECTED_RENAMES.values():
        check(f"磁盘存在 {want}", want in on_disk, True)

    print("\n  未执行的条目应保持原样：")
    check("重名冲突源文件保留", "[A] Title [01][1080p].mp4" in on_disk, True)
    check("预置同名文件未被覆盖", "Title2 - S01E01 - A.mp4" in on_disk, True)
    check("无法识别文件保留", "random_video.mp4" in on_disk, True)
    check("非目标格式文件保留", "cover.jpg" in on_disk, True)
    check("重名冲突未产生多余副本",
          sum(1 for p in on_disk if os.path.basename(p) == "Title - S01E01 - A.mp4"), 1)

    # ---------------- 4. 撤销 ----------------
    print("\n【4】撤销重命名")
    undo_path = save_undo(records, root)
    check("撤销文件已生成", bool(undo_path and os.path.exists(undo_path)), True)
    payload = load_undo(undo_path)
    check("撤销记录条数", payload["count"], 8)

    restored, undo_errs = undo_records(payload["records"])
    check("还原数", len(restored), 8)
    check("还原失败数", len(undo_errs), 0)

    back = list_files(root)
    check("原 mp4 已还原", SAMPLES[0] in back, True)
    check("原 srt 已还原", SAMPLES[5] in back, True)
    check("中文文件名已还原", SAMPLES[4] in back, True)
    check("原 mkv 已还原", SAMPLES[2] in back, True)
    check("子目录原名已还原", SUBDIR_SAMPLE in back, True)
    check("撤销后不再存在新文件名",
          not any(os.path.basename(p) == "番剧名 - S01E05 - 字幕组.mp4" for p in back), True)

    # ---------------- 5. 幂等性 ----------------
    print("\n【5】重复扫描的幂等性")
    again = scan_directory(root, recursive=True, include_subtitles=True)
    check("还原后仍为 8 个待重命名", again.count(STATUS_OK), 8)
    # 沙盒中预置的 Title2 - S01E01 - A.mp4 始终符合目标格式，故恒为 1
    check("还原后已是目标格式条目仅剩预置文件", again.count(STATUS_DONE), 1)
    check("还原后已是目标格式条目名",
          next(it.old_rel for it in again.items if it.status == STATUS_DONE),
          "Title2 - S01E01 - A.mp4")

    redo_records, _ = execute_plan(again.items)
    check("二次执行成功数", len(redo_records), 8)
    rescan = scan_directory(root, recursive=True, include_subtitles=True)
    check("重命名后 0 个待重命名", rescan.count(STATUS_OK), 0)
    check("重命名后 9 个已是目标格式", rescan.count(STATUS_DONE), 9)

    undo_records(load_undo(save_undo(redo_records, root))["records"])

    # ---------------- 6. 边界情况 ----------------
    print("\n【6】边界情况")
    try:
        scan_directory(os.path.join(root, "__not_exists__"))
        check("不存在目录应报错", False, True)
    except ValueError as exc:
        check("不存在目录抛 ValueError", "目录不存在" in str(exc), True)

    try:
        scan_directory("")
        check("空目录参数应报错", False, True)
    except ValueError as exc:
        check("空目录参数抛 ValueError", "请先选择" in str(exc), True)

    shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 62)
    if failures:
        print(f"结果：{len(failures)} 项未通过 -> {failures}")
        return 1
    print("结果：全部断言通过（引擎层功能正常）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
