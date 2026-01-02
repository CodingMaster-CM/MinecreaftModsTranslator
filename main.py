import os
import sys
import json
import re
import shutil
import zipfile
import tempfile
import requests
from pathlib import Path
from urllib.parse import quote
from tqdm import tqdm
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# Try to import TOML parser (Python 3.11+ has tomllib, older needs tomli)
try:
    import tomllib as toml
except ImportError:
    try:
        import tomli as toml
    except ImportError:
        toml = None

# API資料和回復map陣列
VERSION = "2.6.0"

# Load language data from external JSON file
try:
    with open('InteractLanguage.json', 'r', encoding='utf-8') as f:
        lang_data = json.load(f)
    UI_STRINGS = lang_data.get("UI_STRINGS", {})
    LANGUAGE_INFO = lang_data.get("LANGUAGE_INFO", {})
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"Error loading InteractLanguage.json: {e}")
    sys.exit(1)


# List of core/API mods that should never be processed.
IGNORED_MODS = [
    "fabric-api", "fabric-loader", "sodium", "iris", "lithium", "indium", "c2me",
    "cloth-config", "architectury", "geckolib", "modernfix", "modmenu",
    "forge", "minecraft", "bclib", "betterend", "betternether",
    "porting_lib", "puzzleslib", "bookshelf", "moonlight",
    "cardinal-components", "owo-lib", "pehkui", "spell_engine", "resourcefullib",
    "yungsapi", "attributefix"
]

# Multi-language terminology for fallback translation.
TERMINOLOGY = {
    "zh_tw": {
        "Copper": "銅", "Aluminum": "鋁", "Aluminium": "鋁", "Lead": "鉛", "Silver": "銀",
        "Nickel": "鎳", "Uranium": "鈾", "Constantan": "康銅", "Electrum": "琥珀金", "Steel": "鋼",
        "Iron": "鐵", "Gold": "金", "Tin": "錫", "Zinc": "鋅", "Brass": "黃銅", "Ingot": "錠",
        "Ore": "礦", "Block": "方塊", "Plate": "板", "Dust": "粉", "Nugget": "粒", "Stick": "棒",
        "Rod": "桿", "Tool": "工具", "Machine": "機器", "Generator": "發電機", "Engineer": "工程師",
        "Workbench": "工作台", "Furnace": "熔爐", "Crucible": "坩堝", "Conveyor": "輸送帶",
        "Pump": "泵", "Tank": "儲罐", "Silo": "筒倉", "Barrel": "桶", "Bucket": "桶",
        "Helmet": "頭盔", "Chestplate": "胸甲", "Leggings": "護腿", "Boots": "靴子",
        "Fluid": "流體", "Item": "物品", "Wire": "電線", "Cable": "電纜", "Pipe": "管",
        "Manual": "手冊", "Pickaxe": "鎬", "Shovel": "鏟", "Axe": "斧", "Hoe": "鋤", "Sword": "劍"
    },
    "zh_cn": {
        "Copper": "铜", "Aluminum": "铝", "Aluminium": "铝", "Lead": "铅", "Silver": "银",
        "Nickel": "镍", "Uranium": "铀", "Constantan": "康铜", "Electrum": "琥珀金", "Steel": "钢",
        "Iron": "铁", "Gold": "金", "Tin": "锡", "Zinc": "锌", "Brass": "黄铜", "Ingot": "锭",
        "Ore": "矿石", "Block": "方块", "Plate": "板", "Dust": "粉", "Nugget": "粒", "Stick": "棒",
        "Rod": "杆", "Tool": "工具", "Machine": "机器", "Generator": "发电机", "Engineer": "工程师",
        "Workbench": "工作台", "Furnace": "熔炉", "Crucible": "坩埚", "Conveyor": "传送带",
        "Pump": "泵", "Tank": "储罐", "Silo": "筒仓", "Barrel": "桶", "Bucket": "桶",
        "Helmet": "头盔", "Chestplate": "胸甲", "Leggings": "护腿", "Boots": "靴子",
        "Fluid": "流体", "Item": "物品", "Wire": "电线", "Cable": "电缆", "Pipe": "管",
        "Manual": "手册", "Pickaxe": "镐", "Shovel": "铲", "Axe": "斧", "Hoe": "锄", "Sword": "剑"
    },
}

