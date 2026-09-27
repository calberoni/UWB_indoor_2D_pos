#!/usr/bin/env bash
# Descarga el driver DW3000 de Qorvo (dwt_uwb_driver 08.02.02) y lo empaqueta
# como librería Arduino en firmware/lib/decadriver/.
#
# El driver no se versiona en este repo: conserva su licencia
# (LicenseRef-QORVO-2) dentro de la carpeta generada.
set -euo pipefail

REPO=https://github.com/br101/dw3000-decadriver-source
COMMIT=67dfbb7f2c5b1a4157b8c265b19f08776e91b1cc

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=$ROOT/firmware/lib/.cache/dw3000-decadriver-source
DEST=$ROOT/firmware/lib/decadriver

if [ -f "$DEST/.commit" ] && [ "$(cat "$DEST/.commit")" = "$COMMIT" ]; then
    echo "driver: ya instalado ($COMMIT)"
    exit 0
fi

if [ ! -d "$CACHE/.git" ]; then
    mkdir -p "$CACHE"
    git -C "$CACHE" init --quiet
    git -C "$CACHE" remote add origin "$REPO"
fi
git -C "$CACHE" fetch --quiet --depth 1 origin "$COMMIT"
git -C "$CACHE" -c advice.detachedHead=false checkout --quiet FETCH_HEAD

rm -rf "$DEST"
mkdir -p "$DEST/src/dw3000"

DRV=$CACHE/dwt_uwb_driver
cp "$DRV"/deca_device_api.h "$DRV"/deca_interface.h "$DRV"/deca_interface.c \
   "$DRV"/deca_private.h "$DRV"/deca_rsl.c "$DRV"/deca_rsl.h \
   "$DRV"/deca_types.h "$DRV"/deca_version.h "$DEST/src/"
cp "$DRV"/lib/qmath/include/qmath.h "$DRV"/lib/qmath/src/qmath.c "$DEST/src/"
cp "$DRV"/dw3000/dw3000_device.c "$DRV"/dw3000/dw3000_deca_regs.h \
   "$DRV"/dw3000/dw3000_deca_vals.h "$DEST/src/"
# deca_compat.c incluye las cabeceras del chip con el prefijo dw3000/
cp "$DRV"/dw3000/dw3000_deca_regs.h "$DRV"/dw3000/dw3000_deca_vals.h "$DEST/src/dw3000/"
# Variante de deca_compat.c sin el ioctl genérico, para un solo chip por placa
cp "$CACHE"/platform/deca_compat.c "$CACHE"/platform/deca_ull.h \
   "$CACHE"/platform/deca_probe_interface.h "$DEST/src/"

cp -R "$DRV"/LICENSES "$DEST/"
cp "$CACHE"/LICENSE.txt "$DEST/LICENSE-platform.txt"

cat > "$DEST/library.properties" <<EOF
name=decadriver
version=8.2.2
author=Qorvo
maintainer=Qorvo
sentence=Driver dwt_uwb_driver para DW3000
paragraph=Fuente oficial de Qorvo, version 08.02.02, empaquetada sin cambios funcionales.
category=Communication
url=$REPO
architectures=*
EOF

echo "$COMMIT" > "$DEST/.commit"
echo "driver: instalado en firmware/lib/decadriver ($COMMIT)"
