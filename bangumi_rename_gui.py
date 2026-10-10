# -*- coding: utf-8 -*-
"""
番剧文件名批量重命名工具 —— 图形界面版（适配 Emby 刮削）
========================================================

统一输出格式：``标题 - SXXEXX - 发布组.ext``

设计说明
--------
程序分为两层，便于独立测试与复用：

* **引擎层**（``PlanItem`` / ``scan_directory`` / ``execute_plan`` / ``undo_records``）
  纯函数实现，不依赖任何界面组件，可被脚本化调用。
* **界面层**（``RenameApp``）
  基于 tkinter 标准库，负责目录选择、预览表格、勾选执行与日志输出。
  自绘了 ``Card`` / ``FlatButton`` / ``StepIndicator`` 等圆角组件，
  以纯标准库实现现代化外观，不引入任何第三方 UI 依赖。

核心解析逻辑直接复用同目录下的 ``bangumi_rename.py``（``extract_info``），
确保命令行版与界面版的行为完全一致。

运行环境：Python 3.7+ 且已编译 tkinter（Windows 官方安装包默认自带）。
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

# --------------------------------------------------------------------------- #
# 复用命令行版的核心逻辑
# --------------------------------------------------------------------------- #
#: 是否运行在 PyInstaller 打包环境中
IS_FROZEN = bool(getattr(sys, "frozen", False))

if IS_FROZEN:
    # onefile 模式下 __file__ 指向临时解包目录，会被进程退出后清理，
    # 因此资源根目录必须取可执行文件所在目录，撤销记录才能持久保存。
    _THIS_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _BUNDLE_DIR = getattr(sys, "_MEIPASS", _THIS_DIR)
else:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = _THIS_DIR

for _p in (_BUNDLE_DIR, _THIS_DIR):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import bangumi_rename as core
    from bangumi_rename import VIDEO_EXTENSIONS, build_filename, extract_info
except Exception as _exc:  # pragma: no cover - 环境异常时的兜底提示
    _msg = (
        "无法导入核心模块 bangumi_rename.py。\n\n"
        "请确认 bangumi_rename.py 与本程序位于同一目录下。\n\n"
        f"详细错误：{_exc}"
    )
    try:
        _root = tk.Tk()
        _root.withdraw()
        messagebox.showerror("启动失败", _msg)
        _root.destroy()
    except Exception:
        # --windowed 打包后 sys.stderr 可能为 None，此处必须容错
        try:
            print(_msg, file=sys.stderr)
        except Exception:
            pass
    raise SystemExit(1)

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".mks"}

# --------------------------------------------------------------------------- #
# 引擎层
# --------------------------------------------------------------------------- #

STATUS_OK = "ok"
STATUS_SAME = "same"
STATUS_CONFLICT = "conflict"
STATUS_EXISTS = "exists"
STATUS_DONE = "done"
STATUS_INVALID = "invalid"

#: 状态 -> (显示文本, 前景色)
STATUS_META: Dict[str, Tuple[str, str]] = {
    STATUS_OK: ("待重命名", "#15803d"),
    STATUS_SAME: ("无需修改", "#94a3b8"),
    STATUS_CONFLICT: ("目标重名", "#b91c1c"),
    STATUS_EXISTS: ("目标已存在", "#b45309"),
    STATUS_DONE: ("已是目标格式", "#0f766e"),
    STATUS_INVALID: ("无法识别", "#64748b"),
}

#: 输出格式特征：``标题 - SXXEXX - 发布组``，用于识别「已经处理过」的文件
OUTPUT_PATTERN = re.compile(r"^.+? - S(\d{1,2})E(\d{1,3}) - .+$")

#: Windows 文件名非法字符
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Windows 保留设备名
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename_part(text: str) -> Tuple[str, bool]:
    """清理文件名中不适用于 Windows 的字符。

    返回 ``(清理后的文本, 是否发生过替换)``。
    """
    cleaned = _ILLEGAL_CHARS.sub("_", text)
    cleaned = cleaned.rstrip(" .")
    changed = cleaned != text
    if cleaned and cleaned.upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
        changed = True
    return cleaned, changed


def _norm_key(path: str) -> str:
    """生成用于比较的规范化路径键（忽略大小写与相对路径差异）。"""
    return os.path.normcase(os.path.abspath(path))


@dataclass
class PlanItem:
    """一条重命名计划。"""

    old_path: str
    new_path: str
    old_rel: str
    new_rel: str
    sub_dir: str
    status: str = STATUS_OK
    note: str = ""
    selected: bool = False


@dataclass
class ScanResult:
    """扫描结果汇总。"""

    directory: str
    items: List[PlanItem] = field(default_factory=list)
    unparsed: List[str] = field(default_factory=list)
    ignored_count: int = 0

    def count(self, status: str) -> int:
        return sum(1 for it in self.items if it.status == status)

    @property
    def actionable(self) -> List[PlanItem]:
        """已勾选且可立即执行的条目。"""
        return [it for it in self.items if it.selected and it.status == STATUS_OK]


def scan_directory(
    directory: str,
    recursive: bool = True,
    default_season: int = 1,
    include_subtitles: bool = True,
) -> ScanResult:
    """扫描目录，生成重命名计划。

    参数
    ----
    directory
        待处理的番剧根目录。
    recursive
        是否递归遍历子目录。
    default_season
        文件名未标注季数时采用的季数，会同步写入核心模块的同名配置。
    include_subtitles
        是否一并处理字幕文件。

    异常
    ----
    ValueError
        目录不存在或不可读时抛出。
    """
    if not directory:
        raise ValueError("请先选择番剧目录。")
    if not os.path.isdir(directory):
        raise ValueError(f"目录不存在或不可访问：\n{directory}")

    # extract_info 依赖模块级 DEFAULT_SEASON，这里同步用户选择，保持单一数据源
    core.DEFAULT_SEASON = int(default_season)

    exts = set(VIDEO_EXTENSIONS)
    if not include_subtitles:
        exts -= SUBTITLE_EXTENSIONS

    if recursive:
        walked = [(r, fs) for r, _dirs, fs in os.walk(directory)]
    else:
        try:
            names = os.listdir(directory)
        except OSError as exc:
            raise ValueError(f"无法读取目录：{exc}")
        walked = [(directory, names)]

    result = ScanResult(directory=directory)

    for root, files in walked:
        for filename in sorted(files):
            full_path = os.path.join(root, filename)
            if not os.path.isfile(full_path):
                continue

            ext = os.path.splitext(filename)[1].lower()
            if ext not in exts:
                result.ignored_count += 1
                continue

            info = extract_info(filename)
            if not info:
                rel = os.path.relpath(full_path, directory)
                # 已经符合输出格式的文件（例如上次运行的结果）单独归类，避免误报为「无法识别」
                if OUTPUT_PATTERN.match(os.path.splitext(filename)[0]):
                    result.items.append(
                        PlanItem(
                            old_path=full_path,
                            new_path=full_path,
                            old_rel=rel,
                            new_rel=rel,
                            sub_dir=os.path.dirname(rel) or ".",
                            status=STATUS_DONE,
                            note="已是目标格式，无需处理",
                        )
                    )
                else:
                    result.unparsed.append(rel)
                continue

            group, title, season, episode, file_ext = info
            title, t_fixed = sanitize_filename_part(title)
            group, g_fixed = sanitize_filename_part(group)
            if not title:
                result.unparsed.append(os.path.relpath(full_path, directory))
                continue

            try:
                # 复用核心模块的拼接规范（无发布组时省略末段），
                # 与命令行版保持单一数据源，避免两版行为分叉
                new_filename = build_filename(group, title, season, episode, file_ext)
            except (TypeError, ValueError):
                result.unparsed.append(os.path.relpath(full_path, directory))
                continue

            old_rel = os.path.relpath(full_path, directory)
            new_path = os.path.join(root, new_filename)
            note = "已替换文件名非法字符" if (t_fixed or g_fixed) else ""
            result.items.append(
                PlanItem(
                    old_path=full_path,
                    new_path=new_path,
                    old_rel=old_rel,
                    new_rel=os.path.relpath(new_path, directory),
                    sub_dir=os.path.dirname(old_rel) or ".",
                    note=note,
                )
            )

    _assign_status(result.items)
    # 初始勾选：仅「待重命名」条目默认选中，其余需用户显式确认
    for it in result.items:
        it.selected = it.status == STATUS_OK
    return result


def _assign_status(items: Sequence[PlanItem]) -> None:
    """就地判定每条计划的状态：待重命名 / 无需修改 / 目标重名 / 目标已存在。

    .. important::
       本函数**不修改** ``selected`` 字段。勾选状态属于用户意图，
       只应在扫描时初始化，或由用户交互、编辑操作显式变更。
    """
    seen: Dict[str, str] = {}
    for it in items:
        key = _norm_key(it.new_path)

        # 已符合输出格式的文件保持原状态。
        # 无需登记到 seen：其自身路径必然已存在于磁盘，后续条目会被
        # os.path.exists 判为「目标已存在」，语义比「目标重名」更准确。
        if it.status == STATUS_DONE:
            continue

        if _norm_key(it.old_path) == key:
            it.status = STATUS_SAME
            it.note = it.note or "新旧文件名一致"
        elif key in seen:
            it.status = STATUS_CONFLICT
            it.note = f"与 {seen[key]} 重名"
        elif os.path.exists(it.new_path):
            it.status = STATUS_EXISTS
            it.note = "同名文件已存在于磁盘"
        else:
            it.status = STATUS_OK

        # 同名目标只认第一个，后续重复项标记为冲突
        seen.setdefault(key, it.old_rel)


def execute_plan(items: Sequence[PlanItem]) -> Tuple[List[dict], List[Tuple[PlanItem, str]]]:
    """执行已勾选且状态为「待重命名」的计划。

    返回 ``(成功记录, 失败列表)``，成功记录可直接写入撤销文件。
    """
    records: List[dict] = []
    failures: List[Tuple[PlanItem, str]] = []

    for it in items:
        if not it.selected or it.status != STATUS_OK:
            continue
        try:
            if os.path.exists(it.new_path) and _norm_key(it.new_path) != _norm_key(it.old_path):
                failures.append((it, "目标文件已存在，已跳过"))
                continue
            os.rename(it.old_path, it.new_path)
            records.append({"src": it.old_path, "dst": it.new_path})
        except OSError as exc:
            failures.append((it, str(exc)))

    return records, failures


def undo_records(records: Sequence[dict]) -> Tuple[List[dict], List[Tuple[str, str]]]:
    """按逆序还原一次重命名操作。"""
    restored: List[dict] = []
    failures: List[Tuple[str, str]] = []

    for rec in reversed(list(records)):
        src, dst = rec.get("src"), rec.get("dst")
        if not src or not dst:
            continue
        try:
            if not os.path.exists(dst):
                failures.append((dst, "文件已不存在，无法还原"))
                continue
            if os.path.exists(src):
                failures.append((src, "原文件名已被占用，已跳过"))
                continue
            os.rename(dst, src)
            restored.append(rec)
        except OSError as exc:
            failures.append((dst, str(exc)))

    return restored, failures


# --------------------------------------------------------------------------- #
# 撤销记录的持久化
# --------------------------------------------------------------------------- #

def history_dir() -> str:
    """返回撤销记录存放目录，优先放在脚本目录下。"""
    candidates = [os.path.join(_THIS_DIR, "rename_history")]
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            candidates.append(os.path.join(home, ".bangumi_rename", "history"))
    except Exception:
        pass

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return path
        except OSError:
            continue

    import tempfile
    fb = os.path.join(tempfile.gettempdir(), "bangumi_rename_history")
    os.makedirs(fb, exist_ok=True)
    return fb


def save_undo(records: Sequence[dict], directory: str) -> Optional[str]:
    """把本次重命名记录写入撤销文件，返回文件路径。"""
    if not records:
        return None
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "directory": directory,
        "count": len(records),
        "records": list(records),
    }
    filename = datetime.now().strftime("undo_%Y%m%d_%H%M%S.json")
    path = os.path.join(history_dir(), filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def latest_undo_file() -> Optional[str]:
    """返回最近一次撤销记录文件。"""
    folder = history_dir()
    try:
        entries = [
            os.path.join(folder, n)
            for n in os.listdir(folder)
            if n.startswith("undo_") and n.endswith(".json")
        ]
    except OSError:
        return None
    if not entries:
        return None
    return max(entries, key=os.path.getmtime)


def load_undo(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 界面层
# --------------------------------------------------------------------------- #

#: 设计令牌：统一在此调整整体配色
C_PAGE = "#f5f6f8"            # 页面底色
C_SURFACE = "#ffffff"         # 卡片 / 面板
C_BORDER = "#e7e9ee"          # 卡片描边
C_BORDER_SOFT = "#f0f2f5"     # 分隔线
C_BORDER_STRONG = "#d7dbe1"   # 输入框描边
C_TEXT = "#1f2328"            # 主文字
C_TEXT_2 = "#5b6470"          # 次级文字
C_MUTED = "#98a1ad"           # 弱化文字
C_ACCENT = "#2f6fe0"          # 主题色
C_ACCENT_DARK = "#2757ab"
C_ACCENT_SOFT = "#eef4fe"
C_DANGER = "#d93a3a"
C_DANGER_SOFT = "#fdf0f0"
C_WARN_BG = "#fdfaf2"
C_ROW_STRIPE = "#fafbfc"
C_ROW_HOVER = "#edf4fd"

#: 状态 -> 行外观（前景色, 偶数行背景, 奇数行背景）
ROW_STYLE: Dict[str, Tuple[str, str, str]] = {
    STATUS_OK: ("#1f7a5a", "#ffffff", C_ROW_STRIPE),
    STATUS_DONE: ("#0f6f74", "#ffffff", C_ROW_STRIPE),
    STATUS_SAME: ("#98a1ad", "#ffffff", C_ROW_STRIPE),
    STATUS_INVALID: ("#8a929e", "#ffffff", C_ROW_STRIPE),
    STATUS_CONFLICT: ("#b23a48", "#fdf6f6", "#fbf1f1"),
    STATUS_EXISTS: ("#9a6412", C_WARN_BG, "#fcf7ec"),
}

#: 状态圆点颜色（与 ROW_STYLE 前景保持一致）
DOT_COLORS: Dict[str, str] = {k: v[0] for k, v in ROW_STYLE.items()}


def _round_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    """生成圆角矩形的多边形顶点，配合 ``smooth=True`` 画圆角。"""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


def _make_dot(color: str, size: int = 11) -> tk.PhotoImage:
    """用像素画出一个实心圆点，避免引入 Pillow 依赖。"""
    img = tk.PhotoImage(width=size, height=size)
    c = (size - 1) / 2
    radius = c - 0.5
    for y in range(size):
        for x in range(size):
            if (x - c) ** 2 + (y - c) ** 2 <= radius ** 2:
                img.put(color, to=(x, y, x + 1, y + 1))
    return img


def _enable_dpi_awareness() -> None:
    """开启 Windows 高 DPI 感知，避免界面被系统缩放拉伸后发虚。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 系统级 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class Card(tk.Canvas):
    """圆角卡片容器。

    Canvas 负责绘制圆角背景与描边，内部嵌入一个普通 ``Frame`` 承载真实控件，
    从而在纯 tkinter 环境下获得圆角外观。
    """

    def __init__(
        self,
        master,
        *,
        radius: int = 12,
        fill: str = C_SURFACE,
        border: str = C_BORDER,
        padding: Tuple[int, int] = (18, 15),
        page_bg: str = C_PAGE,
        height: Optional[int] = None,
    ) -> None:
        options = {} if height is None else {"height": height}
        super().__init__(master, highlightthickness=0, bd=0, bg=page_bg, **options)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._pad_x, self._pad_y = padding
        self._shape: Optional[int] = None

        self.interior = tk.Frame(self, bg=fill)
        self._window = self.create_window(self._pad_x, self._pad_y,
                                          window=self.interior, anchor="nw")
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 2 or height <= 2:
            return
        if self._shape is not None:
            self.delete(self._shape)
        points = _round_points(1, 1, width - 1, height - 1, self._radius)
        self._shape = self.create_polygon(points, smooth=True, splinesteps=24,
                                          fill=self._fill, outline=self._border, width=1)
        self.tag_lower(self._shape)
        self.itemconfigure(self._window,
                           width=max(width - 2 * self._pad_x, 1),
                           height=max(height - 2 * self._pad_y, 1))
        self.coords(self._window, self._pad_x, self._pad_y)

    def fit_height(self) -> None:
        """按内部内容的实际需求高度设定卡片高度。"""
        self.interior.update_idletasks()
        self.configure(height=self.interior.winfo_reqheight() + 2 * self._pad_y)
        self._redraw()


