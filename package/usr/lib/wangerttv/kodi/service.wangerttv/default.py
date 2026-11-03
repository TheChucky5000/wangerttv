# WangertTV: Befehle per RunScript(service.wangerttv,<befehl>) – leitet an den laufenden Dienst weiter.
# Beispiele: zap,1  zap,-1  info  back  guide  num,5
import sys

import xbmc

SENDER = "service.wangerttv"
args = sys.argv[1:]
cmd = args[0] if args else "info"
if cmd == "zap":
    xbmc.executebuiltin(f"NotifyAll({SENDER},{'wtv_down' if args[1:] and args[1].startswith('-') else 'wtv_up'})")
elif cmd == "num" and args[1:] and args[1].isdigit():
    xbmc.executebuiltin(f"NotifyAll({SENDER},wtv_num{args[1]})")
elif cmd in ("info", "back", "guide"):
    xbmc.executebuiltin(f"NotifyAll({SENDER},wtv_{cmd})")
