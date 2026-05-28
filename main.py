import sys
from platform import system
from os.path import dirname
from os import W_OK, access, stat
from stat import FILE_ATTRIBUTE_HIDDEN
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote, urlencode
from base64 import b64encode, b64decode
from pathlib import Path
import json
import re
import struct
import threading
import time
from shutil import copyfile
from settings import SettingsManager # type: ignore
from helpers import get_ssl_context # type: ignore
import decky # type: ignore

WINDOWS = system() == "Windows"

if WINDOWS:
    from winreg import QueryValueEx, OpenKey, HKEY_CURRENT_USER

    # workaound for py_modules not being added to path on windoge
    sys.path.append(decky.DECKY_PLUGIN_DIR)
    from py_modules.vdf import binary_dump, binary_load
else:
    from vdf import binary_dump, binary_load

def get_steam_path():
    if WINDOWS:
        return Path(QueryValueEx(OpenKey(HKEY_CURRENT_USER, r"Software\Valve\Steam"), "SteamPath")[0])
    else:
        return Path(decky.DECKY_USER_HOME) / '.local' / 'share' / 'Steam'

def get_steam_userdata():
    return get_steam_path() / 'userdata'

def get_steam_libcache():
    return get_steam_path() / 'appcache' / 'librarycache'

def get_userdata_config(steam32):
    return get_steam_userdata() / steam32 / 'config'


SGDB_API_KEY = '6465636b796c6f616465723432303639'
SGDB_API_BASE = 'https://www.steamgriddb.com/api/v2'
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
TARGET_ASSET_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp']
BULK_VERSION = 'bulk-v20-conservative-cover-replace'
ZAZA_PROFILE_STEAM64 = '76561198128354791'
ZAZA_AUTHOR_NAME = 'zazamastro'
TRANSPARENT_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVQYV2NgYAAAAAMAAWgmWQ0AAAAASUVORK5CYII='


_bulk_job_lock = threading.Lock()
_bulk_job = {
    'id': 0,
    'running': False,
    'done': True,
    'label': '',
    'phase': 'idle',
    'current': 0,
    'total': 0,
    'percent': 0,
    'found': 0,
    'downloaded': 0,
    'skipped': 0,
    'failed': 0,
    'message': 'Idle',
    'errors': [],
    'result': None,
    'started_at': None,
    'updated_at': None,
    'cancel_requested': False,
}


_plugin_settings = None


def _get_custom_sgdb_api_key():
    try:
        if _plugin_settings is None:
            return ''
        value = _plugin_settings.getSetting('bulk_sgdb_api_key', '')
        return str(value or '').strip()
    except Exception:
        return ''


def _active_sgdb_api_key():
    return _get_custom_sgdb_api_key() or SGDB_API_KEY


def _bulk_job_snapshot():
    with _bulk_job_lock:
        return json.loads(json.dumps(_bulk_job, default=str))



def _bulk_cancel_requested():
    try:
        with _bulk_job_lock:
            return bool(_bulk_job.get('cancel_requested'))
    except Exception:
        return False


def _bulk_cancel_result(label, found, downloaded, skipped, failed, errors=None, progress_callback=None):
    msg = f'Cancelled {label}. Found {found}, downloaded {downloaded}, skipped {skipped}, failed {failed}.'
    result = {
        'ok': False,
        'cancelled': True,
        'found': found,
        'downloaded': downloaded,
        'skipped': skipped,
        'failed': failed,
        'message': msg,
        'errors': list(errors or [])[:50],
    }
    if progress_callback:
        try:
            progress_callback({
                'phase': 'cancelled',
                'current': found,
                'total': found,
                'found': found,
                'downloaded': downloaded,
                'skipped': skipped,
                'failed': failed,
                'message': msg,
                'errors': list(errors or [])[:50],
                'result': result,
                'done': True,
                'running': False,
                'cancelled': True,
            })
        except Exception:
            pass
    return result

def _bulk_job_update(job_id=None, **changes):
    with _bulk_job_lock:
        if job_id is not None and _bulk_job.get('id') != job_id:
            return json.loads(json.dumps(_bulk_job, default=str))
        _bulk_job.update(changes)
        _bulk_job['updated_at'] = time.time()
        total = _safe_int(_bulk_job.get('total'), 0)
        current = _safe_int(_bulk_job.get('current'), 0)
        if total > 0:
            _bulk_job['percent'] = max(0, min(100, int((current / total) * 100)))
        elif _bulk_job.get('done'):
            _bulk_job['percent'] = 100
        else:
            _bulk_job['percent'] = 0
        return json.loads(json.dumps(_bulk_job, default=str))


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


def _read_image_size(path):
    try:
        with open(path, 'rb') as f:
            data = f.read(64 * 1024)
    except Exception:
        return None

    try:
        # PNG
        if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
            return struct.unpack('>II', data[16:24])

        # JPEG
        if data.startswith(b'\xff\xd8'):
            i = 2
            length = len(data)
            while i + 9 < length:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                i += 2
                if marker in (0xD8, 0xD9):
                    continue
                if i + 2 > length:
                    break
                segment_length = int.from_bytes(data[i:i + 2], 'big')
                if segment_length < 2:
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if i + 7 <= length:
                        height = int.from_bytes(data[i + 3:i + 5], 'big')
                        width = int.from_bytes(data[i + 5:i + 7], 'big')
                        return width, height
                    break
                i += segment_length

        # WebP VP8X / VP8L / VP8
        if data.startswith(b'RIFF') and data[8:12] == b'WEBP' and len(data) >= 30:
            fourcc = data[12:16]
            if fourcc == b'VP8X' and len(data) >= 30:
                width = 1 + int.from_bytes(data[24:27], 'little')
                height = 1 + int.from_bytes(data[27:30], 'little')
                return width, height
            if fourcc == b'VP8L' and len(data) >= 25:
                b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
                width = 1 + (((b1 & 0x3F) << 8) | b0)
                height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
                return width, height
            if fourcc == b'VP8 ' and len(data) >= 30:
                start = data.find(b'\x9d\x01\x2a', 20, 64)
                if start != -1 and start + 7 <= len(data):
                    width = int.from_bytes(data[start + 3:start + 5], 'little') & 0x3FFF
                    height = int.from_bytes(data[start + 5:start + 7], 'little') & 0x3FFF
                    return width, height
    except Exception:
        return None

    return None


def _image_has_size(path, width, height):
    size = _read_image_size(path)
    return bool(size and size[0] == width and size[1] == height)


def _grid_dirs():
    roots = []
    userdata = get_steam_userdata()
    try:
        for user_dir in userdata.iterdir():
            grid_dir = user_dir / 'config' / 'grid'
            if grid_dir.is_dir():
                roots.append(grid_dir)
    except Exception:
        pass
    return roots


def _first_grid_dir():
    grid_dirs = _grid_dirs()
    if grid_dirs:
        return grid_dirs[0]
    try:
        userdata = get_steam_userdata()
        for user_dir in userdata.iterdir():
            config_dir = user_dir / 'config'
            if config_dir.is_dir():
                grid_dir = config_dir / 'grid'
                grid_dir.mkdir(parents=True, exist_ok=True)
                return grid_dir
    except Exception:
        pass
    return None


def _shortcut_names_by_appid():
    names = {}
    try:
        for user_dir in get_steam_userdata().iterdir():
            shortcuts_vdf = user_dir / 'config' / 'shortcuts.vdf'
            if not shortcuts_vdf.exists():
                continue
            try:
                data = binary_load(open(shortcuts_vdf, 'rb'))
                for shortcut in data.get('shortcuts', {}).values():
                    shortcut_appid = (shortcut.get('appid', 0) & 0xffffffff) | 0x80000000
                    name = shortcut.get('AppName') or shortcut.get('appname') or shortcut.get('name') or ''
                    if name:
                        names[int(shortcut_appid)] = str(name)
            except Exception:
                continue
    except Exception:
        pass
    return names


def _read_text_file(path):
    for encoding in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            return Path(path).read_text(encoding=encoding, errors='ignore')
        except Exception:
            pass
    return ''


def _steam_library_paths():
    paths = []
    steam_root = get_steam_path()
    default_steamapps = steam_root / 'steamapps'
    if default_steamapps.is_dir():
        paths.append(default_steamapps)

    libraryfolders = default_steamapps / 'libraryfolders.vdf'
    text = _read_text_file(libraryfolders)
    for match in re.finditer(r'"path"\s+"([^\"]+)"', text):
        path = Path(match.group(1).replace('\\\\', '\\')) / 'steamapps'
        if path.is_dir() and path not in paths:
            paths.append(path)

    # Old Steam format: numbered entries can directly contain paths.
    for match in re.finditer(r'"\d+"\s+"([^\"]+)"', text):
        path = Path(match.group(1).replace('\\\\', '\\')) / 'steamapps'
        if path.is_dir() and path not in paths:
            paths.append(path)

    return paths


def _installed_steam_appids():
    return set(_steam_app_names_by_appid().keys())


def _acf_unescape(value):
    try:
        return value.replace('\\\"', '\"').replace('\\\\', '\\').strip()
    except Exception:
        return str(value or '').strip()