class FlatButton(tk.Canvas):
    """圆角扁平按钮，支持悬停与按下反馈。

    风格：``primary``（主操作）/ ``secondary``（次操作）/ ``ghost``（弱化）/ ``danger``（危险操作）。
    """

    _VARIANTS: Dict[str, Tuple[str, str, str, str, str]] = {
        # (底色, 悬停色, 按下色, 文字色, 描边色)
        "primary": (C_ACCENT, C_ACCENT_DARK, "#22509e", "#ffffff", C_ACCENT),
        "secondary": ("#ffffff", "#f3f6fa", "#e9edf3", C_TEXT, C_BORDER_STRONG),
        "ghost": ("#ffffff", "#f5f7fa", "#eceff3", C_TEXT_2, C_BORDER),
        "danger": ("#ffffff", C_DANGER_SOFT, "#fbe1e1", C_DANGER, "#f0c9c9"),
    }

    def __init__(
        self,
        master,
        text: str = "",
        command=None,
        *,
        variant: str = "secondary",
        height: int = 34,
        radius: int = 9,
        page_bg: str = C_PAGE,
        font: Optional[tkfont.Font] = None,
        min_width: int = 74,
        pad_x: int = 17,
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, bg=page_bg,
                         height=height, cursor="hand2")
        self._variant = variant if variant in self._VARIANTS else "secondary"
        self._font = font or tkfont.Font(family="Microsoft YaHei UI", size=10)
        self._text = text
        self._command = command
        self._radius = radius
        self._height = height
        self._pad_x = pad_x
        self._min_width = min_width
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._width = max(min_width, self._font.measure(text) + 2 * pad_x)
        self.configure(width=self._width)

        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # ----------------------------------------------------------- 状态与外观
    def _colors(self) -> Tuple[str, str, str]:
        base, hover, press, fg, border = self._VARIANTS[self._variant]
        if not self._enabled:
            return "#f2f4f7", "#e3e7ed", "#b4bbc6"
        if self._pressed:
            return press, border, fg
        if self._hover:
            return hover, border, fg
        return base, border, fg

    def _draw(self, _event=None) -> None:
        width = self.winfo_width() if self.winfo_width() > 1 else self._width
        height = self.winfo_height() if self.winfo_height() > 1 else self._height
        fill, border, fg = self._colors()
        self.delete("all")
        self.create_polygon(_round_points(0.5, 0.5, width - 0.5, height - 0.5, self._radius),
                            smooth=True, splinesteps=20,
                            fill=fill, outline=border, width=1)
        self.create_text(width / 2, height / 2 + 0.5, text=self._text, fill=fg, font=self._font)

    def _relayout(self) -> None:
        self._width = max(self._min_width, self._font.measure(self._text) + 2 * self._pad_x)
        self.configure(width=self._width)
        self._draw()

    # ------------------------------------------------------------- 交互事件
    def _on_enter(self, _event) -> None:
        if self._enabled:
            self._hover = True
            self.configure(cursor="hand2")
            self._draw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event) -> None:
        if self._enabled:
            self._pressed = True
            self._draw()

    def _on_release(self, event) -> None:
        if not self._enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._draw()
        if was_pressed and inside and self._command:
            self._command()

    # ------------------------------------------------------------ 配置接口
    def configure(self, cnf=None, **kw):  # type: ignore[override]
        """兼容 ttk 风格调用：支持 ``state`` 与 ``text`` 两个语义化键。"""
        if "state" in kw:
            self._enabled = str(kw.pop("state")) != "disabled"
            self.configure(cursor="hand2" if self._enabled else "arrow")
            self._draw()
        if "text" in kw:
            self._text = kw.pop("text")
            self._relayout()
        if cnf or kw:
            super().configure(cnf, **kw)

    config = configure

    @property
    def enabled(self) -> bool:
        return self._enabled


