#!/bin/bash

# This is convenient, as it allows not to install the package to run the
# test suite and might also remind the AI how to do it

export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python3 -m pytest tests/ -v --tb=short