def _steam_app_names_by_appid():
    names = {}
    for steamapps in _steam_library_paths():
        try:
            for manifest in steamapps.glob('appmanifest_*.acf'):
                m = re.match(r'appmanifest_(\d+)\.acf$', manifest.name, re.IGNORECASE)
                if not m:
                    continue
                appid = int(m.group(1))
                text = _read_text_file(manifest)
                name_match = re.search(r'"name"\s+"([^"]+)"', text, re.IGNORECASE)
                if name_match:
                    names[appid] = _acf_unescape(name_match.group(1))
                else:
                    names.setdefault(appid, str(appid))
        except Exception:
            pass
    return names


def _game_names_by_appid():
    names = _steam_app_names_by_appid()
    names.update(_shortcut_names_by_appid())
    return names


def _simplified_game_search_terms(name):
    cleaned = str(name or '').strip()
    if not cleaned:
        return []

    terms = []
    def add(term):
        term = re.sub(r'\s+', ' ', str(term or '').strip())
        if term and term.lower() not in {t.lower() for t in terms}:
            terms.append(term)

    add(cleaned)
    simplified = cleaned
    simplified = re.sub(r'[®™©]', '', simplified)
    simplified = re.sub(r'\s*[-–—:]\s*(Digital Deluxe|Deluxe Edition|Ultimate Edition|Complete Edition|Game of the Year Edition|GOTY Edition|Enhanced Edition|Definitive Edition|Remastered|Remaster)\s*$', '', simplified, flags=re.IGNORECASE)
    simplified = re.sub(r'\s*\((.*?)\)\s*$', '', simplified)
    add(simplified)
    return terms


def _librarycache_appids_for_kind(source_kind, source_width, source_height):
    appids = set()
    cache_dir = get_steam_libcache()
    if not cache_dir.is_dir():
        return appids

    if source_kind == 'hero':
        stem_patterns = [
            re.compile(r'^(\d+)_library_hero$', re.IGNORECASE),
            re.compile(r'^(\d+)_hero$', re.IGNORECASE),
        ]
    else:
        stem_patterns = [
            re.compile(r'^(\d+)_header$', re.IGNORECASE),
            re.compile(r'^(\d+)_capsule_616x353$', re.IGNORECASE),
            re.compile(r'^(\d+)$', re.IGNORECASE),
        ]

    try:
        for path in cache_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            appid = 0
            for pattern in stem_patterns:
                m = pattern.match(path.stem)
                if m:
                    appid = _safe_int(m.group(1))
                    break
            if appid <= 0:
                continue
            # Prefer exact source size, but do not make librarycache detection fail if
            # Steam stores a retina/equivalent asset. Librarycache is often a cache of
            # official Steam artwork rather than the exact user-facing file.
            size = _read_image_size(path)
            if size and (size == (source_width, source_height) or source_kind == 'wide'):
                appids.add(appid)
    except Exception:
        pass
    return appids


def _grid_appids_for_kind(source_kind, source_width, source_height):
    matches = []
    pattern = re.compile(r'^(\d+)(_hero|p|_icon|_logo)?$', re.IGNORECASE)
    for grid_dir in _grid_dirs():
        try:
            files = list(grid_dir.iterdir())
        except Exception:
            continue
        for path in files:
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            match = pattern.match(path.stem)
            if not match:
                continue
            appid = _safe_int(match.group(1))
            suffix = (match.group(2) or '').lower()
            if appid <= 0:
                continue
            if source_kind == 'wide' and suffix:
                continue
            if source_kind == 'hero' and suffix != '_hero':
                continue
            if not _image_has_size(path, source_width, source_height):
                continue
            matches.append((grid_dir, appid, path))
    return matches



def _target_stems_for_kind(appid, target_kind):
    if target_kind == 'portrait':
        return [f'{appid}p']
    if target_kind == 'wide':
        return [f'{appid}']
    if target_kind == 'hero':
        return [f'{appid}_hero']
    if target_kind == 'logo':
        return [f'{appid}_logo']
    if target_kind == 'icon':
        return [f'{appid}_icon']
    return [f'{appid}']


def _target_asset_paths(grid_dir, appid, target_kind):
    paths = []
    for stem in _target_stems_for_kind(appid, target_kind):
        for ext in TARGET_ASSET_EXTENSIONS:
            paths.append(grid_dir / f'{stem}{ext}')
    return paths


def _image_has_acceptable_format(path, target_kind, target_width=None, target_height=None):
    size = _read_image_size(path)
    if not size:
        return False
    width, height = size
    if width <= 0 or height <= 0:
        return False
    if target_kind == 'logo':
        # Logos do not have a meaningful fixed size for this bulk tool.
        return True
    if target_width and target_height and size == (target_width, target_height):
        return True
    if target_kind == 'portrait':
        # Do not overwrite portrait covers that already have the right 2:3 format
        # even if they are not exactly 600x900.
        return abs((width / height) - (2 / 3)) < 0.025 and height > width
    if target_kind == 'wide':
        return target_width and target_height and size == (target_width, target_height)
    if target_kind == 'hero':
        return target_width and target_height and size == (target_width, target_height)
    return False



def _image_is_portrait(path):
    size = _read_image_size(path)
    if not size:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    return height > width


def _cover_name_suggests_portrait(stem, appid=None):
    name = str(stem or '').lower()
    appid_s = str(appid).lower() if appid is not None else ''
    rest = name
    if appid_s and name.startswith(appid_s):
        rest = name[len(appid_s):].lstrip('_- ')
    return (
        name == f'{appid_s}p'
        or (appid_s and name.startswith(f'{appid_s}p'))
        or rest.startswith('p')
        or '600x900' in rest
        or 'portrait' in rest
        or 'cover' in rest
        or 'poster' in rest
        or 'vertical' in rest
    )


def _cover_name_is_excluded(stem):
    name = str(stem or '').lower()
    excluded_tokens = (
        'hero', 'logo', 'icon', 'clienticon', 'header', 'banner', 'wide',
        'capsule_616x353', 'library_header', 'library_hero', 'library_logo',
    )
    return any(token in name for token in excluded_tokens)


def _cover_name_is_non_cover_artwork(stem):
    name = str(stem or '').lower()
    # For cover detection, exclude Steam headers/banners/heroes/logos/icons.
    # The v19 rule was too broad because it treated any horizontal app-related
    # image in appcache/librarycache as a bad cover. Steam keeps lots of
    # horizontal headers there, so almost the whole library was re-downloaded.
    excluded_tokens = (
        'hero', 'logo', 'icon', 'clienticon', 'header', 'banner', 'wide',
        'capsule_616x353', 'library_header', 'library_hero', 'library_logo',
    )
    return any(token in name for token in excluded_tokens)


def _image_is_horizontal(path):
    size = _read_image_size(path)
    if not size:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    return width > height


def _path_is_inside_appid_cache_dir(path, appid):
    try:
        return path.parent.name == str(appid)
    except Exception:
        return False


def _grid_portrait_cover_status(grid_dirs, appid):
    prefix = str(appid).lower()
    portrait = []
    explicit_legacy_horizontal = []
    bad_portrait_named = []

    for grid_dir in grid_dirs:
        if not grid_dir:
            continue
        try:
            for path in grid_dir.iterdir():
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                stem = path.stem.lower()
                if _cover_name_is_excluded(stem):
                    continue

                is_portrait_name = _cover_name_suggests_portrait(stem, appid)
                is_legacy_wide_name = stem == prefix

                # In Steam's custom grid folder, portrait covers are normally
                # <appid>p.*. The unsuffixed <appid>.* is the old horizontal
                # capsule. Only that exact legacy file should force a replace.
                if not is_portrait_name and not is_legacy_wide_name:
                    continue

                portrait_flag = _image_is_portrait(path)
                if portrait_flag is True:
                    portrait.append(path)
                elif portrait_flag is False:
                    if is_legacy_wide_name:
                        explicit_legacy_horizontal.append(path)
                    elif is_portrait_name:
                        bad_portrait_named.append(path)
                elif is_portrait_name:
                    # Explicit portrait file but unreadable: be conservative.
                    portrait.append(path)
                elif is_legacy_wide_name:
                    explicit_legacy_horizontal.append(path)
        except Exception:
            pass

    # Custom grid legacy <appid>.* wins, because Big Picture can prefer it over
    # a valid Steam cache portrait. This is the one horizontal case we do want
    # to replace even if a portrait exists elsewhere.
    if explicit_legacy_horizontal:
        return 'wrong', explicit_legacy_horizontal
    if portrait:
        return 'correct', portrait
    if bad_portrait_named:
        return 'wrong', bad_portrait_named
    return 'missing', []

