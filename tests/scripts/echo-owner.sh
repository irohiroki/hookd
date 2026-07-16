#!/bin/bash
echo "USER=$USER HOME=$HOME uid=$(id -u) gid=$(id -g)" \
    > $HOME/hookd/tests/owner-check.txt
