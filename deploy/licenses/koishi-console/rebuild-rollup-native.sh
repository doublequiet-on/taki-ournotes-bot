#!/bin/bash
set -euo pipefail
shared=$(cd "$1" && pwd)
archive=$(realpath "$2")
patch=$(realpath "$3")
registry=$(realpath "$4")
test "$(sha256sum "$archive" | cut -d' ' -f1)" = 951175a04825fabbf078497a7de4ff0d318cc9727b449614a704ee1fe4556f2a
(
  cd "$shared/tools"
  sha256sum -c <<'SUMS'
ea0157efeacae238bd8cbab0a9465e90c5240b3f6d607c98b1118668b3033295  rustc-nightly-x86_64-unknown-linux-gnu.tar.xz
d16f82e2845513ccf914666595b54363e46d748468ff406f5318cd998638bd13  rust-std-nightly-x86_64-unknown-linux-gnu.tar.xz
fccc62fc199c5704f34d5696d6e4ad63376640362ad4db2173cbbf3f6a67edd5  cargo-nightly-x86_64-unknown-linux-gnu.tar.xz
02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239  zig-x86_64-linux-0.15.2.tar.xz
630615d8e98ac33eba7fbe472626dff5c899c85af3c024585ae109166a6909d0  cmake-3.31.8-linux-x86_64.tar.gz
0830252db77884957a1a4b87b05a1e2d9b5f658b8367f82999a941884cbe0238  ninja-linux.zip
SUMS
)
temp=$(mktemp -d /tmp/taki-rollup09-XXXXXX)
echo "$temp" > "$shared/native-scratch.txt"
mkdir "$temp/tools" "$temp/toolchain"
for tool in cargo rustc rust-std; do
  tar -xJf "$shared/tools/$tool-nightly-x86_64-unknown-linux-gnu.tar.xz" -C "$temp/tools"
  bash "$temp/tools/$tool-nightly-x86_64-unknown-linux-gnu/install.sh" --prefix="$temp/toolchain" --disable-ldconfig
done
tar -xJf "$shared/tools/zig-x86_64-linux-0.15.2.tar.xz" -C "$temp/tools"
tar -xzf "$shared/tools/cmake-3.31.8-linux-x86_64.tar.gz" -C "$temp/tools"
mkdir "$temp/tools/ninja"
python3 -m zipfile -e "$shared/tools/ninja-linux.zip" "$temp/tools/ninja"
chmod +x "$temp/tools/ninja/ninja"
zig="$temp/tools/zig-x86_64-linux-0.15.2/zig"
cat > "$temp/cc" <<EOF
#!/bin/bash
set -euo pipefail
args=()
for arg in "\$@"; do
  case "\$arg" in
    --target=x86_64-unknown-linux-gnu) ;;
    --target=*|-target) echo "unexpected C compiler target: \$arg" >&2; exit 1 ;;
    *) args+=("\$arg") ;;
  esac
done
exec "$zig" cc -target x86_64-linux-gnu.2.17 "\${args[@]}"
EOF
printf '#!/bin/bash\nexec "%s" ar "$@"\n' "$zig" > "$temp/ar"
sed 's/ cc -target/ c++ -target/' "$temp/cc" > "$temp/cxx"
chmod +x "$temp/cc" "$temp/cxx" "$temp/ar"
export PATH="$temp/toolchain/bin:$temp/tools/cmake-3.31.8-linux-x86_64/bin:$temp/tools/ninja:/usr/bin:/bin"
export RUSTC="$temp/toolchain/bin/rustc"
export LD_LIBRARY_PATH="$temp/toolchain/lib"
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="$temp/cc"
export CC="$temp/cc" CXX="$temp/cxx" AR="$temp/ar"
export CMAKE_GENERATOR=Ninja
export CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2 CARGO_NET_OFFLINE=true SOURCE_DATE_EPOCH=0
rustc -vV
cargo -V
"$zig" version
cmake --version
ninja --version
uname -a
build_one() (
  set -euo pipefail
  name=$1
  work="$temp/$name"
  mkdir "$work" "$work/cargo-home" "$work/cargo-home/registry"
  cp -a "$registry/cache" "$registry/index" "$work/cargo-home/registry/"
  export CARGO_HOME="$work/cargo-home"
  export ZIG_GLOBAL_CACHE_DIR="$work/zig-cache"
  export ZIG_LOCAL_CACHE_DIR="$work/zig-local-cache"
  export RUSTFLAGS="--remap-path-prefix=$work=/taki-rollup"
  tar -xzf "$archive" -C "$work"
  cd "$work"/rollup-*
  git apply --check "$patch"
  git apply "$patch"
  test "$(sha256sum rust/Cargo.lock | cut -d' ' -f1)" = 4419e784a43fe1546066b7c350e3fc52c55776992558243a369a1a2fc2f3fab6
  cargo tree --manifest-path rust/Cargo.toml --offline --locked -p bindings_napi --target x86_64-unknown-linux-gnu -e normal,build --prefix none --format '{p}|{f}' > "$shared/native-tree-$name.txt"
  if grep -E '^base-encode ' "$shared/native-tree-$name.txt"; then exit 1; fi
  cargo build --manifest-path rust/Cargo.toml --offline --locked --release -p bindings_napi --target x86_64-unknown-linux-gnu
  nodefile=rust/target/x86_64-unknown-linux-gnu/release/libbindings_napi.so
  test -f "$nodefile"
  test ! -e "$shared/rollup.linux-x64-gnu-$name.node"
  cp "$nodefile" "$shared/rollup.linux-x64-gnu-$name.node"
  sha256sum "$shared/rollup.linux-x64-gnu-$name.node"
  readelf --version-info "$nodefile" > "$shared/elf-versions-$name.txt"
)
build_one A > "$shared/native-A.log" 2>&1 & a=$!
build_one B > "$shared/native-B.log" 2>&1 & b=$!
rc=0
wait "$a" || rc=1
wait "$b" || rc=1
exit "$rc"
