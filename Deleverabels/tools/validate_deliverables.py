"""Validate the deliverables folder and write FILE_INVENTORY.md. Read-only except for the inventory file."""

import hashlib
import re
import struct
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parent
problems, notes = [], []
files = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name != "FILE_INVENTORY.md")

# 1. Every deliverable folder has content.
for folder in ["01_Backend_Source_Code", "02_Frontend_Source_Code", "03_AI_Prediction_APIs", "04_Workforce_Dashboards",
               "05_Analytics_Reports", "06_UI_Screenshots_Demo", "07_API_Documentation", "08_Database_Schema", "09_Test_Results"]:
    count = sum(1 for f in files if f.relative_to(OUT).parts[0] == folder)
    if count == 0:
        problems.append(f"{folder} is empty")
for sub in ["Authentication", "Dashboard", "Employee_Management", "Attendance_Leave", "Payroll", "AI_Risk_Monitoring",
            "Analytics_Forecasting", "Interventions_Tasks", "Performance_Reviews", "Notifications_Search", "Reports_Audit"]:
    if not list((OUT / "06_UI_Screenshots_Demo" / sub).glob("*.png")):
        problems.append(f"06_UI_Screenshots_Demo/{sub} has no screenshots")

# 2. Real image and document files.
pngs = [f for f in files if f.suffix == ".png"]
for png in pngs:
    head = png.read_bytes()[:24]
    width, height = struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    if not (width >= 800 and height >= 500 and png.stat().st_size > 10_000):
        problems.append(f"suspicious PNG {png.relative_to(OUT)} ({width}x{height}, {png.stat().st_size} bytes)")
for f in files:
    data = f.read_bytes()[:4]
    if f.suffix == ".pdf" and data != b"%PDF":
        problems.append(f"not a PDF: {f.relative_to(OUT)}")
    if f.suffix == ".xlsx" and data != b"PK\x03\x04":
        problems.append(f"not an XLSX: {f.relative_to(OUT)}")

# 3. No secrets, environments or caches.
forbidden_parts = {".venv", "node_modules", "__pycache__", ".pytest_cache", "dist"}
for f in files:
    rel = f.relative_to(OUT)
    if forbidden_parts & set(rel.parts) or f.name == ".env" or f.suffix in (".pyc", ".sqlite", ".joblib", ".pkl"):
        problems.append(f"excluded content copied: {rel}")
    if f.suffix in (".png", ".pdf", ".xlsx") or f.name == "validate_deliverables.py":
        continue  # binary files; the validator itself contains the patterns it searches for
    text = f.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", text):
        problems.append(f"JWT-like token in {rel}")
    if re.search(r'"Demo-[A-Za-z0-9_-]{10,}', text) or "BEGIN PRIVATE KEY" in text or "BEGIN RSA PRIVATE KEY" in text:
        problems.append(f"credential-like value in {rel}")
    for line in text.splitlines():
        if re.match(r"\s*(DB_PASSWORD|JWT_SECRET_KEY)\s*=\s*\S", line) and f.name.endswith(".env.example"):
            problems.append(f"filled secret in template {rel}")
if any("pytest-only-jwt-secret" in f.read_text(errors="ignore") for f in files if f.suffix == ".py"):
    notes.append("The copied backend tests contain the TEST-ONLY placeholder secret from tests/conftest.py "
                 "(\"pytest-only-jwt-secret-...\"); it is a fixture constant for the in-memory test database, not a credential.")

# 4. Relative Markdown links resolve inside the package.
for md in (f for f in files if f.suffix == ".md" and "01_Backend_Source_Code" not in f.parts and "02_Frontend_Source_Code" not in f.parts):
    for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (md.parent / target.split("#")[0]).resolve()
        if not path.exists():
            problems.append(f"broken link in {md.relative_to(OUT)}: {target}")
        elif OUT.resolve() not in path.parents and path != OUT.resolve():
            problems.append(f"link leaves the package in {md.relative_to(OUT)}: {target}")

# 5. Source copies are byte-identical to the originals (and the originals still exist).
mismatch = 0
for copy_root, original_root in ((OUT / "01_Backend_Source_Code" / "backend", ROOT / "backend"),
                                 (OUT / "01_Backend_Source_Code" / "database", ROOT / "database"),
                                 (OUT / "02_Frontend_Source_Code" / "frontend", ROOT / "frontend")):
    for copied in (p for p in copy_root.rglob("*") if p.is_file()):
        original = original_root / copied.relative_to(copy_root)
        if not original.exists() or hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(copied.read_bytes()).digest():
            mismatch += 1
if mismatch:
    problems.append(f"{mismatch} copied source files differ from or are missing in the original project")

# Inventory.
groups: dict[str, list[Path]] = {}
for f in files:
    groups.setdefault(f.relative_to(OUT).parts[0] if len(f.relative_to(OUT).parts) > 1 else "(root)", []).append(f)
lines = ["# File inventory", "", f"All {len(files) + 1} files in this package (including this inventory), grouped by top-level folder.",
         "Source-code folders are summarised by sub-folder; every other file is listed.", ""]
for group, items in groups.items():
    lines += [f"## {group} ({len(items)} files)", ""]
    if group in ("01_Backend_Source_Code", "02_Frontend_Source_Code"):
        sub: dict[str, int] = {}
        for f in items:
            parts = f.relative_to(OUT / group).parts
            sub["/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1]) or "."] = sub.get(
                "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1]) or ".", 0) + 1
        lines += [f"- `{k}/`: {v} files" for k, v in sorted(sub.items())]
    else:
        lines += [f"- `{f.relative_to(OUT).as_posix()}` ({f.stat().st_size:,} bytes)" for f in items]
    lines.append("")
lines += ["## Missing evidence", "", "See the \"Not generated, and why\" section of [README.md](README.md).", ""]
(OUT / "FILE_INVENTORY.md").write_text("\n".join(lines), encoding="utf-8")

print(f"files: {len(files) + 1}; screenshots: {len(pngs)}; per folder:",
      {g: len(i) for g, i in groups.items()})
for note in notes:
    print("NOTE:", note)
print("PROBLEMS:" if problems else "No problems found.")
for problem in problems:
    print(" -", problem)
sys.exit(1 if problems else 0)
