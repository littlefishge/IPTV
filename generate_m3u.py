#!/usr/bin/env python3
"""Generate a clean CCTV-only M3U playlist."""

from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

try:
    import yaml
except ImportError:
    yaml = None

IPTV_ORG_CHINA_PLAYLIST = "https://iptv-org.github.io/iptv/countries/cn.m3u"

FANMINGMING_BASE = "https://live.fanmingming.com/tv"


def build_url(channel_id: str, source_base: str) -> str:
    if source_base.endswith(".m3u8"):
        return source_base
    return f"{source_base.rstrip('/')}/{channel_id}.m3u8"


def render_entry(channel: dict[str, str], url: str) -> str:
    return (
        f"#EXTINF:-1 tvg-id=\"{channel['tvg-id']}\" "
        f"tvg-name=\"{channel['tvg-name']}\" "
        f"tvg-logo=\"{channel['tvg-logo']}\" "
        f"group-title=\"{channel['group-title']}\",{channel['display-name']}\n"
        f"{url}\n"
    )


def generate_m3u(source_base: str, channels: Sequence[dict[str, str]], epg_url: str = "https://epg.112114.xyz/pp.xml") -> str:
    entries = [render_entry(ch, build_url(ch["id"], source_base)) for ch in channels]
    # sort rendered entries by CCTV numeric order (CCTV1, CCTV2...) when possible
    def _extract_cctv_number(s: str) -> int:
        if not s:
            return 9999
        m = re.search(r"cctv[^0-9]{0,3}(\d{1,2})", s, re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 9999
        m2 = re.search(r"\b(\d{1,2})\b", s)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                return 9999
        return 9999

    def _cctv_sort_entry(text: str) -> tuple[int, str]:
        # attempt to extract CCTV number from the rendered EXTINF line
        # fallback to group/name ordering
        group = ""
        name = text
        m = re.match(r"#EXTINF:[^,]* .*group-title=\"(?P<group>[^\"]*)\".*,(?P<name>.*)", text)
        if m:
            group = m.group('group')
            name = m.group('name')
        number = _extract_cctv_number(text) or _extract_cctv_number(name) or _extract_cctv_number(group)
        return (number, (group or "").lower() + "|" + (name or "").lower())

    body = "#EXTM3U x-tvg-url=\"{epg_url}\"\n\n".format(epg_url=epg_url)
    for entry in sorted(entries, key=_cctv_sort_entry):
        body += entry + "\n"
    return body


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CCTV-only M3U playlist for IPTV players."
    )
    parser.add_argument(
        "--output",
        "-o",
        default="list.m3u",
        help="Output M3U filename (default: list.m3u)",
    )
    parser.add_argument(
        "--primary-source",
        dest="primary_source",
        default=IPTV_ORG_CHINA_PLAYLIST,
        help=(
            "Primary playlist source to use first. "
            "Defaults to the IPTV-ORG China playlist URL."
        ),
    )
    parser.add_argument(
        "--source",
        "--fallback-source",
        dest="fallback_source",
        default=FANMINGMING_BASE,
        help=(
            "Fallback playlist source used when the primary source does not provide a working stream. "
            "Defaults to the FanMingMing base URL; can also be a playlist URL or a source template like 'iptv-org'."
        ),
    )
    parser.add_argument(
        "--epg-url",
        default="https://epg.112114.xyz/pp.xml",
        help="EPG XML URL to embed in the playlist (default: https://epg.112114.xyz/pp.xml)",
    )
    parser.add_argument(
        "--channels",
        help=(
            "Optional comma-separated list of channel IDs to include. "
            "Supported: CCTV1–CCTV17 plus specialty channels such as CCTV+, CCTV-Golf&Tennis, CCTV-Health, CCTV-Storm. "
            "Default includes all channels."
        ),
    )
    parser.add_argument(
        "--channels-file",
        dest="channels_file",
        default="channels.yaml",
        help=(
            "Path to a YAML file containing channel categories and channel definitions. "
            "Defaults to channels.yaml when no file is provided."
        ),
    )
    parser.add_argument(
        "--categories",
        dest="categories",
        help="Optional comma-separated category names to select from the channel list.",
    )
    validate_group = parser.add_mutually_exclusive_group()
    validate_group.add_argument(
        "--validate",
        dest="validate",
        action="store_true",
        default=True,
        help="Validate each generated channel URL after generating the playlist (default).",
    )
    validate_group.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Skip URL validation after generating the playlist.",
    )
    parser.add_argument(
        "--lookup",
        dest="lookup",
        action="store_true",
        help="Print matching source URLs for the requested channels without generating a playlist.",
    )
    return parser.parse_args()