# 翻譯引擎
class Translator:
    def __init__(self):
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.cache = {}

    def _protect_formatting(self, text):
        placeholders = {}
        pattern = re.compile(r'(§[0-9a-fk-or]|%[0-9]*\$?[sd]|%[sd]|\{[a-zA-Z0-9_]+\})')
        
        def replace_match(match):
            key = f"__FMT{len(placeholders)}__"
            placeholders[key] = match.group(0)
            return key

        protected_text = pattern.sub(replace_match, text)
        return protected_text, placeholders

    def _restore_formatting(self, text, placeholders):
        text = re.sub(r'__\s*FMT(\d+)\s*__', r'__FMT\1__', text)
        for key, value in placeholders.items():
            text = text.replace(key, value)
        return text

    def _google_translate_api(self, text, source_lang, target_lang):
        encoded_text = quote(text)
        url = f'https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={encoded_text}'
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        r = self.session.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        
        response_json = r.json()
        result = "".join(segment[0] for segment in response_json[0] if segment[0])

        if result:
            result = result.replace('“', '"').replace('”', '"')
            result = result.replace('：', ':')
            result = result.replace('｛', '{').replace('｝', '}')
            result = result.replace('［', '[').replace('］', ']')
            if text.lower() == 'true': return 'true'
            if text.lower() == 'false': return 'false'
        return result

    def _fallback_translate(self, text, target_lang):
        result = text
        if target_lang in TERMINOLOGY:
            terminology = TERMINOLOGY[target_lang]
            sorted_keys = sorted(terminology.keys(), key=len, reverse=True)
            for eng in sorted_keys:
                trans = terminology[eng]
                result = re.sub(rf'\b{re.escape(eng)}\b', trans, result, flags=re.IGNORECASE)
        
        if result == text and target_lang != "en_us" and re.search(r'[a-zA-Z]', text):
            lang_prefix = LANGUAGE_INFO.get(target_lang, {}).get("name", target_lang)
            return f"[{lang_prefix}] {text}"
        return result

    def translate(self, ui_lang, text, source_lang, target_lang):
        if text is None or not str(text).strip():
            return text
        text = str(text)

        protected_text, placeholders = self._protect_formatting(text)
        cache_key = f"{source_lang}:{target_lang}:{protected_text}"
        if cache_key in self.cache:
            return self._restore_formatting(self.cache[cache_key], placeholders)

        try:
            translated = self._google_translate_api(protected_text, source_lang, target_lang)
            if translated:
                self.cache[cache_key] = translated
                return self._restore_formatting(translated, placeholders)
        except Exception as e:
            if "429" in str(e):
                print(f"  {ui_get(ui_lang, 'throttled')} {text[:30]}...")
            else:
                print(f"  {ui_get(ui_lang, 'trans_err')} {str(e)}")
        
        # Fallback if API fails
        fallback_result = self._fallback_translate(protected_text, target_lang.replace("-", "_").lower())
        return self._restore_formatting(fallback_result, placeholders)

# 介面與系統輔助函式
def ui_get(ui_lang: str, key: str) -> str:
    # Update banner title with the correct version at runtime
    if key == "banner_title":
        return UI_STRINGS.get(ui_lang, UI_STRINGS["en_us"]).get(key, "").format(VERSION=VERSION)
    return UI_STRINGS.get(ui_lang, UI_STRINGS["en_us"]).get(key, key)

def hr():
    print("─" * 80)

