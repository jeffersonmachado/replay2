#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

RELEASE_RUN_ID="release-$(date -u +%Y%m%dT%H%M%SZ)-$(python3 -c "import hashlib,os,time;print(hashlib.sha256((os.urandom(16)+str(time.time()).encode())).hexdigest()[:8])")"
STARTED_AT=$(date -Iseconds)
echo "=== FINAL ACCEPTANCE $RELEASE_RUN_ID ==="

# ── 1. Dependency check ──
echo "=== DEPENDENCY CHECK ==="
for cmd in python3 pytest node tclsh; do
  command -v "$cmd" >/dev/null || { echo "ERROR: $cmd not found"; exit 1; }
done
python3 -c "import websocket, PIL.Image" 2>/dev/null || { echo "ERROR: websocket-client or Pillow missing"; exit 1; }
CHROMIUM=$(python3 -c "import shutil; print(shutil.which('google-chrome-stable') or shutil.which('google-chrome') or shutil.which('chromium-browser') or shutil.which('chromium') or '')")
[ -n "$CHROMIUM" ] || { echo "ERROR: Chromium not found"; exit 1; }
echo "All dependencies present"

# ── 2. Clean old artifacts ──
echo "=== CLEANING ==="
rm -f artifacts/final-acceptance-report.md artifacts/final-acceptance-results.json
rm -f artifacts/manual-validation.json artifacts/visual-test-result.json
rm -f artifacts/source-tree-manifest.sha256 artifacts/source-tree-hash.json
rm -rf artifacts/acceptance-logs/
mkdir -p artifacts/acceptance-logs/
rm -f dist/*.tar.gz dist/*.manifest.json dist/*.sha256 dist/*.tar.gz.sha256

# ── 3. Source tree hash BEFORE ──
SOURCE_TREE_SHA256_BEFORE=$(python3 scripts/tree_hash.py)
echo "source_tree_sha256_before=$SOURCE_TREE_SHA256_BEFORE"

# ── 4. Baseline ──
echo "=== BASELINE CHECK ==="
BASELINE_OK_ORIGINAL=False
if sha256sum -c artifacts/acceptance-test-baseline.sha256; then
  BASELINE_OK_ORIGINAL=True
else
  echo "ERROR: baseline mismatch"; exit 1
fi

# ── 5. Phase 08 original ──
echo "=== TREE GATE (ORIGINAL) ==="
PHASE08_ORIG_RC=0
bash scripts/acceptance/run-phase-08-full.sh || PHASE08_ORIG_RC=$?
TREE_GATE_PASSED=False
[ "$PHASE08_ORIG_RC" -eq 0 ] && TREE_GATE_PASSED=True
echo "tree_gate=$TREE_GATE_PASSED (rc=$PHASE08_ORIG_RC)"

# ── 6. Process + zombie check (original) ──
REMAINING_ORIG=$(python3 -c "
import subprocess,os
r=subprocess.run(['pgrep','-U',str(os.getuid()),'-f','user-data-dir.*dakota-visual-'],capture_output=True,text=True)
pids=[l for l in r.stdout.strip().splitlines() if l.strip().isdigit() and l!=str(os.getpid())]
print(len(pids))
")
ZOMBIES_ORIG=$(python3 -c "
import subprocess,os
r=subprocess.run(['ps','-U',str(os.getuid()),'-o','pid,stat','--no-headers'],capture_output=True,text=True)
z=[l.split()[0] for l in r.stdout.strip().splitlines() if len(l.split())>=2 and 'Z' in l.split()[1]]
print(len(z))
")
echo "original remaining=$REMAINING_ORIG zombies=$ZOMBIES_ORIG"

# ── 7. Visual + contamination validation ──
VISUAL_OK=False; CONTAMINATION_OK=False
if [ -f artifacts/visual-test-result.json ]; then
  python3 -c "
import json
v=json.load(open('artifacts/visual-test-result.json'))
v['release_run_id']='$RELEASE_RUN_ID'
v['run_id']='$RELEASE_RUN_ID-original-visual'
json.dump(v,open('artifacts/visual-test-result.json','w'),indent=2)
"
  VIS_PASSED=$(python3 -c "import json;v=json.load(open('artifacts/visual-test-result.json'));print(str(v.get('passed',False)).lower())")
  VIS_HASH=$(python3 -c "import json;v=json.load(open('artifacts/visual-test-result.json'));print(v.get('source_tree_sha256',''))")
  [ "$VIS_PASSED" = "true" ] && [ "$VIS_HASH" = "$SOURCE_TREE_SHA256_BEFORE" ] && VISUAL_OK=True
  if [ -f artifacts/acceptance-logs/current/phase07-contamination.result.json ]; then
    CONTAMINATION_OK=$(python3 -c "import json; d=json.load(open('artifacts/acceptance-logs/current/phase07-contamination.result.json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
    [ "$CONTAMINATION_OK" = "true" ] && CONTAMINATION_OK=True || CONTAMINATION_OK=False
  fi
fi
echo "visual_ok=$VISUAL_OK contamination_ok=$CONTAMINATION_OK"

# ── 8. Source tree hash AFTER ──
SOURCE_TREE_SHA256_AFTER=$(python3 scripts/tree_hash.py)
SOURCE_TREE_UNCHANGED=False
[ "$SOURCE_TREE_SHA256_BEFORE" = "$SOURCE_TREE_SHA256_AFTER" ] && SOURCE_TREE_UNCHANGED=True
echo "source_tree_unchanged=$SOURCE_TREE_UNCHANGED"

# ── 9. Export env vars for safe Python consumption ──
export RELEASE_RUN_ID STARTED_AT
export SOURCE_TREE_SHA256_BEFORE SOURCE_TREE_SHA256_AFTER
export REMAINING_ORIG ZOMBIES_ORIG
# Export as separate BOOL_ variables to avoid overwriting shell True/False
for var in TREE_GATE_PASSED BASELINE_OK_ORIGINAL VISUAL_OK CONTAMINATION_OK SOURCE_TREE_UNCHANGED; do
  [ "${!var}" = "True" ] && export "BOOL_${var}=1" || export "BOOL_${var}=0"
done
PY_VER=$(python3 --version 2>&1); export PY_VER
PYTEST_VER=$(python3 -m pytest --version 2>&1 | head -1); export PYTEST_VER
NODE_VER=$(node --version 2>&1); export NODE_VER
TCL_VER=$(echo 'puts [info patchlevel]' | tclsh 2>&1); export TCL_VER
CHROMIUM_VER=$("$CHROMIUM" --version 2>&1 || echo "unknown"); export CHROMIUM_VER
FINISHED_AT=$(date -Iseconds); export FINISHED_AT

# ── 10. Generate internal reports (BEFORE tarball, so they get packaged) ──
echo "=== GENERATING REPORTS ==="
python3 << 'PYEOF'
import json, os, sys, hashlib, re
from pathlib import Path
from datetime import datetime, timezone

def env_bool(name):
    v = os.environ.get(name, '').strip()
    if v == '1': return True
    if v == '0': return False
    raise ValueError(f"invalid boolean env {name}={v!r}")

rrid = os.environ['RELEASE_RUN_ID']
fin = os.environ.get('FINISHED_AT', datetime.now(timezone.utc).isoformat())
base = Path('artifacts/acceptance-logs/current')

def parse_log_result(log_rel):
    """Load result directly from .result.json, falling back to log parsing."""
    rj = base / log_rel.replace('.log', '.result.json')
    if rj.exists():
        try:
            d = json.loads(rj.read_text())
            return {
                'name': d.get('name', log_rel.replace('.log','')),
                'success': d.get('success', False),
                'exit_code': d.get('exit_code', None),
                'timed_out': d.get('timed_out', False),
                'duration_seconds': d.get('duration_seconds', 0),
                'log_path': str(base / log_rel),
            }
        except Exception:
            pass
    # Fallback: try log file
    lp = base / log_rel
    r = {'name': log_rel.replace('.log',''), 'log_path': str(lp),
         'success': False, 'exit_code': None, 'timed_out': False, 'duration_seconds': 0}
    if not lp.exists(): return r
    r['log_sha256'] = hashlib.sha256(lp.read_bytes()).hexdigest()
    return r

def parse_pytest_counts(log_rel):
    lp = base / log_rel
    if not lp.exists(): return {}
    text = lp.read_text(errors='replace')
    m = re.search(r'(\d+)\s+passed', text); passed = int(m.group(1)) if m else 0
    m = re.search(r'(\d+)\s+failed', text); failed = int(m.group(1)) if m else 0
    m = re.search(r'(\d+)\s+skipped', text); skipped = int(m.group(1)) if m else 0
    m = re.search(r'(\d+)\s+subtests?\s+passed', text); subtests = int(m.group(1)) if m else 0
    return {'passed': passed, 'failed': failed, 'skipped': skipped, 'subtests': subtests}

commands = {}
for phase in ['01-comparison','02-diffs','03-sessions','04-snapshots-gateway','05-payload-frontend','06-decoder-fixture','07-visual-runner']:
    commands[f'phase-{phase}'] = parse_log_result(f'phase-{phase}.log')
commands['acceptance-baseline'] = parse_log_result('acceptance-baseline.log')
commands['python-acceptance'] = {**parse_log_result('python-acceptance.log'), **parse_pytest_counts('python-acceptance.log')}
commands['python-full'] = {**parse_log_result('python-full.log'), **parse_pytest_counts('python-full.log')}
commands['gateway-tests'] = {**parse_log_result('gateway-tests.log'), **parse_pytest_counts('gateway-tests.log')}
commands['javascript-tests'] = parse_log_result('javascript-tests.log')
commands['tcl-tests'] = parse_log_result('tcl-tests.log')
commands['test-all'] = parse_log_result('test-all.log')
commands['process-cleanup'] = parse_log_result('process-cleanup.log')
commands['visual-evidence'] = parse_log_result('visual-evidence-exists.log')
cc = parse_log_result('phase07-contamination.log')
cc['name'] = 'contamination-regression'
# Load real values from contamination result JSON if available
crj = base / 'phase07-contamination.result.json'
if crj.exists():
    try:
        cd = json.loads(crj.read_text())
        cc = {**cc, **cd}
    except Exception:
        pass
commands['contamination-regression'] = cc

vis = {}
vp = Path('artifacts/visual-test-result.json')
if vp.exists(): vis = json.loads(vp.read_text())

results = {
    'schema_version': '1.0', 'release_run_id': rrid, 'run_id': f'{rrid}-original',
    'generated_at': fin,
    'tree_validation_passed': env_bool('BOOL_TREE_GATE_PASSED'),
    'no_pending_issues': env_bool('BOOL_TREE_GATE_PASSED') and env_bool('BOOL_SOURCE_TREE_UNCHANGED') and env_bool('BOOL_CONTAMINATION_OK') and (int(os.environ.get('REMAINING_ORIG','1')) == 0) and (int(os.environ.get('ZOMBIES_ORIG','1')) == 0),
    'source_tree_sha256_before': os.environ['SOURCE_TREE_SHA256_BEFORE'],
    'source_tree_sha256_after': os.environ['SOURCE_TREE_SHA256_AFTER'],
    'source_tree_unchanged': env_bool('BOOL_SOURCE_TREE_UNCHANGED'),
    'baseline_verified': env_bool('BOOL_BASELINE_OK_ORIGINAL'),
    'visual_test_verified': vis.get('passed', False),
    'timeline_verified': vis.get('timeline_render_calls', 0) >= 1,
    'contamination_regression_verified': env_bool('BOOL_CONTAMINATION_OK'),
    'full_python_suite_passed': commands['python-full']['success'],
    'gateway_suite_passed': commands['gateway-tests']['success'],
    'javascript_suite_passed': commands['javascript-tests']['success'],
    'tcl_suite_passed': commands['tcl-tests']['success'],
    'test_all_passed': commands['test-all']['success'],
    'phase_08_passed': env_bool('BOOL_TREE_GATE_PASSED'),
    'remaining_processes': int(os.environ.get('REMAINING_ORIG','0')),
    'remaining_zombies': int(os.environ.get('ZOMBIES_ORIG','0')),
    'commands': commands,
}
Path('artifacts/final-acceptance-results.json').write_text(json.dumps(results, indent=2))
lines = [
    f'# Final Acceptance Report — {rrid}',
    f'## Tree: {results["source_tree_sha256_before"]}',
    f'## Tree Gate: {"PASSED" if results["tree_validation_passed"] else "FAILED"}',
    f'## Baseline: {"VERIFIED" if results["baseline_verified"] else "FAILED"}',
    f'## Visual: bytes={vis.get("screenshot_bytes",0)} timeline={vis.get("timeline_render_calls",0)}',
    f'## Contamination: {"VERIFIED" if results["contamination_regression_verified"] else "FAILED"}',
    f'## Python full: {"PASSED" if results["full_python_suite_passed"] else "FAILED"}',
    f'## Gateway: {"PASSED" if results["gateway_suite_passed"] else "FAILED"}',
    f'## JS: {"PASSED" if results["javascript_suite_passed"] else "FAILED"}',
    f'## TCL: {"PASSED" if results["tcl_suite_passed"] else "FAILED"}',
    f'## test-all: {"PASSED" if results["test_all_passed"] else "FAILED"}',
    f'## Source unchanged: {results["source_tree_unchanged"]}',
    f'## Remaining: processes={results["remaining_processes"]} zombies={results["remaining_zombies"]}',
    '', '## Commands',
]
for name, cmd in commands.items():
    lines.append(f'- **{name}**: exit={cmd["exit_code"]} success={cmd["success"]}')
Path('artifacts/final-acceptance-report.md').write_text('\n'.join(lines) + '\n')
Path('artifacts/manual-validation.json').write_text(json.dumps({
    'schema_version': '1.0', 'release_run_id': rrid, 'run_id': f'{rrid}-original',
    'generated_at': fin, 'manual_checks_pending': False,
    'all_gates_passed': env_bool('BOOL_TREE_GATE_PASSED'),
}, indent=2))

# source-tree-manifest.sha256 — uses tree_hash.py rules (including EXCLUDE_DIR_PREFIXES)
sys.path.insert(0, str(Path('scripts').resolve()))
from tree_hash import EXCLUDE_DIRS, EXCLUDE_DIR_PREFIXES, EXCLUDE_FILE_EXTENSIONS, EXCLUDE_FILES
def should_exclude(fn):
    for ext in EXCLUDE_FILE_EXTENSIONS:
        if fn.endswith(ext): return True
    return fn.endswith('.tar.gz')
mlines = []; fcount = 0
for dp, dns, fns in os.walk('.'):
    dns[:] = [
        d for d in dns
        if d not in EXCLUDE_DIRS
        and not any(str(Path(dp) / d).startswith(str(Path('.') / p)) for p in EXCLUDE_DIR_PREFIXES)
    ]
    for fn in sorted(fns):
        if should_exclude(fn): continue
        fp = Path(dp) / fn; rel = str(fp.relative_to('.'))
        if rel in EXCLUDE_FILES: continue
        try: content = fp.read_bytes()
        except: continue
        mlines.append(f'{hashlib.sha256(content).hexdigest()}  {rel}')
        fcount += 1
Path('artifacts/source-tree-manifest.sha256').write_text('\n'.join(sorted(mlines)) + '\n')
Path('artifacts/source-tree-hash.json').write_text(json.dumps({
    'schema_version': '1.0', 'algorithm': 'sha256-path-mode-size-content-v1',
    'file_count': fcount, 'tree_sha256': os.environ['SOURCE_TREE_SHA256_BEFORE'],
}, indent=2))
# evidence-manifest.sha256 — evidence files generated during this release run
elines = []
for ep in sorted(Path('artifacts').glob('**/*')):
    if ep.is_dir(): continue
    erel = str(ep.relative_to('.'))
    # Include only evidence artifacts, not source manifests
    if erel.startswith('artifacts/acceptance-test-baseline'): continue
    if erel.startswith('artifacts/source-tree-'): continue
    try:
        eh = hashlib.sha256(ep.read_bytes()).hexdigest()
        elines.append(f'{eh}  {erel}')
    except: pass
