import os
import re

# ========== 配置项 ==========
DEFAULT_SEASON = 1  # 标题无季数标识时，默认使用的季数
# 支持的视频/字幕扩展名，可自行添加
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".rmvb", ".m4v", ".srt", ".ass", ".ssa", ".vtt", ".mks"}
# ============================


def extract_info(filename: str) -> tuple | None:
    """
    从文件名提取 发布组、标题、季数、集数、原扩展名
    兼容所有常见番剧命名格式：
    1. [发布组] 标题 [集数] [冗余信息].ext
    2. [发布组] 标题 - S01E02 - [冗余信息].ext
    3. [发布组] 标题 S01E01 [冗余信息].ext
    4. [发布组] 标题 - 02v2 [冗余信息].ext
    """
    # 分离文件名主体和扩展名
    name_body, file_ext = os.path.splitext(filename)
    if file_ext.lower() not in VIDEO_EXTENSIONS:
        return None

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
            new_filename = f"{title} - S{season:02d}E{ep.zfill(2)} - {group}{ext}"
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
        print(f"\n⚠️  跳过无法识别的文件：{skip_count} 个")

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