def _librarycache_cover_paths(appid):
    """Return Steam librarycache files that can plausibly be the game's portrait cover.

    This is intentionally conservative. The cache also contains horizontal
    headers/banners. Those must not count as bad covers, otherwise every game is
    treated as needing a new portrait cover.
    """
    cache_dir = get_steam_libcache()
    if not cache_dir.is_dir():
        return []

    prefix = str(appid).lower()
    paths = []
    seen = set()

    def add(path):
        try:
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                return
            key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            paths.append(path)
        except Exception:
            pass

    def direct_file_matches_appid(path):
        stem = path.stem.lower()
        if not stem.startswith(prefix):
            return False
        # Avoid appid-prefix false positives, e.g. app 70 matching 703200_...
        if len(stem) > len(prefix):
            next_char = stem[len(prefix)]
            if next_char not in ('_', '-', 'p'):
                return False
        return True

    def cache_file_is_plausible_cover(path):
        stem = path.stem.lower()
        if _cover_name_is_non_cover_artwork(stem):
            return False
        if _cover_name_suggests_portrait(stem, appid):
            return True
        if stem == prefix or stem == f'{prefix}p':
            return True
        # Some appid subfolders use generic or hashed filenames. Add them only
        # when dimensions are already portrait. Horizontal generic images are
        # usually headers and must not trigger replacement.
        portrait_flag = _image_is_portrait(path)
        return portrait_flag is True

    try:
        # Direct legacy/standard files in appcache/librarycache.
        for path in cache_dir.iterdir():
            if path.is_file():
                if not direct_file_matches_appid(path):
                    continue
                if cache_file_is_plausible_cover(path):
                    add(path)
                continue

            # Newer cache layouts often store artwork under a numeric appid folder.
            if path.is_dir() and path.name == str(appid):
                try:
                    for child in path.rglob('*'):
                        if not child.is_file() or child.suffix.lower() not in IMAGE_EXTENSIONS:
                            continue
                        if cache_file_is_plausible_cover(child):
                            add(child)
                except Exception:
                    pass
    except Exception:
        pass

    return paths


def _librarycache_portrait_cover_status(appid):
    portrait = []
    horizontal_or_unknown = []
    for path in _librarycache_cover_paths(appid):
        portrait_flag = _image_is_portrait(path)
        if portrait_flag is True:
            portrait.append(path)
        elif portrait_flag is False:
            horizontal_or_unknown.append(path)
        elif _cover_name_suggests_portrait(path.stem, appid) or _path_is_inside_appid_cache_dir(path, appid):
            # If Steam says this is a portrait/cover file, or if it is inside
            # the game's cache folder and was accepted as plausible cover, be
            # conservative and skip instead of overwriting blindly.
            portrait.append(path)
        else:
            horizontal_or_unknown.append(path)

    # In librarycache, a horizontal image should only mean "wrong" when no
    # portrait exists. Steam stores many horizontal headers alongside valid
    # portrait covers, and those must not force a re-download.
    if portrait:
        return 'correct', portrait
    if horizontal_or_unknown:
        return 'wrong', horizontal_or_unknown
    return 'missing', []


def _librarycache_has_portrait_cover(appid):
    status, _paths = _librarycache_portrait_cover_status(appid)
    return status == 'correct'

def _target_asset_status(grid_dirs, appid, target_kind, target_width, target_height):
    if target_kind == 'portrait':
        # Cover rule: download ONLY when the cover is missing, or when the
        # custom grid folder contains the explicit old horizontal <appid>.*
        # legacy capsule. Do not treat Steam cache headers as bad covers.
        grid_status, grid_paths = _grid_portrait_cover_status(grid_dirs, appid)
        cache_status, cache_paths = _librarycache_portrait_cover_status(appid)

        # Explicit legacy custom grid cover must be replaced, because Steam can
        # prefer it over a valid librarycache portrait.
        if grid_status == 'wrong':
            return 'wrong', grid_paths
        if grid_status == 'correct' or cache_status == 'correct':
            return 'correct', (grid_paths or []) + (cache_paths or [])
        if cache_status == 'wrong':
            return 'wrong', cache_paths
        return 'missing', []

    existing = []
    correct = []
    wrong = []
    for grid_dir in grid_dirs:
        if not grid_dir:
            continue
        for candidate in _target_asset_paths(grid_dir, appid, target_kind):
            if not candidate.exists() or not candidate.is_file():
                continue
            existing.append(candidate)
            if _image_has_acceptable_format(candidate, target_kind, target_width, target_height):
                correct.append(candidate)
            else:
                wrong.append(candidate)
    if correct:
        return 'correct', correct
    if wrong:
        return 'wrong', wrong
    if target_kind != 'logo' and _librarycache_has_acceptable_asset(appid, target_kind, target_width, target_height):
        return 'correct', []
    return 'missing', []



def _librarycache_has_logo(appid):
    cache_dir = get_steam_libcache()
    if not cache_dir.is_dir():
        return False
    stems = [f'{appid}_logo', f'{appid}_library_logo']
    for stem in stems:
        for ext in TARGET_ASSET_EXTENSIONS:
            if (cache_dir / f'{stem}{ext}').is_file():
                return True
    return False


def _librarycache_has_acceptable_asset(appid, target_kind, target_width=None, target_height=None):
    cache_dir = get_steam_libcache()
    if not cache_dir.is_dir():
        return False
    prefix = str(appid)
    try:
        for path in cache_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if not path.stem.startswith(prefix):
                continue
            if _image_has_acceptable_format(path, target_kind, target_width, target_height):
                return True
    except Exception:
        return False
    return False



def _target_or_cached_logo_status(grid_dirs, appid):
    status, paths = _target_asset_status(grid_dirs, appid, 'logo', None, None)
    if status == 'correct':
        return 'correct', paths
    if _librarycache_has_logo(appid):
        return 'correct', []
    return status, paths


def _collect_all_known_appids(stats):
    grid_appids = set()
    pattern = re.compile(r'^(\d+)(?:_hero|p|_icon|_logo)?$', re.IGNORECASE)
    for grid_dir in _grid_dirs():
        try:
            for path in grid_dir.iterdir():
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                m = pattern.match(path.stem)
                if m:
                    appid = _safe_int(m.group(1))
                    if appid > 0:
                        grid_appids.add(appid)
        except Exception:
            pass

    librarycache_appids = set()
    cache_dir = get_steam_libcache()
    if cache_dir.is_dir():
        try:
            for path in cache_dir.iterdir():
                if path.is_dir() and path.name.isdigit():
                    appid = _safe_int(path.name)
                    if appid > 0:
                        librarycache_appids.add(appid)
                    continue
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                m = re.match(r'^(\d+)', path.stem)
                if m:
                    appid = _safe_int(m.group(1))
                    if appid > 0:
                        librarycache_appids.add(appid)
        except Exception:
            pass

    installed_appids = _installed_steam_appids()
    shortcut_appids = set(_shortcut_names_by_appid().keys())
    stats['grid_appids'] = len(grid_appids)
    stats['librarycache_candidates'] = len(librarycache_appids)
    stats['installed_steam_apps'] = len(installed_appids)
    stats['shortcut_apps'] = len(shortcut_appids)

    # Important: appcache/librarycache is an artwork cache, not the source of
    # truth for the user's library. Steam can keep stale images, duplicate
    # entries, DLC/app related assets, or old non-Steam shortcut IDs there.
    # Use librarycache only inside _target_asset_status() to decide whether a
    # known game's cover is already vertical/horizontal/missing. Do not use it
    # to enumerate games, otherwise the bulk cover job can detect nearly double
    # the real library.
    return grid_appids | installed_appids | shortcut_appids


def _iter_target_missing_assets(target_kind, target_width, target_height, stats):
    seen = set()
    grid_dirs = _grid_dirs()
    fallback_grid = _first_grid_dir()
    if not grid_dirs and fallback_grid:
        grid_dirs = [fallback_grid]

    appids = _collect_all_known_appids(stats)
    target_correct = 0
    target_wrong = 0
    target_missing = 0

    for appid in sorted(appids):
        destinations = grid_dirs if grid_dirs else ([fallback_grid] if fallback_grid else [])
        if not destinations:
            continue
        status, paths = _target_asset_status(destinations, appid, target_kind, target_width, target_height)
        if status == 'correct':
            target_correct += 1
            continue
        if status == 'wrong':
            target_wrong += 1
        else:
            target_missing += 1
        grid_dir = destinations[0]
        key = (str(grid_dir), appid, target_kind)
        if key in seen:
            continue
        seen.add(key)
        yield grid_dir, appid, paths[0] if paths else None, f'target-{status}'

    stats['target_correct'] = target_correct
    stats['target_wrong'] = target_wrong
    stats['target_missing'] = target_missing