Path('artifacts/evidence-manifest.sha256').write_text('\n'.join(sorted(elines)) + '\n')
print('Internal reports and manifests generated')
PYEOF

# ── 11. Build tarball ──
echo "=== BUILDING TARBALL ==="
bash scripts/build-tarball.sh
TB=$(ls -t dist/dakota-replay2-*.tar.gz 2>/dev/null | head -1)
[ -n "$TB" ] && [ -f "$TB" ] || { echo "ERROR: tarball not created"; exit 1; }
TB_HASH=$(sha256sum "$TB" | cut -d' ' -f1)
TB_SIZE=$(stat -c%s "$TB")
TB_ENTRIES=$(tar -tzf "$TB" | wc -l)
echo "tarball=$TB hash=$TB_HASH size=$TB_SIZE entries=$TB_ENTRIES"

# ── 10. Extract and run FULL phase 8 on extracted tree ──
echo "=== EXTRACTED VALIDATION ==="
EXTRACT_DIR=$(mktemp -d)
tar -xzf "$TB" -C "$EXTRACT_DIR"
mapfile -t roots < <(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d -name 'dakota-replay2-*' -print)
[ "${#roots[@]}" -eq 1 ] || { echo "ERROR: expected 1 root, got ${#roots[@]}"; rm -rf "$EXTRACT_DIR"; exit 1; }
EXTRACT_ROOT="${roots[0]}"
echo "extract_root=$EXTRACT_ROOT"

