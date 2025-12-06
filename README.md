# PogoPrint

A lightweight Flask-based web interface to control and monitor your 3D printer over USB. Supports SD card browsing, file upload, printing, and real-time progress tracking.

---

## Features

* Browse and print G-code files from the printer’s SD card.
* Upload new G-code files directly to the printer.
* Start, cancel, and monitor prints in real-time.
* View logs of printer communication.
* Simple Flask web interface accessible over the local network.

---

## Requirements

* Python 3.7+
* Flask
* pySerial
* Linux-based system with USB access to your printer

---

## Setup

1. **Clone the repository**

```bash
git clone <repo-url>
cd pogoprint
```

2. **Set up a virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Configure the serial port**

Edit `pogoPrint.py`:

```python
SERIAL_PORT = "/dev/ttyUSB0"  # Change to your printer's USB port
BAUD = 500000
```

4. **Run the app**

```bash
source venv/bin/activate
python pogoPrint.py
```

The interface will be available at:

```
http://<your-machine-ip>:5000
```

---

## Running as a Service (Legacy SysV init)

If your system doesn’t use `systemd`:

1. Create `/etc/init.d/pogoprint`:

```bash
#!/bin/sh
### BEGIN INIT INFO
# Provides:          pogoprint
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: PogoPrint Flask server
### END INIT INFO

PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
DAEMON=/home/ioakeim/pogoprint/venv/bin/python
DAEMON_OPTS="/home/ioakeim/pogoprint/pogoPrint.py"
NAME=pogoprint
PIDFILE=/var/run/$NAME.pid
LOGFILE=/home/ioakeim/pogoprint/pogoprint.log

case "$1" in
  start)
    echo "Starting $NAME..."
    start-stop-daemon --start --background --pidfile $PIDFILE --make-pidfile --chdir /home/ioakeim/pogoprint --exec $DAEMON -- $DAEMON_OPTS >> $LOGFILE 2>&1
    ;;
  stop)
    echo "Stopping $NAME..."
    start-stop-daemon --stop --pidfile $PIDFILE
    ;;
  status)
    if [ -f $PIDFILE ]; then
        echo "$NAME is running with PID $(cat $PIDFILE)"
    else
        echo "$NAME is not running"
    fi
    ;;
  restart)
    $0 stop
    $0 start
    ;;
  *)
    echo "Usage: $0 {start|stop|status|restart}"
    exit 1
esac
exit 0
```

2. Make it executable:

```bash
sudo chmod +x /etc/init.d/pogoprint
```

3. Add it to startup:

```bash
sudo update-rc.d pogoprint defaults
```

4. Control the service:

```bash
sudo /etc/init.d/pogoprint start
sudo /etc/init.d/pogoprint stop
sudo /etc/init.d/pogoprint status
```

---

## Notes

* The `/logs` page now safely displays the last 1000 log lines.
* Real-time progress is available via the `/progress` endpoint.
* Make sure your user has permission to access the USB device.