class StepIndicator(tk.Canvas):
    """顶部流程指示器：选择目录 → 扫描预览 → 执行重命名。"""

    STEPS = ("选择目录", "扫描预览", "执行重命名")

    def __init__(self, master, page_bg: str = C_SURFACE,
                 font: Optional[tkfont.Font] = None) -> None:
        self._font = font or tkfont.Font(family="Microsoft YaHei UI", size=9)
        self._page_bg = page_bg
        self._current = 0
        self._pill_w = [self._font.measure(s) + 26 for s in self.STEPS]
        self._gap = 20
        total = sum(self._pill_w) + self._gap * (len(self.STEPS) - 1) + 2
        super().__init__(master, highlightthickness=0, bd=0, bg=page_bg,
                         height=26, width=int(total))
        self._draw()

    def set_step(self, index: int) -> None:
        self._current = max(0, min(index, len(self.STEPS) - 1))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        x = 1.0
        for i, name in enumerate(self.STEPS):
            width = self._pill_w[i]
            if i < self._current:
                fill, fg, border = C_ACCENT_SOFT, C_ACCENT, "#d3e2fb"
            elif i == self._current:
                fill, fg, border = C_ACCENT, "#ffffff", C_ACCENT
            else:
                fill, fg, border = "#f3f5f8", C_MUTED, "#eceff3"
            self.create_polygon(_round_points(x, 0.5, x + width, 25.5, 13),
                                smooth=True, splinesteps=18,
                                fill=fill, outline=border, width=1)
            mark = "✓ " if i < self._current else f"{i + 1} "
            self.create_text(x + width / 2, 13.5, text=mark + name,
                             fill=fg, font=self._font)
            x += width + self._gap
            if i < len(self.STEPS) - 1:
                self.create_text(x - self._gap / 2, 13.5, text="›",
                                 fill="#c6ccd4", font=self._font)


