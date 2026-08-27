@echo off
cd /d %~dp0
title galaxy-bridge server
python main.py --config config.ini
pause