def _iter_existing_assets(source_kind, source_width, source_height, stats):
    seen = set()
    grid_matches = _grid_appids_for_kind(source_kind, source_width, source_height)
    stats['grid_exact'] = len(grid_matches)
    for grid_dir, appid, source_path in grid_matches:
        key = (str(grid_dir), appid, source_kind)
        if key not in seen:
            seen.add(key)
            yield grid_dir, appid, source_path, 'grid-exact'

    grid_dirs = _grid_dirs()
    fallback_grid = _first_grid_dir()
    if source_kind == 'wide':
        # The original implementation only scanned exact 460x215 files in
        # userdata/config/grid, which can easily find just a couple of custom images.
        # For Steam games, the normal artwork usually lives in appcache/librarycache
        # or is represented by the installed app manifests, so include those too.
        appids = set()
        librarycache_appids = _librarycache_appids_for_kind(source_kind, source_width, source_height)
        installed_appids = _installed_steam_appids()
        shortcut_appids = set(_shortcut_names_by_appid().keys())
        appids.update(librarycache_appids)
        appids.update(installed_appids)
        appids.update(shortcut_appids)
        stats['librarycache_candidates'] = len(librarycache_appids)
        stats['installed_steam_apps'] = len(installed_appids)
        stats['shortcut_apps'] = len(shortcut_appids)
        for appid in sorted(appids):
            destinations = grid_dirs if appid >= 0x80000000 else (grid_dirs or ([fallback_grid] if fallback_grid else []))
            for grid_dir in destinations:
                if not grid_dir:
                    continue
                key = (str(grid_dir), appid, source_kind)
                if key in seen:
                    continue
                seen.add(key)
                yield grid_dir, appid, None, 'broad-library'
    else:
        librarycache_appids = _librarycache_appids_for_kind(source_kind, source_width, source_height)
        stats['librarycache_candidates'] = len(librarycache_appids)
        for appid in sorted(librarycache_appids):
            destinations = grid_dirs or ([fallback_grid] if fallback_grid else [])
            for grid_dir in destinations:
                if not grid_dir:
                    continue
                key = (str(grid_dir), appid, source_kind)
                if key in seen:
                    continue
                seen.add(key)
                yield grid_dir, appid, None, 'librarycache-hero'


def _remove_asset_variants(grid_dir, appid, target_kind):
    if target_kind == 'portrait':
        stems = [f'{appid}p']
    elif target_kind == 'wide':
        stems = [f'{appid}']
    elif target_kind == 'hero':
        stems = [f'{appid}_hero']
    elif target_kind == 'logo':
        stems = [f'{appid}_logo']
    elif target_kind == 'icon':
        stems = [f'{appid}_icon']
    else:
        stems = []
    for stem in stems:
        for ext in TARGET_ASSET_EXTENSIONS:
            candidate = grid_dir / f'{stem}{ext}'
            try:
                if candidate.exists():
                    candidate.unlink()
            except Exception:
                pass

    if target_kind == 'portrait':
        # Old Steam / SteamGridDB cover replacements can be stored as <appid>.*
        # and are usually horizontal 460x215 images. If we leave them around,
        # Steam can keep showing the legacy horizontal artwork even after a
        # correct <appid>p.* portrait has been downloaded. Remove only legacy
        # non-portrait <appid>.* files; keep any valid portrait just in case.
        for ext in TARGET_ASSET_EXTENSIONS:
            candidate = grid_dir / f'{appid}{ext}'
            try:
                if candidate.exists() and _image_is_portrait(candidate) is not True:
                    candidate.unlink()
            except Exception:
                pass


def _target_path_for_asset(grid_dir, appid, target_kind, url):
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        ext = '.png'
    if target_kind == 'portrait':
        name = f'{appid}p{ext}'
    elif target_kind == 'wide':
        name = f'{appid}{ext}'
    elif target_kind == 'hero':
        name = f'{appid}_hero{ext}'
    elif target_kind == 'logo':
        name = f'{appid}_logo{ext}'
    elif target_kind == 'icon':
        name = f'{appid}_icon{ext}'
    else:
        name = f'{appid}{ext}'
    return grid_dir / name


def _sgdb_request_with_key(path, api_key):
    url = f'{SGDB_API_BASE}{path}'
    req = Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) SteamGridDBDecky/1.0 Safari/537.36',
            'Accept': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
    )
    try:
        with urlopen(req, context=get_ssl_context(), timeout=30) as res:
            raw = res.read().decode('utf-8', errors='replace')
    except HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            body = ''
        hint = ''
        if exc.code in (401, 403):
            hint = ' API key/auth failed; try adding a personal SteamGridDB API key in Playhub Features.'
        raise Exception(f'SGDB HTTP {exc.code} for {path}.{hint} {body}'.strip())
    except URLError as exc:
        raise Exception(f'SGDB network error for {path}: {exc}')

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise Exception(f'SGDB invalid JSON for {path}: {exc}; response={raw[:300]}')
    if not payload.get('success'):
        errors = payload.get('errors') or ['SGDB API request failed']
        raise Exception(', '.join(map(str, errors)))
    return payload.get('data')


def _sgdb_request(path):
    return _sgdb_request_with_key(path, _active_sgdb_api_key())


def _first_sgdb_game_id(appid, game_names):
    name = str(game_names.get(appid, '') or '').strip()

    def resolve_by_steam_id():
        if appid >= 0x80000000:
            return None
        try:
            data = _sgdb_request(f'/games/steam/{appid}')
            if isinstance(data, dict) and data.get('id'):
                return int(data['id'])
            if isinstance(data, list) and data and data[0].get('id'):
                return int(data[0]['id'])
        except Exception:
            return None
        return None

    def resolve_by_name():
        for term in _simplified_game_search_terms(name):
            try:
                # Match the original plugin behavior: encode twice so punctuation
                # survives the SteamGridDB autocomplete route consistently.
                encoded = quote(quote(term, safe=''), safe='')
                data = _sgdb_request(f'/search/autocomplete/{encoded}')
                if isinstance(data, list) and data and data[0].get('id'):
                    return int(data[0]['id'])
            except Exception:
                pass
        return None

    return resolve_by_steam_id() or resolve_by_name()


def _asset_api_type(asset_type):
    if asset_type in ('grid_p', 'grid_l'):
        return 'grids'
    if asset_type == 'hero':
        return 'heroes'
    if asset_type == 'logo':
        return 'logos'
    return asset_type


def _asset_styles(asset_type):
    if asset_type == 'hero':
        return 'alternate,blurred,material'
    if asset_type == 'logo':
        return 'official,white,black,custom'
    if asset_type == 'icon':
        return 'official,custom'
    return 'alternate,white_logo,no_logo,blurred,material'


def _asset_query(dimensions, asset_type):
    # Use the same defaults as the original frontend search as closely as
    # possible. Logos intentionally do not force dimensions: the first usable
    # logo is good enough.
    mimes = 'image/png,image/jpeg,image/webp'
    if asset_type == 'logo':
        # Match the original plugin: logos are PNG/WebP only. Including JPEG can
        # make the SGDB logo endpoint return no usable results on some calls.
        mimes = 'image/png,image/webp'
    query = {
        'page': '0',
        'styles': _asset_styles(asset_type),
        'dimensions': dimensions or '',
        'mimes': mimes,
        'nsfw': 'false',
        'humor': 'any',
        'epilepsy': 'any',
        'oneoftag': '',
        'types': '',
    }
    return urlencode(query)


def _asset_query_variants(dimensions, asset_type):
    primary = _asset_query(dimensions, asset_type)
    variants = [primary]
    if asset_type == 'logo':
        variants.extend([
            urlencode({
                'page': '0',
                'styles': 'official,white,black,custom',
                'dimensions': '',
                'mimes': 'image/png,image/webp',
                'nsfw': 'false',
                'humor': 'any',
                'epilepsy': 'any',
                'oneoftag': '',
                'types': '',
            }),
            urlencode({'page': '0', 'styles': 'official,white,black,custom', 'mimes': 'image/png,image/webp'}),
            urlencode({'page': '0'}),
            '',
        ])
    seen = set()
    out = []
    for item in variants:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _url_from_asset_list(data):
    if isinstance(data, list) and data:
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ('url', 'thumb'):
                url = item.get(key)
                if url:
                    return str(url)
    return None


def _asset_is_from_zazamastro(item):
    if not isinstance(item, dict):
        return False
    author = item.get('author') or {}
    steam64 = str(author.get('steam64') or '').strip()
    name = str(author.get('name') or '').strip().lower()
    return steam64 == ZAZA_PROFILE_STEAM64 or name == ZAZA_AUTHOR_NAME


def _url_from_asset_list_by_author(data):
    if isinstance(data, list) and data:
        for item in data:
            if not _asset_is_from_zazamastro(item):
                continue
            for key in ('url', 'thumb'):
                url = item.get(key)
                if url:
                    return str(url), item
    return None, None


def _first_asset_url(game_id, asset_type, dimensions):
    api_type = _asset_api_type(asset_type)
    errors = []
    for qs in _asset_query_variants(dimensions, asset_type):
        try:
            path = f'/{api_type}/game/{game_id}?{qs}' if qs else f'/{api_type}/game/{game_id}'
            data = _sgdb_request(path)
            url = _url_from_asset_list(data)
            if url:
                return url
            errors.append('game endpoint returned no asset')
        except Exception as exc:
            errors.append(str(exc))
    return None


def _first_asset_url_for_app(appid, game_names, asset_type, dimensions):
    api_type = _asset_api_type(asset_type)
    errors = []

    if appid < 0x80000000:
        for qs in _asset_query_variants(dimensions, asset_type):
            try:
                path = f'/{api_type}/steam/{appid}?{qs}' if qs else f'/{api_type}/steam/{appid}'
                data = _sgdb_request(path)
                url = _url_from_asset_list(data)
                if url:
                    return url, 'steam-direct', None
                errors.append('steam-direct returned no asset')
            except Exception as exc:
                errors.append(f'steam-direct: {exc}')

    try:
        game_id = _first_sgdb_game_id(appid, game_names)
        if game_id:
            url = _first_asset_url(game_id, asset_type, dimensions)
            if url:
                return url, f'game-id {game_id}', None
            errors.append(f'game-id {game_id} returned no asset')
        else:
            errors.append('no SGDB game match')
    except Exception as exc:
        errors.append(f'game-search: {exc}')

    return None, '', '; '.join(errors[:4])