class RenameApp(tk.Tk):
    """番剧批量重命名工具主窗口。"""

    def __init__(self) -> None:
        _enable_dpi_awareness()
        super().__init__()

        self.title("番剧批量重命名工具 · 适配 Emby 刮削")

        # 依据真实 DPI 校准字号，避免高分屏下字体过小或模糊
        try:
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        except Exception:
            pass

        self._setup_window()
        self._init_fonts()
        self._init_state()
        self._setup_style()

        self._build_header()
        # 状态栏先于主体布局，确保 pack 时优先占据底部空间
        self._build_statusbar()
        self._build_body()

        self._bind_shortcuts()
        self.dir_var.set(_THIS_DIR)
        self._set_step(0)
        self.update_status()
        self.refresh_table()

        self.log("就绪。请选择番剧根目录，然后点击「① 扫描预览」。")
        self.log("输出格式：标题 - SXXEXX - 发布组.ext")
        self.log(f"支持的扩展名：{'、'.join(sorted(VIDEO_EXTENSIONS))}")

    # ------------------------------------------------------------------ 初始化
    def _setup_window(self) -> None:
        width, height = 1200, 800
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(980, 680)
        self.configure(bg=C_PAGE)

    def _init_fonts(self) -> None:
        available = set(tkfont.families())
        family = "Microsoft YaHei UI"
        for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                     "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
            if cand in available:
                family = cand
                break
        self.font_family = family

        self.f_title = tkfont.Font(family=family, size=15, weight="bold")
        self.f_sub = tkfont.Font(family=family, size=9)
        self.f_body = tkfont.Font(family=family, size=10)
        self.f_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(family=family, size=9)
        self.f_tiny = tkfont.Font(family=family, size=8)
        self.f_step = tkfont.Font(family=family, size=9)
        for mono in ("Consolas", "Cascadia Mono", "Courier New"):
            if mono in available:
                self.f_mono = tkfont.Font(family=mono, size=9)
                break
        else:
            self.f_mono = tkfont.Font(family=family, size=9)

    def _init_state(self) -> None:
        self.scan_result: Optional[ScanResult] = None
        self._iid_to_item: Dict[str, PlanItem] = {}
        self._log_lines = 0
        self._last_undo_path: Optional[str] = None
        self._hover_iid: Optional[str] = None
        self._dot_images: Dict[str, tk.PhotoImage] = {}

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=self.f_body, background=C_PAGE, foreground=C_TEXT)
        style.configure("TFrame", background=C_PAGE)
        style.configure("TLabel", background=C_PAGE, foreground=C_TEXT, font=self.f_body)

        # ---- 输入框：聚焦时描边转为主题色 ----
        for name in ("TEntry", "TSpinbox"):
            style.configure(name, fieldbackground="#ffffff", foreground=C_TEXT,
                            bordercolor=C_BORDER_STRONG, lightcolor=C_BORDER_STRONG,
                            darkcolor=C_BORDER_STRONG, insertcolor=C_TEXT,
                            padding=6, arrowcolor=C_TEXT_2)
            style.map(name,
                      bordercolor=[("focus", C_ACCENT), ("hover", "#c2c8d0")],
                      lightcolor=[("focus", C_ACCENT)],
                      darkcolor=[("focus", C_ACCENT)])

        # ---- 复选框：勾选后指示块为主题色 ----
        style.configure("TCheckbutton", background=C_SURFACE, foreground=C_TEXT_2,
                        font=self.f_body, focuscolor=C_SURFACE, padding=(0, 2))
        style.map("TCheckbutton",
                  background=[("active", C_SURFACE)],
                  foreground=[("active", C_TEXT)],
                  indicatorcolor=[("selected", C_ACCENT), ("!selected", "#ffffff")])

        # ---- 表格：去边框、加行高，表头更轻 ----
        style.configure("Treeview", background=C_SURFACE, fieldbackground=C_SURFACE,
                        foreground=C_TEXT, rowheight=32, font=self.f_body,
                        borderwidth=0, relief="flat")
        style.configure("Treeview.Heading", background=C_SURFACE, foreground=C_MUTED,
                        font=self.f_small, relief="flat", padding=(8, 10), borderwidth=0)
        style.map("Treeview.Heading", background=[("active", "#f6f8fa")],
                  foreground=[("active", C_TEXT_2)])
        style.map("Treeview", background=[("selected", C_ROW_HOVER)],
                  foreground=[("selected", C_TEXT)])
        try:
            # 去掉 Treeview 外围边框，并消除根节点的缩进
            style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
            style.configure("Treeview", indent=0)
        except tk.TclError:
            pass

        # ---- 滚动条：细窄、低对比 ----
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar", background="#d5dae1",
                            troughcolor=C_SURFACE, bordercolor=C_SURFACE,
                            arrowcolor=C_MUTED, darkcolor="#d5dae1",
                            lightcolor="#d5dae1", gripcount=0, relief="flat")
            style.map(f"{orient}.TScrollbar", background=[("active", "#bcc4ce")])

    # ------------------------------------------------------------------ 头部
    def _build_header(self) -> None:
        header = tk.Frame(self, bg=C_SURFACE)
        header.pack(fill="x", side="top")

        inner = tk.Frame(header, bg=C_SURFACE)
        inner.pack(fill="x", padx=22, pady=14)

        logo = tk.Canvas(inner, width=38, height=38, bg=C_SURFACE, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        logo.create_polygon(_round_points(0.5, 0.5, 37.5, 37.5, 11), smooth=True,
                            splinesteps=20, fill=C_ACCENT, outline=C_ACCENT)
        logo.create_polygon([15, 12, 15, 26, 27, 19], fill="#ffffff", outline="")

        titles = tk.Frame(inner, bg=C_SURFACE)
        titles.pack(side="left")
        tk.Label(titles, text="番剧批量重命名工具", bg=C_SURFACE, fg=C_TEXT,
                 font=self.f_title).pack(anchor="w")
        tk.Label(titles,
                 text="统一输出「标题 - SXXEXX - 发布组.ext」，自动剔除分辨率 / 编码等信息 · "
                      "执行前完整预览 · 支持一键撤销",
                 bg=C_SURFACE, fg=C_MUTED, font=self.f_sub).pack(anchor="w", pady=(3, 0))

        self.steps = StepIndicator(inner, font=self.f_step)
        self.steps.pack(side="right")

        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", side="top")

    # ------------------------------------------------------------------ 主体
    def _build_body(self) -> None:
        body = tk.Frame(self, bg=C_PAGE)
        body.pack(fill="both", expand=True, padx=18, pady=(14, 10))

        self._build_setup_card(body)
        self._build_action_row(body)
        self._build_table_card(body)
        self._build_log_card(body)

    def _build_setup_card(self, parent) -> None:
        card = Card(parent, padding=(18, 15))
        card.pack(fill="x")

        # ---- 第一行：目录选择 ----
        row1 = tk.Frame(card.interior, bg=C_SURFACE)
        row1.pack(fill="x")
        tk.Label(row1, text="番剧目录", bg=C_SURFACE, fg=C_TEXT_2,
                 font=self.f_small).pack(side="left", padx=(0, 10))

        self.dir_var = tk.StringVar()
        self.dir_entry = ttk.Entry(row1, textvariable=self.dir_var, font=self.f_body)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        FlatButton(row1, "浏览…", self.choose_directory, variant="secondary",
                   page_bg=C_SURFACE, font=self.f_body).pack(side="left", padx=(0, 6))
        FlatButton(row1, "打开目录", self.open_directory, variant="ghost",
                   page_bg=C_SURFACE, font=self.f_body).pack(side="left", padx=(0, 6))
        FlatButton(row1, "脚本目录", lambda: self.dir_var.set(_THIS_DIR), variant="ghost",
                   page_bg=C_SURFACE, font=self.f_body).pack(side="left")

        tk.Frame(card.interior, bg=C_BORDER_SOFT, height=1).pack(fill="x", pady=13)

        # ---- 第二行：处理选项 ----
        row2 = tk.Frame(card.interior, bg=C_SURFACE)
        row2.pack(fill="x")
        tk.Label(row2, text="默认季数", bg=C_SURFACE, fg=C_TEXT_2,
                 font=self.f_small).pack(side="left", padx=(0, 8))

        self.season_var = tk.StringVar(value=str(core.DEFAULT_SEASON))
        ttk.Spinbox(row2, from_=1, to=99, width=4, textvariable=self.season_var,
                    font=self.f_body).pack(side="left")

        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="递归子目录", variable=self.recursive_var,
                        cursor="hand2").pack(side="left", padx=(22, 0))

        self.subtitle_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="同步处理字幕", variable=self.subtitle_var,
                        cursor="hand2").pack(side="left", padx=(18, 0))

        tk.Frame(row2, bg=C_BORDER_SOFT, width=1).pack(side="left", fill="y", padx=18, pady=2)

        self.only_ok_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="仅显示待重命名", variable=self.only_ok_var,
                        command=self.refresh_table, cursor="hand2").pack(side="left")

        self.only_checked_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="仅显示已勾选", variable=self.only_checked_var,
                        command=self.refresh_table, cursor="hand2").pack(side="left", padx=(18, 0))

        card.fit_height()

    def _build_action_row(self, parent) -> None:
        actions = tk.Frame(parent, bg=C_PAGE)
        actions.pack(fill="x", pady=(12, 0))

        self.scan_btn = FlatButton(actions, "① 扫描预览", self.do_scan, variant="primary",
                                   page_bg=C_PAGE, font=self.f_bold)
        self.scan_btn.pack(side="left")

        for text, cmd in (("全选", lambda: self.set_all_selected(True)),
                          ("全不选", lambda: self.set_all_selected(False)),
                          ("反选", self.invert_selection),
                          ("导出预览 CSV", self.export_csv)):
            FlatButton(actions, text, cmd, variant="ghost", page_bg=C_PAGE,
                       font=self.f_body).pack(side="left", padx=(8, 0))

        self.exec_btn = FlatButton(actions, "② 执行重命名", self.do_execute, variant="primary",
                                   page_bg=C_PAGE, font=self.f_bold)
        self.exec_btn.pack(side="right")

        self.undo_btn = FlatButton(actions, "撤销上次", self.do_undo, variant="danger",
                                   page_bg=C_PAGE, font=self.f_body)
        self.undo_btn.pack(side="right", padx=(0, 8))

    def _build_table_card(self, parent) -> None:
        # 显式给一个较小的请求高度：卡片虽由 expand 撑开，但窗口缩小时需要能够收缩，
        # 否则 Canvas 的默认请求高度会顶住布局，导致底部日志区被裁切。
        self.table_card = Card(parent, padding=(10, 10), height=180)
        self.table_card.pack(fill="both", expand=True, pady=(12, 0))
        host = self.table_card.interior

        columns = ("sel", "status", "old", "new", "dir")
        self.tree = ttk.Treeview(host, columns=columns, show="tree headings",
                                 selectmode="none")

        self.tree.heading("#0", text="")
        self.tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")
        headings = {
            "sel": ("勾选", 52, "center"),
            "status": ("状态", 98, "center"),
            "old": ("原文件名", 400, "w"),
            "new": ("重命名后", 410, "w"),
            "dir": ("所在子目录", 150, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=60, anchor=anchor,
                             stretch=(key in ("old", "new")))

        vsb = ttk.Scrollbar(host, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(host, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)

        # 状态圆点：用像素绘制，避免额外依赖
        for status, color in DOT_COLORS.items():
            self._dot_images[status] = _make_dot(color)

        for status, (fg, bg_even, bg_odd) in ROW_STYLE.items():
            self.tree.tag_configure(status, foreground=fg, background=bg_even)
            self.tree.tag_configure(f"{status}_zebra", foreground=fg, background=bg_odd)
        self.tree.tag_configure("hover", background=C_ROW_HOVER)

        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<space>", lambda _e: self.toggle_focused())
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda _e: self._clear_hover())

        self.menu = tk.Menu(self, tearoff=0, bg=C_SURFACE, fg=C_TEXT,
                            activebackground=C_ACCENT_SOFT, activeforeground=C_ACCENT,
                            bd=0, relief="flat", font=self.f_body,
                            activeborderwidth=0)
        self.menu.add_command(label="编辑重命名后文件名…", command=self.edit_selected_row)
        self.menu.add_command(label="打开文件所在目录", command=self.reveal_selected_row)

        # 空状态提示：覆盖在表格之上，扫描出结果后隐藏
        self.empty = tk.Frame(host, bg=C_SURFACE)
        self.empty_title = tk.Label(self.empty, text="尚未扫描", bg=C_SURFACE,
                                    fg=C_TEXT_2, font=self.f_bold)
        self.empty_title.pack()
        self.empty_hint = tk.Label(self.empty, text="", bg=C_SURFACE, fg=C_MUTED,
                                   font=self.f_small)
        self.empty_hint.pack(pady=(6, 0))

        tk.Label(host, text="单击任意行切换勾选 · 右键可手动修改重命名结果 · "
                            "快捷键 Ctrl+O 选目录 / F5 扫描 / Ctrl+Enter 执行",
                 bg=C_SURFACE, fg=C_MUTED, font=self.f_tiny).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_log_card(self, parent) -> None:
        card = Card(parent, padding=(18, 12))
        card.pack(fill="x", pady=(12, 0))

        head = tk.Frame(card.interior, bg=C_SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="运行日志", bg=C_SURFACE, fg=C_TEXT_2,
                 font=self.f_bold).pack(side="left")
        FlatButton(head, "清空", self.clear_log, variant="ghost", height=26,
                   page_bg=C_SURFACE, font=self.f_small, min_width=58,
                   pad_x=12).pack(side="right")

        box = tk.Frame(card.interior, bg=C_BORDER_SOFT)
        box.pack(fill="x", pady=(9, 0))
        self.log_text = tk.Text(box, height=6, wrap="none", bg="#fcfdfe", fg=C_TEXT_2,
                                font=self.f_mono, relief="flat", bd=0,
                                padx=12, pady=8, insertbackground=C_TEXT,
                                selectbackground=C_ACCENT_SOFT,
                                highlightthickness=0)
        log_vsb = ttk.Scrollbar(box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        log_vsb.pack(side="right", fill="y", pady=1, padx=(0, 1))

        self.log_text.tag_configure("warn", foreground="#9a6412")
        self.log_text.tag_configure("error", foreground=C_DANGER)
        self.log_text.tag_configure("success", foreground="#1f7a5a")

        card.fit_height()

    # ---------------------------------------------------------------- 状态栏
    def _build_statusbar(self) -> None:
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=C_SURFACE)
        bar.pack(fill="x", side="bottom")

        self.chips = tk.Frame(bar, bg=C_SURFACE)
        self.chips.pack(side="left", padx=18, pady=9)

        self.status_var = tk.StringVar(value="尚未扫描")
        self.status_tail = tk.Label(bar, text="", bg=C_SURFACE, fg=C_MUTED,
                                    font=self.f_small)
        self.status_tail.pack(side="right", padx=18)

    def _render_chips(self, entries: Sequence[Tuple[str, str, str]]) -> None:
        for child in self.chips.winfo_children():
            child.destroy()
        for text, dot_color, text_color in entries:
            item = tk.Frame(self.chips, bg=C_SURFACE)
            item.pack(side="left", padx=(0, 15))
            tk.Label(item, text="●", bg=C_SURFACE, fg=dot_color,
                     font=self.f_tiny).pack(side="left", padx=(0, 4))
            tk.Label(item, text=text, bg=C_SURFACE, fg=text_color,
                     font=self.f_small).pack(side="left")

    # ------------------------------------------------------------------ 日志
    def log(self, message: str, tag: str = "") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", tag or ())
        # 限制日志行数，避免长时间运行占用过多内存
        self._log_lines += 1
        if self._log_lines > 400:
            self.log_text.delete("1.0", "100.0")
            self._log_lines -= 99
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_lines = 0

    def _set_step(self, index: int) -> None:
        if hasattr(self, "steps"):
            self.steps.set_step(index)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _e: self.choose_directory())
        self.bind("<F5>", lambda _e: self.do_scan())
        self.bind("<Control-Return>", lambda _e: self.do_execute())

    # -------------------------------------------------------------- 目录操作
    def choose_directory(self) -> None:
        initial = self.dir_var.get().strip() or _THIS_DIR
        if not os.path.isdir(initial):
            initial = _THIS_DIR
        chosen = filedialog.askdirectory(title="选择番剧根目录", initialdir=initial)
        if chosen:
            self.dir_var.set(os.path.normpath(chosen))
            self.log(f"已选择目录：{self.dir_var.get()}")
            self._set_step(1)

    def open_directory(self) -> None:
        path = self.dir_var.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showwarning("提示", "目录不存在或不可访问。")
            return
        try:
            self._open_path(path)
        except Exception as exc:
            messagebox.showerror("无法打开", str(exc))

    @staticmethod
    def _open_path(path: str) -> None:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    # ------------------------------------------------------------------ 扫描
    def do_scan(self) -> None:
        directory = self.dir_var.get().strip()
        try:
            season = int(self.season_var.get())
            if season < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("参数有误", "默认季数必须是大于 0 的整数。")
            return

        self.scan_btn.configure(state="disabled")
        self.update_idletasks()
        self.log("─" * 64)
        self.log(f"开始扫描：{directory}")
        try:
            result = scan_directory(
                directory,
                recursive=self.recursive_var.get(),
                default_season=season,
                include_subtitles=self.subtitle_var.get(),
            )
        except ValueError as exc:
            self.log(f"扫描失败：{exc}", "error")
            messagebox.showerror("扫描失败", str(exc))
            self.scan_btn.configure(state="normal")
            return
        except Exception:
            self.log("扫描出现未预期的错误：\n" + traceback.format_exc(), "error")
            messagebox.showerror("扫描失败", "发生未预期的错误，详见日志。")
            self.scan_btn.configure(state="normal")
            return
        finally:
            self.scan_btn.configure(state="normal")

        self.scan_result = result
        self.refresh_table()

        self.log(f"扫描完成，命中可识别文件 {len(result.items)} 个；"
                 f"跳过非目标格式 {result.ignored_count} 个；"
                 f"无法识别 {len(result.unparsed)} 个。")
        if result.count(STATUS_DONE):
            self.log(f"其中 {result.count(STATUS_DONE)} 个已符合目标格式，无需重复处理。")
        if result.count(STATUS_CONFLICT) or result.count(STATUS_EXISTS):
            self.log(f"检测到 {result.count(STATUS_CONFLICT)} 个目标重名、"
                     f"{result.count(STATUS_EXISTS)} 个目标已存在，这些条目不会被自动执行。", "warn")
        if result.unparsed:
            preview = "、".join(result.unparsed[:5])
            more = f" 等 {len(result.unparsed)} 个" if len(result.unparsed) > 5 else ""
            self.log(f"无法识别的文件：{preview}{more}", "warn")
        self.log("请在表格中确认结果，勾选需要处理的条目后点击「② 执行重命名」。")
        self._set_step(2)

    # ------------------------------------------------------------------ 表格
    def refresh_table(self) -> None:
        self._hover_iid = None
        self.tree.delete(*self.tree.get_children())
        self._iid_to_item.clear()

        if self.scan_result:
            only_ok = self.only_ok_var.get()
            only_checked = self.only_checked_var.get()
            for idx, item in enumerate(self.scan_result.items):
                if only_ok and item.status != STATUS_OK:
                    continue
                if only_checked and not item.selected:
                    continue
                iid = f"row{idx}"
                zebra = f"{item.status}_zebra" if idx % 2 else item.status
                self.tree.insert(
                    "", "end", iid=iid,
                    image=self._dot_images.get(item.status, ""),
                    values=("☑" if item.selected else "☐",
                            STATUS_META[item.status][0],
                            item.old_rel,
                            item.new_rel,
                            item.sub_dir),
                    tags=(zebra,),
                )
                self._iid_to_item[iid] = item

        self._update_empty_state()
        self.update_status()

    def _update_empty_state(self) -> None:
        rows = self.tree.get_children()
        if rows:
            self.empty.place_forget()
            return
        if self.scan_result is None:
            self.empty_title.configure(text="尚未扫描")
            self.empty_hint.configure(
                text="选择番剧根目录后点击「① 扫描预览」，这里会列出全部重命名方案")
        elif self.scan_result.items:
            self.empty_title.configure(text="没有符合筛选条件的条目")
            self.empty_hint.configure(text="取消「仅显示…」筛选即可看到全部结果")
        else:
            self.empty_title.configure(text="未找到可识别的番剧文件")
            self.empty_hint.configure(
                text="请确认目录是否正确，或文件名是否包含方括号发布组与集数信息")
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def update_status(self) -> None:
        """更新状态栏。

        界面以彩色分组呈现；``status_var`` 同步保存一份纯文本摘要，
        供程序化调用与测试断言使用。
        """
        if not self.scan_result:
            self.status_var.set("尚未扫描")
            self._render_chips([("尚未扫描", C_MUTED, C_MUTED)])
            self.status_tail.configure(text="")
            return

        r = self.scan_result
        checked = sum(1 for it in r.items if it.selected)

        entries: List[Tuple[str, str, str]] = []
        for status in (STATUS_OK, STATUS_DONE, STATUS_CONFLICT,
                       STATUS_EXISTS, STATUS_SAME):
            count = r.count(status)
            if count:
                entries.append((f"{STATUS_META[status][0]} {count}",
                                DOT_COLORS[status], C_TEXT_2))
        if r.unparsed:
            entries.append((f"无法识别 {len(r.unparsed)}", C_MUTED, C_MUTED))
        if not entries:
            entries.append(("无可处理条目", C_MUTED, C_MUTED))
        self._render_chips(entries)

        self.status_tail.configure(text=f"共 {len(r.items)} 项 · 已勾选 {checked}")
        self.status_var.set(
            f"共 {len(r.items)} 项 ｜ 待重命名 {r.count(STATUS_OK)} ｜ "
            f"已是目标格式 {r.count(STATUS_DONE)} ｜ 目标重名 {r.count(STATUS_CONFLICT)} ｜ "
            f"目标已存在 {r.count(STATUS_EXISTS)} ｜ 无需修改 {r.count(STATUS_SAME)} ｜ "
            f"无法识别 {len(r.unparsed)} ｜ 已勾选 {checked}"
        )

    # -------------------------------------------------------------- 行交互
    def _clear_hover(self) -> None:
        if self._hover_iid:
            try:
                tags = [t for t in self.tree.item(self._hover_iid, "tags") if t != "hover"]
                self.tree.item(self._hover_iid, tags=tags)
            except tk.TclError:
                pass
            self._hover_iid = None

    def _on_tree_motion(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid == self._hover_iid:
            return
        self._clear_hover()
        if iid:
            try:
                tags = list(self.tree.item(iid, "tags"))
                tags.append("hover")  # 置于末尾以覆盖斑马纹背景
                self.tree.item(iid, tags=tags)
            except tk.TclError:
                return
            self._hover_iid = iid

    def on_tree_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.toggle_row(iid)

    def toggle_row(self, iid: str) -> None:
        item = self._iid_to_item.get(iid)
        if item is None:
            return
        item.selected = not item.selected
        self.tree.set(iid, "sel", "☑" if item.selected else "☐")
        # 目标重名 / 已存在 / 无法识别的条目不可执行，勾选后给出提示
        if item.selected and item.status != STATUS_OK:
            self.log(f"提示：「{item.old_rel}」当前状态为{STATUS_META[item.status][0]}，"
                     f"执行阶段将自动跳过。{item.note}", "warn")
        self.update_status()

    def toggle_focused(self) -> None:
        iid = self.tree.focus()
        if iid:
            self.toggle_row(iid)

    def set_all_selected(self, value: bool) -> None:
        if not self.scan_result:
            return
        visible = set(self.tree.get_children())
        for iid, item in self._iid_to_item.items():
            if iid in visible:
                item.selected = value
                self.tree.set(iid, "sel", "☑" if value else "☐")
        self.update_status()

    def invert_selection(self) -> None:
        if not self.scan_result:
            return
        for iid, item in self._iid_to_item.items():
            item.selected = not item.selected
            self.tree.set(iid, "sel", "☑" if item.selected else "☐")
        self.update_status()

    def on_tree_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _focused_item(self) -> Optional[PlanItem]:
        iid = self.tree.focus()
        return self._iid_to_item.get(iid) if iid else None

    def edit_selected_row(self) -> None:
        from tkinter import simpledialog

        item = self._focused_item()
        if item is None:
            messagebox.showinfo("提示", "请先选中一行。")
            return

        current = os.path.basename(item.new_path)
        new_name = simpledialog.askstring(
            "编辑重命名结果",
            f"原文件：{item.old_rel}\n\n请输入新的文件名（含扩展名）：",
            initialvalue=current, parent=self,
        )
        if not new_name or new_name == current:
            return

        new_name = new_name.strip()
        stem, ext = os.path.splitext(new_name)
        if not stem or _ILLEGAL_CHARS.search(new_name):
            messagebox.showwarning("名称无效", "文件名不能为空，且不能包含 \\ / : * ? \" < > | 等字符。")
            return
        if not ext:
            new_name += os.path.splitext(item.old_path)[1]

        item.new_path = os.path.join(os.path.dirname(item.old_path), new_name)
        item.new_rel = os.path.relpath(item.new_path, self.scan_result.directory)
        item.note = "已手动指定"
        _assign_status(self.scan_result.items)
        # 手动编辑表达了处理意图，但仅在结果可执行时才勾选
        item.selected = item.status == STATUS_OK
        self.refresh_table()
        self.log(f"已手动修改：{item.old_rel} → {os.path.basename(item.new_path)}")

    def reveal_selected_row(self) -> None:
        item = self._focused_item()
        if item is None:
            return
        try:
            self._open_path(os.path.dirname(item.old_path))
        except Exception as exc:
            messagebox.showerror("无法打开", str(exc))

    # ------------------------------------------------------------------ 执行
    def do_execute(self) -> None:
        if not self.scan_result:
            messagebox.showinfo("提示", "请先执行扫描预览。")
            return

        # 每次执行前重新校验状态，避免文件被外部程序改动后产生误判
        _assign_status(self.scan_result.items)
        for item in self.scan_result.items:
            if item.status != STATUS_OK:
                item.selected = False
        self.refresh_table()

        targets = self.scan_result.actionable
        if not targets:
            messagebox.showinfo("无可执行项", "当前没有处于「待重命名」状态且已勾选的文件。")
            return

        confirmed = messagebox.askyesno(
            "确认执行",
            f"即将重命名 {len(targets)} 个文件。\n\n"
            "程序仅修改文件名，不会改动文件内容与所在目录。\n"
            "操作记录会被保存，可通过「撤销上次」一键还原。\n\n"
            "是否继续？",
            default="no", icon="warning",
        )
        if not confirmed:
            self.log("用户取消了本次重命名。")
            return

        self.exec_btn.configure(state="disabled")
        self.update_idletasks()
        try:
            records, failures = execute_plan(self.scan_result.items)
            self.log("─" * 64)
            self.log(f"执行完成：成功 {len(records)} 个，失败 {len(failures)} 个。",
                     "success" if records and not failures else ("warn" if failures else ""))
            for item, reason in failures[:20]:
                self.log(f"  跳过 {item.old_rel} —— {reason}", "warn")
            if len(failures) > 20:
                self.log(f"  …… 其余 {len(failures) - 20} 条失败记录已省略。", "warn")

            undo_path = save_undo(records, self.scan_result.directory)
            if undo_path:
                self._last_undo_path = undo_path
                self.log(f"撤销记录已保存：{undo_path}")
        finally:
            self.exec_btn.configure(state="normal")

        self.do_scan()

    # ------------------------------------------------------------------ 撤销
    def do_undo(self) -> None:
        path = self._last_undo_path
        if not path or not os.path.exists(path):
            path = latest_undo_file()
        if not path:
            messagebox.showinfo("无可撤销记录", "未找到任何重命名记录。")
            return

        try:
            payload = load_undo(path)
        except Exception as exc:
            messagebox.showerror("读取失败", f"无法读取撤销记录：\n{exc}")
            return

        records = payload.get("records", [])
        if not records:
            messagebox.showinfo("无可撤销记录", "该记录文件为空。")
            return

        if not messagebox.askyesno(
            "确认撤销",
            f"将还原 {len(records)} 个文件名。\n\n"
            f"记录时间：{payload.get('time', '未知')}\n"
            f"处理目录：{payload.get('directory', '未知')}\n\n"
            "若原文件名已被其他文件占用，该条目会被自动跳过。\n确认撤销？",
            default="no", icon="warning",
        ):
            self.log("用户取消了撤销操作。")
            return

        restored, failures = undo_records(records)
        self.log("─" * 64)
        self.log(f"撤销完成：还原 {len(restored)} 个，失败 {len(failures)} 个。",
                 "success" if restored and not failures else "warn")
        for target, reason in failures[:20]:
            self.log(f"  跳过 {target} —— {reason}", "warn")

        self._last_undo_path = None
        if self.scan_result:
            self.do_scan()

    # ------------------------------------------------------------------ 导出
    def export_csv(self) -> None:
        if not self.scan_result or not self.scan_result.items:
            messagebox.showinfo("提示", "暂无可导出的预览结果，请先扫描。")
            return

        default_name = f"重命名预览_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="导出预览结果",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            # utf-8-sig 保证 Excel 打开中文不乱码
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["状态", "勾选", "原文件名", "重命名后", "所在子目录", "备注"])
                for it in self.scan_result.items:
                    writer.writerow([
                        STATUS_META[it.status][0],
                        "是" if it.selected else "否",
                        it.old_rel, it.new_rel, it.sub_dir, it.note,
                    ])
            self.log(f"预览结果已导出：{path}", "success")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))

    # ------------------------------------------------------------------ 关闭
    def on_close(self) -> None:
        if self.scan_result and any(it.selected for it in self.scan_result.items):
            if not messagebox.askyesno("退出确认",
                                       "存在已勾选但尚未执行的条目，确认退出？",
                                       default="no"):
                return
        self.destroy()


