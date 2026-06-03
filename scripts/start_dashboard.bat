@echo off
echo React dashboard: http://localhost:8503
nssm start pypoc-react
nssm start pypoc-api
