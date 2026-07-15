#!/bin/bash
echo "USER=$USER HOME=$HOME uid=$(id -u) gid=$(id -g)" \
    > /home/rocky/hookd/tests/owner-check.txt