def get_generation_timestamp() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S") + " UTC+8"


def save_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    print(f"已生成：{path.resolve()}")


def download_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; IPTV-List/1.0)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_m3u_playlist(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue

        if line.startswith("#EXTINF:"):
            metadata = line
            index += 1
            while index < len(lines):
                next_line = lines[index].strip()
                if not next_line:
                    index += 1
                    continue
                if next_line.startswith(("#EXTINF:", "http://", "https://", "rtp://", "udp://")):
                    break
                if next_line.startswith("#EXTVLCOPT:"):
                    index += 1
                    continue
                metadata += next_line
                index += 1

            url = ""
            if index < len(lines):
                next_line = lines[index].strip()
                if next_line.startswith(("http://", "https://", "rtp://", "udp://")):
                    url = next_line
                    index += 1

            entry = {
                "url": url,
                "tvg-id": "",
                "tvg-name": "",
                "tvg-logo": "",
                "group-title": "",
                "display-name": "",
            }
            parts = metadata.split(",", 1)
            if len(parts) == 2:
                entry["display-name"] = parts[1].strip()
            attrs = parts[0].replace("#EXTINF:-1", "").strip()
            for token in attrs.split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                value = value.strip('"')
                if key in entry:
                    entry[key] = value

            entries.append(entry)
            continue

        index += 1

    return entries


def load_channel_list(path: Path, categories: set[str] | None = None) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix not in {".yaml", ".yml"}:
        raise SystemExit("Channel list must be a YAML file with .yaml or .yml extension.")
    if yaml is None:
        raise SystemExit("YAML support requires PyYAML. install it with 'pip install pyyaml'.")
    raw = yaml.safe_load(text)

    if not isinstance(raw, list):
        raise SystemExit("Channel list must be a YAML array of category groups.")

    channels: list[dict[str, str]] = []
    for group in raw:
        if not isinstance(group, dict):
            continue
        category_name = str(group.get("category", ""))
        if categories and category_name.strip().upper() not in categories:
            continue
        for channel in group.get("channels", []):
            if not isinstance(channel, dict) or not channel.get("id"):
                continue
            raw_urls = channel.get("urls", channel.get("url", []))
            if isinstance(raw_urls, str):
                urls = [raw_urls.strip()] if raw_urls.strip() else []
            elif isinstance(raw_urls, list):
                urls = [str(u).strip() for u in raw_urls if str(u).strip()]
            else:
                urls = []
            normalized = {
                "id": channel["id"],
                "tvg-id": channel.get("tvg-id", channel["id"]),
                "tvg-name": channel.get("tvg-name", channel.get("tvg-id", channel["id"])),
                "tvg-logo": channel.get("tvg-logo", ""),
                "group-title": channel.get("group-title", category_name),
                "display-name": channel.get("display-name", channel.get("name", channel["id"])),
                "url": urls[0] if urls else "",
                "urls": urls,
            }
            channels.append(normalized)
    return channels


def load_source_entries(source: str) -> list[dict[str, str]] | None:
    if source == "iptv-org":
        url = "https://iptv-org.github.io/iptv/index.m3u"
    elif source == "iptv-org-cn":
        url = IPTV_ORG_CHINA_PLAYLIST
    elif source.startswith("http") and source.endswith((".m3u", ".m3u8")):
        url = source
    else:
        return None

    try:
        playlist_text = download_text(url)
        return parse_m3u_playlist(playlist_text)
    except Exception as exc:
        print(f"警告：加载源失败 {url} -> {exc}")
        return None


def _matches_channel_id(value: str, requested: str) -> bool:
    normalized = value.upper()
    token = requested.strip().upper()
    if not token:
        return False
    pattern = rf"(^|[^0-9A-Z]){re.escape(token)}([^0-9A-Z]|$)"
    return re.search(pattern, normalized) is not None


def _select_request_key(entry: dict[str, str], requested_ids: set[str]) -> str:
    tvg_id = entry.get("tvg-id", "")
    display_name = entry.get("display-name", "")
    for request in requested_ids:
        if _matches_channel_id(tvg_id, request) or _matches_channel_id(display_name, request):
            return request
    return ""


def _prefers_hd(entry: dict[str, str]) -> int:
    tvg_id = entry.get("tvg-id", "")
    display_name = entry.get("display-name", "")
    score = 0
    if re.search(r"\b4K\b|\b2160p\b", tvg_id + " " + display_name, re.I):
        score += 200
    if re.search(r"\bHD\b", tvg_id + " " + display_name, re.I):
        score += 100
    if re.search(r"@HD\b", tvg_id, re.I):
        score += 100
    if re.search(r"\b1080p\b", tvg_id + " " + display_name, re.I):
        score += 80
    if re.search(r"\bSD\b", tvg_id + " " + display_name, re.I):
        score -= 50
    return score


def _matches_channel(entry: dict[str, str], channel: dict[str, str]) -> bool:
    # match by id, tvg-id or display-name
    for key in ("tvg-id", "display-name", "id"):
        val = str(entry.get(key, "")).upper()
        if val and _matches_channel_id(val, str(channel.get("id", ""))):
            return True
        if val and _matches_channel_id(val, str(channel.get("tvg-id", ""))):
            return True
        if val and _matches_channel_id(val, str(channel.get("display-name", ""))):
            return True
    return False


def _find_best_match(entries: list[dict[str, str]], channel: dict[str, str]) -> dict[str, str] | None:
    matches = [e for e in entries if _matches_channel(e, channel)]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return max(matches, key=_prefers_hd)


def _merge_source_metadata(channel: dict[str, str], source_entry: dict[str, str]) -> dict[str, str]:
    merged = channel.copy()
    merged["url"] = source_entry.get("url", merged.get("url", ""))
    merged["urls"] = source_entry.get("urls", merged.get("urls", []))
    if not merged.get("tvg-logo") and source_entry.get("tvg-logo"):
        merged["tvg-logo"] = source_entry["tvg-logo"]
    if not merged.get("group-title") and source_entry.get("group-title"):
        merged["group-title"] = source_entry["group-title"]
    if not merged.get("display-name") and source_entry.get("display-name"):
        merged["display-name"] = source_entry["display-name"]
    if not merged.get("tvg-name") and source_entry.get("tvg-name"):
        merged["tvg-name"] = source_entry["tvg-name"]
    return merged


def _find_all_matches(entries: list[dict[str, str]], channel: dict[str, str]) -> list[dict[str, str]]:
    matches = [e for e in entries if _matches_channel(e, channel)]
    return sorted(matches, key=_prefers_hd, reverse=True)


def filter_entries(entries: list[dict[str, str]], requested_ids: set[str]) -> list[dict[str, str]]:
    if not requested_ids:
        return entries

    requested_set = {item.strip().upper() for item in requested_ids}
    grouped: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        if any(_matches_channel_id(entry.get("tvg-id", ""), request) or _matches_channel_id(entry.get("display-name", ""), request) for request in requested_set):
            key = _select_request_key(entry, requested_set)
            grouped.setdefault(key, []).append(entry)

    filtered = []
    for key, group in grouped.items():
        if len(group) == 1:
            filtered.append(group[0])
            continue
        best = max(group, key=_prefers_hd)
        filtered.append(best)
    return filtered


def generate_m3u_from_entries(entries: list[dict[str, str]], epg_url: str = "https://epg.112114.xyz/pp.xml", order: list[str] | None = None) -> str:
    timestamp = get_generation_timestamp()
    output = [f"# Generated: {timestamp}\n", f"#EXTM3U x-tvg-url=\"{epg_url}\"\n"]
    # sort by CCTV numeric order (CCTV1, CCTV2...) when possible
    # build lookup from provided order (channels.yaml) if available
    order_map: dict[str, int] = {}
    if order:
        for idx, ident in enumerate(order):
            order_map[ident.upper()] = idx

    def _extract_cctv_number_entry(e: dict[str, str]) -> int:
        tvg = str(e.get("tvg-id", ""))
        name = str(e.get("display-name", ""))
        group = str(e.get("group-title", ""))
        for s in (tvg, name, group):
            m = re.search(r"cctv[^0-9]{0,3}(\d{1,2})", s, re.I)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    continue
        for s in (name, group):
            m2 = re.search(r"\b(\d{1,2})\b", s)
            if m2:
                try:
                    return int(m2.group(1))
                except ValueError:
                    continue
        return 9999

    def _sort_key(e: dict[str, str]) -> tuple[int, int, str]:
        # primary: position in channels.yaml (if provided)
        # Prefer the channel `id` (from channels.yaml) when available, then tvg-id, then display-name.
        id_key = str(e.get("id", "")).upper()
        tvg = str(e.get("tvg-id", "")).upper()
        name = str(e.get("display-name", "")).upper()
        if order_map:
            # try id first, then tvg-id, then display-name
            pos = order_map.get(id_key, order_map.get(tvg, order_map.get(name, 9999)))
        else:
            pos = 9999
        return (pos if pos != 9999 else _extract_cctv_number_entry(e), pos, str(e.get("display-name", "")).lower())

    for entry in sorted(entries, key=_sort_key):
        output.append(
            f"#EXTINF:-1 tvg-id=\"{entry.get('tvg-id','')}\" "
            f"tvg-name=\"{entry.get('tvg-name','')}\" "
            f"tvg-logo=\"{entry.get('tvg-logo','')}\" "
            f"group-title=\"{entry.get('group-title','')}\",{entry.get('display-name','')}\n"
            f"{entry.get('url','')}\n"
        )
    return "\n".join(output).strip() + "\n"


def check_url(url: str, timeout: int = 10) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.getcode()
            if 200 <= code < 400:
                return True, f"{code} {response.headers.get('content-type', 'unknown')}"
            return False, str(code)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"URL error: {exc.reason}"
    except Exception as exc:
        return False, f"connection error: {exc}"


def write_logs(available: list[str], failed: list[str]) -> None:
    log_dir = Path("log")
    log_dir.mkdir(exist_ok=True)
    info_path = log_dir / "info"
    error_path = log_dir / "error"
    timestamp = get_generation_timestamp()
    info_header = f"# Generated: {timestamp}\n"
    error_header = f"# Generated: {timestamp}\n"
    info_path.write_text(info_header + "\n".join(available) + ("\n" if available else ""), encoding="utf-8")
    error_path.write_text(error_header + "\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")


def validate_channels(entries: list[dict[str, str]]) -> tuple[bool, list[str], list[str]]:
    print("正在验证频道源...")
    all_ok = True
    available: list[str] = []
    failed: list[str] = []
    for entry in entries:
        url = entry.get("url", "")
        ok, msg = check_url(url)
        channel_id = entry.get("tvg-id", entry.get("display-name", "unknown"))
        if ok:
            print(f"✔ {channel_id} -> {msg}")
            available.append(channel_id)
        else:
            print(f"✖ {channel_id} -> {url} -> {msg}")
            failed.append(channel_id)
            all_ok = False
    return all_ok, available, failed


def main() -> None:
    args = parse_args()

    channels_list: list[dict[str, str]] | None = None
    if args.channels_file:
        channels_path = Path(args.channels_file)
        category_set = {cat.strip().upper() for cat in args.categories.split(",") if cat.strip()} if args.categories else None
        channels_list = load_channel_list(channels_path, category_set)
        if not channels_list:
            raise SystemExit("错误：未能从频道列表文件加载任何频道，请检查文件路径和类别名称。")
    order: list[str] | None = None
    if channels_list:
        order = [ch.get("id", "").upper() for ch in channels_list]

    if args.channels:
        requested = {ch_id.strip().upper() for ch_id in args.channels.split(",") if ch_id.strip()}
    else:
        requested = set()
        for channel in channels_list:
            requested.add(str(channel.get("id", "")).upper())
            requested.add(str(channel.get("tvg-id", "")).upper())
            requested.add(str(channel.get("display-name", "")).upper())
    # Use primary source first, then fallback source for missing or broken channels
    primary_entries = load_source_entries(args.primary_source) or []
    if not primary_entries:
        print(f"警告：无法加载主源 {args.primary_source}")

    fallback_entries = []
    if args.fallback_source and args.fallback_source != args.primary_source:
        fallback_entries = load_source_entries(args.fallback_source) or []
        if not fallback_entries:
            print(f"警告：无法加载备用源 {args.fallback_source}")

    if args.lookup:
        if not channels_list:
            raise SystemExit("错误：源查找需要频道列表文件。")
        print("正在查找匹配的频道源...")
        for ch in channels_list:
            if args.channels and ch["id"].upper() not in requested:
                continue
            print(f"\n{ch.get('display-name')} ({ch.get('id')})")
            primary_matches = _find_all_matches(primary_entries, ch) if primary_entries else []
            if primary_matches:
                print("  主源候选：")
                for entry in primary_matches:
                    print(f"    - {entry.get('url')} ({entry.get('tvg-id')} / {entry.get('display-name')})")
            else:
                print("  主源未找到匹配项")
            if fallback_entries:
                fallback_matches = _find_all_matches(fallback_entries, ch)
                if fallback_matches:
                    print("  备用源候选：")
                    for entry in fallback_matches:
                        print(f"    - {entry.get('url')} ({entry.get('tvg-id')} / {entry.get('display-name')})")
                else:
                    print("  备用源未找到匹配项")
        return

    if channels_list:
        available_entries: list[dict[str, str]] = []
        available_ids: list[str] = []
        failed_ids: list[str] = []

        for ch in channels_list:
            if args.channels and ch["id"].upper() not in requested:
                continue

            channel_label = f"{ch.get('display-name')} ({ch.get('id')})"
            print(f"正在处理频道：{channel_label}")

            chosen: dict[str, str] | None = None
            primary_failure: str | None = None
            fallback_failure: str | None = None
            direct_url_failure: str | None = None
            channel_id = ch.get("tvg-id", ch.get("display-name", ch.get("id", "unknown")))
            direct_urls = [u for u in ch.get("urls", []) if str(u).strip()]

            # 1) try primary source (IPTV_ORG_CHINA_PLAYLIST by default)
            if primary_entries:
                cand = _find_best_match(primary_entries, ch)
                if cand:
                    ok, msg = check_url(cand.get("url", ""))
                    if ok:
                        chosen = _merge_source_metadata(ch, cand)
                    else:
                        primary_failure = msg
                else:
                    primary_failure = "not found in primary"
            else:
                primary_failure = "primary source unavailable"

            # 2) try fallback source if primary failed
            if chosen is None and fallback_entries:
                cand = _find_best_match(fallback_entries, ch)
                if cand:
                    ok, msg = check_url(cand.get("url", ""))
                    if ok:
                        chosen = _merge_source_metadata(ch, cand)
                    else:
                        fallback_failure = msg
                else:
                    fallback_failure = "not found in fallback"

            # 3) try direct override URL from channels.yaml last
            if chosen is None:
                for direct_url in direct_urls:
                    direct_url = str(direct_url).strip()
                    ok, msg = check_url(direct_url)
                    if ok:
                        chosen = {**ch, "url": direct_url}
                        break
                    direct_url_failure = f"direct url invalid ({msg})"

            if chosen:
                available_entries.append(chosen)
                available_ids.append(str(channel_id))
            else:
                failure_desc = ""
                if primary_failure and fallback_failure:
                    failure_desc = f"primary invalid ({primary_failure}); fallback invalid ({fallback_failure})"
                elif primary_failure and not fallback_entries:
                    failure_desc = primary_failure
                elif primary_failure and not primary_failure.startswith("not found") and not fallback_failure:
                    failure_desc = primary_failure
                elif fallback_failure and not primary_failure:
                    failure_desc = fallback_failure
                elif direct_url_failure:
                    failure_desc = direct_url_failure
                else:
                    failure_desc = "not found in any source"
                failed_ids.append(f"{channel_id} ({failure_desc})")

        write_logs(available_ids, failed_ids)
        if available_ids:
            print("\n=== 处理结果 ===")
            print(f"成功频道：{', '.join(available_ids)}")
        if failed_ids:
            print(f"失败频道：{', '.join(failed_ids)}")
        if not available_entries:
            raise SystemExit("错误：未能为任一频道找到可用流，请检查网络或提供替代源。")
        generated = generate_m3u_from_entries(available_entries, args.epg_url, order)
        save_file(Path(args.output), generated)
        return
    else:
        source_base = FANMINGMING_BASE if args.source == "fanmingming" else args.source
        source_channels = channels_list
        selected_channels = [
            ch for ch in source_channels if ch["id"].upper() in requested
        ]
        if not selected_channels:
            raise SystemExit("错误：未找到有效的频道 ID，请检查 --channels 或 --channels-file 参数。")

        all_entries = [
            {**ch, "url": build_url(ch["id"], source_base)} for ch in selected_channels
        ]
        if args.validate:
            ok, available, failed = validate_channels(all_entries)
            if not ok:
                if args.source != "iptv-org-cn":
                    print("当前源验证失败，尝试使用 IPTV_ORG_CHINA_PLAYLIST 回退...")
                    fallback_entries = load_source_entries("iptv-org-cn")
                    if not fallback_entries:
                        write_logs(available, failed)
                        raise SystemExit("错误：无法加载 IPTV_ORG_CHINA_PLAYLIST 回退源。")
                    fallback_selected = filter_entries(fallback_entries, requested)
                    if not fallback_selected:
                        write_logs(available, failed)
                        raise SystemExit("错误：回退源未找到匹配的频道。")
                    ok, available, failed = validate_channels(fallback_selected)
                    if available:
                        fallback_valid = [
                            entry for entry in fallback_selected
                            if entry.get("tvg-id") in available or entry.get("display-name") in available
                        ]
                        write_logs(available, failed)
                        generated = generate_m3u_from_entries(fallback_valid, args.epg_url)
                        save_file(Path(args.output), generated)
                        return
                    write_logs(available, failed)
                    raise SystemExit("部分频道源验证失败，即使回退到 IPTV_ORG_CHINA_PLAYLIST。")
                if available:
                    selected_valid = [
                        entry for entry in selected_channels
                        if entry.get("tvg-id") in available or entry.get("display-name") in available
                    ]
                    write_logs(available, failed)
                    generated = generate_m3u_from_entries(selected_valid, args.epg_url)
                    save_file(Path(args.output), generated)
                    return
                write_logs(available, failed)
                raise SystemExit("部分频道源验证失败，请替换为可用源地址。")
            write_logs(available, failed)
        else:
            write_logs([ch["id"] for ch in selected_channels], [])
        generated = generate_m3u(source_base, selected_channels, args.epg_url)
        save_file(Path(args.output), generated)


if __name__ == "__main__":
    main()