def _first_author_asset_url_for_app(appid, game_names, asset_type, dimensions):
    api_type = _asset_api_type(asset_type)
    qs = _asset_query(dimensions, asset_type)
    errors = []

    if appid < 0x80000000:
        try:
            data = _sgdb_request(f'/{api_type}/steam/{appid}?{qs}')
            url, item = _url_from_asset_list_by_author(data)
            if url:
                return url, 'steam-direct-zazamastro', None, item
            errors.append('steam-direct returned no ZazaMastro asset')
        except Exception as exc:
            errors.append(f'steam-direct: {exc}')

    try:
        game_id = _first_sgdb_game_id(appid, game_names)
        if game_id:
            data = _sgdb_request(f'/{api_type}/game/{game_id}?{qs}')
            url, item = _url_from_asset_list_by_author(data)
            if url:
                return url, f'game-id {game_id}', None, item
            errors.append(f'game-id {game_id} returned no ZazaMastro asset')
        else:
            errors.append('no SGDB game match')
    except Exception as exc:
        errors.append(f'game-search: {exc}')

    return None, '', '; '.join(errors[:4]), None


def _download_to_target(url, target_path):
    req = Request(url, headers={'User-Agent': 'decky-steamgriddb bulk backend'})
    with urlopen(req, context=get_ssl_context(), timeout=45) as res:
        image_data = res.read()
    with open(target_path, 'wb') as f:
        f.write(image_data)


def _save_min_logo_position_hint(appids):
    try:
        appids = [int(x) for x in appids if int(x) > 0]
    except Exception:
        appids = []
    return appids


def _bulk_zazamastro_fix(progress_callback=None):
    shortcut_names = _shortcut_names_by_appid()
    steam_names = _steam_app_names_by_appid()
    game_names = dict(steam_names)
    game_names.update(shortcut_names)
    stats = {
        'grid_appids': 0,
        'librarycache_candidates': 0,
        'installed_steam_apps': 0,
        'shortcut_apps': len(shortcut_names),
    }
    appids = sorted(_collect_all_known_appids(stats))
    total = len(appids)
    found = total
    downloaded = 0
    skipped = 0
    failed = 0
    errors = []
    logo_hide_appids = []
    grid_dirs = _grid_dirs()
    fallback_grid = _first_grid_dir()
    if not grid_dirs and fallback_grid:
        grid_dirs = [fallback_grid]

    def progress(**changes):
        if progress_callback:
            try:
                progress_callback(changes)
            except Exception:
                pass

    progress(
        phase='scanning', current=0, total=total, found=found,
        downloaded=0, skipped=0, failed=0,
        message=f'scanning library for ZazaMastro fix candidates...'
    )

    for index, appid in enumerate(appids, start=1):
        if _bulk_cancel_requested():
            return _bulk_cancel_result('ZazaMastro fix', found, downloaded, skipped, failed, errors, progress_callback)
        app_label = game_names.get(appid, str(appid))
        progress(
            phase='resolving', current=index - 1, total=total, found=found,
            downloaded=downloaded, skipped=skipped, failed=failed,
            message=f'{index}/{total} resolving ZazaMastro assets for {app_label}...'
        )
        try:
            destinations = grid_dirs if grid_dirs else ([fallback_grid] if fallback_grid else [])
            if not destinations:
                skipped += 1
                continue
            grid_dir = destinations[0]
            downloaded_any = False
            hero_downloaded = False

            # Banner / wide artwork 920x430
            wide_url, wide_method, wide_error, _wide_item = _first_author_asset_url_for_app(appid, game_names, 'grid_l', '920x430')
            if wide_url:
                progress(
                    phase='downloading', current=index - 1, total=total, found=found,
                    downloaded=downloaded, skipped=skipped, failed=failed,
                    message=f'{index}/{total} downloading ZazaMastro banner for {app_label} via {wide_method}...'
                )
                _remove_asset_variants(grid_dir, appid, 'wide')
                grid_dir.mkdir(parents=True, exist_ok=True)
                target_path = _target_path_for_asset(grid_dir, appid, 'wide', wide_url)
                _download_to_target(wide_url, target_path)
                downloaded_any = True

            # Hero artwork 3840x1240
            hero_url, hero_method, hero_error, _hero_item = _first_author_asset_url_for_app(appid, game_names, 'hero', '3840x1240')
            if hero_url:
                progress(
                    phase='downloading', current=index - 1, total=total, found=found,
                    downloaded=downloaded, skipped=skipped, failed=failed,
                    message=f'{index}/{total} downloading ZazaMastro hero for {app_label} via {hero_method}...'
                )
                _remove_asset_variants(grid_dir, appid, 'hero')
                grid_dir.mkdir(parents=True, exist_ok=True)
                target_path = _target_path_for_asset(grid_dir, appid, 'hero', hero_url)
                _download_to_target(hero_url, target_path)
                # ZazaMastro heroes already include the logo in the hero itself.
                # Keep the real Steam logo asset intact and let the frontend reduce
                # the logo position/size to Steam Big Picture's minimum.
                downloaded_any = True
                hero_downloaded = True

            if hero_url and appid not in logo_hide_appids:
                logo_hide_appids.append(appid)

            if downloaded_any:
                downloaded += 1
                progress(
                    phase='downloading', current=index, total=total, found=found,
                    downloaded=downloaded, skipped=skipped, failed=failed,
                    message=f'{index}/{total} applied ZazaMastro fix to {app_label}.'
                )
            else:
                skipped += 1
                detail_parts = []
                if wide_error:
                    detail_parts.append(f'banner: {wide_error}')
                if hero_error:
                    detail_parts.append(f'hero: {hero_error}')
                progress(
                    phase='downloading', current=index, total=total, found=found,
                    downloaded=downloaded, skipped=skipped, failed=failed,
                    message=f'{index}/{total} skipped {app_label} (no ZazaMastro banner/hero found).'
                )
        except Exception as exc:
            failed += 1
            errors.append(f'{appid} / {app_label}: {exc}')
            progress(
                phase='downloading', current=index, total=total, found=found,
                downloaded=downloaded, skipped=skipped, failed=failed,
                errors=errors[-5:],
                message=f'{index}/{total} failed {app_label}: {exc}'
            )

    msg = (
        f'Found {found}, downloaded {downloaded}, skipped {skipped}, failed {failed}. '
        f'Applied ZazaMastro banner/hero replacements where available. '
        f'Games with ZazaMastro heroes get the Steam logo position minimized while keeping the logo asset intact. '
        'Restart Steam if the library does not refresh.'
    )
    if errors:
        msg += ' First errors: ' + '; '.join(errors[:3])
    result = {
        'ok': failed == 0,
        'found': found,
        'downloaded': downloaded,
        'skipped': skipped,
        'failed': failed,
        'message': msg,
        'errors': errors[:50],
        'zaza_logo_hide_appids': _save_min_logo_position_hint(logo_hide_appids),
    }
    progress(
        phase='complete', current=found, total=found, found=found,
        downloaded=downloaded, skipped=skipped, failed=failed,
        message=msg, errors=errors[:50], result=result,
        zaza_logo_hide_appids=_save_min_logo_position_hint(logo_hide_appids),
        done=True, running=False,
    )
    return result


def _write_transparent_logo(grid_dir, appid):
    try:
        grid_dir.mkdir(parents=True, exist_ok=True)
        _remove_asset_variants(grid_dir, appid, 'logo')
        logo_path = grid_dir / f'{appid}_logo.png'
        logo_path.write_bytes(b64decode(TRANSPARENT_PNG_BASE64))
        return logo_path
    except Exception:
        return None


