# 番剧文件名批量重命名工具(适配Emby刮削)

一个通用的番剧视频与字幕文件批量重命名脚本，自动适配多种主流番剧命名规则，统一输出 `标题 - SXXEXX - 发布组` 标准格式，支持递归遍历子目录，自带预览确认机制，避免误操作。

## 功能特性

- **多格式兼容**：覆盖市面绝大多数番剧命名规则，方括号集数、横杠集数、SxxExx 原生格式均可识别
- **递归遍历**：自动扫描当前目录下所有层级子文件夹，视频无需整理到同一目录
- **预览确认**：执行重命名前完整展示所有变更，确认无误后再执行
- **重名保护**：自动检测目标文件名冲突，跳过重复文件，防止覆盖
- **字幕同步**：支持常见字幕格式同步重命名，保持视频与字幕文件名一致
- **轻量无依赖**：仅使用 Python 标准库，无需安装额外第三方包
- **可打包分发**：可打包为独立 EXE 文件，在无 Python 环境的电脑上直接运行

## 支持的命名格式

### 输入示例与输出对应

| 原文件名 | 重命名后 |
|---------|---------|
| `[orion origin] Yuusha no Kuzu [01] [1080p] [H265 AAC] [CHT_JPN].mp4` | `Yuusha no Kuzu - S01E01 - orion origin.mp4` |
| `[BeanSub][Tensei Shitara Slime Datta Ken S4][02_74][CHT][1080P].mp4` | `Tensei Shitara Slime Datta Ken - S04E02 - BeanSub.mp4` |
| `[smzase] LV999 no Murabito - S01E02 - [CHT_JPN][WebRip 1080P].mkv` | `LV999 no Murabito - S01E02 - smzase.mkv` |
| `[Nix-Raws] Reiwa no Dara-san S01E01 [CR WEB-DL 1080p][SC_TC].mp4` | `Reiwa no Dara-san - S01E01 - Nix-Raws.mp4` |
| `[字幕组] 番剧名 - 05v2 [1080p][简繁内嵌].mp4` | `番剧名 - S01E05 - 字幕组.mp4` |

### 输出格式规范

- 季数、集数自动补零为两位
- 自动丢弃分辨率、编码、音轨、语言等冗余信息
- 完整保留原文件扩展名

## 支持的文件格式

**视频格式**：`mp4`、`mkv`、`avi`、`mov`、`flv`、`wmv`、`rmvb`、`m4v`

**字幕格式**：`srt`、`ass`、`ssa`、`vtt`、`mks`

可在脚本顶部 `VIDEO_EXTENSIONS` 配置项中自行扩展。

## 使用方法

### 环境要求

- Python 3.7 及以上版本

### 快速开始

1. 将 `bangumi_rename.py` 放入番剧根目录
2. 打开终端/命令行，执行命令：

```bash
python bangumi_rename.py
```

### 指定目录运行

修改脚本末尾的调用参数，传入目标文件夹绝对路径即可：

```python
if __name__ == "__main__":
    batch_rename_videos(r"E:\Bangumi暂存")
```

### 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DEFAULT_SEASON` | 文件名未标注季数时，默认使用的季数 | `1` |
| `VIDEO_EXTENSIONS` | 需要处理的文件扩展名集合 | 见脚本内预设 |

## 打包为独立 EXE

可通过 PyInstaller 打包为单文件可执行程序，在无 Python 环境的电脑上直接运行：

1. 安装 PyInstaller：

```bash
pip install pyinstaller
```

2. 执行打包命令：

```bash
pyinstaller --onefile --name "番剧批量重命名(字幕版)" bangumi_rename.py
```

3. 打包完成后，可执行文件位于 `dist` 目录内。

## 注意事项

1. 重要文件建议先备份，确认预览结果无误后再执行重命名
2. 脚本仅修改文件名，不会改动文件内容与存储位置
3. `.pptd`、`.cfg`、`.part` 等下载临时文件会自动跳过
4. 若目标文件名已存在，将自动跳过该文件，避免覆盖
5. 文件名含特殊系统保留字符时，以操作系统支持规则为准
