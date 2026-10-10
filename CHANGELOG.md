# 更新记录

## v1.1 — 2026-10-10

> 下载：[`release/v1.1/番剧批量重命名(字幕版).exe`](release/v1.1/%E7%95%AA%E5%89%A7%E6%89%B9%E9%87%8F%E9%87%8D%E5%91%BD%E5%90%8D(%E5%AD%97%E5%B9%95%E7%89%88).exe)

### 修复 1：外站英文点分（scene）发布名无法识别

**现象**：遇到这类文件名直接报「未在当前目录及子文件夹中找到可识别的文件」，整批退出，一个文件都不改。

```
Now.That.I.Can.Control.Reality.With.A.Mouse.Cursor.Im.Gonna.Click.Away.On.The.Girls
.S01E01.1080p.UNCENSORED.ADN.WEB-DL.DUAL.AAC2.0.H.264.MSubs-ToonsHub.mkv
```

**为什么要改这里**：原来的解析器把「开头第一个方括号」当成发布组的唯一来源（`^\[(.+?)\]`），匹配不到就直接 `return None`。这是国内字幕组 `[组] 标题 - 01` 的习惯，而 ToonsHub、SubsPlease、Erai-raws 等外站资源的命名是**发布组放结尾、标题用点连接、集号点分夹在中段**，开头根本没有方括号，因此在第一道门就被判为无法识别。

**改了什么**：

1. 新增 `extract_dot_scene_info()`：先判断是不是英文点分名（无空格、至少两个点、不含中日文/全角字符、不以 `[` 或 `(` 开头），命中后按点切段解析；解析不出集号仍返回 `None`，交回原有流程，不影响国内命名。
2. 集号只认**独立成段**的 `SxxExx` / `Exx`（允许 `v2` 之类的版本后缀），**不认裸数字段** —— 否则 `H.264` 里的 `264`、`1080p` 里的 `1080` 会被当成集号。
3. 标题 = 集号段之前的点分段还原为空格连接；集号段之后的副标题与技法信息一律丢弃（与原有行为一致：只用集号前的部分当标题）。
4. 发布组 = 末段；新增 `SCENE_TECH_TOKENS` 白名单，末段若只是 `1080p` / `x264` / `WEB-DL` / `chs` 这类冗余标签，则视为没有发布组。
5. 新增 `SCENE_GROUP_PREFIXES`：末段形如 `MSubs-ToonsHub`、`BluRay-Group`、`264-Group` 时剥掉技法前缀，只留 `ToonsHub` / `Group`。**用白名单而不是「见到横杠就拆」**，是因为 `Erai-raws`、`Nix-Raws`、`DBD-Raws`、`UHA-WINGS` 都是真实带横杠的组名，拆了就错。
6. 没有发布组时输出省略末段，得到 `标题 - SXXEXX.ext`（原来是硬拼 ` - {发布组}`，会留下一个空尾巴）。

### 修复 2：工作区里混有识别不了的文件时整个程序崩溃

**现象**：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\u26a0'
[PYI-57500:ERROR] Failed to execute script 'bangumi_rename' due to unhandled exception!
```

预览已经打印出来，但**确认后一个文件都没改名** —— 崩溃发生在执行前的提示行。

**为什么要改这里**：打包成 EXE 后，stdout 的编码由 PyInstaller 按系统区域设置固定为 GBK，而且此时环境变量 `PYTHONIOENCODING` **不生效**（bootloader 在 Python 初始化前就把编码定死了，实测设置无效）。原来「跳过无法识别的文件」那行带了个 `⚠️`（U+26A0），GBK 编不出来直接抛异常。触发条件是工作区里**同时存在**能识别与不能识别的文件；平时送入的工作区都被预处理成清一色可识别文件，所以这个坑一直没暴露。

**改了什么**：

1. 新增 `_make_std_streams_safe()`：启动时把 stdout / stderr 的错误策略改为 `errors="replace"`，超出 GBK 的字符降级为 `?`。不仅修掉 `⚠️`，**文件名里的 `½` `♪` `☆` 之类字符也再不会把程序搞崩**（虽然文件操作本身走 Windows Unicode API 一直是安全的，崩的只是「打印」这一步）。
2. 提示语里的 `⚠️` 改为 `[!]`，GBK 控制台下显示正常，不出现问号。

### 其他改动

- 抽出 `build_filename()` 统一拼接输出文件名（原来在 `batch_rename_videos()` 里硬拼字符串），便于单元测试与复用。
- 新增 `tests/test_extract_info.py`：24 项断言，覆盖五类命名格式，以及「点分但无集号」「无方括号无点分」等**不应被误吞**的反例。

### 验证

- `python tests/test_extract_info.py` → 24/24 通过。
- 打包后的 EXE 真机实测（stdout 为管道、未设 `PYTHONIOENCODING`，与自动化环境一致）：
  - 点分名 → `Now That I Can Control Reality With A Mouse Cursor Im Gonna Click Away On The Girls - S01E01 - ToonsHub.mkv` ✅
  - 同目录再放一个 `random_video.mp4`（不可识别）→ 程序正常跑完，打印 `[!] 跳过无法识别的文件：2 个`，改名成功 ✅
  - 原有 `[组] 标题 - S01E02`、`[组] 标题 [01]` 形态输出与旧版完全一致，无回归 ✅

### 兼容性

输出格式、命令行行为、需要输入 `y` 确认的交互方式都没变；旧版能处理的名字，新版结果完全相同。只多了「点分名现在也能处理」这一类。

## v1.0 — 首个版本

方括号集数 / 横杠集数 / SxxExx 三类命名的批量重命名：递归遍历、预览确认、重名保护、字幕同步、可打包 EXE。
