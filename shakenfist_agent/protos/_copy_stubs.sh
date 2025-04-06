#!/bin/bash

for proto in agent common; do
    cp ~/src/shakenfist/shakenfist/protos/${proto}* .
    cp ~/src/shakenfist/shakenfist/shakenfist/protos/${proto}* .
done

for item in *.py; do
    sed -i '' "s/from shakenfist.protos import/from shakenfist_agent.protos import/g" ${item}
done