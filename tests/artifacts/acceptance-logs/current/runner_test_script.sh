#!/bin/bash
bash -c 'bash -c "trap \"\" TERM; sleep 60" & wait' &
sleep 60
