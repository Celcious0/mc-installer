# mc_installer.py
# Windows 전용: Forge(1.20.1-47.4.10) 설치 + mods.zip 배치 + 런처 프로필 JVM -Xmx6G 적용
# 빌드는 Windows에서 PyInstaller로 수행하세요:
#   pip install pyinstaller requests tqdm
#   pyinstaller --onefile mc_installer.py
#
# 실행 옵션:
#   --headless          : Forge 무인설치(ForgeCLI 사용, 실패 시 GUI 폴백)
#   --force-reinstall   : Forge가 있어도 다시 설치
#   --no-backup         : 기존 mods 폴더 백업 생략

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests 가 필요합니다. 먼저 'pip install requests tqdm' 을 실행하세요.")
    sys.exit(1)

try:
    from tqdm import tqdm
except Exception:
    tqdm = None  # 진행바 미사용 허용


# ===== 사용자 설정 영역 =========================================================
FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-installer.jar"

# mods.zip Dropbox 공유 링크(사용자가 최신 것으로 교체 가능)
# - 설치기는 dl=0 → dl=1 로 강제 다운로드로 변환함
MODS_ZIP_URL_ORIG = "https://www.dropbox.com/scl/fi/8j78vjexxj5smh69jeakd/mods.zip?rlkey=3z55ghjtgq6yom8nhfg1tht2t&st=xa3vx6cf&dl=0"

# Forge 설치 후 생성되는 버전 디렉터리명(존재 확인에 사용)
FORGE_VERSION_DIRNAME = "1.20.1-forge-47.4.10"

# 무인 설치 시 사용할 ForgeCLI (실패 시 GUI로 폴백)
# 최신 버전으로 교체 가능. 실패해도 GUI로 자동 폴백하므로 URL 고장에 의한 전체 실패는 방지됨.
FORGECLI_RELEASE_URL = "https://github.com/Kamesuta/ForgeCLI/releases/download/1.0.1/ForgeCLI-1.0.1-all.jar"
# =============================================================================


def log(msg: str) -> None:
    print(f"[Installer] {msg}")


def is_windows() -> bool:
    return os.name == "nt"


def get_minecraft_dir() -> Path:
    """
    Java Edition 기본 경로: %APPDATA%\\.minecraft
    """
    if not is_windows():
        raise RuntimeError("이 설치기는 Windows 전용입니다.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 환경변수를 찾을 수 없습니다.")
    return Path(appdata) / ".minecraft"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def direct_dropbox_url(url: str) -> str:
    """
    Dropbox 공유 링크를 강제 다운로드 링크로 변환 (dl=1)
    """
    if "dropbox.com" in url:
        if "dl=" in url:
            url = re.sub(r"dl=\d", "dl=1", url)
        else:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}dl=1"
    return url


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"다운로드 시작: {url}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        pbar = None
        if tqdm and total > 0:
            pbar = tqdm(total=total, unit="B", unit_scale=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))
        if pbar:
            pbar.close()
    log(f"다운로드 완료: {dest}")


