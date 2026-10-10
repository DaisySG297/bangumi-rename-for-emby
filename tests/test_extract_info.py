# -*- coding: utf-8 -*-
"""解析引擎单元测试：``extract_info`` 全量命名规则回归。

只依赖标准库，无需 tkinter，可无人值守执行::

    python tests/test_extract_info.py

覆盖五类命名：
1. ``[发布组] 标题 [集数]``            方括号集数
2. ``[发布组] 标题 - S01E02``          横杠 + SxxExx
3. ``[发布组] 标题 S01E01``            空格 + SxxExx
4. ``[发布组] 标题 - 05v2``            横杠集数 + 版本号
5. ``标题.S01E02.1080p...MSubs-发布组`` 外站英文点分(scene)名
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from bangumi_rename import build_filename, extract_info  # noqa: E402

# (文件名, 期望的 (发布组, 标题, 季数, 集数, 扩展名)；None 表示应识别失败)
CASES = [
    # ---- 1. 方括号集数 ----
    ("[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4",
     ("orion origin", "Yuusha no Kuzu", 1, "01", ".mp4")),
    ("[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4",
     ("BeanSub", "Tensei Shitara Slime Datta Ken", 4, "02", ".mp4")),
    # ---- 2./3. 横杠 / 空格 + SxxExx ----
    ("[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv",
     ("smzase", "LV999 no Murabito", 1, "02", ".mkv")),
    ("[Nix-Raws] Reiwa no Dara-san S01E01 [CR WEB-DL 1080p][SC_TC].mp4",
     ("Nix-Raws", "Reiwa no Dara-san", 1, "01", ".mp4")),
    # ---- 4. 横杠集数 + 版本号 ----
    ("[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4",
     ("字幕组", "番剧名", 1, "05", ".mp4")),
    # ---- 5. 外站英文点分(scene)发布名 ----
    ("Now.That.I.Can.Control.Reality.With.A.Mouse.Cursor.Im.Gonna.Click.Away.On.The.Girls"
     ".S01E01.1080p.UNCENSORED.ADN.WEB-DL.DUAL.AAC2.0.H.264.MSubs-ToonsHub.mkv",
     ("ToonsHub",
      "Now That I Can Control Reality With A Mouse Cursor Im Gonna Click Away On The Girls",
      1, "01", ".mkv")),
    ("JoJos.Bizarre.Adventure.S06E04.The.Devils.Palm.1080p.NF.WEB-DL.DUAL.AAC2.0"
     ".H.264.MSubs-ToonsHub.mkv",
     ("ToonsHub", "JoJos Bizarre Adventure", 6, "04", ".mkv")),
    ("The.Detective.Is.Already.Dead.S02E01.To.See.You.Once.More.1080p.CR.WEB-DL.JPN"
     ".AAC2.0.H.264.MSubs-ToonsHub.mkv",
     ("ToonsHub", "The Detective Is Already Dead", 2, "01", ".mkv")),
    ("Tokyo.Revengers.S04E02.Be.Left.Behind.the.Times.1080p.DSNP.WEB-DL.DUAL.AAC2.0"
     ".H.264.MSubs-ToonsHub.mkv",
     ("ToonsHub", "Tokyo Revengers", 4, "02", ".mkv")),
    # 带横杠的组名前缀是编码/封装标记时剥掉，只留真正的组名
    ("Show.Name.S01E05.1080p.BluRay-Group.mkv",
     ("Group", "Show Name", 1, "05", ".mkv")),
    ("Show.Name.S01E05.1080p.WEB-DL.264-Group.mkv",
     ("Group", "Show Name", 1, "05", ".mkv")),
    # 带横杠的组名（前缀不是技法标记）必须原样保留
    ("Reiwa.no.Dara-san.S01E01.CR.WEB-DL.1080p.Erai-raws.mkv",
     ("Erai-raws", "Reiwa no Dara-san", 1, "01", ".mkv")),
    ("Frame.Arms.Girl.S01E03.1080p.WEB-DL.H.264.Nix-Raws.mkv",
     ("Nix-Raws", "Frame Arms Girl", 1, "03", ".mkv")),
    ("CLANNAD.AFTER.STORY.S02E03.1080p.BDRip.DBD-Raws.mkv",
     ("DBD-Raws", "CLANNAD AFTER STORY", 2, "03", ".mkv")),
    # 末段是冗余标签 -> 无发布组，输出省略末段
    ("Show.Name.E07.1080p.WEB-DL.x264.mkv",
     ("", "Show Name", 1, "07", ".mkv")),
    ("Show.Name.S02E13.2160p.WEB-DL.DDP5.1.H.265.mkv",
     ("", "Show Name", 2, "13", ".mkv")),
    # 字幕文件同样适用
    ("Show.Name.S01E05.1080p.WEB-DL.chs.ass",
     ("", "Show Name", 1, "05", ".ass")),
    # ---- 反例：不应被点分名规则误吞 ----
    ("Some.Random.Movie.2024.1080p.BluRay.mkv", None),      # 无集号信号
    ("random_video.mp4", None),                             # 无点分、无方括号
    ("cover.jpg", None),                                    # 扩展名不支持
    ("Title.2024.1080p.WEB-DL.H.264.mkv", None),            # 点分但无集号
]

failures = []


def check(label, got, want):
    if got == want:
        print(f"  [PASS] {label}: {got}")
    else:
        print(f"  [FAIL] {label}: 期望 {want!r}，实际 {got!r}")
        failures.append(label)


def main():
    print("== extract_info 解析 ==")
    for filename, want in CASES:
        check(filename, extract_info(filename), want)

    print("== build_filename 拼接 ==")
    check("有发布组", build_filename("ToonsHub", "Show Name", 1, "5", ".mkv"),
          "Show Name - S01E05 - ToonsHub.mkv")
    check("无发布组", build_filename("", "Show Name", 1, "5", ".mkv"),
          "Show Name - S01E05.mkv")
    check("季/集补零", build_filename("A", "T", 12, "7", ".mp4"),
          "T - S12E07 - A.mp4")

    print("-" * 60)
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        sys.exit(1)
    print(f"全部通过：{len(CASES) + 3} 项")


if __name__ == "__main__":
    main()