def install_required_packages(ui_lang: str):
    required = ['requests', 'tqdm']
    if sys.version_info < (3, 11):
        required.append('tomli')
    
    missing = []
    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    if missing:
        print(ui_get(ui_lang, "need_packages") + " " + ", ".join(missing))
        confirm = input(ui_get(ui_lang, "install_confirm")).strip().lower()
        if not confirm or confirm in ['y', 'yes']:
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--user'] + missing)
                print("Packages installed successfully. Please restart the script.")
                sys.exit(0)
            except Exception as e:
                print(f"{ui_get(ui_lang, 'install_fail')} {e}")
                print(ui_get(ui_lang, "install_hint"))
                sys.exit(1)
        else:
            sys.exit(1)

def get_folder_path_from_user(ui_lang: str) -> Path:
    while True:
        hr()
        print(ui_get(ui_lang, "enter_mods_path"))
        common_paths = [p for p in [Path.home() / ".minecraft/mods", Path(os.getenv("APPDATA", "")) / ".minecraft/mods" if os.name == "nt" else None] if p and p.exists()]
        if common_paths:
            print(ui_get(ui_lang, "common_paths"))
            for i, p in enumerate(common_paths, 1):
                print(f"  {i}. {p}")
        
        folder_path_str = input(ui_get(ui_lang, "path")).strip().strip('"\'')
        folder_path = Path(folder_path_str)

        if folder_path.is_dir() and any(f.suffix == '.jar' for f in folder_path.iterdir()):
            print(f"Found {len(list(folder_path.glob('*.jar')))} .jar files.")
            return folder_path

        print(ui_get(ui_lang, "path_invalid"))
        retry = input(ui_get(ui_lang, "retry")).strip().lower()
        if retry in ['n', 'no']:
            sys.exit(0)

