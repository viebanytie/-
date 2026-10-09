"""Create a source-only ZIP; never include venvs, comments or local inventory exports."""
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ["backend.py", "startup_inspector.py", "inspect_windows.ps1", "demo.json",
         "BUILD.cmd", "START.cmd", "Build_StartupInspector.ps1", "requirements-build.txt",
         "README_RU.md", "TEST_REPORT.md", "make_source_zip.py", ".gitignore",
         "tests/test_backend.py", "tests/gui_smoke.py", "tests/check_powershell.ps1"]


def main():
    archive_path = ROOT.parent / "WindowsStartupInspector_v1.0.0.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for filename in sorted(FILES):
            data = (ROOT / filename).read_bytes()
            if filename.endswith((".cmd", ".ps1")):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo("WindowsStartupInspector/" + filename, date_time=(2026, 10, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    print(f"{archive_path.name}: {len(FILES)} source files, SHA256 {digest}")


if __name__ == "__main__":
    main()
