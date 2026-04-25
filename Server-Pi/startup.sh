#!/bin/bash
#==================================================================================================
# THIS FILE IS ONLY FOR THE RASPBERRY PI 5
# WHEN MAKING CHANGES AND WANTING TO COMPILE AND RUN SERVER OR STATEMACHINE CODE THROUGH VSCODE ON STATEMACHINE
# RUN $ sudo systemctl stop rover.service
# TO CONTINUE OR REBOOT, RUN $ sudo systemctl restart rover.service
#==================================================================================================

# Define Project DIrectory
PROJECT_DIR="~/Lunabotics/FIU-Luna1/Server-Pi"
cd $PROJECT_DIR

#Pull latest changes
git pull origin main || true

#Recompile and run Go Server in background while logging process
cd Network-Stack
go mod tidy
go build -o server server.go
./server -public -serial-device /dev/ttyACM0 &
PID_GO=$!
cd $PROJECT_DIR

#Recompile and run C State Machine in background while logging process
cd Rover
gcc main.c -o statemachine
./statemachine &
PID_C=$!

wait $PID_C $PID_GO
