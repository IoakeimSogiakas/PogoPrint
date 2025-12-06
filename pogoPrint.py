from flask import Flask, request, render_template, redirect, jsonify
import serial, time, re, threading
from collections import deque

app = Flask(__name__)

SERIAL_PORT = "/dev/ttyUSB0"  # Change if needed
BAUD = 500000

# Globals / state
serial_conn = None
reader_thread = None
reader_running = threading.Event()

printer_lock = threading.Lock()        # serialize writes
log_lines = []                         # persistent in-memory log for this Python session
MAX_LOG_LINES = 5000
log_lines = deque(maxlen=MAX_LOG_LINES)  # keeps only the most recent 5000 log lines
recent_lines = deque(maxlen=2000)      # recent lines for lookups
sd_cache = {}                          # long_name -> short_name mapping
printing_file = None                   # user-visible filename (long name) or placeholder
printing_progress = {"percent": 0, "status": ""}

# ----- Serial reader -----
def start_serial_reader():
    global serial_conn, reader_thread
    reader_running.set()
    reader_thread = threading.Thread(target=_serial_reader_loop, daemon=True)
    reader_thread.start()

def _append_log(line):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    entry = f"[{ts}] {line}"
    log_lines.append(entry)
    recent_lines.append(entry)

def _serial_reader_loop():
    global serial_conn
    while reader_running.is_set():
        try:
            if serial_conn is None or not getattr(serial_conn, "is_open", False):
                try:
                    serial_conn = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
                    _append_log(f"Serial opened {SERIAL_PORT}@{BAUD}")
                except Exception as e:
                    _append_log(f"Serial open error: {e}")
                    serial_conn = None
                    time.sleep(3)
                    continue

            # read lines continuously
            while reader_running.is_set():
                try:
                    raw = serial_conn.readline()
                except Exception as e:
                    _append_log(f"Serial read error: {e}")
                    try:
                        serial_conn.close()
                    except:
                        pass
                    serial_conn = None
                    break
                if not raw:
                    continue
                try:
                    line = raw.decode(errors="ignore").strip()
                except:
                    line = repr(raw)
                if line:
                    _append_log(f"Received: {line}")
                    _parse_line(line)
        except Exception as e:
            _append_log(f"Reader loop exception: {e}")
            time.sleep(2)

# ----- Parsing lines for progress / M73 / temps -----
def _parse_line(line):
    global printing_progress, printing_file
    # M73 style: "echo: M73 Progress: 15%; Time left: 12m; ..."
    m73 = re.search(r"(?i)M73.*?Progress:\s*(\d+)%", line)
    if m73:
        printing_progress["percent"] = int(m73.group(1))
    # Some firmwares echo "echo: M73 Progress: 15%" or "Progress: 15%"
    m73_alt = re.search(r"(?i)Progress:\s*(\d+)%", line)
    if m73_alt:
        printing_progress["percent"] = int(m73_alt.group(1))

    # Generic Pxx line parsing if present (older handling)
    pmatch = re.search(r"(?i)\bP(\d+)\b", line)
    if pmatch:
        try:
            printing_progress["percent"] = int(pmatch.group(1))
        except:
            pass

    # Temperature line T:xxx /yyy B:zz /aaa ...
    t_match = re.search(r"T:([\d\.\-]+).*?B:([\d\.\-]+)", line)
    if t_match:
        printing_progress["status"] = f"T:{t_match.group(1)} B:{t_match.group(2)}"

    # If we see progress > 0 and no printing_file set, latch onto "unknown" print
    if printing_progress.get("percent", 0) > 0 and not printing_file:
        printing_file = "<in-progress>"
        _append_log("Detected in-progress print (no cached filename). Latching onto progress.")

# ----- Command sending (writes to persistent serial) -----
def send_command(cmd, expect=None, timeout=3):
    """
    Write a command to the serial port and optionally wait until an 'expect' substring
    appears in recent incoming lines. We log the send and rely on the reader to record receives.
    """
    global serial_conn, log_lines
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    _append_log(f"Sending: {cmd}")
    with printer_lock:
        # ensure serial_conn exists
        start_open = time.time()
        while (serial_conn is None or not getattr(serial_conn, "is_open", False)) and time.time() - start_open < 5:
            time.sleep(0.1)
        if serial_conn is None or not getattr(serial_conn, "is_open", False):
            _append_log("Serial not open for write")
            return ""
        try:
            serial_conn.write((cmd + "\r\n").encode())
            serial_conn.flush()
        except Exception as e:
            _append_log(f"Write error: {e}")
            return ""
    # If expect is set, wait for it in recent_lines
    if expect:
        end = time.time() + timeout
        expect_low = expect.lower()
        last_len = len(recent_lines)
        while time.time() < end:
            # check recent lines for expect
            for entry in list(recent_lines)[last_len:]:
                if expect_low in entry.lower():
                    return True
            time.sleep(0.05)
        return False
    return True