def _bulk_download_missing_logos(progress_callback=None):
    shortcut_names = _shortcut_names_by_appid()
    steam_names = _steam_app_names_by_appid()
    game_names = dict(steam_names)
    game_names.update(shortcut_names)
    stats = {
        'grid_appids': 0,
        'librarycache_candidates': 0,
        'installed_steam_apps': 0,
        'shortcut_apps': len(shortcut_names),
        'steam_app_names': len(steam_names),
    }
    grid_dirs = _grid_dirs()
    fallback_grid = _first_grid_dir()
    if not grid_dirs and fallback_grid:
        grid_dirs = [fallback_grid]
    appids = sorted(_collect_all_known_appids(stats))
    total = len(appids)
    candidates = []
    skipped = 0
    downloaded = 0
    failed = 0
    errors = []

    def progress(**changes):
        if progress_callback:
            try:
                progress_callback(changes)
            except Exception:
                pass

    progress(
        phase='scanning', current=0, total=total, found=0,
        downloaded=0, skipped=0, failed=0,
        message=f'scanning games with missing Steam logos...'
    )

    seen = set()
    for index, appid in enumerate(appids, start=1):
        if _bulk_cancel_requested():
            break
        destinations = grid_dirs if grid_dirs else ([fallback_grid] if fallback_grid else [])
        if not destinations:
            continue
        status, paths = _target_or_cached_logo_status(destinations, appid)
        if status == 'correct':
            skipped += 1
        else:
            grid_dir = destinations[0]
            key = (str(grid_dir), appid, 'logo')
            if key not in seen:
                seen.add(key)
                candidates.append((grid_dir, appid))
        if progress_callback and (index == 1 or index == total or index % 25 == 0):
            progress(
                phase='scanning', current=index, total=total, found=len(candidates),
                downloaded=downloaded, skipped=skipped, failed=failed,
                message=f'scanning logos {index}/{total} • need download {len(candidates)} • skipped {skipped} • failed {failed}'
            )

    found = len(candidates)
    if _bulk_cancel_requested():
        return _bulk_cancel_result('download missing Steam logos', found, downloaded, skipped, failed, errors, progress_callback)
    progress(
        phase='downloading' if found else 'complete', current=0, total=found, found=found,
        downloaded=downloaded, skipped=skipped, failed=failed,
        message=f'found {found} missing Steam logo(s); skipped {skipped} already present.'
    )

    for index, (grid_dir, appid) in enumerate(candidates, start=1):
        if _bulk_cancel_requested():
            return _bulk_cancel_result('download missing Steam logos', found, downloaded, skipped, failed, errors, progress_callback)
        app_label = game_names.get(appid, str(appid))
        try:
            progress(
                phase='searching', current=index - 1, total=found, found=found,
                downloaded=downloaded, skipped=skipped, failed=failed,
                message=f'{index}/{found} searching first available logo for {app_label}...'
            )
            url, resolve_method, resolve_error = _first_asset_url_for_app(appid, game_names, 'logo', None)
            if not url:
                failed += 1
                detail = resolve_error or 'no logo asset'
                errors.append(f'{appid} / {app_label}: {detail}')
                progress(
                    phase='downloading', current=index, total=found, found=found,
                    downloaded=downloaded, skipped=skipped, failed=failed,
                    errors=errors[-5:],
                    message=f'{index}/{found} no logo for {app_label}: {detail}'
                )
                continue
            progress(
                phase='downloading', current=index - 1, total=found, found=found,
                downloaded=downloaded, skipped=skipped, failed=failed,
                message=f'{index}/{found} downloading logo for {app_label} via {resolve_method}...'
            )
            _remove_asset_variants(grid_dir, appid, 'logo')
            grid_dir.mkdir(parents=True, exist_ok=True)
            target_path = _target_path_for_asset(grid_dir, appid, 'logo', url)
            _download_to_target(url, target_path)
            downloaded += 1
            progress(
                phase='downloading', current=index, total=found, found=found,
                downloaded=downloaded, skipped=skipped, failed=failed,
                message=f'{index}/{found} saved logo for {app_label}.'
            )
        except Exception as exc:
            failed += 1
            errors.append(f'{appid} / {app_label}: {exc}')
            progress(
                phase='downloading', current=index, total=found, found=found,
                downloaded=downloaded, skipped=skipped, failed=failed,
                errors=errors[-5:],
                message=f'{index}/{found} failed {app_label}: {exc}'
            )

    msg = (
        f'Found {found}, downloaded {downloaded}, skipped {skipped}, failed {failed}. '
        'Downloaded first available SteamGridDB logo where Steam/custom logos were missing. '
        'Restart Steam if the library does not refresh.'
    )
    if errors:
        msg += ' First errors: ' + '; '.join(errors[:3])
    result = {'ok': failed == 0, 'found': found, 'downloaded': downloaded, 'skipped': skipped, 'failed': failed, 'message': msg, 'errors': errors[:50]}
    progress(phase='complete', current=found, total=found, found=found, downloaded=downloaded, skipped=skipped, failed=failed, message=msg, errors=errors[:50], result=result, done=True, running=False)
    return result