# 核心邏輯
class ModProcessor:
    def __init__(self, ui_lang, translator):
        self.ui_lang = ui_lang
        self.translator = translator

    def analyze_jar(self, jar_path, target_lang):
        # 1. Blacklist Filter
        if any(ignored in jar_path.name.lower() for ignored in IGNORED_MODS):
            return (jar_path, 1, "ignored (core/library mod)")

        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                file_list = jar.namelist()
                
                # 2. Author Declaration Filter (Fabric)
                if 'fabric.mod.json' in file_list:
                    with jar.open('fabric.mod.json') as f:
                        try:
                            mod_info = json.load(f)
                            if mod_info.get('custom', {}).get('modmenu', {}).get('api') is True:
                                return (jar_path, 1, "ignored (author marked as API)")
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass # Ignore malformed json

                # 3. Basic Conditions Check
                lang_files = [f for f in file_list if '/lang/' in f.lower() and f.lower().endswith('.json')]
                en_us_path = next((f for f in lang_files if f.lower().endswith('en_us.json')), None)

                if not en_us_path:
                    return (jar_path, 1, "missing en_us.json")
                
                if any(f'/{target_lang}.json' in f.lower() for f in lang_files):
                    return (jar_path, 1, f"already has {target_lang}.json")

                with jar.open(en_us_path) as f:
                    try:
                        en_data = json.load(f)
                        translatable_keys = [k for k in en_data if k not in ("language", "language.code", "language.region")]
                        if not translatable_keys:
                            return (jar_path, 1, "en_us.json has no translatable content")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                         return (jar_path, 1, "en_us.json is malformed")


                # 4. Content Footprint Analysis
                is_content_mod = (
                    len(lang_files) > 1 or
                    any("textures/gui" in f.lower() for f in file_list) or
                    any("patchouli_books" in f.lower() for f in file_list) or
                    any("advancements" in f.lower() for f in file_list)
                )
                if not is_content_mod:
                    return (jar_path, 1, "likely API/library (no content footprints)")

                return (jar_path, 0, "needs translation")
        except Exception as e:
            return (jar_path, 1, f"scan error: {str(e)}")

    def scan_mods(self, folder_path, target_lang):
        jar_files = list(folder_path.glob('*.jar'))
        mods_to_translate, mods_skipped = [], []

        print("\n" + ui_get(self.ui_lang, "scan"))
        with ThreadPoolExecutor(max_workers=16) as executor:
            future_to_jar = {executor.submit(self.analyze_jar, jar, target_lang): jar for jar in jar_files}
            
            for future in tqdm(as_completed(future_to_jar), total=len(jar_files), desc=ui_get(self.ui_lang, "scan_progress"), unit="mod"):
                jar_path, status, msg = future.result()
                (mods_to_translate if status == 0 else mods_skipped).append((jar_path, msg))
        
        return mods_to_translate, mods_skipped

    def _patch_jar(self, jar_path, patches: dict):
        tmp_path = jar_path.with_suffix('.jar.tmp')
        with zipfile.ZipFile(jar_path, 'r') as src, \
             zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as dst:
            
            # Get a sorted list of files to ensure deterministic order
            infolist = sorted(src.infolist(), key=lambda i: i.filename)
            
            for info in infolist:
                # Skip signatures
                if 'META-INF' in info.filename.upper() and info.filename.upper().endswith(('.SF', '.RSA', '.DSA', '.EC')):
                    continue
                
                # Use patched content if available, otherwise use original
                content = patches.pop(info.filename, None)
                if content is None:
                    content = src.read(info.filename)
                
                dst.writestr(info, content)
            
            # Add new files that were not in the original jar
            for filename, content in patches.items():
                dst.writestr(filename, content)

        shutil.move(tmp_path, jar_path)

    def process_mod(self, jar_path, target_lang, index, total):
        hr()
        print(f"[{index}/{total}] {ui_get(self.ui_lang, 'translate_mod')} {jar_path.name}")
        
        backup_path = self._create_backup(jar_path)
        
        try:
            patches = {}
            messages = []
            
            with zipfile.ZipFile(jar_path, 'r') as source_zip:
                files_to_translate = [f for f in source_zip.namelist() if f.lower().endswith('/lang/en_us.json')]
                
                if not files_to_translate:
                    return False, backup_path, "No en_us.json found (internal check)."

                new_files = self._translate_files_in_memory(source_zip, files_to_translate, target_lang, messages)
                patches.update(new_files)

            if not patches:
                return False, backup_path, "No translatable content found."

            self._patch_jar(jar_path, patches)
            return True, backup_path, "\n".join(messages)

        except Exception as e:
            print(f"  ❌ CRITICAL FAILURE: {e}. Restoring backup...")
            self._restore_backup(backup_path, jar_path)
            return False, backup_path, f"critical error: {e}"

    def _translate_files_in_memory(self, source_zip, files_to_translate, target_lang, messages):
        translated_contents = {}
        lang_info = LANGUAGE_INFO.get(target_lang, {})
        google_target = target_lang.replace('_', '-') if target_lang != "zh_tw" else "zh-TW"
        if google_target.lower() == "zh-cn": google_target = "zh-CN"

        for en_us_path in files_to_translate:
            try:
                with source_zip.open(en_us_path) as f:
                    en_data = json.load(f)

                target_data = {"language": lang_info.get('name'), "language.code": lang_info.get('code'), "language.region": lang_info.get('region')}
                keys_to_translate = {k: v for k, v in en_data.items() if k not in target_data}
                
                if not keys_to_translate:
                    continue

                with tqdm(total=len(keys_to_translate), desc=f"  Translating {Path(en_us_path).parent.name}", unit="keys", leave=False) as pbar:
                    for key, value in keys_to_translate.items():
                        target_data[key] = self.translator.translate(self.ui_lang, value, 'en', google_target)
                        pbar.update(1)
                
                target_path = en_us_path.rsplit('/', 1)[0] + f'/{target_lang}.json'
                translated_contents[target_path] = json.dumps(target_data, ensure_ascii=False, indent=2).encode('utf-8')
                messages.append(f"- {Path(en_us_path).name} -> {Path(target_path).name}: ok")
            except Exception as e:
                messages.append(f"- Failed to process {en_us_path}: {e}")
        return translated_contents

    def _create_backup(self, jar_path):
        backup_path = jar_path.with_suffix('.jar.backup')
        if not backup_path.exists():
            print(f"  {ui_get(self.ui_lang, 'backup_created')} {backup_path.name}")
            shutil.copy2(jar_path, backup_path)
        else:
            print(f"  {ui_get(self.ui_lang, 'backup_exists')} {backup_path.name}")
        return backup_path

    def _restore_backup(self, backup_path, original_path):
        try:
            if Path(backup_path).exists():
                shutil.move(backup_path, original_path)
                print(f"  ✅ Backup for {original_path.name} restored.")
                return True
        except Exception as e:
            print(f"  ❌ FAILED TO RESTORE BACKUP: {e}")
        return False

    def restore_all_backups(self, folder_path):
        print("\n" + ui_get(self.ui_lang, "restore_start"))
        backup_files = list(folder_path.glob('*.jar.backup'))
        
        if not backup_files:
            print(ui_get(self.ui_lang, "restore_found").format(n=0))
            return

        print(ui_get(self.ui_lang, "restore_found").format(n=len(backup_files)))
        confirm = input(ui_get(self.ui_lang, "restore_confirm").format(n=len(backup_files))).strip().lower()
        if confirm not in ['', 'y', 'yes']:
            print(ui_get(self.ui_lang, "cancel"))
            return

        restored_count = 0
        for backup_file in tqdm(backup_files, desc="Restoring", unit="file"):
            original_jar_path = backup_file.with_suffix('').with_suffix('.jar')
            if self._restore_backup(backup_file, original_jar_path):
                restored_count += 1
        print("\n" + ui_get(self.ui_lang, "restore_done").format(n=restored_count))

