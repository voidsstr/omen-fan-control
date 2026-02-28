#!/bin/bash
# omenfan installer — detects distro, installs dependencies, sets up omenfan.
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*" >&2; }

# ── Detect distro ─────────────────────────────────────────────
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO_ID="$ID"
        DISTRO_NAME="${PRETTY_NAME:-$ID}"
    elif [ -f /etc/debian_version ]; then
        DISTRO_ID="debian"
        DISTRO_NAME="Debian $(cat /etc/debian_version)"
    elif [ -f /etc/redhat-release ]; then
        DISTRO_ID="rhel"
        DISTRO_NAME="$(cat /etc/redhat-release)"
    else
        DISTRO_ID="unknown"
        DISTRO_NAME="Unknown Linux"
    fi
}

# ── Check root ────────────────────────────────────────────────
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "This installer needs root privileges."
        echo "  Run: sudo $0"
        exit 1
    fi
}

# ── Check Python ──────────────────────────────────────────────
check_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON=python
    else
        error "Python 3 not found. Install Python 3.8+ first."
        exit 1
    fi

    PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
        error "Python 3.8+ required (found $PY_VER)"
        exit 1
    fi
    info "Python $PY_VER found"
}

# ── Install packages per distro ───────────────────────────────
install_deps() {
    info "Detected: $DISTRO_NAME"

    case "$DISTRO_ID" in
        ubuntu|debian|linuxmint|pop|elementary|zorin)
            apt-get update -qq
            apt-get install -y -qq lm-sensors acpi-call-dkms 2>/dev/null || \
                apt-get install -y -qq lm-sensors
            ;;
        arch|manjaro|endeavouros|garuda)
            pacman -Sy --noconfirm --needed lm_sensors acpi_call 2>/dev/null || \
                pacman -Sy --noconfirm --needed lm_sensors
            ;;
        fedora)
            dnf install -y -q lm_sensors
            warn "acpi_call not in Fedora repos — build from source if needed for HP OMEN"
            ;;
        opensuse*|suse*)
            zypper install -y lm_sensors acpi_call-kmp-default 2>/dev/null || \
                zypper install -y lm_sensors
            ;;
        gentoo)
            emerge --noreplace sys-apps/lm-sensors sys-power/acpi_call 2>/dev/null || \
                emerge --noreplace sys-apps/lm-sensors
            ;;
        void)
            xbps-install -Sy lm_sensors acpi_call-dkms 2>/dev/null || \
                xbps-install -Sy lm_sensors
            ;;
        nixos)
            warn "NixOS: add lm_sensors and acpi_call to your configuration.nix"
            warn "  hardware.sensor.lm_sensors.enable = true;"
            warn "  boot.extraModulePackages = [ config.boot.kernelPackages.acpi_call ];"
            return
            ;;
        *)
            warn "Unrecognized distro '$DISTRO_ID' — install lm-sensors manually"
            warn "  Most distros: lm-sensors or lm_sensors package"
            ;;
    esac
}

# ── Load kernel modules ──────────────────────────────────────
load_modules() {
    # Load acpi_call if available (for HP OMEN WMI)
    if modprobe acpi_call 2>/dev/null; then
        info "Loaded acpi_call module"
    fi

    # Run sensors-detect to probe and load the right hwmon driver
    if command -v sensors-detect >/dev/null 2>&1; then
        info "Running sensors-detect to find fan controllers..."
        yes "" | sensors-detect --auto >/dev/null 2>&1 || true
        info "Sensor modules loaded"
    else
        # Try loading common fan drivers manually
        for mod in nct6775 it87 dell-smm-hwmon thinkpad_acpi; do
            modprobe "$mod" 2>/dev/null && info "Loaded $mod" || true
        done
    fi
}

# ── Install omenfan ──────────────────────────────────────────
install_omenfan() {
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    # Check if we're in the repo directory
    if [ -f "$SCRIPT_DIR/omenfan/__main__.py" ]; then
        info "Installing from source directory..."

        # Build zipapp binary
        if [ -f "$SCRIPT_DIR/Makefile" ]; then
            make -C "$SCRIPT_DIR" build 2>/dev/null && \
                info "Built omenfan.pyz zipapp binary"
        fi

        # Install to /usr/local/bin
        if [ -f "$SCRIPT_DIR/omenfan.pyz" ]; then
            cp "$SCRIPT_DIR/omenfan.pyz" /usr/local/bin/omenfan
            chmod +x /usr/local/bin/omenfan
            info "Installed omenfan to /usr/local/bin/omenfan"
        else
            # Fallback: create a wrapper script
            cat > /usr/local/bin/omenfan << WRAPPER
#!/bin/bash
exec $PYTHON -m omenfan "\$@"
WRAPPER
            chmod +x /usr/local/bin/omenfan
            # Also install the package
            cd "$SCRIPT_DIR"
            $PYTHON -m pip install . --quiet 2>/dev/null || \
                $PYTHON setup.py install --quiet 2>/dev/null || \
                warn "Could not install as package — use 'sudo python3 -m omenfan' from $SCRIPT_DIR"
        fi
    else
        error "Run this script from the omenfan source directory"
        exit 1
    fi
}

# ── Main ─────────────────────────────────────────────────────
main() {
    echo ""
    echo "  omenfan installer"
    echo "  Linux fan control + system monitor"
    echo ""

    check_root
    detect_distro
    check_python
    install_deps
    load_modules
    install_omenfan

    echo ""
    info "Installation complete!"
    echo ""
    echo "  Run:  sudo omenfan"
    echo "  Or:   sudo python3 -m omenfan"
    echo ""
    echo "  Monitor-only (no root):  python3 -m omenfan"
    echo ""
}

main "$@"
