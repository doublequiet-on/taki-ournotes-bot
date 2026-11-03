#!/usr/bin/env bash
# Component research only; never installs a Taki product or publishes packages.
set -euo pipefail
MATERIALS=$(cd "$(dirname "$0")" && pwd)
WORK=${1:?provide a new absolute work directory}
[[ "$WORK" = /* && ! -e "$WORK" ]] || { echo 'new absolute directory required' >&2; exit 1; }
mkdir -p "$WORK/home" "$WORK/logs"
cp -a "$MATERIALS/source" "$WORK/source"
tar -xf "$MATERIALS/tools/node-v24.19.0-linux-x64.tar.xz" -C "$WORK"
export PATH="$WORK/node-v24.19.0-linux-x64/bin:/usr/bin:/bin"
export HOME="$WORK/home"
export YARN_ENABLE_GLOBAL_CACHE=false YARN_ENABLE_TELEMETRY=0 YARN_ENABLE_NETWORK=0
export YARN_ENABLE_IMMUTABLE_INSTALLS=true
cd "$WORK/source"
YARN="$MATERIALS/tools/yarn-4.10.3.cjs"
{ uname -a; node --version; node "$YARN" --version; } > "$WORK/logs/environment.txt"
node "$YARN" install --immutable --immutable-cache --mode=skip-build > "$WORK/logs/install.log" 2>&1
# Preserve pipeline failures: self-hosting client lib is initially absent; current
# resolution then hits client dispatch interop. Neither is called an upstream pass.
for attempt in 1 2; do
  status=0
  node "$YARN" build > "$WORK/logs/pipeline-$attempt.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$WORK/logs/pipeline-$attempt.exit"
done
node --require tsx/cjs -e 'require("./packages/client/scripts/client.ts").default()' > "$WORK/logs/client.log" 2>&1
python3 - "$MATERIALS" "$WORK" <<'PY'
import hashlib, json, pathlib, sys
materials, work = map(pathlib.Path, sys.argv[1:])
rows = []
for row in json.loads((materials / 'report/provenance.json').read_text()):
    path = work / 'source/plugins/console' / row['path']
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    rows.append(dict(path=row['path'], sha256=actual, matches_reconstruction=actual == row['rebuilt_sha256']))
(work / 'logs/replay-comparison.json').write_text(json.dumps(rows, indent=2))
assert all(row['matches_reconstruction'] for row in rows), 'reconstruction differs; preserve logs and outputs'
print('Reconstruction matches saved experiment; original npm provenance remains unproven.')
PY
