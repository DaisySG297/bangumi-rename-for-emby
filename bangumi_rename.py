import os
import re
import sys


def _make_std_streams_safe() -> None:
    """把标准输出/错误的编码错误策略改为「替换」。

    打包成 EXE 后 stdout 的编码由 PyInstaller 按系统区域设置固定为 GBK
    （此时环境变量 ``PYTHONIOENCODING`` 不生效），一旦要打印的字符超出 GBK
    范围——例如提示语里的 ``⚠``，或文件名里的 ``½`` ``♪`` ``☆``——就会抛
    ``UnicodeEncodeError`` 直接中断整个重命名流程。改为 ``errors="replace"``
    后这类字符安全降级为 ``?``，不再影响重命名本身。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_make_std_streams_safe()

# ========== 配置项 ==========
DEFAULT_SEASON = 1  # 标题无季数标识时，默认使用的季数
# 支持的视频/字幕扩展名，可自行添加
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".rmvb", ".m4v", ".srt", ".ass", ".ssa", ".vtt", ".mks"}

# 英文点分(scene)发布名末段里，哪些标记永远不是发布组（来源/编码/音轨/语种等冗余标签）。
# 比较前会去掉非字母数字字符并转小写，因此这里统一写成紧凑小写形式。
SCENE_TECH_TOKENS = {
    # 来源 / 平台
    "web", "webdl", "webrip", "hdrip", "hdtv", "dvdrip", "bdrip", "bd", "bluray", "bdmv",
    "remux", "nf", "cr", "adn", "dsnp", "dsnpu", "amzn", "hulu", "abema", "baha", "bglobal",
    "bilibili", "rakuten", "vrv", "funi", "atvp", "pcok",
    # 编码 / 画质
    "x264", "x265", "h264", "h265", "avc", "hevc", "av1", "vp9", "8bit", "10bit", "10bits",
    "hi10p", "hdr", "hdr10", "sdr", "dolby", "dv", "4k", "2160p", "1440p", "1080p", "1080i",
    "720p", "576p", "480p",
    # 音轨 / 封装 / 修版
    "aac", "aac20", "aac51", "ac3", "eac3", "dd", "ddp", "ddp51", "dts", "dtshd", "flac",
    "opus", "mp3", "truehd", "dual", "dualaudio", "multi", "multiaudio", "msub", "msubs",
    "multisub", "multisubs", "dualsub", "dualsubs", "sub", "subs", "uncensored", "repack",
    "proper", "v2", "v3", "complete",
    # 语言 / 字幕语种
    "jpn", "jap", "jp", "eng", "en", "chi", "chs", "cht", "zh", "sc", "tc", "hk", "tw", "cn",
    "kor", "kr", "ita", "spa", "fre", "ger", "japanese", "english", "chinese", "korean",
    "simplified", "traditional",
}

# 末段形如 "<技法标记>-<发布组>" 时，剥掉这些前缀只保留真正的组名。
# 例："MSubs-ToonsHub" -> "ToonsHub"、"BluRay-Group" -> "Group"；
# 只收录"技术上不可能做组名"的来源/编码/封装标记，因此 "Erai-raws"、"Nix-Raws"、
# "DBD-Raws"、"UHA-WINGS" 这类带横杠的真实组名会原样保留，不会被拆坏。
SCENE_GROUP_PREFIXES = {
    "msub", "msubs", "multisub", "multisubs", "dualsub", "dualsubs",
    "x264", "x265", "h264", "h265", "hevc", "avc", "hi10p",
    "bluray", "bd", "bdrip", "bdmv", "remux", "web", "webdl", "webrip",
    "hdrip", "hdtv", "dvdrip", "repack", "proper",
}

# 点分名中作为独立一段出现的集号：S01E02 / S01E02v2 / E07 / E07v2
_SCENE_SE_TOKEN = re.compile(r'^S(\d{1,2})E(\d{1,3})(?:v\d+)?$', re.IGNORECASE)
_SCENE_E_TOKEN = re.compile(r'^E(\d{1,3})(?:v\d+)?$', re.IGNORECASE)
# 中日文与全角字符：含这类字符的名字一律不按英文点分名处理（避免抢走中文命名路径）
_CJK_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
# ============================


def _looks_like_dot_scene(name_body: str) -> bool:
    """是否是英文点分(scene)发布名：无空格、至少两个点、不含中日文/全角、不以 [ 或 ( 开头。"""
    if not name_body or " " in name_body or name_body.count(".") < 2:
        return False
    if name_body.lstrip()[:1] in ("[", "("):
        return False
    return _CJK_RE.search(name_body) is None


def _is_tech_token(token: str) -> bool:
    """判断点分名中的某一段是否只是分辨率/编码/语种之类的冗余标签（不可能当发布组）。"""
    if not token:
        return True
    if token.isdigit():
        return True
    return re.sub(r'[^a-z0-9]', '', token.lower()) in SCENE_TECH_TOKENS


def _clean_scene_group(token: str) -> str:
    """点分名末段 -> 发布组名：剥掉 MSubs- / x264- / 264- 这类技法前缀，其余原样保留。"""
    if "-" not in token:
        return token
    head, tail = token.rsplit("-", 1)
    head_key = re.sub(r'[^a-z0-9]', '', head.lower())
    if len(tail) < 2 or _is_tech_token(tail):
        return token
    if head_key in SCENE_GROUP_PREFIXES or head.isdigit():
        return tail
    return token


def extract_dot_scene_info(name_body: str) -> tuple | None:
    """
    解析英文点分(scene)发布名，返回 (发布组, 标题, 季数, 集数)；解析不出返回 None。

    典型形态：无 [发布组] 前缀、组名在结尾、标题用点连接、集号点分夹在中段::

        Now.That.I.Can.Control.Reality.With.A.Mouse.Cursor.Im.Gonna.Click.Away.
        On.The.Girls.S01E01.1080p.UNCENSORED.ADN.WEB-DL.DUAL.AAC2.0.H.264.
        MSubs-ToonsHub.mkv

    只认点分隔的 ``SxxExx`` / ``Exx`` 段（可带 v2 等版本后缀），不认纯数字段，
    免得把 ``264`` / ``1080`` 当成集号；末段若是冗余标签则视为没有发布组。
    """
    if not _looks_like_dot_scene(name_body):
        return None

    tokens = [t for t in name_body.split(".") if t]
    ep_index = None
    season = DEFAULT_SEASON
    episode = None
    for i, token in enumerate(tokens):
        hit = _SCENE_SE_TOKEN.match(token)
        if hit:
            ep_index, season, episode = i, int(hit.group(1)), hit.group(2)
            break
        hit = _SCENE_E_TOKEN.match(token)
        if hit:
            ep_index, season, episode = i, DEFAULT_SEASON, hit.group(1)
            break

    if ep_index is None or ep_index == 0:
        return None

    # 集号段之前是标题（点还原为空格）
    title = " ".join(tokens[:ep_index]).replace("_", " ")
    title = re.sub(r'\s+', ' ', title).strip(' -')
    if not title:
        return None

    # 集号段之后的内容（副标题、分辨率、编码、组名……）不参与标题
    if ep_index >= len(tokens) - 1:
        group = ""
    else:
        last = tokens[-1]
        group = "" if _is_tech_token(last) else _clean_scene_group(last)

    return group, title, season, episode


def extract_info(filename: str) -> tuple | None:
    """
    从文件名提取 发布组、标题、季数、集数、原扩展名
    兼容所有常见番剧命名格式：
    1. [发布组] 标题 [集数] [冗余信息].ext
    2. [发布组] 标题 - S01E02 - [冗余信息].ext
    3. [发布组] 标题 S01E01 [冗余信息].ext
    4. [发布组] 标题 - 02v2 [冗余信息].ext
    5. 标题.S01E02.1080p.WEB-DL.DUAL.AAC2.0.H.264.MSubs-发布组.ext（外站英文点分名）
    """
    # 分离文件名主体和扩展名
    name_body, file_ext = os.path.splitext(filename)
    if file_ext.lower() not in VIDEO_EXTENSIONS:
        return None

    # 0. 外站英文点分(scene)发布名：没有 [发布组] 前缀，发布组在结尾
    if not name_body.lstrip().startswith(("[", "(")):
        scene = extract_dot_scene_info(name_body)
        if scene:
            group, title, season, episode = scene
            return group, title, season, episode, file_ext

    # 1. 提取开头第一个方括号：发布组
    group_match = re.match(r'^\[(.+?)\]', name_body)
    if not group_match:
        return None
    release_group = group_match.group(1).strip()
    rest_content = name_body[group_match.end():]

    # 2. 提取季数+集数，优先级：SxxExx > 方括号集数 > 横杠集数
    season = DEFAULT_SEASON
    episode = None
    ep_pos = -1

    # 模式C：最高优先级 - 文件名中包含 S01E01 格式（无论前面是空格还是横杠）
    se_pattern = re.search(r'S(\d+)E(\d+)', rest_content, re.IGNORECASE)
    if se_pattern:
        season = int(se_pattern.group(1))
        episode = se_pattern.group(2)
        ep_pos = se_pattern.start()
    else:
        # 模式A：方括号内的数字集数 [02]、[02_74]
        ep_pattern1 = re.search(r'\[(\d+)(?:_\d+)?\]', rest_content)
        # 模式B：横杠分隔的集数 - 02、- 05v2
        ep_pattern2 = re.search(r'-\s*(\d+)(?:v\d+)?', rest_content)

        if ep_pattern1:
            episode = ep_pattern1.group(1)
            ep_pos = ep_pattern1.start()
        elif ep_pattern2:
            episode = ep_pattern2.group(1)
            ep_pos = ep_pattern2.start()
        else:
            return None  # 未识别到集数则跳过

        # 从标题区域补充识别季数（仅非SxxExx格式时生效）
        season_match = re.search(r'S(\d+)', rest_content[:ep_pos], re.IGNORECASE)
        if season_match:
            season = int(season_match.group(1))

    # 3. 清理并提取标题
    title_raw = rest_content[:ep_pos]
    # 移除季数标识、方括号、首尾横杠/下划线/多余空格
    title = re.sub(r'S\d+', '', title_raw, flags=re.IGNORECASE)
    title = re.sub(r'[\[\]]', '', title)
    title = title.strip(' -_')
    title = re.sub(r'\s+', ' ', title).strip()

    if not title:
        return None

    return release_group, title, season, episode, file_ext


def build_filename(group: str, title: str, season: int, episode: str, file_ext: str) -> str:
    """按统一规范拼接新文件名：``标题 - SXXEXX - 发布组.ext``（无发布组时省略末段）。"""
    name = f"{title} - S{int(season):02d}E{str(episode).zfill(2)}"
    if group:
        name += f" - {group}"
    return name + file_ext


def batch_rename_videos(directory: str = ".") -> None:
    """递归遍历所有子文件夹，批量重命名目录下的番剧视频/字幕文件"""
    rename_mapping = []  # 存储 (旧完整路径, 新完整路径, 旧相对路径, 新相对路径)
    skip_count = 0

    # 递归遍历所有子目录
    for root, _, files in os.walk(directory):
        for filename in files:
            old_full_path = os.path.join(root, filename)
            info = extract_info(filename)

            if not info:
                # 是支持格式但识别失败的计数
                _, ext = os.path.splitext(filename)
                if ext.lower() in VIDEO_EXTENSIONS:
                    skip_count += 1
                continue

            group, title, season, ep, ext = info
            # 生成统一格式新文件名
            new_filename = build_filename(group, title, season, ep, ext)
            new_full_path = os.path.join(root, new_filename)

            # 计算相对路径，方便预览
            old_rel = os.path.relpath(old_full_path, directory)
            new_rel = os.path.relpath(new_full_path, directory)

            rename_mapping.append((old_full_path, new_full_path, old_rel, new_rel))

    if not rename_mapping:
        print("未在当前目录及子文件夹中找到可识别的文件。")
        if skip_count > 0:
            print(f"跳过无法识别的文件：{skip_count} 个")
        return

    # 预览重命名结果
    print("=" * 80)
    print(f"共找到 {len(rename_mapping)} 个可重命名的文件，预览结果：")
    print("-" * 80)
    for _, _, old_rel, new_rel in rename_mapping:
        print(f"旧：{old_rel}")
        print(f"新：{new_rel}")
        print("-" * 60)

    if skip_count > 0:
        print(f"\n[!] 跳过无法识别的文件：{skip_count} 个")

    # 二次确认后执行
    confirm = input("\n确认执行全部重命名？输入 y 确认，其他字符取消：").strip().lower()
    if confirm == "y":
        success = 0
        for old_path, new_path, _, _ in rename_mapping:
            # 重名冲突处理
            if os.path.exists(new_path):
                print(f"跳过：{os.path.basename(old_path)} → 目标文件名已存在")
                continue
            os.rename(old_path, new_path)
            success += 1
        print(f"\n完成！成功重命名 {success} 个文件。")
    else:
        print("\n已取消重命名操作。")


if __name__ == "__main__":
    # 默认处理当前脚本所在目录及所有子文件夹
    # 也可手动指定路径，例如 r"E:\Bangumi暂存"
    batch_rename_videos()