def check_java_cmd() -> str | None:
    """
    PATH 에서 java 또는 javaw 실행 가능 여부 확인.
    반환: 실행파일 이름("java" 또는 "javaw") 또는 None
    """
    candidates = ["javaw", "java"]  # GUI 무표시 우선
    for c in candidates:
        try:
            proc = subprocess.run([c, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if proc.returncode == 0 or proc.returncode == 1:
                return c
        except FileNotFoundError:
            continue
    return None


def get_java_major(java_cmd: str) -> int | None:
    """
    java -version 출력에서 메이저 버전 정수 추출(예: 17)
    """
    try:
        out = subprocess.check_output([java_cmd, "-version"], stderr=subprocess.STDOUT).decode("utf-8", "ignore")
    except Exception:
        return None
    # 예: 'openjdk version "17.0.11"' 또는 'java version "1.8.0_312"'
    m = re.search(r'version\s+"(?P<ver>[\d._]+)"', out)
    if not m:
        return None
    ver = m.group("ver")
    # 1.8.x → 8 로 맵핑
    if ver.startswith("1.8"):
        return 8
    # 17.0.11 → 17
    try:
        return int(ver.split(".")[0])
    except Exception:
        return None


def is_forge_installed(mc_dir: Path) -> bool:
    versions_dir = mc_dir / "versions" / FORGE_VERSION_DIRNAME
    return versions_dir.exists()


def run_forge_installer_gui(java_cmd: str, installer_path: Path) -> int:
    """
    Forge 공식 인스톨러 GUI 실행 (사용자 클릭 필요)
    """
    log("Forge 설치 프로그램(GUI)을 실행합니다. 'Install client' 상태로 설치를 완료해 주세요.")
    proc = subprocess.Popen([java_cmd, "-jar", str(installer_path)], cwd=installer_path.parent)
    proc.wait()
    return proc.returncode


def run_forge_installer_headless(java_cmd: str, forgecli_jar: Path, installer_path: Path, mc_dir: Path) -> int:
    """
    제3자 ForgeCLI를 사용하여 클라이언트 무인 설치 시도
    """
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


def extract_zip_to(path_zip: Path, target_dir: Path) -> None:
    """
    mods.zip 안전 전개:
      - 허용 디렉터리 화이트리스트: mods/, config/, resourcepacks/, shaderpacks/
      - 루트 .jar 모음 → .minecraft/mods 로 전개
      - 런처/계정/옵션 관련 파일은 절대 복사하지 않음
    """
    SAFE_DIRS = ("mods/", "config/", "resourcepacks/", "shaderpacks/")
    BLOCK_FILES = {
        "launcher_profiles.json", "launcher_profile.json",
        "launcher_profiles_microsoft_store.json",
        "launcher_accounts.json", "launcher_settings.json",
        "options.txt", "profiles.json"
    }

    with zipfile.ZipFile(path_zip) as zf:
        names = zf.namelist()
        has_any_copied = False
        for name in names:
            if name.endswith("/"):
                continue

            lower = name.lower()
            base = Path(name).name.lower()

            # 위험 파일 차단
            if base in BLOCK_FILES:
                continue

            # 허용 디렉터리만 통과
            if any(lower.startswith(d) for d in SAFE_DIRS):
                src = zf.open(name)
                dst = target_dir / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "wb") as f:
                    shutil.copyfileobj(src, f)
                has_any_copied = True
            else:
                # 루트에 jar만 있으면 mods/로 유도
                if lower.endswith(".jar") and "/" not in lower:
                    mods_dir = target_dir / "mods"
                    mods_dir.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(mods_dir / Path(name).name, "wb") as f:
                        shutil.copyfileobj(src, f)
                    has_any_copied = True
                # 그 외는 무시

        if not has_any_copied:
            log("경고: ZIP에서 설치할 항목을 찾지 못했습니다. ZIP 구조를 확인하세요.")


# ---------- 런처 프로필 조정: 기본 Xmx = 6G -----------------------------------

def _profiles_json_path(mc_dir: Path) -> Path:
    """
    공식 런처: launcher_profiles.json
    MS Store 런처: launcher_profiles_microsoft_store.json
    둘 중 존재하는 파일 경로를 반환, 둘 다 없으면 기본(공식) 경로를 반환
    """
    p1 = mc_dir / "launcher_profiles.json"
    p2 = mc_dir / "launcher_profiles_microsoft_store.json"
    if p2.exists():
        return p2
    return p1


def _strip_and_set_xmx(args_str: str, xmx: str = "6G") -> str:
    # 기존 -Xmx? 제거 후 마지막에 -Xmx{xmx} 추가
    args = (args_str or "").strip()
    args = re.sub(r"-Xmx\s*\S+", "", args, flags=re.IGNORECASE).strip()
    if args:
        args += " "
    args += f"-Xmx{xmx}"
    return args.strip()


def set_default_xmx_for_forge_profile(mc_dir: Path, xmx: str = "6G") -> None:
    """
    launcher_profiles*.json 에서 lastVersionId == FORGE_VERSION_DIRNAME 인
    프로필의 javaArgs에 -Xmx{xmx}를 주입. 없으면 새 프로필 생성.
    """
    prof_path = _profiles_json_path(mc_dir)
    ensure_dir(prof_path.parent)

    # 백업
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if prof_path.exists():
        shutil.copy(prof_path, prof_path.with_suffix(f".json.bak_{ts}"))

    # 로드 또는 기본 스켈레톤
    if prof_path.exists():
        try:
            with open(prof_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"profiles": {}, "settings": {"enableAdvanced": True}}
    else:
        data = {"profiles": {}, "settings": {"enableAdvanced": True}}

    profiles = data.setdefault("profiles", {})
    target_keys = []

    # 기존 Forge 1.20.1 프로필 찾기
    for key, prof in list(profiles.items()):
        if not isinstance(prof, dict):
            continue
        if prof.get("lastVersionId") == FORGE_VERSION_DIRNAME:
            target_keys.append(key)

    # 없으면 새 프로필 생성
    if not target_keys:
        new_key = str(uuid.uuid4())
        profiles[new_key] = {
            "name": f"Forge {FORGE_VERSION_DIRNAME} (6G)",
            "type": "custom",
            "created": datetime.utcnow().isoformat() + "Z",
            "lastVersionId": FORGE_VERSION_DIRNAME,
            "javaArgs": f"-Xmx{xmx}",
        }
        target_keys = [new_key]
    else:
        # 있으면 -Xmx만 정리/주입
        for k in target_keys:
            prof = profiles[k]
            prof["javaArgs"] = _strip_and_set_xmx(prof.get("javaArgs", ""), xmx)

    # 고급 설정 UI 토글(사용자 편의)
    settings = data.setdefault("settings", {})
    settings.setdefault("enableAdvanced", True)

    # 저장
    with open(prof_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"런처 프로필에 -Xmx{xmx} 적용 완료: {prof_path}")


# ---------- 메인 로직 ----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Minecraft 1.20.1 Forge + Mods 설치기")
    parser.add_argument("--headless", action="store_true", help="ForgeCLI를 사용해 무인 설치 시도(실패 시 GUI 폴백)")
    parser.add_argument("--force-reinstall", action="store_true", help="Forge를 이미 설치했더라도 다시 설치")
    parser.add_argument("--no-backup", action="store_true", help="기존 mods 폴더 백업 생략")
    parser.add_argument("--xmx", default="6G", help="기본 JVM 최대 메모리(-Xmx) 값, 예: 4G / 6144m (기본 6G)")
    args = parser.parse_args()

    if not is_windows():
        log("Windows 전용 설치기입니다.")
        sys.exit(1)

    mc_dir = get_minecraft_dir()
    log(f"Minecraft 디렉터리: {mc_dir}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # --- 1) Forge 설치 확인/설치 ---
        need_install = (not is_forge_installed(mc_dir)) or args.force_reinstall
        if not need_install:
            log(f"이미 Forge({FORGE_VERSION_DIRNAME})가 설치된 것으로 보입니다. 설치 단계는 건너뜁니다.")
        else:
            java_cmd = check_java_cmd()
            if not java_cmd:
                log("Java 실행 파일을 찾지 못했습니다. Java(Temurin 17 이상)를 먼저 설치한 뒤 다시 실행하세요.")
                log("다운로드 안내: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)

            # Java 17 확인
            major = get_java_major(java_cmd) or 0
            if major < 17:
                log(f"Java {major} 감지: Forge 1.20.1에는 Java 17 이상이 필요합니다.")
                log("Temurin 17 설치 후 다시 실행하세요: https://adoptium.net/temurin/releases/?version=17")
                sys.exit(2)

            installer_path = tmp / "forge-installer.jar"
            download_file(FORGE_INSTALLER_URL, installer_path)

            if args.headless:
                # ForgeCLI 다운로드 (실패하면 GUI 폴백)
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

            # 설치 후 체크
            if not is_forge_installed(mc_dir):
                log("경고: Forge 버전 디렉터리를 찾지 못했습니다. 런처를 한 번 실행하여 프로필이 생성되었는지 확인하세요.")

        # --- 2) 모드 ZIP 다운로드/설치 ---
        mods_url = direct_dropbox_url(MODS_ZIP_URL_ORIG)
        mods_zip_path = tmp / "mods.zip"
        download_file(mods_url, mods_zip_path)

        if not args.no_backup:
            backup_mods(mc_dir)

        extract_zip_to(mods_zip_path, mc_dir)

        # --- 3) 런처 프로필 JVM -Xmx 적용(기본 6G, 옵션으로 변경 가능) ---
        try:
            set_default_xmx_for_forge_profile(mc_dir, xmx=args.xmx)
        except Exception as e:
            log(f"프로필 -Xmx 적용 중 예외: {e}")

        # --- 4) 사후 검증(Forge 폴더 존재) ---
        expected = mc_dir / "versions" / FORGE_VERSION_DIRNAME
        if expected.exists():
            log(f"Forge 설치 확인: {expected}")
        else:
            log("경고: 예상된 Forge 버전 폴더가 보이지 않습니다. Java 17 여부/런처 최초 실행 여부를 확인하세요.")

        log("모든 작업이 완료되었습니다.")
        log("Minecraft 런처에서 'Forge 1.20.1' 프로필을 선택하고 실행하세요.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"치명적인 오류: {e}")
        sys.exit(99)