EXTRACTED_HASH=$(cd "$EXTRACT_ROOT" && python3 scripts/tree_hash.py 2>/dev/null || echo "")
echo "extracted_hash=$EXTRACTED_HASH"
[ "$EXTRACTED_HASH" = "$SOURCE_TREE_SHA256_BEFORE" ] || { echo "ERROR: hash mismatch"; rm -rf "$EXTRACT_DIR"; exit 1; }

BASELINE_OK_EXTRACTED=False
(cd "$EXTRACT_ROOT" && sha256sum -c artifacts/acceptance-test-baseline.sha256) && BASELINE_OK_EXTRACTED=True

EXTRACTED_GATE_PASSED=False; EXTRACTED_GATE_RC=1
export PYTHONPATH="$EXTRACT_ROOT/gateway"
rm -f "$EXTRACT_ROOT/artifacts/visual-test-result.json"
rm -rf "$EXTRACT_ROOT/artifacts/acceptance-logs/"
mkdir -p "$EXTRACT_ROOT/artifacts/acceptance-logs/"
if (cd "$EXTRACT_ROOT" && bash scripts/acceptance/run-phase-08-full.sh); then
  EXTRACTED_GATE_PASSED=True; EXTRACTED_GATE_RC=0
  echo "EXTRACTED TREE GATE PASSED"
