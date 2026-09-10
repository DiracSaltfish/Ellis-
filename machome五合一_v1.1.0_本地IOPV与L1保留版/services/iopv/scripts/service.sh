#!/bin/zsh
set -eu
label="com.ellis.intranet-iopv"
domain="gui/$(id -u)"
case "${1:-status}" in
 status) launchctl print "$domain/$label" ;;
 restart) launchctl kill SIGTERM "$domain/$label" ;;
 stop) launchctl bootout "$domain/$label" ;;
 start) launchctl bootstrap "$domain" "$HOME/Library/LaunchAgents/$label.plist" ;;
 *) print 'usage: service.sh status|start|stop|restart'; exit 2 ;;
esac
