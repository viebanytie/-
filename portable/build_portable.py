"""Build the self-contained Windows kit from the unmodified official download."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

SOURCE = 'https://packaging.moodle.org/windows/MoodleWindowsInstaller-latest-405.zip'
SHA256 = '7587bd868080ef12150a750cde1e325a6e32f7834d1a49e72c24335213ff4ac7'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--zip', type=Path)
    args = ap.parse_args()
    src = args.source or args.output.parent / 'official-moodle-windows.zip'
    if not src.exists():
        with urllib.request.urlopen(urllib.request.Request(SOURCE, headers={'User-Agent':'Mozilla/5.0'}), timeout=180) as r, src.open('wb') as f:
            shutil.copyfileobj(r, f)
    digest = hashlib.file_digest(src.open('rb'), 'sha256').hexdigest()
    if digest != SHA256:
        raise SystemExit('Official download changed: checksum mismatch; refusing to package it.')
    dest = args.output
    if dest.exists():
        raise SystemExit('Output must be a new empty directory.')
    dest.mkdir(parents=True)
    with zipfile.ZipFile(src) as z:
        for item in z.infolist():
            name = item.filename
            if name.startswith(('server/moodle/', 'server/php/', 'server/mysql/')) and not name.startswith('server/php/windowsXamppPhp/'):
                if '..' in Path(name).parts:
                    raise ValueError('Unsafe upstream archive path')
                z.extract(item, dest)
    # The upstream MariaDB directory includes redistributable VC runtime DLLs.
    # App-local copies also let PHP start on a clean Windows 10 x64 installation.
    for dll in (dest/'server/mysql/bin').glob('*140*.dll'):
        shutil.copy2(dll, dest/'server/php'/dll.name)
    source = Path(__file__).parent
    for name in ['Start-Moodle.ps1','Stop-Moodle.ps1','php.ini.template','bootstrap.php','install-db.php','database.php','status.php','router.php','shutdown.php','README_RU.txt']:
        shutil.copy2(source/name, dest/name)
    shutil.copy2(source/'config.php', dest/'server/moodle/config.php')
    shutil.copy2(source/'Digital_safety_course.mbz', dest/'Digital_safety_course.mbz')
    lang = source/'ru.zip'
    if hashlib.file_digest(lang.open('rb'), 'sha256').hexdigest() != '847ce6483fa92182df2c81a124a7db68f42bfe8f33e0b295693d2abc648db25a':
        raise SystemExit('Russian language pack checksum mismatch')
    langroot = dest/'runtime/moodledata/lang'
    langroot.mkdir(parents=True)
    with zipfile.ZipFile(lang) as z:
        z.extractall(langroot)
    for action in ['Start','Stop']:
        content = '@echo off\r\nchcp 65001 >nul\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0'+action+'-Moodle.ps1"\r\nif errorlevel 1 pause\r\n'
        (dest/(action+'-Moodle.cmd')).write_bytes(content.encode('utf-8'))
    # Windows PowerShell 5.1 requires a BOM to recognise Cyrillic in scripts.
    for name in ['Start-Moodle.ps1','Stop-Moodle.ps1']:
        p = dest/name
        p.write_text(p.read_text(encoding='utf-8'), encoding='utf-8-sig')
    (dest/'SOURCE.json').write_text(json.dumps({'url':SOURCE,'sha256':SHA256,'moodle':'4.5.15','build':'20261005','php':'8.2.4','mariadb':'10.4.28','changes':'Added launcher, course backup, local configuration and app-local VC runtime copies. Upstream application sources unchanged.'}, indent=2), encoding='utf-8')
    if args.zip:
        with zipfile.ZipFile(args.zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            for p in sorted(dest.rglob('*')):
                if p.is_file(): out.write(p, Path('Moodle_Portable')/p.relative_to(dest))
        print(args.zip.name, args.zip.stat().st_size, hashlib.file_digest(args.zip.open('rb'),'sha256').hexdigest())

if __name__ == '__main__': main()