# 主應用程式
class Application:
    def __init__(self):
        self.ui_lang = self.choose_language("en_us", "choose_ui", "zh_tw")
        install_required_packages(self.ui_lang)
        self.translator = Translator()
        self.processor = ModProcessor(self.ui_lang, self.translator)

    def run(self):
        self.print_banner()
        mode = self.select_mode()
        folder_path = get_folder_path_from_user(self.ui_lang)
        
        if not self.confirm_folder(folder_path):
            print(ui_get(self.ui_lang, "cancel"))
            sys.exit(0)

        if mode == "restore":
            self.processor.restore_all_backups(folder_path)
        else:
            self.run_translate_mode(folder_path)

        print("\n" + ui_get(self.ui_lang, "done"))
        print(f"©coding master.{2025}")

    def run_translate_mode(self, folder_path):
        target_lang = self.choose_language(self.ui_lang, "choose_target", self.ui_lang)
        backup_option = self.select_backup_option()

        mods_to_translate, mods_skipped = self.processor.scan_mods(folder_path, target_lang)

        if not mods_to_translate:
            print("\n" + ui_get(self.ui_lang, "no_need"))
            if mods_skipped: self.print_skipped_summary(mods_skipped)
            return
        
        mods_to_translate.sort(key=lambda item: item[0].name.lower())

        print("\n" + ui_get(self.ui_lang, "found_need_translate").format(n=len(mods_to_translate)))
        for i, (jar_path, _) in enumerate(mods_to_translate[:10], 1):
            print(f"  {i}. {jar_path.name}")
        if len(mods_to_translate) > 10: print(f"  ... +{len(mods_to_translate) - 10}")

        confirm = input(ui_get(self.ui_lang, "confirm_translate")).strip().lower()
        if confirm not in ['', 'y', 'yes']:
            print(ui_get(self.ui_lang, "cancel"))
            return

        results = {'success': [], 'failed': []}
        for i, (jar_path, _) in enumerate(mods_to_translate, 1):
            success, backup_path, message = self.processor.process_mod(jar_path, target_lang, i, len(mods_to_translate))
            
            if success:
                results['success'].append((jar_path, backup_path))
            else:
                results['failed'].append((jar_path, message))

            if backup_option == "delete_all" or (backup_option == "delete_success" and success):
                if backup_path.exists():
                    try:
                        os.remove(backup_path)
                        print(f"  {ui_get(self.ui_lang, 'delete_backup_ok')} {backup_path.name}")
                    except OSError as e:
                        print(f"  {ui_get(self.ui_lang, 'delete_backup_fail')} {e}")
        
        self.print_final_summary(results, folder_path, target_lang, backup_option)

    def print_skipped_summary(self, mods_skipped):
        hr()
        print(f"Skipped Mods ({len(mods_skipped)}):")
        reasons = {}
        for _, reason in mods_skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda item: item[1], reverse=True):
            print(f"  - {reason}: {count} mod(s)")

    def print_final_summary(self, results, folder, target_lang, backup_policy):
        hr()
        print(ui_get(self.ui_lang, "summary"))
        hr()
        print(f"{ui_get(self.ui_lang, 'ok')} {len(results['success'])}")
        print(f"{ui_get(self.ui_lang, 'fail')} {len(results['failed'])}")
        if results['failed']:
            for jar, msg in results['failed']:
                print(f"  - {jar.name}: {msg}")
        print(f"{ui_get(self.ui_lang, 'folder')} {folder}")
        print(f"{ui_get(self.ui_lang, 'target_lang')} {LANGUAGE_INFO.get(target_lang, {}).get('name')} ({target_lang})")
        print(f"{ui_get(self.ui_lang, 'backup_policy')} {backup_policy}")

    def print_banner(self):
        title = ui_get(self.ui_lang, "banner_title").format(VERSION=VERSION)
        print("\n" + "═" * 80)
        print(f"{title.center(80)}")
        print("═" * 80 + "\n")
        print(ui_get(self.ui_lang, "desc"))

    def choose_language(self, ui_lang, title_key, default_code):
        hr()
        print(ui_get(ui_lang, title_key))
        hr()
        lang_list = list(LANGUAGE_INFO.keys())
        for i, code in enumerate(lang_list, 1):
            info = LANGUAGE_INFO[code]
            print(f"  {i}. {info['name']} ({code})")
        
        default_idx = lang_list.index(default_code) + 1 if default_code in lang_list else 1
        while True:
            raw = input(ui_get(ui_lang, "choose_range").format(n=len(lang_list), d=default_idx)).strip()
            if not raw: return lang_list[default_idx - 1]
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(lang_list): return lang_list[idx]
            except ValueError: pass
            print(ui_get(ui_lang, "choose_invalid"))

    def select_mode(self):
        hr()
        print(ui_get(self.ui_lang, "mode_select"))
        hr()
        print("  " + ui_get(self.ui_lang, "mode_translate"))
        print("  " + ui_get(self.ui_lang, "mode_restore"))
        while True:
            raw = input(ui_get(self.ui_lang, "choose_range").format(n=2, d=1)).strip()
            if raw in ['', '1']: return "translate"
            if raw == '2': return "restore"
            print(ui_get(self.ui_lang, "choose_invalid"))

    def select_backup_option(self):
        hr()
        print(ui_get(self.ui_lang, "backup_menu"))
        hr()
        print("  " + ui_get(self.ui_lang, "backup_keep"))
        print("  " + ui_get(self.ui_lang, "backup_delete_success"))
        print("  " + ui_get(self.ui_lang, "backup_delete_all"))
        while True:
            raw = input(ui_get(self.ui_lang, "choose_range").format(n=3, d="1")).strip()
            if raw in ['', '1']: return "keep"
            if raw == '2': return "delete_success"
            if raw == '3': return "delete_all"
            print(ui_get(self.ui_lang, "choose_invalid"))

    def confirm_folder(self, folder_path):
        hr()
        print(f"{ui_get(self.ui_lang, 'folder')} {folder_path}")
        confirm = input(ui_get(self.ui_lang, "confirm_folder")).strip().lower()
        return confirm in ['', 'y', 'yes']

if __name__ == "__main__":
    try:
        app = Application()
        app.run()
    except (KeyboardInterrupt, EOFError):
        print("\n❌ Operation cancelled by user.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        input("\nPress Enter to exit...")