# ----- SD listing & caching (long -> short) -----
def list_sd(force_refresh=False):
    global sd_cache
    if sd_cache and not force_refresh:
        return list(sd_cache.keys())
    # Clear cache and request the SD list
    sd_cache.clear()
    ok = send_command("M20 L", expect="End file list", timeout=5)
    # Parse recent lines for the file list (we scan recent_lines for the last "Begin file list" block)
    # Build a list of entries between "Begin file list" and "End file list"
    lines = list(recent_lines)
    # find last occurrence of "Begin file list"
    begin_idx = -1
    end_idx = -1
    for i in range(len(lines)-1, -1, -1):
        if "begin file list" in lines[i].lower():
            begin_idx = i
            break
    for i in range(begin_idx+1 if begin_idx>=0 else 0, len(lines)):
        if "end file list" in lines[i].lower():
            end_idx = i
            break
    block = []
    if begin_idx >= 0 and end_idx > begin_idx:
        # extract raw lines (strip timestamp prefix)
        for item in lines[begin_idx+1:end_idx]:
            # item looks like "[ts] Received: STRATE~1.GCO 5079421 Stratego.gcode"
            # strip the leading "[... ] Received: "
            try:
                raw = item.split("] ", 1)[1]
            except:
                raw = item
            # Remove "Received: " if present
            if raw.lower().startswith("received:"):
                raw = raw.split(":",1)[1].strip()
            block.append(raw)
    else:
        # fallback: parse any recent lines that look like SD file entries
        for item in lines[-200:]:
            raw = item.split("] ",1)[-1]
            if raw.lower().startswith("received:"):
                raw = raw.split(":",1)[1].strip()
            if re.match(r"[A-Z0-9~]+\.GCO", raw, re.IGNORECASE):
                block.append(raw)
    # Now parse block entries like "STRATE~1.GCO 5079421 Stratego.gcode"
    for l in block:
        m = re.match(r"([A-Z0-9~]+\.GCO)\s+\d+\s+(.+\.gcode)", l, re.IGNORECASE)
        if m:
            short_name = m.group(1).strip()
            long_name = m.group(2).strip()
            sd_cache[long_name] = short_name
    return list(sd_cache.keys())

# ----- Flask routes -----
@app.route("/")
def index():
    files = list_sd()
    return render_template("index.html",
                           files=files,
                           printing=(printing_file is not None and printing_progress.get("percent",0) < 100),
                           progress=printing_progress)

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return redirect("/")
    fname = file.filename.replace(" ", "_")
    send_command(f'M28 {fname}', expect="Writing", timeout=5)
    # stream upload (write directly)
    with printer_lock:
        if serial_conn and getattr(serial_conn, "is_open", False):
            serial_conn.write(file.read())
            serial_conn.flush()
    send_command("M29", expect="ok", timeout=5)
    # refresh cache
    list_sd(force_refresh=True)
    return redirect("/")

@app.route("/print")
def start_print():
    global printing_file
    name = request.args.get("name")
    if not name:
        return redirect("/")
    if printing_file:
        return redirect("/")
    # ensure cache is up to date
    if name not in sd_cache:
        list_sd(force_refresh=True)
    short_name = sd_cache.get(name)
    if not short_name:
        _append_log(f"Print requested but short name not found for: {name}")
        return redirect("/")
    # set printing_file for UI
    printing_file = name
    # Send M23 (short name, no quotes) and M24 to start
    send_command(f'M23 {short_name}', expect="ok", timeout=2)
    send_command("M24", expect="ok", timeout=2)
    _append_log(f"Started print: {name} -> {short_name}")
    return redirect("/")

@app.route("/cancel")
def cancel():
    global printing_file, printing_progress
    if printing_file:
        # stronger cancel sequence per request: emergency stop + restart
        send_command("M112")  # emergency stop
        send_command("M999")  # restart after M112
        _append_log("Sent CANCEL (M112/M999)")
    printing_file = None
    printing_progress = {"percent": 0, "status": ""}
    return redirect("/")

@app.route("/logs")
def logs():
    try:
        tail = list(log_lines)[-1000:]
    except Exception as e:
        tail = [f"Error reading logs: {e}"]
    return render_template("logs.html", logs=tail)


@app.route("/refresh_sd")
def refresh_sd():
    list_sd(force_refresh=True)
    return redirect("/")

@app.route("/progress")
def progress():
    # return latest progress
    return jsonify(printing_progress)

# start background reader on module import
start_serial_reader()

if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000)
    finally:
        reader_running.clear()
        try:
            if serial_conn:
                serial_conn.close()
        except:
            pass
