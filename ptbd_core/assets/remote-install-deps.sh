set -eu

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
fi

id="${ID:-}"
id_like="${ID_LIKE:-}"
spectrum_mode="${PTBD_AUDIO_SPECTRUM_MODE:-single}"

case "$spectrum_mode" in
  single|combined) ;;
  *)
    echo "status=invalid-audio-spectrum-mode" >&2
    exit 2
    ;;
esac

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

has_py_mod() {
  has_cmd python3 && python3 -c "import $1" >/dev/null 2>&1
}

word_match() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}

is_debian_like() {
  word_match "$id $id_like" debian || word_match "$id $id_like" ubuntu
}

is_alpine_like() {
  word_match "$id $id_like" alpine
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return $?
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
    return $?
  fi
  return 97
}

enable_ubuntu_universe() {
  if [ "$id" != "ubuntu" ]; then
    return 0
  fi
  if apt-cache show mediainfo >/dev/null 2>&1 && apt-cache show ffmpeg >/dev/null 2>&1; then
    return 0
  fi
  echo "[remote] Ubuntu universe repo looks unavailable; trying to enable it" >&2
  if ! command -v add-apt-repository >/dev/null 2>&1; then
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common >/dev/null
  fi
  if command -v add-apt-repository >/dev/null 2>&1; then
    as_root add-apt-repository -y universe >/dev/null 2>&1 || true
  fi
}

enable_alpine_community() {
  if grep -Eq '^[[:space:]]*[^#].*/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
    return 0
  fi
  echo "[remote] Alpine community repo looks unavailable; trying to enable it" >&2
  as_root sh -eu <<'EOS'
tmp_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

if grep -Eq '^[[:space:]]*#.*\/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
  awk '
    /^[[:space:]]*#/ && /\/community\/?[[:space:]]*$/ { sub(/^[[:space:]]*#[[:space:]]*/, "", $0) }
    { print }
  ' /etc/apk/repositories > "$tmp_file"
else
  awk '
    { print }
    /^[[:space:]]*[^#].*\/main\/?[[:space:]]*$/ {
      line=$0
      sub(/\/main\/?[[:space:]]*$/, "/community", line)
      print line
    }
  ' /etc/apk/repositories > "$tmp_file"
fi

cp "$tmp_file" /etc/apk/repositories
EOS
}

missing_required=""
for cmd in tar bash python3 curl ffmpeg ffprobe mediainfo; do
  if ! has_cmd "$cmd"; then
    missing_required="$missing_required $cmd"
  fi
done
need_spectrum_packages="0"
if [ "$spectrum_mode" = "combined" ]; then
  if ! has_py_mod numpy; then
    need_spectrum_packages="1"
  fi
  if ! has_py_mod PIL; then
    need_spectrum_packages="1"
  fi
fi

need_optional_bd="0"
if ! has_cmd BDInfo && ! has_cmd bd_info; then
  need_optional_bd="1"
fi

if [ -z "${missing_required# }" ] && [ "$need_spectrum_packages" = "0" ] && [ "$need_optional_bd" = "0" ]; then
  echo "status=ready"
  exit 0
fi

if is_debian_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Debian/Ubuntu detected; installing system packages for PT-BDtool" >&2
  as_root apt-get update >/dev/null
  if [ "$need_spectrum_packages" = "1" ]; then
    install_packages="bash curl python3 tar ffmpeg mediainfo zip python3-numpy python3-pil"
  else
    install_packages="bash curl python3 tar ffmpeg mediainfo zip"
  fi
  # Package names are fixed above; intentional splitting passes them as arguments.
  # shellcheck disable=SC2086
  if ! as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $install_packages >/dev/null 2>&1; then
    enable_ubuntu_universe
    as_root apt-get update >/dev/null
    # shellcheck disable=SC2086
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $install_packages >/dev/null
  fi
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y libbluray-bin >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

if is_alpine_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Alpine detected; installing system packages for PT-BDtool" >&2
  if [ "$need_spectrum_packages" = "1" ]; then
    install_packages="bash curl python3 tar ffmpeg mediainfo zip py3-numpy py3-pillow"
  else
    install_packages="bash curl python3 tar ffmpeg mediainfo zip"
  fi
  # shellcheck disable=SC2086
  if ! as_root apk add --no-cache $install_packages >/dev/null 2>&1; then
    enable_alpine_community
    as_root apk update >/dev/null
    # shellcheck disable=SC2086
    as_root apk add --no-cache $install_packages >/dev/null
  fi
  as_root apk add --no-cache libbluray >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

echo "status=unsupported"
