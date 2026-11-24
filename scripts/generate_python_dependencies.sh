#!/bin/sh

# For now we exclude the following dependencies, because they import an older version
# of nomad-lab causing inevitable conflicts.
#    dependencies/parsers/atomistic/pyproject.toml \
#    dependencies/parsers/database/pyproject.toml \
#    dependencies/parsers/electronic/pyproject.toml \
#    dependencies/parsers/workflow/pyproject.toml \

set -e

working_dir=$(pwd)
project_dir=$(dirname $(dirname $(realpath $0)))

cd $project_dir

# Compile requirements for a production installation
uv pip compile -U --universal -p 3.11 --annotation-style=line \
    --extra=infrastructure \
    --output-file=requirements.txt \
    pyproject.toml

# Compile requirements for a dev installation
uv pip compile -U --universal -p 3.11 --annotation-style=line \
    --extra=dev --extra=infrastructure \
    --output-file=requirements-dev.txt \
    requirements.txt \
    pyproject.toml

# Compile requirements for the CI installation.
uv pip compile --universal -p 3.11 --annotation-style=line \
    --output-file=requirements-plugins.txt \
    --unsafe-package nomad-lab \
    -c requirements-dev.txt \
    test_plugins.txt