else
  EXTRACTED_GATE_RC=$?
  echo "EXTRACTED TREE GATE FAILED (rc=$EXTRACTED_GATE_RC)"
fi

# Check extracted visual + contamination
EXTRACTED_VISUAL_OK=False; EXTRACTED_CONTAMINATION_OK=False
if [ -f "$EXTRACT_ROOT/artifacts/visual-test-result.json" ]; then
  python3 -c "
import json
v=json.load(open('$EXTRACT_ROOT/artifacts/visual-test-result.json'))
v['release_run_id']='$RELEASE_RUN_ID'
v['run_id']='$RELEASE_RUN_ID-extracted-visual'
json.dump(v,open('$EXTRACT_ROOT/artifacts/visual-test-result.json','w'),indent=2)
"
  EVP=$(python3 -c "import json;v=json.load(open('$EXTRACT_ROOT/artifacts/visual-test-result.json'));print(str(v.get('passed',False)).lower())")
  EVH=$(python3 -c "import json;v=json.load(open('$EXTRACT_ROOT/artifacts/visual-test-result.json'));print(v.get('source_tree_sha256',''))")
  [ "$EVP" = "true" ] && [ "$EVH" = "$EXTRACTED_HASH" ] && EXTRACTED_VISUAL_OK=True
  [ -f "$EXTRACT_ROOT/artifacts/acceptance-logs/current/phase07-contamination.result.json" ] && \
    EXTRACTED_CONTAMINATION_OK=$(cd "$EXTRACT_ROOT" && python3 -c "import json; d=json.load(open('artifacts/acceptance-logs/current/phase07-contamination.result.json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
  [ "$EXTRACTED_CONTAMINATION_OK" = "true" ] && EXTRACTED_CONTAMINATION_OK=True || EXTRACTED_CONTAMINATION_OK=False
fi

EXTRACTED_REMAINING=$(python3 -c "
import subprocess,os
r=subprocess.run(['pgrep','-U',str(os.getuid()),'-f','user-data-dir.*dakota-visual-'],capture_output=True,text=True)
pids=[l for l in r.stdout.strip().splitlines() if l.strip().isdigit() and l!=str(os.getpid())]
print(len(pids))
")
EXTRACTED_ZOMBIES=$(python3 -c "
import subprocess,os
r=subprocess.run(['ps','-U',str(os.getuid()),'-o','pid,stat','--no-headers'],capture_output=True,text=True)
z=[l.split()[0] for l in r.stdout.strip().splitlines() if len(l.split())>=2 and 'Z' in l.split()[1]]
print(len(z))
")

# ── 11. test-all results from structured JSON ──
TEST_ALL_ORIG=False; TEST_ALL_EXTR=False
[ -f artifacts/acceptance-logs/current/test-all.result.json ] && \
  TEST_ALL_ORIG=$(python3 -c "import json; d=json.load(open('artifacts/acceptance-logs/current/test-all.result.json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
[ "$TEST_ALL_ORIG" = "true" ] && TEST_ALL_ORIG=True || TEST_ALL_ORIG=False
[ -f "$EXTRACT_ROOT/artifacts/acceptance-logs/current/test-all.result.json" ] && \
  TEST_ALL_EXTR=$(cd "$EXTRACT_ROOT" && python3 -c "import json; d=json.load(open('artifacts/acceptance-logs/current/test-all.result.json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
[ "$TEST_ALL_EXTR" = "true" ] && TEST_ALL_EXTR=True || TEST_ALL_EXTR=False

# ── 12. Compute release validation ──
RELEASE_OK=False
if [ "$TREE_GATE_PASSED" = "True" ] && [ "$EXTRACTED_GATE_PASSED" = "True" ] && \
   [ "$BASELINE_OK_ORIGINAL" = "True" ] && [ "$BASELINE_OK_EXTRACTED" = "True" ] && \
   [ "$VISUAL_OK" = "True" ] && [ "$EXTRACTED_VISUAL_OK" = "True" ] && \
   [ "$CONTAMINATION_OK" = "True" ] && [ "$EXTRACTED_CONTAMINATION_OK" = "True" ] && \
   [ "$TEST_ALL_ORIG" = "True" ] && [ "$TEST_ALL_EXTR" = "True" ] && \
   [ "$SOURCE_TREE_UNCHANGED" = "True" ] && [ "$EXTRACTED_HASH" = "$SOURCE_TREE_SHA256_BEFORE" ] && \
   [ "$REMAINING_ORIG" -eq 0 ] && [ "$EXTRACTED_REMAINING" -eq 0 ] && \
   [ "$ZOMBIES_ORIG" -eq 0 ] && [ "$EXTRACTED_ZOMBIES" -eq 0 ]; then
  RELEASE_OK=True
fi

# ── 13. Export env vars for safe Python consumption ──
export RELEASE_RUN_ID STARTED_AT
export SOURCE_TREE_SHA256_BEFORE SOURCE_TREE_SHA256_AFTER EXTRACTED_HASH
export TB_HASH TB_SIZE TB_ENTRIES
TB_NAME=$(basename "$TB"); export TB_NAME
export REMAINING_ORIG ZOMBIES_ORIG EXTRACTED_REMAINING EXTRACTED_ZOMBIES
# Export booleans as 1/0 for os.environ
for var in TREE_GATE_PASSED EXTRACTED_GATE_PASSED BASELINE_OK_ORIGINAL BASELINE_OK_EXTRACTED \
           VISUAL_OK EXTRACTED_VISUAL_OK CONTAMINATION_OK EXTRACTED_CONTAMINATION_OK \
           TEST_ALL_ORIG TEST_ALL_EXTR SOURCE_TREE_UNCHANGED RELEASE_OK; do
  [ "${!var}" = "True" ] && export "BOOL_${var}=1" || export "BOOL_${var}=0"
done

PY_VER=$(python3 --version 2>&1); export PY_VER
PYTEST_VER=$(python3 -m pytest --version 2>&1 | head -1); export PYTEST_VER
NODE_VER=$(node --version 2>&1); export NODE_VER
TCL_VER=$(echo 'puts [info patchlevel]' | tclsh 2>&1); export TCL_VER
CHROMIUM_VER=$("$CHROMIUM" --version 2>&1 || echo "unknown"); export CHROMIUM_VER
FINISHED_AT=$(date -Iseconds); export FINISHED_AT

# ── 14. Generate external manifest via Python ──
echo "=== EXTERNAL MANIFEST ==="
python3 << 'PYEOF'
import json, os
from datetime import datetime, timezone

def env_bool(name):
    v = os.environ.get(name, '').strip()
    if v == '1': return True
    if v == '0': return False
    raise ValueError(f"invalid boolean env {name}={v!r}")

rrid = os.environ['RELEASE_RUN_ID']
fin = os.environ.get('FINISHED_AT', datetime.now(timezone.utc).isoformat())

mn = f'dist/{os.environ["TB_NAME"].replace(".tar.gz","")}.manifest.json'
manifest = {
    'schema_version': '1.0', 'release_run_id': rrid, 'created_at': fin,
    'tarball_name': os.environ['TB_NAME'],
    'tarball_sha256': os.environ['TB_HASH'],
    'tarball_size': int(os.environ['TB_SIZE']),
    'tarball_entries': int(os.environ['TB_ENTRIES']),
    'source_tree_sha256': os.environ['SOURCE_TREE_SHA256_BEFORE'],
    'source_tree_sha256_after': os.environ['SOURCE_TREE_SHA256_AFTER'],
    'extracted_tree_sha256': os.environ['EXTRACTED_HASH'],
    'original_tree_gate_passed': env_bool('BOOL_TREE_GATE_PASSED'),
    'extracted_tree_gate_passed': env_bool('BOOL_EXTRACTED_GATE_PASSED'),
    'baseline_verified_original': env_bool('BOOL_BASELINE_OK_ORIGINAL'),
    'baseline_verified_extracted': env_bool('BOOL_BASELINE_OK_EXTRACTED'),
    'visual_verified_original': env_bool('BOOL_VISUAL_OK'),
    'visual_verified_extracted': env_bool('BOOL_EXTRACTED_VISUAL_OK'),
    'contamination_verified_original': env_bool('BOOL_CONTAMINATION_OK'),
    'contamination_verified_extracted': env_bool('BOOL_EXTRACTED_CONTAMINATION_OK'),
    'test_all_passed_original': env_bool('BOOL_TEST_ALL_ORIG'),
    'test_all_passed_extracted': env_bool('BOOL_TEST_ALL_EXTR'),
    'remaining_processes_original': int(os.environ.get('REMAINING_ORIG','0')),
    'remaining_processes_extracted': int(os.environ.get('EXTRACTED_REMAINING','0')),
    'remaining_zombies_original': int(os.environ.get('ZOMBIES_ORIG','0')),
    'remaining_zombies_extracted': int(os.environ.get('EXTRACTED_ZOMBIES','0')),
    'environment': {
        'python': os.environ.get('PY_VER',''), 'pytest': os.environ.get('PYTEST_VER',''),
        'node': os.environ.get('NODE_VER',''), 'tcl': os.environ.get('TCL_VER',''),
        'chromium': os.environ.get('CHROMIUM_VER',''),
    },
    'release_validation_passed': env_bool('BOOL_RELEASE_OK'),
}
with open(mn, 'w') as f:
    json.dump(manifest, f, indent=2); f.write('\n')
for key in ['original_tree_gate_passed','extracted_tree_gate_passed','release_validation_passed']:
    assert isinstance(manifest[key], bool), f'{key} must be bool'
assert manifest['tarball_name'] == os.environ['TB_NAME']
assert manifest['tarball_sha256'] == os.environ['TB_HASH']
print(f'Manifest: {mn}')
print('Validated: all booleans are bool, no NameError')
PYEOF
# ── 15. Generate .sha256 and verify ──
echo "$TB_HASH  $(basename "$TB")" > "dist/$(basename "$TB").sha256"
(cd dist && sha256sum -c "$(basename "$TB").sha256") || { echo "ERROR: .sha256 verification failed"; RELEASE_OK=False; }

# ── 16. Cleanup ──
rm -rf "$EXTRACT_DIR"

# ── 17. Final report ──
echo ""
echo "=== FINAL ACCEPTANCE COMPLETE ==="
echo "release_run_id: $RELEASE_RUN_ID"
echo "tree_hash: $SOURCE_TREE_SHA256_BEFORE"
echo "tree_gate: $TREE_GATE_PASSED"
echo "extracted_gate: $EXTRACTED_GATE_PASSED"
echo "release_valid: $RELEASE_OK"
echo "tarball: $TB"
echo "tarball_hash: $TB_HASH"
echo "tarball_size: $TB_SIZE"
echo "tarball_entries: $TB_ENTRIES"

if [ "$RELEASE_OK" != "True" ]; then
  echo "ERROR: Release validation failed"
  exit 1
fi
echo "RELEASE VALIDATION PASSED"
