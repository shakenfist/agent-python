#!/bin/bash

# Copy the proto sources and generated stubs from a shakenfist checkout
# and rewrite their imports to this package. Run from this directory.

set -e

for proto in agent common; do
    cp ~/src/shakenfist/shakenfist/protos/"${proto}"* .
    cp ~/src/shakenfist/shakenfist/shakenfist/protos/"${proto}"* .
done

# A suffix attached to -i is the one spelling GNU and BSD sed both accept;
# the BSD-only `sed -i ''` fails on Linux with "can't read : No such file".
for item in *.py; do
    sed -i.bak "s/from shakenfist.protos import/from shakenfist_agent.protos import/g" "${item}"
    rm -f "${item}.bak"
done