def run_selftest(report_path: Optional[str] = None) -> int:
    """无界面自检：在临时目录跑一遍扫描 / 重命名 / 撤销，用于验证打包结果。

    参数
    ----
    report_path
        指定后把自检报告写入该文件。``--windowed`` 打包后没有控制台，
        因此把结果落到文件是唯一可靠的反馈渠道。

    返回
    ----
    进程退出码，0 表示全部通过。
    """
    import shutil
    import tempfile

    lines: List[str] = []
    failures: List[str] = []

    def record(label: str, got, want) -> None:
        ok = got == want
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {label}: 期望 {want!r}，实际 {got!r}"
                     if not ok else f"[PASS] {label}: {got}")
        if not ok:
            failures.append(label)

    lines.append(f"自检开始 · 打包模式={IS_FROZEN} · 资源根目录={_THIS_DIR}")
    lines.append(f"Python {sys.version.split()[0]} · Tk {tk.TkVersion}")

    sandbox = tempfile.mkdtemp(prefix="bangumi_selftest_")
    try:
        samples = {
            "[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4":
                "Yuusha no Kuzu - S01E01 - orion origin.mp4",
            "[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4":
                "Tensei Shitara Slime Datta Ken - S04E02 - BeanSub.mp4",
            "[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4":
                "番剧名 - S01E05 - 字幕组.mp4",
            "[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv":
                "LV999 no Murabito - S01E02 - smzase.mkv",
        }
        for name in samples:
            with open(os.path.join(sandbox, name), "w", encoding="utf-8") as fh:
                fh.write("x")

        lines.append("— 核心解析")
        result = scan_directory(sandbox, recursive=True, default_season=1,
                                include_subtitles=True)
        record("可执行条目数", len(result.actionable), len(samples))
        mapping = {it.old_rel: it.new_rel for it in result.items}
        for old, want in samples.items():
            record(f"解析 {old}", mapping.get(old), want)

        lines.append("— 执行重命名")
        records, errs = execute_plan(result.items)
        record("成功重命名数", len(records), len(samples))
        record("失败数", len(errs), 0)
        on_disk = set(os.listdir(sandbox))
        for want in samples.values():
            record(f"磁盘存在 {want}", want in on_disk, True)

        lines.append("— 撤销还原")
        undo_path = save_undo(records, sandbox)
        record("撤销记录路径非空", bool(undo_path), True)
        record("撤销记录可读", os.path.exists(undo_path or ""), True)
        restored, undo_errs = undo_records(load_undo(undo_path)["records"])
        record("还原数", len(restored), len(samples))
        record("还原失败数", len(undo_errs), 0)
        back = set(os.listdir(sandbox))
        for old in samples:
            record(f"已还原 {old}", old in back, True)

        lines.append("— 撤销记录目录")
        lines.append(f"history_dir = {history_dir()}")
        record("撤销记录目录可写", os.path.isdir(history_dir()), True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    lines.append("=" * 52)
    lines.append(f"自检结果：{'全部通过' if not failures else f'{len(failures)} 项未通过 {failures}'}")
    report = "\n".join(lines)

    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report)
    try:
        print(report)  # --windowed 打包后无控制台，此处静默失败即可
    except Exception:
        pass

    return 1 if failures else 0


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        report = argv[1] if len(argv) > 1 else None
        raise SystemExit(run_selftest(report))

    app = RenameApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