def _bulk_restore_original_steam_artwork(progress_callback=None):
    grid_dirs = _grid_dirs()
    entries = []
    for grid_dir in grid_dirs:
        try:
            for path in grid_dir.iterdir():
                entries.append(path)
        except Exception:
            pass

    total = len(entries)
    deleted = 0
    failed = 0
    errors = []

    def progress(**changes):
        if progress_callback:
            try:
                progress_callback(changes)
            except Exception:
                pass

    progress(
        phase='deleting', current=0, total=total, found=total,
        downloaded=0, skipped=0, failed=0,
        message=f'restoring original Steam artwork by emptying grid folders ({total} item(s))...'
    )

    for index, path in enumerate(entries, start=1):
        if _bulk_cancel_requested():
            return _bulk_cancel_result('restore original Steam artwork', total, deleted, 0, failed, errors, progress_callback)
        try:
            if path.is_dir():
                for child in path.rglob('*'):
                    try:
                        if child.is_file() or child.is_symlink():
                            child.unlink()
                    except Exception:
                        pass
                # Remove directories deepest first
                for child in sorted(path.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                    try:
                        if child.is_dir():
                            child.rmdir()
                    except Exception:
                        pass
                try:
                    path.rmdir()
                except Exception:
                    pass
            else:
                path.unlink()
            deleted += 1
        except Exception as exc:
            failed += 1
            errors.append(f'{path.name}: {exc}')
        if index == 1 or index == total or index % 25 == 0:
            progress(
                phase='deleting', current=index, total=total, found=total,
                downloaded=deleted, skipped=0, failed=failed,
                errors=errors[-5:],
                message=f'restoring originals {index}/{total} • removed {deleted} • failed {failed}'
            )

    msg = f'emptied Steam grid folders: removed {deleted} item(s), failed {failed}. Restart Steam so it can repopulate artwork/cache.'
    if errors:
        msg += ' First errors: ' + '; '.join(errors[:3])
    result = {'ok': failed == 0, 'found': total, 'downloaded': deleted, 'skipped': 0, 'failed': failed, 'message': msg, 'errors': errors[:50]}
    progress(phase='complete', current=total, total=total, found=total, downloaded=deleted, skipped=0, failed=failed, message=msg, errors=errors[:50], result=result, done=True, running=False)
    return result



def _collect_target_missing_assets(target_kind, target_width, target_height, stats, progress_callback=None):
    seen = set()
    grid_dirs = _grid_dirs()
    fallback_grid = _first_grid_dir()
    if not grid_dirs and fallback_grid:
        grid_dirs = [fallback_grid]

    appids = sorted(_collect_all_known_appids(stats))
    total = len(appids)
    candidates = []
    target_correct = 0
    target_wrong = 0
    target_missing = 0

    for index, appid in enumerate(appids, start=1):
        if _bulk_cancel_requested():
            break
        destinations = grid_dirs if grid_dirs else ([fallback_grid] if fallback_grid else [])
        if not destinations:
            continue
        status, paths = _target_asset_status(destinations, appid, target_kind, target_width, target_height)
        if status == 'correct':
            target_correct += 1
        else:
            if status == 'wrong':
                target_wrong += 1
            else:
                target_missing += 1
            grid_dir = destinations[0]
            key = (str(grid_dir), appid, target_kind)
            if key not in seen:
                seen.add(key)
                candidates.append((grid_dir, appid, paths[0] if paths else None, f'target-{status}'))

        if progress_callback and (index == 1 or index == total or index % 25 == 0):
            try:
                percent = round((index / total) * 100, 1) if total else 0
                progress_callback(
                    phase='scanning',
                    current=index,
                    total=total,
                    percent=percent,
                    found=len(candidates),
                    downloaded=0,
                    skipped=target_correct,
                    failed=0,
                    message=(
                        f'scanning {index}/{total} • '
                        f'need download {len(candidates)} • skipped {target_correct} • failed 0'
                    )
                )
            except Exception:
                pass

    stats['target_correct'] = target_correct
    stats['target_wrong'] = target_wrong
    stats['target_missing'] = target_missing
    return candidates


def _bulk_download_artwork(source_kind, source_size, target_kind, asset_type, target_dimensions, target_size, progress_callback=None, fallback_dimensions=None):
    shortcut_names = _shortcut_names_by_appid()
    steam_names = _steam_app_names_by_appid()
    game_names = dict(steam_names)
    game_names.update(shortcut_names)
    found = 0
    downloaded = 0
    skipped = 0
    failed = 0
    errors = []
    stats = {
        'grid_exact': 0,
        'librarycache_candidates': 0,
        'installed_steam_apps': 0,
        'shortcut_apps': len(shortcut_names),
        'steam_app_names': len(steam_names),
        'grid_appids': 0,
        'target_correct': 0,
        'target_wrong': 0,
        'target_missing': 0,
    }

    def progress(**changes):
        if progress_callback:
            try:
                progress_callback(changes)
            except Exception:
                pass

    progress(
        phase='scanning',
        current=0,
        total=0,
        found=0,
        downloaded=0,
        skipped=0,
        failed=0,
        message=f'scanning games: covers download only when missing or explicit legacy horizontal cover...'
    )

    candidates = _collect_target_missing_assets(target_kind, target_size[0], target_size[1], stats, progress)
    found = len(candidates)
    skipped = _safe_int(stats.get('target_correct'), 0)
    if _bulk_cancel_requested():
        return _bulk_cancel_result(f'download {target_dimensions} artwork', found, downloaded, skipped, failed, errors, progress_callback)

    progress(
        phase='downloading' if found else 'complete',
        current=0,
        total=found,
        found=found,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        message=(
            f'found {found} game(s) with missing or explicit legacy horizontal cover. '
            f'Scan: grid apps {stats.get("grid_appids", 0)}, librarycache cache entries {stats.get("librarycache_candidates", 0)}, '
            f'installed Steam apps {stats.get("installed_steam_apps", 0)}, shortcuts {stats.get("shortcut_apps", 0)}, '
            f'correct target {stats.get("target_correct", 0)}, wrong target {stats.get("target_wrong", 0)}, missing target {stats.get("target_missing", 0)}.'
        )
    )

    for index, (grid_dir, appid, source_path, origin) in enumerate(candidates, start=1):
        if _bulk_cancel_requested():
            return _bulk_cancel_result(f'download {target_dimensions} artwork', found, downloaded, skipped, failed, errors, progress_callback)
        app_label = game_names.get(appid, str(appid))
        progress(
            phase='resolving',
            current=index - 1,
            total=found,
            found=found,
            downloaded=downloaded,
            skipped=skipped,
            failed=failed,
            message=f'{index}/{found} resolving {app_label} ({origin})...'
        )
        try:
            progress(
                phase='searching',
                current=index - 1,
                total=found,
                found=found,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                message=f'{index}/{found} searching {target_dimensions} artwork for {app_label}...'
            )
            url, resolve_method, resolve_error = _first_asset_url_for_app(appid, game_names, asset_type, target_dimensions)
            used_dimensions = target_dimensions
            if not url and fallback_dimensions:
                progress(
                    phase='searching',
                    current=index - 1,
                    total=found,
                    found=found,
                    downloaded=downloaded,
                    skipped=skipped,
                    failed=failed,
                    message=f'{index}/{found} no {target_dimensions} artwork for {app_label}; trying fallback {fallback_dimensions}...'
                )
                fallback_url, fallback_method, fallback_error = _first_asset_url_for_app(appid, game_names, asset_type, fallback_dimensions)
                if fallback_url:
                    url = fallback_url
                    resolve_method = f'{fallback_method} fallback {fallback_dimensions}'
                    used_dimensions = fallback_dimensions
                else:
                    primary_error = resolve_error or f'no {target_dimensions} asset'
                    fallback_error = fallback_error or 'no fallback asset'
                    resolve_error = f'{primary_error}; fallback {fallback_dimensions}: {fallback_error}'
            if not url:
                failed += 1
                detail = resolve_error or f'no {target_dimensions} asset'
                errors.append(f'{appid} / {app_label}: {detail} ({origin})')
                progress(
                    phase='downloading',
                    current=index,
                    total=found,
                    found=found,
                    downloaded=downloaded,
                    skipped=skipped,
                    failed=failed,
                    errors=errors[-5:],
                    message=f'{index}/{found} no {target_dimensions} artwork for {app_label}: {detail}'
                )
                continue

            progress(
                phase='downloading',
                current=index - 1,
                total=found,
                found=found,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                message=f'{index}/{found} downloading {used_dimensions} artwork for {app_label} via {resolve_method}...'
            )
            _remove_asset_variants(grid_dir, appid, target_kind)
            grid_dir.mkdir(parents=True, exist_ok=True)
            target_path = _target_path_for_asset(grid_dir, appid, target_kind, url)
            req = Request(url, headers={'User-Agent': 'decky-steamgriddb bulk backend'})
            with urlopen(req, context=get_ssl_context(), timeout=45) as res:
                image_data = res.read()
            with open(target_path, 'wb') as f:
                f.write(image_data)
            downloaded += 1
            progress(
                phase='downloading',
                current=index,
                total=found,
                found=found,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                message=f'{index}/{found} saved {used_dimensions} artwork for {app_label}.'
            )
        except Exception as exc:
            failed += 1
            errors.append(f'{appid} / {app_label}: {exc}')
            progress(
                phase='downloading',
                current=index,
                total=found,
                found=found,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                errors=errors[-5:],
                message=f'{index}/{found} failed {app_label}: {exc}'
            )

    msg = (
        f'Found {found}, downloaded {downloaded}, skipped {skipped}, failed {failed}. '
        f'Scan: grid apps {stats.get("grid_appids", 0)}, librarycache cache entries {stats.get("librarycache_candidates", 0)}, '
        f'installed Steam apps {stats.get("installed_steam_apps", 0)}, shortcuts {stats.get("shortcut_apps", 0)}, '
        f'correct target {stats.get("target_correct", 0)}, wrong target {stats.get("target_wrong", 0)}, missing target {stats.get("target_missing", 0)}. '
        'Restart Steam if the library does not refresh.'
    )
    if errors:
        msg += ' First errors: ' + '; '.join(errors[:3])
    result = {
        'ok': failed == 0,
        'found': found,
        'downloaded': downloaded,
        'skipped': skipped,
        'failed': failed,
        'message': msg,
        'errors': errors[:50],
    }
    progress(
        phase='complete',
        current=found,
        total=found,
        found=found,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        message=msg,
        errors=errors[:50],
        result=result,
        done=True,
        running=False,
    )
    return result


def _bulk_download_all_artwork(progress_callback=None):
    steps = [
        ('covers', _BULK_COMMANDS['bulk_download_portrait_capsules']['args']),
        ('banners', _BULK_COMMANDS['bulk_download_wide_banners']['args']),
        ('heroes', _BULK_COMMANDS['bulk_download_high_res_heroes']['args']),
        ('logos', None),
    ]
    total_steps = len(steps)
    aggregate = {'found': 0, 'downloaded': 0, 'skipped': 0, 'failed': 0, 'errors': []}

    def emit(changes):
        if progress_callback:
            try:
                progress_callback(changes)
            except Exception:
                pass

    emit({
        'phase': 'scanning', 'current': 0, 'total': total_steps,
        'found': 0, 'downloaded': 0, 'skipped': 0, 'failed': 0,
        'message': 'starting all artwork operations: covers, banners, heroes and logos...',
    })

    for step_index, (label, args) in enumerate(steps, start=1):
        if _bulk_cancel_requested():
            return _bulk_cancel_result('download all artwork', aggregate['found'], aggregate['downloaded'], aggregate['skipped'], aggregate['failed'], aggregate['errors'], progress_callback)
        is_last = step_index == total_steps

        def step_progress(changes, step_label=label, index=step_index, last=is_last):
            changes = dict(changes or {})
            message = changes.get('message') or ''
            changes['message'] = f'[{index}/{total_steps}] {step_label}: {message}'
            if changes.get('done') and not last and not _bulk_cancel_requested():
                changes.pop('done', None)
                changes.pop('result', None)
                changes['running'] = True
                changes['phase'] = 'between-steps'
            emit(changes)

        if label == 'logos':
            result = _bulk_download_missing_logos(progress_callback=step_progress)
        else:
            result = _bulk_download_artwork(**args, progress_callback=step_progress)

        aggregate['found'] += _safe_int(result.get('found'), 0) if isinstance(result, dict) else 0
        aggregate['downloaded'] += _safe_int(result.get('downloaded'), 0) if isinstance(result, dict) else 0
        aggregate['skipped'] += _safe_int(result.get('skipped'), 0) if isinstance(result, dict) else 0
        aggregate['failed'] += _safe_int(result.get('failed'), 0) if isinstance(result, dict) else 0
        if isinstance(result, dict) and result.get('errors'):
            aggregate['errors'].extend(result.get('errors') or [])
        if isinstance(result, dict) and result.get('cancelled'):
            return _bulk_cancel_result('download all artwork', aggregate['found'], aggregate['downloaded'], aggregate['skipped'], aggregate['failed'], aggregate['errors'], progress_callback)

    msg = (
        f'All artwork operations complete. Found {aggregate["found"]}, downloaded {aggregate["downloaded"]}, '
        f'skipped {aggregate["skipped"]}, failed {aggregate["failed"]}. '
        'Covers replace missing and explicit legacy horizontal covers; banners and heroes use fallback sizes when high-res assets are unavailable. Restart Steam if the library does not refresh.'
    )
    if aggregate['errors']:
        msg += ' First errors: ' + '; '.join(aggregate['errors'][:3])
    final = {
        'ok': aggregate['failed'] == 0,
        'found': aggregate['found'],
        'downloaded': aggregate['downloaded'],
        'skipped': aggregate['skipped'],
        'failed': aggregate['failed'],
        'message': msg,
        'errors': aggregate['errors'][:50],
    }
    emit({
        'phase': 'complete', 'current': total_steps, 'total': total_steps,
        'found': aggregate['found'], 'downloaded': aggregate['downloaded'],
        'skipped': aggregate['skipped'], 'failed': aggregate['failed'],
        'message': msg, 'errors': aggregate['errors'][:50],
        'result': final, 'done': True, 'running': False,
    })
    return final


_BULK_COMMANDS = {
    'bulk_download_portrait_capsules': {
        'label': '600×900 covers',
        'args': dict(
            source_kind='wide',
            source_size=(460, 215),
            target_kind='portrait',
            asset_type='grid_p',
            target_dimensions='600x900',
            target_size=(600, 900),
        ),
    },
    'bulk_download_wide_banners': {
        'label': '920×430 banners',
        'args': dict(
            source_kind='wide',
            source_size=(460, 215),
            target_kind='wide',
            asset_type='grid_l',
            target_dimensions='920x430',
            target_size=(920, 430),
            fallback_dimensions='460x215',
        ),
    },
    'bulk_download_high_res_heroes': {
        'label': '3840×1240 heroes',
        'args': dict(
            source_kind='hero',
            source_size=(1920, 620),
            target_kind='hero',
            asset_type='hero',
            target_dimensions='3840x1240',
            target_size=(3840, 1240),
            fallback_dimensions='1920x620',
        ),
    },
    'bulk_download_missing_logos': {
        'label': 'missing Steam logos',
        'args': None,
    },
    'bulk_zazamastro_fix': {
        'label': 'ZazaMastro fix',
        'args': None,
    },
    'bulk_restore_original_steam_artwork': {
        'label': 'restore original Steam artwork',
        'args': None,
    },
    'bulk_download_all_artwork': {
        'label': 'download all artwork',
        'args': None,
    },
}


def _bulk_worker(job_id, command_name):
    command = _BULK_COMMANDS.get(command_name)
    if not command:
        _bulk_job_update(
            job_id,
            running=False,
            done=True,
            phase='error',
            message=f'Unknown bulk command: {command_name}',
            failed=1,
        )
        return

    try:
        if command_name == 'bulk_zazamastro_fix':
            _bulk_zazamastro_fix(
                progress_callback=lambda changes: _bulk_job_update(job_id, **changes)
            )
        elif command_name == 'bulk_download_all_artwork':
            _bulk_download_all_artwork(
                progress_callback=lambda changes: _bulk_job_update(job_id, **changes)
            )
        elif command_name == 'bulk_download_missing_logos':
            _bulk_download_missing_logos(
                progress_callback=lambda changes: _bulk_job_update(job_id, **changes)
            )
        elif command_name == 'bulk_restore_original_steam_artwork':
            _bulk_restore_original_steam_artwork(
                progress_callback=lambda changes: _bulk_job_update(job_id, **changes)
            )
        else:
            _bulk_download_artwork(
                **command['args'],
                progress_callback=lambda changes: _bulk_job_update(job_id, **changes)
            )
    except Exception as exc:
        _bulk_job_update(
            job_id,
            running=False,
            done=True,
            phase='error',
            message=f'bulk job failed: {exc}',
            failed=_safe_int(_bulk_job.get('failed'), 0) + 1,
            errors=[str(exc)],
        )



class Plugin:
    async def _main(self):
        self.settings = SettingsManager(name="steamgriddb", settings_directory=decky.DECKY_PLUGIN_SETTINGS_DIR)
        global _plugin_settings
        _plugin_settings = self.settings

    async def _unload(self):
        pass

    async def download_as_base64(self, url=''):
        req = Request(url, headers={'User-Agent': 'decky-steamgriddb backend'})
        content = urlopen(req, context=get_ssl_context()).read()
        return b64encode(content).decode('utf-8')

    async def read_file_as_base64(self, path=''):
        with open(path, 'rb') as image_file:
            return b64encode(image_file.read()).decode('utf-8')

    async def get_local_start(self):
        return decky.DECKY_USER_HOME

    async def download_file(self, url='', output_dir='', file_name=''):
        decky.logger.debug({url, output_dir, file_name})
        try:
            if access(dirname(output_dir), W_OK):
                req = Request(url, headers={'User-Agent': 'decky-steamgriddb backend'})
                res = urlopen(req, context=get_ssl_context())
                if res.status == 200:
                    with open(Path(output_dir) / file_name, mode='wb') as f:
                        f.write(res.read())
                    return str(Path(output_dir) / file_name)
                return False
        except:
            return False

        return False

    async def set_shortcut_icon_from_path(self, appid, owner_id, path):
        ext = Path(path).suffix
        iconname = "%s_icon%s" % (appid, ext)
        output_file = get_userdata_config(owner_id) / 'grid' / iconname
        saved_path = str(copyfile(path, output_file))
        return await self.set_shortcut_icon(appid, owner_id, path=saved_path)

    async def set_shortcut_icon_from_url(self, appid, owner_id, url):
        output_dir = get_userdata_config(owner_id) / 'grid'
        ext = Path(urlparse(url).path).suffix
        iconname = "%s_icon%s" % (appid, ext)
        saved_path = await self.download_file(url, output_dir, file_name=iconname)
        if saved_path:
            return await self.set_shortcut_icon(appid, owner_id, path=saved_path)
        else:
            raise Exception("Failed to download icon from %s" % url)

    async def set_shortcut_icon(self, appid, owner_id, path=None):
        shortcuts_vdf = get_userdata_config(owner_id) / 'shortcuts.vdf'

        d = binary_load(open(shortcuts_vdf, "rb"))
        for shortcut in d['shortcuts'].values():
            shortcut_appid = (shortcut['appid'] & 0xffffffff) | 0x80000000
            if shortcut_appid == appid:
                if shortcut['icon'] == path:
                    return 'icon_is_same_path'

                # Clear icon
                if path is None:
                    shortcut['icon'] = ''
                else:
                    shortcut['icon'] = path
                binary_dump(d, open(shortcuts_vdf, 'wb'))
                return True
        raise Exception('Could not find shortcut to edit')

    async def set_steam_icon_from_url(self, appid, url):
        await self.download_file(url, get_steam_libcache(), file_name=("%s_icon.jpg" % appid))

    async def set_steam_icon_from_path(self, appid, path):
        copyfile(path, get_steam_libcache() / str("%s_icon.jpg" % appid))

    async def set_setting(self, key, value):
        self.settings.setSetting(key, value)

    async def get_setting(self, key, fallback):
        return self.settings.getSetting(key, fallback)

    async def get_bulk_sgdb_api_key(self):
        return self.settings.getSetting('bulk_sgdb_api_key', '')

    async def set_bulk_sgdb_api_key(self, value=''):
        self.settings.setSetting('bulk_sgdb_api_key', str(value or '').strip())
        return True

    async def validate_bulk_sgdb_api_key(self, value=''):
        api_key = str(value or '').strip()
        if not api_key:
            return {
                'ok': False,
                'message': 'SteamGridDB API key is empty.',
            }
        try:
            _sgdb_request_with_key('/search/autocomplete/Portal', api_key)
            return {
                'ok': True,
                'message': 'SteamGridDB API key is valid.',
            }
        except Exception as exc:
            return {
                'ok': False,
                'message': str(exc),
            }


    async def start_bulk_artwork_job(self, command_name=''):
        command = _BULK_COMMANDS.get(command_name)
        if not command:
            return {
                'ok': False,
                'running': False,
                'done': True,
                'phase': 'error',
                'message': f'Unknown bulk command: {command_name}',
            }

        with _bulk_job_lock:
            if _bulk_job.get('running'):
                return json.loads(json.dumps(_bulk_job, default=str))
            job_id = _safe_int(_bulk_job.get('id'), 0) + 1
            _bulk_job.update({
                'id': job_id,
                'running': True,
                'done': False,
                'label': command.get('label', command_name),
                'phase': 'queued',
                'current': 0,
                'total': 0,
                'percent': 0,
                'found': 0,
                'downloaded': 0,
                'skipped': 0,
                'failed': 0,
                'message': f'queued {command.get("label", command_name)}...',
                'errors': [],
                'result': None,
                'started_at': time.time(),
                'updated_at': time.time(),
                'cancel_requested': False,
                'cancelled': False,
            })
            snapshot = json.loads(json.dumps(_bulk_job, default=str))

        thread = threading.Thread(target=_bulk_worker, args=(job_id, command_name), daemon=True)
        thread.start()
        return snapshot

    async def cancel_bulk_artwork_job(self):
        with _bulk_job_lock:
            if not _bulk_job.get('running'):
                return json.loads(json.dumps(_bulk_job, default=str))
            _bulk_job['cancel_requested'] = True
            _bulk_job['message'] = 'Cancelling current bulk operation...'
            _bulk_job['phase'] = 'cancelling'
            _bulk_job['updated_at'] = time.time()
            return json.loads(json.dumps(_bulk_job, default=str))

    async def get_bulk_artwork_progress(self):
        return _bulk_job_snapshot()

    async def bulk_download_portrait_capsules(self):
        return _bulk_download_artwork(
            source_kind='wide',
            source_size=(460, 215),
            target_kind='portrait',
            asset_type='grid_p',
            target_dimensions='600x900',
            target_size=(600, 900)
        )

    async def bulk_download_wide_banners(self):
        return _bulk_download_artwork(
            source_kind='wide',
            source_size=(460, 215),
            target_kind='wide',
            asset_type='grid_l',
            target_dimensions='920x430',
            target_size=(920, 430),
            fallback_dimensions='460x215'
        )

    async def bulk_download_high_res_heroes(self):
        return _bulk_download_artwork(
            source_kind='hero',
            source_size=(1920, 620),
            target_kind='hero',
            asset_type='hero',
            target_dimensions='3840x1240',
            target_size=(3840, 1240),
            fallback_dimensions='1920x620'
        )

    async def _migration(self):
        decky.migrate_settings(str(Path(decky.DECKY_HOME) / "settings" / "steamgriddb.json"))
