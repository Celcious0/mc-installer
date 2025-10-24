import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/4ds1rqjctlmsese88dgl4/mods.zip?dl=0&e=1&rlkey=mtuu6b4x2jeileel1ojhwi9rz&st=na185aag"
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"

def log(msg):
    print(f"[Installer] {msg}")

def is_windows():
    return os.name == "nt"

def get_minecraft_dir() -> Path:
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def direct_dropbox_url(url: str) -> str:
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        else:
            pbar = None
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")

def check_java() -> str | None:
    candidates = ["javaw", "java"]
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None

def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()

def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install Client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode

def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    log("ForgeCLI를 사용하여 무인 설치를 시도합니다.")
    cmd = [
        java_cmd, "-jar",
        str(forgecli_jar),
        "--installer", str(installer_path),
        "--target", str(mc_dir)
    ]
    log(f"실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode

def backup_mods(mc_dir: Path) -> Path | None:
    mods_dir = mc_dir / "mods"
    if mods_dir.exists() and any(mods_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = mc_dir / f"mods_backup_{ts}"
        shutil.move(str(mods_dir), str(backup_dir))
        log(f"기존 mods 폴더 백업 완료: {backup_dir}")
        return backup_dir
    return None

def extract_zip_to(path_zip: Path, target_dir: Path):
    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        lower = [n.lower() for n in names]
        has_structured_root = any(n.startswith(("mods/", "config/", "resourcepacks/", "shaderpacks/")) for n in lower)
        if has_structured_root:
            log(f"ZIP 구조 인식: 루트에 mods/ 또는 config/ 포함 → .minecraft 루트로 전개")
            ensure_dir(target_dir)
            zf.extractall(target_dir)
        else:
            log(f"ZIP 구조 인식: 루트 JAR 모음 → .minecraft/mods 로 전개")
            mods_dir = target_dir / "mods"
            ensure_dir(mods_dir)
            for name in names:
                if name.endswith("/"):
                    continue
                if name.lower().endswith(".jar"):
                    src = zf.open(name)
                    dst = mods_dir / Path(name).name
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    args = parser.parse_args()
    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)
    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if is_forge_installed(mc_dir) and not args.force_reinstall:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 건너뜁니다.")
        else:
            java_cmd = check_java()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)
            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)
            if args.headless:
                forgecli_jar = tmp / "ForgeCLI-all.jar"
                try:
                    download_file(FORGECLI_RELEASE_URL, forgecli_jar)
                    rc = run_forge_installer_headless(java_cmd, forgecli_jar, installer_path, mc_dir)
                    if rc != 0:
                        log(f"무인 설치 실패(code={rc}). GUI 설치로 폴백합니다.")
                        rc = run_forge_installer_gui(java_cmd, installer_path)
                except Exception as e:
                    log(f"무인 설치 중 예외 발생: {e}. GUI 설치로 폴백합니다.")
                    rc = run_forge_installer_gui(java_cmd, installer_path)
            else:
                rc = run_forge_installer_gui(java_cmd, installer_path)
            if rc != 0:
                log(f"Forge 설치 프로그램이 비정상 종료(code={rc}). 설치를 계속할 수 없습니다.")
                sys.exit(3)
            if not is_forge_installed(mc_dir):
                log("Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)
        if not args.no_backup:
            backup_mods(mc_dir)
        extract_zip_to(mods_zip_path, mc_dir)
        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)

