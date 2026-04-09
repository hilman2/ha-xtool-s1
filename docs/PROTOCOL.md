# xTool S1 Protocol Reference

A living document of everything we've reverse-engineered about the
xTool S1's network surface. The original BassXT writeup
([BassXT/xtool#23](https://github.com/BassXT/xtool/pull/23)) covered
~6 M-codes; this file documents the **HTTP REST gateway**, the
**UDP discovery beacon**, and **30+ additional M-codes** that we
discovered ourselves on 2026-04-08 / 2026-04-09 against hilman2's
device by direct probing and Wireshark captures of the XCS app.

> Status codes: ✅ verified live, 🟡 strong guess, ❓ unknown.

---

## 1. Network surface

| Port | Proto | Purpose |
|---|---|---|
| **8080** | TCP / HTTP | command gateway, info reads, **file server** (`/system`, `/cmd`, `/upload`, `/gcode/`) |
| **8081** | TCP / WebSocket | live state push frames + command stream |
| **20000** | UDP | JSON discovery beacon |
| any other tested | - | closed (80, 443, 8000, 8082, 8329, 8780, 9100, 20001, …) |

The S1 has **no authentication** on any of these. The XCS app uses
the WebSocket exclusively for both reads and writes - but the HTTP
gateway is a viable side-channel that the app ignores.

### 1.1 Cross-model API comparison ✅ (web research 2026-04-09)

xTool devices use **four API generations**. The S1 is its own hybrid:

| Generation | Models | HTTP API | WebSocket | Source |
|---|---|---|---|---|
| Gen 1 | D1, D1 Pro | Rich REST: `/ping`, `/progress`, `/peripherystatus`, `/list`, `/cnc/data?action=pause\|resume\|stop` | Simple text events (`ok:IDLE`, `err:flameCheck`) | 1RandomDev/xTool-Connect |
| Gen 2 | P2, F1, M1, M1 Ultra | JSON REST: `/device/runningStatus`, `/peripheral/gap\|smoking_fan\|ext_purifier\|machine_lock` | Not used for status | BassXT/xtool |
| Gen 3 | M1 (older FW) | `/cnc/status`, `/cnc/cmd`, camera on port 8329 | Not used | fritzw/xtm1_toolkit |
| **S1** | **S1** | **Minimal**: only `/system` (3 actions), `/cmd`, `/upload`, `/gcode/` file server. **No** `/ping`, `/progress`, `/peripheral/*`, `/device/*`, `/cnc/*`, camera. | **Full M-code protocol** - all state via WS push frames | This document |

**Verified 2026-04-09**: Every D1/P2/F1/M1 endpoint was tested
against hilman2's S1 - all return 404. The S1 has the smallest
HTTP surface of any xTool device but the richest WebSocket protocol.

Key differences from other models:
- **No `/peripherystatus`** - lid, flame, tilt sensors are only
  available via WS (`M53`, `M25`, `M340`)
- **No `/progress`** - job progress only via WS (`M7`)
- **No `/cnc/data?action=pause/resume/stop`** - must use WS
  M-codes (`M108` for stop; pause/resume triggers not yet isolated)
- **`M13` scale**: S1 uses `A<0-100> B<0-100>`, D1/M1 uses `S<0-255>`
- **Upload format**: S1 = raw text POST, D1 = multipart form, M1 = zipped body
- **No camera port** (8329) - despite the S1 having a camera, it's
  not network-accessible

---

## 2. UDP discovery (port 20000) ✅

### Request

```json
{"requestId": <int>}
```

Sent unicast or LAN broadcast (`255.255.255.255` works from a host
on the same L2 segment; WSL2 NAT does NOT work).

### Reply

```json
{
  "requestId": <echoed int>,
  "ip":        "192.168.x.y",
  "name":      "<device name or empty>",
  "version":   "V40.32.013.2224.01"
}
```

The reply is tab-indented JSON. `version` matches `M2099` from the
M2003 snapshot (sub-firmware, probably the laser-module one).
`name` is empty by default - the XCS app appears to set it via the
xTool cloud, the local API has no working setter.

---

## 3. HTTP REST gateway (port 8080) ✅

The S1 runs a tiny HTTP server. It's **fire-and-forget**: command
responses arrive on the WebSocket, not in HTTP. The HTTP layer is
mainly useful as:

1. A write path that **survives** XCS-app activity (the app kicks
   the WebSocket but ignores HTTP).
2. A heartbeat we can use as a liveness check.

### Verified endpoints

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/system?action=mac` | device MAC address | plain text + `\n` |
| GET | `/system?action=version` | sub-firmware version | same value as `M2099`, plain text |
| GET | `/system?action=get_dev_name` | device name | always empty body - no working setter |
| GET | `/cmd?cmd=<MCODE>` | single-command queue | echoes the cmd back, command goes to WS layer |
| POST | `/cmd` | multi-line command queue | body is `\n`-separated M-codes; replies `{"result":"ok"}` |

### Things that DON'T exist on the S1

Exhaustively tested 2026-04-09 (200+ paths, all 4 xTool API
generations, 60+ ports):

- **D1-style**: `/ping`, `/progress`, `/peripherystatus`, `/getmachinetype`,
  `/getlaserpowertype`, `/getlaserpowerinfo`, `/getmachineconfig`,
  `/list`, `/read`, `/framing`, `/updater`, `/upgrade`,
  `/cnc/data?action=pause/resume/stop`, `/cnc/status`, `/cnc/cmd`
- **P2/F1/M1-style**: `/device/runningStatus`, `/device/machineInfo`,
  `/device/workingInfo`, `/peripheral/gap`, `/peripheral/smoking_fan`,
  `/peripheral/ext_purifier`, `/peripheral/machine_lock`,
  `/peripheral/airassist`, `/peripheral/drawer`, `/config/get`,
  `/status`, `/processing/stop`
- **M1-camera**: port 8329 closed, `/snap`, `/camera`,
  `/file?action=download`
- **Generic**: `/api/...`, `/v1/...`, `/v2/...`, WebSocket upgrade on
  8080, any REST path not listed in the verified section
- **Other ports**: 8329, 8780, 20001 all closed. Only 8080+8081 TCP
  and 20000 UDP are open.
- **HTTP verbs**: only GET on `/system`, GET+POST on `/cmd`,
  POST on `/upload` and `/delete/`. All other verbs → 405.
- **`/system?action=` setters**: `set_dev_name`, `set_*`,
  `get_working_sta`, `offset`, `dotMode` - all hang (timeout, no
  response). Only `mac*`, `version*`, `get_dev_name*` work.

### `/cmd` is pure passthrough

GET `/cmd?cmd=HELLO` returns the literal string `HELLO`. There's
zero validation - the gateway just dispatches the string to the
internal M-code handler.

### 3.1 `POST /upload` - job-file upload

Found 2026-04-09 by sniffing the XCS desktop app traffic. **This is
the channel the app uses to send a job to the laser.**

```http
POST /upload?taskId=<UUID>&filename=tmp.gcode HTTP/1.1
Host: 192.168.32.55:8080
Content-Type: text/plain
Content-Length: <body length>

<gcode body>
```

Response:
```http
HTTP/1.1 200 OK
Content-Type: text/html
Content-Length: 16
Access-Control-Allow-Origin: *

{"result":"ok"}
```

**Properties**:
- **`taskId`** is a client-generated UUID - the same value will later
  appear in the WS push as `M810 "<uuid>"` once the job is started.
- **`filename`** appears to always be `"tmp.gcode"` - single-slot,
  no file manager.
- **Content-Type is `text/plain`**, NOT multipart/form-data. The
  body is the raw Gcode file as a string.
- **No authentication.** Same as everything else on this device.
- The upload **does not start the job** - it only stages it on the
  SD card as `tmp.gcode`. Starting requires the WebSocket sequence
  described in §3.3 below, plus a physical button press.

**Implication**: HA can upload arbitrary jobs to the S1 by
constructing a Gcode body and POSTing it, then trigger the start
sequence. The hardware safety lock still applies - the user must
press the physical Start button on the device - but the entire
workflow (upload + prepare + trigger) works without XCS, enabling
batch operations from a phone.

### 3.3 Remote job start (verified 2026-04-09)

**Full sequence to start a job without XCS**, verified live against
hilman2's S1. The start commands MUST go through the **WebSocket**
on port 8081 - sending them via HTTP `POST /cmd` has no effect.

```
1. POST /upload?taskId=<UUID>&filename=tmp.gcode   (HTTP, stages file)
2. WS: M322 S1   → M322 R0      (switch SD card to ESP32 read mode)
3. WS: M330 S0   → M330 S0      (ack, purpose unclear)
4. WS: M323 S1   → M323 OK      (arm the start - device waits for button)
5. WS: M323 S1                   (second trigger, timing aid)
6. [User presses physical Start button on device]
7.                → M222 S13     (state → Starting)
8.                → M222 S14     (state → Running)
```

**Key findings**:
- Steps 2–5 do **nothing** when sent via HTTP `/cmd` - they are
  silently accepted but the device ignores them. WebSocket only.
- The physical button press is **mandatory** - the firmware will not
  fire the laser without it. This is a safety feature, not a bug.
- Step 5 (second `M323 S1`) is not strictly required but was present
  in the XCS app's captured sequence. Without it, the device still
  waits for the button; with it, the timing is smoother.
- To **re-run the same job**, skip step 1 (the file is already on
  the SD card) and send steps 2–5 again. This is the "repeat last
  job" use case for batch work.
- `GET /gcode/` lists the SD card contents. `GET /gcode/tmp.gcode`
  downloads the current job file. Files persist across reboots.

### 3.4 SD card file server

The HTTP gateway exposes the laser's SD card at `/gcode/`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/gcode/` | HTML directory listing |
| `GET` | `/gcode/tmp.gcode` | Download last job |
| `GET` | `/gcode/frame.gcode` | Download last frame-run |
| `GET` | `/gcode/logs.txt` | Firmware debug log (~70k lines) |
| `POST` | `/upload?filename=<name>` | Upload a file (max 2 GB) |

The `logs.txt` file contains hardware-level debug output including
lifetime counters (see §7.1), homing sequences, cover sensor events,
and M-code dispatch traces. Not available while a job is running
(SD card busy).

### 3.5 Lifetime counters from logs.txt

The firmware periodically writes accumulated statistics to `logs.txt`:

```
acc_worktime:151566;              # M2008.A - total working seconds
acc_workcount:228;                # M2008.B - number of job starts
acc_sys_runtime:1293589;          # M2008.C - total standby seconds
acc_2w_laserworktime:880;         # per-tool: 2 W IR head
acc_default_laserworktime:0;
acc_10w_laserworktime:0;
acc_20w_laserworktime:0;
acc_40w_laserworktime:3086;       # per-tool: 40 W diode ← M2008.D
```

This confirms M2008.D = accumulated working seconds of the **currently
installed tool type** (per-wattage counter). The firmware stores
separate counters for each laser wattage class. All counters are
persistent across reboots.

### 3.2 Gcode body format

The body the XCS app sends has this structure (verified from a
1%-power vector cut on a 40 W diode head):

```gcode
# date=2026_04_09_02_39_37
# version=1.6.8
# algorithmVersion=1.6.8
# gc={"size":{"w":498,"h":330}}
# gc={"offset":{"x":0,"y":0}}
# gc={"start":{"x":0,"y":0.0000}}
# gc={"keys":["x","y"],"rm":1,"is3DMode":false}
# timeConfig={"vectorAccel":500,"bitmapAccel":700,"thresholdAngle":1}
G90                                  # absolute positioning (RepRap)
G0 F3000                             # set initial feed
# D2 HEAD                            # tool block start ("D2" = ?)
M110 X1 Y1 Z1                        # axes-referenced flag (also seen
                                     # as a push during probe sequences)
M109 S1                              # NEW - RepRap M109 = wait-for-temp;
                                     # here probably "set mode 1"
M223 X498 Y330                       # NEW - workspace bounds in mm.
                                     # (S1 advertised 498×320; the gcode
                                     # uses 498×330 - possibly extended)
M7 S1                                # already-known field, used here as
                                     # a write command
M96 S0                               # NEW - config flag, unknown
G90
G0Z0                                 # park Z
G92 U0                               # set U axis offset to 0
M32 X3200                            # NEW - speed cap? (3200 mm/min)
G0X319.15Y242.47                     # move to start position
G0 F9600
M9064 B3                             # NEW - air-assist control? B3=on
G198 P78 "M9064 B3"                  # G198 wraps a quoted command
                                     # with a "priority" (P78). Looks
                                     # like a structured execute-with-ack
                                     # mechanism.
M9039 C2                             # 🔥 AP2 air-cleaner WRITE - C2 = speed 2
                                     # (we previously only saw M9039 as
                                     # a push frame)
G198 P76 "M9039 V50"                 # AP2 with V50 (level/voltage?)
G198 P76 "M9043 H1 I1 J1 K194 L1 M1" # NEW - looks like AP2 filter
                                     # parameters; H/I/J/K/L match the
                                     # filter slots we saw in M9039 push
G1 F1000

# VECTOR START
G90
M4                                   # RepRap M4 = spindle CCW;
                                     # here probably "laser fire mode"
M15 S1                               # power-state on
# blockConfig={"powerFactor": 0.01, "isVector": true}
                                     #            ↑ 0.01 = 1% power
G0X119.362Y105.187                   # rapid to cut start
G0Z49.643                            # Z move to cut height for THIS job
                                     # - comes from a fresh probe
                                     # measurement done for this job, NOT
                                     # the same M313 reading from §5.5.
                                     # Just shows that cut-height values
                                     # land in the body as plain G0Z..
G0X119.362Y105.187
G1X309.656Y105.187 S10 F1680         # 🔥 actual cut line:
                                     #   S10  = laser power, scale 0-1000
                                     #          (S10 / 1000 = 1% - matches
                                     #          the powerFactor above ✓)
                                     #   F1680 = feed mm/min = 28 mm/s
G1X309.656Y186.855                   # cut continues
G1X119.362Y186.855
G1X119.362Y105.187
G90
G0X319.15Y242.47                     # return to safe position
G0 U0
# END
G0Z0                                 # park Z
M9064 B0                             # air-assist off
G198 P78 "M9064 B0"
M9039 C0                             # AP2 off
G198 P76 "M9039 V0"
# END
M6                                   # RepRap M6 = tool change;
                                     # here probably "end of program"
# D2 TAIL                            # tool block end
```

**Confirmed encodings**:
- **Power**: `S` parameter on `G1` lines, scale **0-1000** (Marlin
  laser-mode standard). `S10` = 1%.
- **Feed**: `F` parameter, units **mm/min**. `F1680` = 28 mm/s.
- **Coordinates**: mm, absolute (G90), 3 decimal precision.
- **Job UUID**: matches the `taskId` from the upload URL and the
  later `M810 "<uuid>"` push.

**XCS Studio User-Agent** (for context):
```
xToolStudio/1.6.8 Chrome/136.0.7103.49 Electron/36.2.0 Safari/537.36
```

XCS is an **Electron app** - that explains some of the WebSocket
quirks (Electron's ws implementation isn't always 100% standard).

### 3.3 File Server (`/gcode/`) ✅ - discovered 2026-04-09

The S1 exposes a **full HTTP file server** built on ESP-IDF's
`file_server.c` example. The storage backend is a **microSD card**
(evidenced by a `System Volume Information/` directory created by
Windows when the card was formatted).

#### Endpoints

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/gcode/` | **Directory listing** (HTML page titled "S1 File Server") | Includes upload form + file table with delete buttons |
| GET | `/gcode/<filename>` | **File download** | Returns raw file content |
| POST | `/upload?filename=<name>` | **File upload** | `taskId` is optional; body is raw file content; max 2 GB |
| POST | `/delete/gcode/<filename>` | **File deletion** | Returns success/failure |

#### Observed files on hilman2's device (2026-04-09)

| Name | Type | Size | Purpose |
|---|---|---|---|
| `System Volume Information/` | dir | - | SD card metadata (Windows-formatted) |
| **`logs.txt`** | file | **3.2 MB** | **Firmware debug log - GOLDMINE** (see §10) |
| `update/` | dir | - | Firmware update staging area |
| `test.gcode` | file | 0 | Empty test file |
| `frame.gcode` | file | 535 | Last frame-preview G-code |
| `tmp.gcode` | file | 842 | Last uploaded job G-code |

#### Key properties
- **Files uploaded via `/upload` are readable back via `/gcode/`** -
  the upload target directory IS the file-server root.
- **`filename` parameter is required** - POST without it returns 500
  "Failed to create file".
- **No subdirectory creation** - `filename=subdir/test.txt` returns
  "Failed to create file".
- **Path traversal blocked** - `filename=../escape.txt` fails.
- **Case-sensitive paths** - `/Gcode/` and `/GCODE/` return 404.
- **`/gcode` (no trailing slash) → 404** - only `/gcode/` works.
- **`/gcode/index.html` → 307 redirect** to `/gcode/`.
- **`logs.txt` grows in real-time** - the firmware appends to it
  continuously. Readable while the device is running.

#### Upload form JavaScript
The HTML page includes a browser-based upload form with this
structure:
```javascript
upload_path = "/upload?filename=" + filePath;
// Max size: 2024*1024*1024 (≈2 GB)
// POST with raw file body (not multipart)
// No spaces allowed in filename
```

#### Implication for the integration
- **`GET /gcode/logs.txt`** = synchronous HTTP read of runtime stats
  (see §10 for what's in the logs). This works even when the
  WebSocket is blocked by XCS - it's a pure HTTP read on the
  file server, not the `/cmd` fire-and-forget path.
- **`GET /gcode/`** can serve as a storage-health check.
- **Job management** is feasible: upload via POST, verify via GET,
  clean up via POST `/delete/`.

### 3.4 `/system?action=` implementation details ✅

Tested 200+ action names on 2026-04-09. The endpoint uses **prefix
matching** with exactly three handlers:

| Prefix | Response | Example |
|---|---|---|
| `mac*` | MAC address (`30:30:f9:71:7a:f4\n`) | `mac`, `machine_info`, `macblabla` |
| `version*` | Sub-firmware version | `version`, `versioninfo`, `version123` |
| `get_dev_name*` | Device name (`xTool S1`) | `get_dev_name`, `get_dev_namex` |

- **Unknown actions cause the server to hang** (no response, curl
  timeout) - NOT a 404. This is important: don't poll with random
  action names, it will tie up the HTTP server.
- **Only GET is supported** - POST/PUT/DELETE/OPTIONS/HEAD all
  return 405.
- **`get_dev_name` now returns `xTool S1`** (was empty on
  2026-04-08). Likely set by the XCS app during a recent session.

---

## 4. WebSocket (port 8081) - base protocol

- Plain WebSocket, no authentication, no Origin check
- Multiple parallel client connections are accepted
- Server sends both push frames (state changes) and command replies
- Most frames are TEXT; a few (notably `M9039`) are BINARY with a
  small non-printable header/footer wrapping a printable M-code body
- The XCS app **kicks other clients** by some mechanism we haven't
  isolated yet - possibly a specific M-code, possibly an HTTP-side
  command. Reconnecting with a new socket is allowed.

---

## 5. M-code reference

### Status legend

- ✅ verified - observed in our test traffic, semantics confirmed
- 🟡 plausible - observed but semantics are educated guesses
- ❓ unknown - observed in capture but no clue what it means

### 5.1 Reads we already use in the integration

| M-code | Source | Direction | Meaning | Status |
|---|---|---|---|---|
| `M2003` | M2003 JSON | request → JSON push | full status snapshot, see §5.6 | ✅ |
| `M303` | push / ping | request → push | position refresh `X<f> Y<f>` | ✅ |
| `M222` | push | push | work-state code (`S<n>`), see §5.7 | ✅ |
| `M810` | push | push | current job filename, `"NULL"` if idle | ✅ |
| `M340` | push | push | alarm code, `A0` = no alarm | ✅ |
| `M313` | push | push | last Z-probe reading: `Z<f>` mm - measures from probe tip, ~2 mm offset to laser | ✅ |
| `M99`  | M2003 field | snapshot field | main firmware version | ✅ |
| `M310` | M2003 field | snapshot field | serial number | ✅ |
| `M27`  | M2003 field | snapshot field | head position `X<f> Y<f> Z<f> U<f>` - Z is 0 at idle / parked, climbs to the cut height during a job (e.g. 27.338 mm) | ✅ |
| `M105` | M2003 field | snapshot field | temperatures `X<f>Y<f>Z<f>` (no spaces!) - values are 0.00 at idle | ✅ |
| `M13`  | M2003 + write | both | **fill-light brightness 0-100** (`A<n> B<n>`, A=B always); writeable via `M13 A<n> B<n>` | ✅ |
| `M100` | M2003 field | snapshot field | model name string (`"xTool S1"`) | ✅ |
| `M1199` | M2003 field | snapshot field | sub-firmware 1 (mainboard?) | ✅ |
| `M2099` | M2003 field | snapshot field | sub-firmware 2 (laser module?) - equals `/system?action=version` | ✅ |
| `M1098` | M2003 field | snapshot field | 10-slot tool firmware array | ✅ |
| `M54`  | M2003 field | snapshot field | tool type code (`T1`) | ✅ |
| `M9039` | push (binary) | push | AP2 air cleaner status (deferred to v2) | ✅ |

### 5.2 Newly captured in M2003 (untouched by integration so far)

These appear in the full M2003 JSON snapshot but the integration
doesn't surface them yet:

| M-code | Example value | Guess |
|---|---|---|
| `M97` | `"S0"` | always `S0` in our captures - secondary state? ❓ |
| `M345` | `"S1"` | door-lock state? ❓ |
| `M321` | `"S0"` | ❓ |
| `M120` | `"A1.2"` | ❓ |
| `M116` | `"X0Y40B1P1L2"` (40W Diode) / `"X1Y2B0P0L0"` (2W IR) | **🔥 TOOL CAPABILITY BITMAP** - `Y` field = wattage in W (verified). Other flags: `X`/`B`/`P`/`L` = capability bits (probably air-assist support, probe support, laser class). See §5.5g | ✅ |
| `M98`  | `"X0.32 Y25.88"` | workpiece origin / calibration offset? 🟡 |

### 5.3 Newly captured via XCS init burst (2026-04-09)

The XCS app issues a 17-code burst right after the WebSocket handshake.
We saw the responses in a Wireshark `Follow → TCP Stream`.

| M-code | Sample response | Direction | Guess | Status |
|---|---|---|---|---|
| `M2002` | `"192.168.32.55"` | reply | **device IP as string** | ✅ |
| `M318` | `N1` | reply | notification count? network mode? | ❓ |
| `M366` | `X507.00 Y0.00` | reply | **workspace bounds** (S1 has ~498×320 mm bed) | 🟡 |
| `M1109` | `A0.324 B25.882 C2.322 D23.562 E58.000` | reply | **NOT the exhaust fan** - 2026-04-09 disproved: fans were OFF during this reading. Possibly mainboard temps + a fixed config value. Real meaning still unknown | ❓ |
| `M1099` | `T10` | reply | tool slot? total runtime hours? | ❓ |
| `M1113` | `X21.201 Y13.563` | reply | material origin offset? | ❓ |
| `M15` | `A1 S0` | reply | power state? A=power-on, S=standby? | 🟡 |
| `M25` | `X0,Y1,Z0,T0,B0` | reply | **5-flag I/O bitmap** - limit-switches / endstops / lid sensor / button? Strong candidate for safety binary_sensors | 🟡 |
| `M21` | `S1` | reply | SD card mounted? (RepRap convention) | ❓ |
| `M7`  | `S0 N0 D10` | reply | job progress? S=state, N=line, D=duration? | 🟡 |
| `M2008` | `A151484 B219 C1263671 D3004` | reply | **lifetime statistics counters** - verified against XCS app screenshot (see §5.4) | ✅ |
| `M9009` | `S-1` | reply | accessory status - `S-1` = no AP2 attached | 🟡 |

### 5.4 `M2008` - lifetime counters ✅ VERIFIED

Verified by comparing the M2008 reply against the XCS app's
"Statistik" screen on hilman2's device on 2026-04-09:

```
M2008 A151484 B219 C1263671 D3004
       │       │      │        │
       │       │      │        └─ unknown - successful jobs? lines processed?
       │       │      └─────────  Standby-Zeit  →  1263671 / 3600 = 351.02 h ✅ exact match
       │       └────────────────  Betriebszeiten →  219                      ✅ exact match
       └────────────────────────  Arbeitszeit    →  151484 / 3600 = 42.08 h  ✅ exact match
```

Perfect 1:1 mapping. We can build sensors from this with full confidence.

### 5.5 Captured during a Z-probe sequence (2026-04-09)

The capture shows the full lifecycle of a "Measure" action in the
XCS app. The trigger M-code (what the app SENDS to start the
measurement) is not yet isolated - we only have the responses:

| M-code | Sample value | Role in the sequence | Status |
|---|---|---|---|
| `M108` | `ok` | acknowledgement / ready signal | 🟡 |
| `M222 S18` | (state push) | **NEW STATE: preparing for measurement** - appears just before S10 | 🟡 |
| `M362 S1` | (push) | measurement-phase indicator | ❓ |
| `M312 S1` / `S0` | (push) | measurement-mode flag, on at start, off at end | ❓ |
| `M311 S0` / `S2` / `R0` / `R2` | (push) | probe sub-phase - S0 approach, S2 measured, R fields = result phase | ❓ |
| `M22 S0` | (push) | unknown; appears multiple times during the sequence | ❓ |
| `M110 X1 Y1 Z1` | (push) | **3-axis referenced flag** - fired after the measurement; X/Y/Z each 0/1 | 🟡 |
| `M313 Z46.996` | (push) | **the Z-probe result** - measured 47 mm vs 49 mm app display (~2 mm offset because the probe tip hangs ~2 mm below the laser nozzle, confirmed by hilman2) | ✅ |

### 5.5b Captured during a Frame-Preview run (2026-04-09)

A second capture during a "Frame Preview" / "Rahmenfahrt" action.
The frame preview is **single-shot** - there is no pause or stop,
the head traces the workpiece outline once and returns. Still no
isolated app→laser trigger yet.

| M-code | Sample value | Role in the sequence | Status |
|---|---|---|---|
| `M53` | `A0` | (push, just before movement starts) | ❓ |
| `M206` | `ok` | (push) acknowledgement (RepRap M206 = home offset; here probably an ack) | 🟡 |
| `M321` | `S0` | (push) - same field as in M2003, here as a separate push | ❓ |
| `M322` | `R0` | (push, before frame run) | ❓ |
| `M323` | `OK` | (push) **multi-stage Start acknowledgement** - see safety note below | ✅ |
| `M330` | `S0` | (push, before frame run) | ❓ |
| `M2008` | `A151484 B219 C1266188 D3004` | (push) | ✅ **C grew by 2517 seconds (= 42 min standby) since the previous capture - confirms C is the standby-time counter in seconds** |

**`M25` flip observed!** Between the idle and frame-prep capture, the
M25 reading went from `X0,Y1,Z0,T0,B0` to `X1,Y1,Z0,T0,B0`. The first
flag (`X`) flipped from 0 to 1. This is the strongest evidence so far
that **M25 is a 5-flag I/O bitmap** for limit-switches / safety states.
Worth a follow-up: open the lid in idle and re-read M25, then close
the lid and re-read - whichever flag flips is the lid sensor.

### 5.5c New work-state codes from frame-preview

| Code | Observed in | Best-guess meaning | Status |
|---|---|---|---|
| `S11` | M2003 snapshot during frame run | **Frame run / job active** - appears for most of the duration, also after the head returns to its original position | 🟡 |
| `S12` | brief push during the actual head movement | **Executing motion** - appears between S11 and the start of the M303 position stream | 🟡 |

### 5.5d Captured during a real job start + pause + resume (2026-04-09)

A third capture: hilman2 loaded a real job file, started it, paused
it, resumed it, and let it finish. The capture is gold - it gives us
a job-parameters frame, a job-submission ack, the **paused state**,
and the full S-code lifecycle of a job.

#### Job-start frame burst (in order)

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M362 S1` | (push, ×4) | pre-job indicator | ❓ |
| `M321 S0` | (push) | state field also seen in M2003 | ❓ |
| **`M2240`** | `A0.500000 B0.800000 C50 D50 M300.0 P0.6 I0` | **NOT per-job power/speed** - verified 2026-04-09: identical bytes for a 1%/10 mm/s job AND a 100%/100 mm/s job. Most likely material defaults / global cut profile. The real per-job power/speed are in the binary upload (see §6.0c). | 🟡 |
| `M322 R0` | (push) | ❓ |
| **`M810 "<uuid>"`** | `"4b0ee700-7a94-435e-9147-ad635769f6e6"` | **the job file is now a UUID instead of `"NULL"`** - confirms M810 carries the active job filename | ✅ |
| `M330 S0` | (push) | ❓ |
| **`M2810 ok`** | (push) | **job submission ack** - fires after the file name is set, just before S13 | 🟡 |
| `M323 OK` | (push) | acknowledgement | 🟡 |
| `M222 S13` | (push) | **Starting** - begins the active state machine | ✅ |
| `M53 A0` | (push) | unknown phase indicator | ❓ |
| `M810 "<uuid>"` (re-pushed) | (push) | filename re-confirmation | |
| `M222 S14` | (push) | **Running** - head starts moving | ✅ |
| `M15 A1 S1` | (push) | power state - `S` flipped from `0` to `1` when running starts | 🟡 |

#### Job-pause and resume sequence

Once `M222 S14` (running) is up and the head is moving, the user
clicks Pause in the XCS app. Observed:

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M22 S1` | (push) | **`M22` becomes the pause-indicator** - was `S0` during running, flips to `S1` on pause | 🟡 |
| **`M222 S15`** | (push) | **NEW STATE: PAUSED** - fires immediately after `M22 S1`, head returns to its origin position and stays | 🟡 |
| `M15 A1 S0` | (push) | power-state `S` flips back to `0` while paused | 🟡 |
| `M2003{…}` | (push, several seconds later) | confirms `"M222":"S15"` and `"M27":"X286.510 Y151.060 Z27.678…"` (Z != 0 - head is at the cut depth) | ✅ |

The user then clicks Resume:

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M53 A0` | (push) | unknown - also fires on resume | ❓ |
| `M222 S14` | (push) | back to **Running** | ✅ |
| `M22 S2` | (push) | **`M22 S2`** - third value of M22, possibly "resuming" or "running after pause" | ❓ |
| `M15 A1 S1` | (push) | power-state `S` back to `1` | 🟡 |

After the job finishes:

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M222 S19` | (push) | **Finishing** | ✅ |
| `M22 S0` | (push) | back to S0 (idle indicator) | 🟡 |
| `M222 S1` | (push) | Ready | ✅ |
| `M222 S3` | (push) | Idle | ✅ |
| `M15 A1 S0` | (push) | back to S0 | 🟡 |

#### Putting M22 together

Across all captures, `M22 S<n>` correlates with the job lifecycle:

| Value | Observed when | Meaning (best guess) |
|---|---|---|
| `S0` | idle, finishing, post-job | **Idle / not running** |
| `S1` | running and pause | **Job in progress** (run or pause) |
| `S2` | only on resume | **Resuming from pause** (transient) |

This makes `M22` a useful **secondary status indicator** alongside `M222`.

#### What we still don't have for pause/resume

**The pause-trigger M-code** (the one the app SENDS to the laser when
the user clicks Pause) is still not isolated - the dump above mixes
both directions and is dominated by server pushes. To find it, we'd
need to repeat this capture but with the Wireshark `Follow → TCP
Stream` direction filter set to **App → Laser only**, and look for an
otherwise-unknown short M-code right before `M22 S1` / `M222 S15`.

### 5.5g Tool change diff (2026-04-09)

hilman2 swapped a 40 W diode head for a 2 W infrared module. Five
fields change deterministically - these are the **tool-identification
fingerprint**.

| Field | 40 W Diode | 2 W IR | Interpretation |
|---|---|---|---|
| **`M1199`** | `V40.32.009.2122.01 B1` | `V40.32.008.2122.01 B3` | Tool firmware version - **eindeutiger Fingerprint** für Lookup-Table |
| **`M116`** | `X0Y40B1P1L2` | `X1Y2B0P0L0` | Capability bitmap: **`Y` = wattage in W** (40 vs 2 - verified). Other flags = capability bits |
| **`M98`** | `X0.32 Y25.88` | `X-0.88 Y20.20` | Tool mounting offset (X, Y) in mm - every tool has a slightly different mechanical offset |
| **`M1109`** | `A0.324 B25.882 C2.322 D23.562 E58.000` | `A-0.879 B20.202 C0.484 D18.999 E58.000` | Higher-precision tool offset (A,B = same as M98 in 3 decimals; C,D = secondary offset; E58.000 = constant Hardware-Maß) |
| **`M2008.D`** | 3035 | 880 | **Per-tool working-time counter in seconds** - switches with the installed tool, persists per tool independently |

#### Recommended sensor strategy

Two independent identification methods:

1. **`M1199` string → Tool name lookup table**
   ```python
   TOOL_FIRMWARE_MAP = {
       "V40.32.009.2122.01 B1": "Diode 40W",
       "V40.32.008.2122.01 B3": "IR 2W",
       # populate as more tool variants are tested
   }
   ```
2. **`M116.Y` → wattage as int** (always available, even for unknown tools)

Plus diagnostic sensors:
- `M2008.D` → tool runtime in seconds (per tool) - convertible to hours
- `M98.X/Y` → tool mounting offset
- `M116` raw → full capability string for advanced users

**Fields that did NOT change** at the tool swap (so they are NOT
tool-specific despite their position in the snapshot):
- `M54` (still `T1`) - was previously assumed to be the tool-type, **disproved**
- `M1098` array - still has `V40.208.002.3D28.01 B1` in slot 2 - probably accessory-slot, not tool
- `M2099` (still `V40.32.013.2224.01 B1`) - sub-firmware of the laser-module mainboard, not the tool head
- `M99` (main firmware) - obviously stays
- `M120`, `M321`, `M345`, `M97`, `M100`, `M310` - device identity / config, all stable

### 5.5h Wireshark-decoded App→Laser stream (2026-04-09 late session)

After enabling `Decode As → WebSocket` and a `websocket.payload.text`
column, we finally see the **unmasked** client→server frames in
plaintext. That nailed three things at once:

#### 🎯 STOP-Trigger isolated: `M108`

```
[App polls M303 repeatedly]
M303    ← App: position read
M303
M108    ← App: STOP ACTION
M303
            ↓ Server reacts:
            M108 ok
            M222 S18 → S1 → S3
            M15 A1 S0
            M22 S1   (sticky abnormal-finish marker)
```

Verified by hilman2 against a live job 2026-04-09 night session.

#### 🎯 Read/Write pattern decoded

This is the structural insight that explains the whole protocol:

- **`M-code` (no arguments)** = **READ request** - the server answers
  with a push frame containing the current value, in the form
  `M-code <value>`.
- **`M-code <args>`** = **WRITE / ACTION** - the server executes the
  command and pushes the resulting state delta.

The init burst we kept seeing as a "server push storm" is actually
**14 read requests** sent by the app and **14 push responses** sent
back by the server. Examples:

| App sends (no args) | Server answers (push) |
|---|---|
| `M2240` | `M2240 A0.500000 B0.800000 C50 D50 M300.0 P0.6 I0` |
| `M322 S1` | `M322 R0` |
| `M810` | `M810 "<uuid>"` |
| `M323 S1` | `M323 OK` |
| `M303` | `M303 X<f> Y<f>` |
| `M2003` | `M2003{...JSON...}` |
| `M2002` | `M2002 "192.168.x.y"` |
| `M99` | `M99 V40.32.015...` |
| `M340` | `M340 A0` |
| `M2008` | `M2008 A.. B.. C.. D..` |
| `M9009` | `M9009 S-1` |
| `M1109` | `M1109 A.. B.. C.. D.. E..` |
| `M366` | `M366 X.. Y..` |
| `M1099` | `M1099 T..` |
| `M1113` | `M1113 X.. Y..` |
| `M25` | `M25 X..,Y..,Z..,T..,B..` |
| `M21` | `M21 S..` |
| `M7` | `M7 S.. N.. D..` |

**This means we can build a complete read API on top of the WebSocket
without needing M2003 at all** - just send the read request for the
field we want, the server pushes back the current value. Useful when
we only want a single field refreshed.

#### 🎯 Why M303 polls kill foreign WebSockets

The app sends `M303` (read request) **once per second** while it's
open. Each M303 read causes the server to push `M303 X<f> Y<f>` to
**all connected clients**. The constant traffic combined with our
foreign WS connection seems to push the firmware over a buffer or
fairness limit and our connection gets dropped.

**Confirmed empirical conclusion**: Desktop XCS app open ⇒ our
WebSocket gets kicked every few seconds, regardless of what we do.
The architecture has to handle this with the Coexist mode in §9.

#### What's still NOT in this capture

- ~~**Pause / Resume triggers**~~ **VERIFIED 2026-04-09**: Pause is
  `M22 S1` (App→Laser), Resume is `M22 S2`. Both work over HTTP
  `POST /cmd`. Resume also works without the physical button - the
  laser resumes immediately on receiving `M22 S2`.
- **The Z-probe trigger** - same situation; not in this capture.

### 5.5e Captured during a real job start + STOP (2026-04-09)

A fourth capture: same job-start sequence as 5.5d, but this time the
user clicks **Stop** while the head is on the way to the first cut
position. The shutdown path is **fundamentally different** from the
pause path - there is no dedicated "stopped" state, the laser simply
runs through Preparing → Ready → Idle.

#### Stop sequence (after a normal job-start)

Job started identically to §5.5d (M362 S1 ×4 / M321 / M2240 / M322 /
M810 with UUID / M330 / M2810 ok / M323 OK / M222 S13 / M53 / M222
S14 / M15 A1 S1). Then:

```
M303 X186.73 Y120.64        ← head moving toward first cut
M303 X186.73 Y126.14        ← next path point

  ↓ user clicks Stop
M108 ok                     ← stop-trigger ack (see below)
M222 S18                    ← Preparing/transitioning
M222 S1                     ← Ready
M222 S3                     ← Idle
M15 A1 S0                   ← power S = 0
M22 S1                      ← M22 stays at S1 (NOT back to S0 like after pause/finish!)
M303 X286.18 Y150.95        ← head ends up at the safe position
[head stable at 286.18, 150.95]
```

#### Stop vs Pause vs Finish - clear differentiation

| Action | Marker push | Resulting state | M22 after | Head position |
|---|---|---|---|---|
| **Pause** | `M22 S1` (then `M222 S15`) | `S15` (lingers) | `S1` | returns to origin |
| **Resume** | `M22 S2` | back to `S14` | `S2` → `S0` later | continues path |
| **Stop** | `M108 ok` | `S18` → `S1` → `S3` (no lingering) | `S1` (sticks!) | stops at the safe position |
| **Job finish (natural)** | `M222 S19` | `S19` → `S1` → `S3` | `S0` | end of path |

**Key differentiators**:
- **Stop** → fires `M108 ok`, jumps through `S18` straight to `S3`, and
  leaves `M22` stuck at `S1` (probably an "abnormal termination"
  marker until the next job clears it)
- **Natural finish** → fires `M222 S19` (Finishing), then `S1` → `S3`,
  and resets `M22` to `S0`
- **Pause** → fires `M22 S1` followed by `M222 S15` (lingering Paused
  state), no M108

This is exactly the discrimination we need to build clean
`button.xtool_s1_pause / resume / stop` entities AND a
`binary_sensor.xtool_s1_stopped_abnormally` (whenever `M22` sticks at
`S1` after the job ended without going through `S19`).

#### `M108` semantics - best guess

`M108 ok` was previously seen in the probe sequence (§5.5) and now
fires once on Stop. Hypothesis: it's a generic **"command accepted"
ack** that the firmware emits when the app sends a state-changing
command. In the probe sequence the trigger was the measurement
command; here it's the stop command. We don't yet know what the app
actually sends - only that the laser acks it with `M108 ok`.

State sequence observed for a frame-preview run:

```
S3   (idle)
↓ user clicks Frame in XCS
… M362 S1 / M321 S0 / M322 R0 / M330 S0 / M323 OK / M206 ok …
S11  (job active)
M53 A0 / M810 "NULL" (no real job file)
S12  (motion executing)
[head traces the workpiece outline: top-left → top-right → bottom-right → bottom-left → back]
[head returns to its original position]
S11  (back from motion)
S18  (preparing/transitioning)
S1   (ready)
S3   (idle)
```

### 5.6 `M2003` snapshot - full reference

Live capture from hilman2's idle device on 2026-04-09:

```json
{
  "M54":   "T1",
  "M345":  "S1",
  "M222":  "S3",
  "M99":   "V40.32.015.2025.01 B10",
  "M1199": "V40.32.009.2122.01 B1",
  "M2099": "V40.32.013.2224.01 B1",
  "M1098": ["", "", "V40.208.002.3D28.01 B1", "", "", "", "", "", "", ""],
  "M97":   "S0",
  "M310":  "MXDK101001240615F7319A3",
  "M98":   "X0.32 Y25.88",
  "M100":  "xTool S1",
  "M116":  "X0Y40B1P1L2",
  "M120":  "A1.2",
  "M321":  "S0",
  "M13":   "A40 B40",
  "M27":   "X0.010 Y99.800 Z0.000 U0.000",
  "M105":  "X0.00Y0.00Z0.00"
}
```

The fields `M340` (alarm) and `M810` (job file) are notably **absent**
from the idle snapshot - they only appear in M2003 when the laser is
in an alarm state or has a job loaded. The integration handles this
gracefully (missing field == leave state untouched).

### 5.7 `M222` work-state codes

Complete map, compiled from WebSocket captures and **firmware logs**
(`logs.txt` on the SD card, see §10). The log entries use the format
`sta_change_to:<n>` followed by `M222 S<n>`.

| Code | Meaning | Count in logs | Status |
|---|---|---|---|
| `S1`  | Ready (brief transition state) | 529 | ✅ |
| `S2`  | **User button interaction** - triggered by physical button press sequence (`comb_key` events) | 2 | 🟡 |
| `S3`  | Idle | 118 | ✅ |
| `S7`  | **🚨 Lid opened during operation - safety interlock!** Always follows `M53 B1` (cover-open signal). Laser immediately disabled. | 6 | ✅ (from logs) |
| `S8`  | **File transfer error** - preceded by `"file trans timeout!!!"` in logs. The ESP32→GD32 gcode download failed. | 4 | ✅ (from logs) |
| `S9`  | **🔥 FIRE ALARM!** Preceded by `"fire first happened alarm!"` and `M53 F1` (event:20). Laser disabled, job cancelled immediately. | 1 | ✅ (from logs) |
| `S10` | Measuring (auto-height Z-probe) | 103 | ✅ |
| `S11` | Frame run / job active - persists after head returns | 15 | ✅ |
| `S12` | Motion executing - brief, between S11 and M303 stream | 8 | 🟡 |
| `S13` | Starting (job queued, preparing to run) | 232 | ✅ |
| `S14` | Running (job actively cutting/engraving) | 238 | ✅ |
| `S15` | Paused (user-initiated via app) | 15 | ✅ |
| `S16` | **Firmware/update mode** - SD bus switches to ESP32, followed by `M2037` calibration data push | 12 | 🟡 |
| `S18` | Preparing / transitioning (between other states) | 142 | ✅ |
| `S19` | Finishing (job wrapping up) | 201 | ✅ |
| `S22` | **Homing error** - observed once after a failed homing sequence (`stop!` + emergency cancel) | 1 | 🟡 |
| `S24` | **Job preloaded, waiting for physical start button!** SD bus switches to GD32 (`plugin_sd_card_trans_switch to GD32 mode!!!`), then waits for `comb_key` button presses. This IS the "armed" state from §6.0d. | 94 | ✅ (from logs) |

**S24 is the missing link**: it's the state between "job uploaded"
and "user pressed the hardware button". The integration can detect
this state to fire a "job armed, press button to start" notification.

### 5.8 `M53` event codes ✅ - decoded from `logs.txt` (2026-04-09)

`M53` is the **hardware I/O event bus**. The firmware logs entries as
`active report:M53 <code>, event:<n>`. Each letter+value pair
represents a different subsystem.

| Code | Event # | Meaning | Count in logs | Status |
|---|---|---|---|---|
| `M53 A0` | 0 | **Idle / no active event** - most frequent, fires when events clear | 484 | ✅ |
| `M53 A1` | 1 | **Active / operation in progress** | 2 | ✅ |
| `M53 A2` | - | Rare transitional state | 3 | ❓ |
| `M53 B0` | 3 | **Cover/lid closed** (debounced, both signals low) | 322 | ✅ |
| `M53 B1` | 4 | **Cover/lid opened or moving** (debounced, one or both signals high). Triggers `M222 S7` safety interlock during operation. | 35 | ✅ |
| `M53 C0` | 5 | **Baseplate check complete** - fires on boot after baseplate ADC read | 71 | ✅ |
| `M53 C1` | - | Baseplate event (rare) | 1 | ❓ |
| `M53 D3` | 18 | **Device restart / reboot** - always appears right before `"The log file is started successfully"` | 26 | ✅ |
| `M53 F1` | 20 | **🔥 FIRE ALARM - flame detected!** Triggers `M222 S9`, immediate laser disable + job cancel | 1 | ✅ |
| `M53 F2` | 21 | **Flame sensor initialized / clear** - fires on boot after laser-type detection | 1 | ✅ |
| `M53 L1` | 19 | **Laser work-time counters flushed to storage** - fires right after `acc_*_laserworktime` lines | 41 | ✅ |
| `M53 S0` | 9 | **Safety key disengaged** (sekey ADC state 0) | 1 | ✅ |
| `M53 S1` | 10 | **Safety key engaged** (sekey ADC state 1) | 1 | ✅ |
| `M53 U0` | 11 | **USB disconnected** | 64 | ✅ |
| `M53 U1` | 12 | **USB connected** | 58 | ✅ |

#### Cover/lid sensor detail (from logs)

The lid has **two sensor signals** with debouncing and a 4-state
machine:

```
cover current signal <sig1> <sig2>, debouncing after signal <sig1> <sig2>, state <n>
```

| State | Signal pattern | Meaning |
|---|---|---|
| 0 | `0 0` | **Lid open** (both sensors clear) |
| 2 | `0 1` | **Lid moving / partially closed** (one sensor triggered) |
| 3 | `1 1` | **Lid fully closed** (both sensors triggered) |

This means `M25` (the 5-flag bitmap) likely maps to:
`X=lid_sensor_1, Y=lid_sensor_2, Z=?, T=?, B=?`

#### Safety key (sekey) detail

The safety key uses an **ADC** (analog-to-digital converter):
- `sekey current adc 4095` → key engaged (max ADC = pulled high)
- `sekey current adc 0-3` → key disengaged (near-zero)
- Has a filter and state machine similar to the lid sensor

### 5.9 `M2037` - motor calibration / flame sensor data 🟡

Discovered in `logs.txt`. Has **two distinct formats**:

#### Format 1: Integer (motor/stepper current readings?)

```
M2037 A25 B25 C25 D25 E25 F25 G25 H25 F53
M2037 A16 B16 C16 D16 E16 F16 G16 H16 F10
```

9 values (A-H + duplicate F with different value). All A-H values
are identical within each reading (25 or 16), while the trailing F
value varies (53, 46, 24, 10, 16). Could be motor/stepper current
sensor readings across 8 axes/channels.

#### Format 2: Float (flame/temperature sensor data?)

```
M2037 A0.000 B0.000 C1.000 D0.000 E2.001 T16    ← boot (cold)
M2037 A0.000 B0.000 C0.000 D0.000 E0.000 T16    ← idle
M2037 A0.000 B0.000 C0.375 D0.003 E0.757 T28    ← after fire alarm!
M2037 A0.000 B0.000 C0.312 D0.003 E0.632 T28    ← after fire alarm!
```

5 float values + `T` (likely temperature in °C: 16°C cold, 28°C
after operation). C and E values spike during/after the fire alarm
event - **strong candidate for flame sensor readings**.

### 5.10 `M363` - job progress indicator ❓

Appears in logs during job execution, always as `M363 S0`:
```
M363 S0    ← appears 5 times in logs, all during active jobs
```

Likely a progress/status code. Possibly `S0` = "in progress" and
other values = "complete" or "error".

### 5.11 `M330` - SD card bus ownership ✅ (from logs)

Confirmed from firmware logs. `M330` controls which MCU has access
to the shared SD card bus:

| Command | Direction | Meaning |
|---|---|---|
| `M330 S0` | USB cmd (internal) | **Switch SD bus to ESP32** (WiFi chip, file server access) |
| `M330 S1` | USB cmd (internal) | **Switch SD bus to GD32** (motion controller, gcode execution) |

The S1 logs show this switching happening dozens of times per session.
During job execution, the GD32 owns the SD card to read gcode; during
idle and file transfers, the ESP32 owns it for the HTTP file server.

---

## 6. Workflows we've decoded

### 6.0 Frame preview (single-shot, no pause/stop)

```
[idle, M222=S3]
  ↓ (App sends trigger - not yet isolated)
M362 S1 (×3-4)
M321 S0
M322 R0
M330 S0
M323 OK
M206 ok
M222 S11           ← job active
M53 A0
M810 "NULL"        ← no real file, this is a frame run
M222 S12           ← motion executing
M303 X.. Y..       ← head traces workpiece outline:
                   ←   top-left  → top-right
                   ←   top-right → bottom-right
                   ←   bottom-right → bottom-left
                   ←   bottom-left  → back to original position
M22 S0
M222 S11           ← back from motion (still "job active")
…
M222 S18           ← transitioning out
M206 ok
M222 S18
M222 S1            ← ready
M222 S3            ← idle
```

### 6.0d ⚠️ Hardware safety: M323 multi-acknowledgement

**The xTool S1 cannot be remote-started.** The firmware enforces a
two-step start sequence with a mandatory physical button press on
the device. This was confirmed by hilman2 on 2026-04-09:

```
[Job loaded, head at start position]
… upload ack, init burst …
M2810 ok               ← upload ack
M323 OK                ← FIRST acknowledgement (XCS app received "Start" click)
                        ↓ at this point the laser is waiting for the
                        ↓ physical button press on the device
M323 OK                ← SECOND acknowledgement (user pressed the button)
M222 S13               ← only NOW does the state machine enter Starting
M222 S14               ← Running
```

**Consequences for the integration**:

1. We **cannot ever** ship a `button.xtool_s1_start` that runs a job
   without the user being physically present. The hardware safety
   lock is not bypassable from the network - and that is correct
   and intentional. Don't try to fight it.
2. We **can** ship a "job armed, waiting for button" sensor: when
   the first `M323 OK` arrives but the second one hasn't, we know
   the laser is sitting at the safety lock. That's a useful HA
   automation trigger - e.g. "send a phone notification 'job armed
   on the S1, press the start button to begin'" when you've
   prepared a job from another room.
3. Pause (`M22 S1`) / Resume (`M22 S2`) / Stop (`M108`) work mid-run
   over HTTP without the safety lock. All three are implemented as
   buttons in the HA integration since v1.1.0.

The "job armed" detection logic:

```python
M323_OK_seen_count    # increment on each M323 OK push
job_armed = (
    M323_OK_seen_count == 1
    and current_state == "S3"   # still idle
    and head_at_start_position  # M27 has moved away from park pos
)
job_running = current_state in ("S13", "S14")
```

### 6.0c Job preload (Send-to-Laser without Start)

When the user wraps a job in XCS and switches to the start screen,
the app **uploads the job to the laser** even before the user clicks
Start. Observed behaviour:

1. The laser receives a chunk of **binary WebSocket frames** containing
   what is almost certainly a Gcode file (we don't parse them - they
   show up as garbage in `Follow → TCP Stream`). The block size scales
   with cut complexity - a 1%/10 mm/s test job is shorter than a
   100%/100 mm/s test of the same path.
2. The head moves from the parking position (`X0.0 Y99.8`) to the
   **Job-Start position** (e.g. `X178.06 Y133.39` for a job with that
   workpiece origin).
3. The init burst pushes its usual 17 M-codes - but `M810` is still
   `"NULL"` and `M2240` is unchanged from idle.
4. **`M27.Z` is still 0** - the Z axis is parked.
5. The head sits at the start position waiting for the user to click
   Start.

This means we can detect "job loaded but not running" by observing
that the head has moved from the parking position to a non-trivial
location while the work state is still `S3` (Idle) and `M810` is
still `"NULL"`.

The actual per-job power/speed values are **inside the binary upload
block** - we don't have a way to read them from any M-code. If we
want them in HA, we'd have to parse the Gcode upload, which is a
much bigger effort than the current integration scope.

### 6.0a Real job start → pause → resume → finish

```
[idle, M222=S3]
  ↓ user clicks Start in XCS
M362 S1 (×4)
M321 S0
M2240 A0.500000 B0.800000 C50 D50 M300.0 P0.6 I0    ← job parameters
M322 R0
M810 "<job-uuid>"      ← real file (not "NULL")
M330 S0
M2810 ok               ← job-submission ack
M323 OK
M222 S13               ← Starting
M53 A0
M810 "<job-uuid>"      ← re-pushed
M222 S14               ← Running, head starts moving
M15 A1 S1              ← power S = 1

[head moves through cut path, M303 stream]

  ↓ user clicks Pause
M22 S1                 ← M22 flips to job-in-progress indicator
M222 S15               ← Paused
M15 A1 S0              ← power S = 0
[head returns to origin, M303 stable at origin]

  ↓ user clicks Resume
M53 A0
M222 S14               ← back to Running
M22 S2                 ← M22 = "resuming" (transient)
M15 A1 S1              ← power S = 1

[head resumes the cut path]

  ↓ job finishes naturally
M222 S19               ← Finishing
M22 S0                 ← back to idle indicator
M222 S1                ← Ready
M222 S3                ← Idle
M15 A1 S0              ← power S = 0
```

### 6.0b Real job start → STOP (abort)

```
[idle, M222=S3]
  ↓ user clicks Start in XCS

[ identical job-start sequence as 6.0a:
  M362 ×4 / M321 / M2240 / M322 / M810 "<uuid>" / M330 /
  M2810 ok / M323 OK / M222 S13 / M53 / M810 / M222 S14 / M15 A1 S1 ]

[head starts moving via M303 stream]

  ↓ user clicks Stop in XCS
M108 ok                ← stop-command ack
M222 S18               ← Preparing/transitioning
M222 S1                ← Ready
M222 S3                ← Idle
M15 A1 S0              ← power S = 0
M22 S1                 ← M22 STAYS at S1 - abnormal-termination marker

[head ends up at the safe position, stable M303 stream]
```

Note the difference vs §6.0a (natural finish):
- **No `M222 S19`** - there is no Finishing state on Stop, the laser
  jumps from S14 → S18 → S1 → S3 directly
- **`M22` stays at `S1`** instead of going back to `S0` - this is
  probably the marker for "the last job was aborted"

### 6.1 Z-probe / auto-focus

```
[idle, M222=S3]
  ↓ (App sends trigger - not yet isolated)
M222 S18         ← preparing
M222 S1
M222 S3
M362 S1          ← measurement phase indicator
M222 S10         ← measuring!
M312 S1          ← measurement mode on
M110 X1 Y1 Z1    ← axes referenced
M303 X.. Y..     ← head moves to probe position
M22  S0
M311 S0          ← probe approach
M313 Z<value>    ← THE RESULT (mm, ~2mm offset to laser nozzle)
M311 S2          ← probe done
M110 X1 Y1 Z1
[head moves back through several positions]
M311 R0
M311 R2          ← result phase complete
M2003{...}       ← full status push
M312 S0          ← measurement mode off
M222 S1
M222 S3          ← back to idle
```

### 6.2 Set fill-light brightness ✅

```
POST /cmd HTTP/1.1
Content-Type: application/x-www-form-urlencoded

M13 A<n> B<n>
```

→ HTTP returns `{"result":"ok"}`. The M13 push frame appears on the
WebSocket. Both `A` and `B` are always set to the same value because
the XCS app's slider drives them in lockstep - they're two physical
LED banks wired to one logical setting.

### 6.3 Get full status

```
WebSocket (or POST /cmd):
M2003

→ WebSocket reply:
M2003{...JSON snapshot...}
```

---

## 7. Open questions / things to capture next

In rough priority order:

1. ~~**Pause / Resume trigger M-codes**~~ **DONE**: Pause = `M22 S1`,
   Resume = `M22 S2`. Both verified 2026-04-09 - work over HTTP, no
   hardware button needed. Resume was initially thought to require a
   physical press, but live testing proved it works purely over the
   network. All three buttons (Stop/Pause/Resume) shipped in v1.1.0.
   to test with one more Wireshark session that pauses+resumes
   instead of stopping.
2. **The Z-probe trigger** - same situation, what M-code does the app
   send to *start* a measurement?
3. ~~**`M2240` field meanings** - power %, speed, passes, dot mode?~~
   **DISPROVED 2026-04-09**: M2240 is bit-identical between a
   1%/10 mm/s job and a 100%/100 mm/s job. The real cut parameters
   are in the binary Gcode upload (see §6.0c). To get them we'd
   have to parse the upload - out of scope for v1.
4. ~~**`M1109` - what is it really?**~~ **SOLVED 2026-04-09** (from
   tool-swap diff §5.5g): M1109 is the **tool mounting offset** in
   high-precision format. A,B = primary offset (matches M98), C,D =
   secondary offset, E58.000 = constant hardware dimension. Changes
   per tool, NOT per temperature/fan state.
5. **`M25` flag bitmap** - the logs partially confirm this: the cover
   sensor has two signals with a 4-state debounce machine (§5.8).
   Still need to verify which M25 flag maps to which sensor by
   toggling the lid while reading M25 via WS.
6. **`M9009`** - connect an AP2 air cleaner, re-read; `S-1` should
   change. That gives us the "is AP2 attached" sensor.
7. ~~**The mechanism the XCS app uses to kick other WebSocket clients**~~
   **ANSWERED 2026-04-09 night**: M303 polling at 1/s → fan-out
   overwhelms firmware. See §5.5h and §9.
8. ~~**Job upload protocol**~~ **ANSWERED 2026-04-09**: `POST /upload?
   taskId=<UUID>&filename=tmp.gcode` with raw gcode body. See §3.1.
   The file goes to the SD card (`/gcode/tmp.gcode`), then the GD32
   reads it via SD bus switch (`M330`). The `start download tmp.gcode`
   log entry confirms this pathway.
9. **`M2037` float format** - the flame sensor hypothesis needs
   verification. Read M2037 with the WebSocket in different states
   (cold boot, mid-job, after flame test if possible) and compare
   the C/E/T values.
10. **`M53` as a readable M-code** - we know M53 fires as push events,
    but can we also READ it with a `M53` request (no args) like the
    other M-codes in §5.5h? If so, it would give us lid state, USB
    state, and fire-alarm state in a single query.
11. **`/delete/` endpoint** - returns "File does not exist" for files
    that ARE listed in `/gcode/`. May require URL-encoded paths,
    different Content-Type, or only work from the HTML form. Needs
    more testing.
12. **`logs.txt` parsing for real-time stats** - the firmware writes
    `acc_worktime`, `acc_workcount`, `acc_sys_runtime`, and per-tool
    `acc_*_laserworktime` every ~5 minutes. These are readable via
    HTTP even when the WebSocket is blocked. Feasibility: parse the
    last few KB of logs.txt to extract current counters (see §10).

---

## 9. Coexistence with the XCS desktop app - required architecture

**Problem** (verified 2026-04-09): The XCS desktop app polls `M303`
every ~1 second over its WebSocket. While the desktop app is open the
foreign WebSocket from this integration gets kicked **almost
continuously** - every reconnect lives only a few seconds before
being kicked again.

**Wrong fix**: Try to reconnect faster. → just thrashes harder.

**Right fix**: Decouple the integration's availability from the
WebSocket entirely. Use HTTP as the primary heartbeat and the
WebSocket as a best-effort live-state stream.

### Three operational modes

| Mode | Trigger | Behaviour |
|---|---|---|
| **Normal** | WS connected, no recent kicks | WS push frames update state in real time. HTTP for writes only. |
| **Coexist** | ≥ 3 kicks within 30 s | Stop hammering the WS. Switch to HTTP heartbeat (`/system?action=mac` every 10 s). State entities show their **last known cached values** with a `stale` attribute. The light entity stays fully usable because writes go via HTTP `/cmd`. Try a single WS reconnect every 60 s - if it survives 30 s, drop back to Normal. |
| **Offline** | HTTP heartbeat failing for ≥ 60 s | Mark all entities unavailable. The device is genuinely unreachable. |

### Entity availability rules

- **`light.fill_light`**: available iff HTTP heartbeat is OK. Writes
  always go via HTTP and never depend on the WebSocket. Stale-state
  reads (the brightness number for the UI slider) use the last known
  M13 value.
- **State sensors** (status, position, fans, alarm, …): available iff
  the integration is in `Normal` or `Coexist` mode. In `Coexist` mode
  they show **cached values** instead of going unavailable. We add an
  `extra_state_attribute["stale"] = True` so power-users can see
  that the data is from before the app took over.
- **Diagnostic sensors** (firmware, serial, MAC, tool name, runtime
  counters): available iff HTTP heartbeat is OK - these don't need
  the WebSocket at all because they only refresh on a timer anyway.
- **`binary_sensor.connection`**: this one **does** mirror the WS
  state directly - it's the diagnostic that tells the user "WS is
  currently kicked, integration in coexist mode".

### Kick detection heuristic

```python
class KickDetector:
    def __init__(self):
        self.disconnects: deque[float] = deque(maxlen=10)  # timestamps

    def note_disconnect(self) -> None:
        self.disconnects.append(time.monotonic())

    def is_kicked_loop(self) -> bool:
        """At least 3 disconnects within the last 30 seconds."""
        if len(self.disconnects) < 3:
            return False
        return time.monotonic() - self.disconnects[-3] < 30.0
```

When `is_kicked_loop()` is True the coordinator switches to Coexist mode.

## 8. Capture methodology

How to add new findings to this document:

### Wireshark setup
- Capture filter: `host <S1-IP> and tcp port 8081`
- After capture, set Display Filter: `ip.addr == <S1-IP> and tcp.len > 0`
- `Right-click → Decode As… → port 8081 → WebSocket` (TCP-reassembly
  must be enabled in `Edit → Preferences → Protocols → TCP`)
- For Klartext: `Right-click → Follow → TCP Stream`, then in the
  popup set the direction dropdown to `<your-IP> → <S1-IP>` to see
  **only** what your client sent

### `poke.py` setup (for testing hypotheses)
```bash
wsl -d Ubuntu -- bash -lc \
  'source ~/venvs/ha-xtool-s1/bin/activate \
   && cd /mnt/d/Git/ha-xtool-s1 \
   && python scripts/poke.py --host <S1-IP> --send <MCODE> --listen 5'
```

### When you find something
- Add it to the relevant section above
- Mark it ✅/🟡/❓
- Record sample values
- Note the date and the device firmware version it was tested against

---

## 10. Hardware architecture (from firmware logs) ✅

Decoded 2026-04-09 from `logs.txt` (3.2 MB, ~70k lines, readable
via `GET /gcode/logs.txt`). The log goes back to the first boot of
hilman2's device in 2023.

### 10.1 Dual-MCU design: ESP32 + GD32

The S1 has two microcontrollers on a shared bus:

| MCU | Role | Evidence from logs |
|---|---|---|
| **ESP32** | WiFi, HTTP server, WebSocket server, file server, cloud connectivity | `plugin_sd_card_trans_switch to ESP32 mode!!!`, `wifi cmd M3xx` |
| **GD32** | Motion control, laser firing, sensor reading, state machine | `plugin_sd_card_trans_switch to GD32 mode!!!`, `usb cmd M3xx`, all stepper/laser/probe operations |

Communication between them happens over an **internal USB bridge**:
- `usb cmd M330 S0` = ESP32 tells GD32 "I need the SD card"
- `usb cmd M330 S1` = ESP32 tells GD32 "you can have the SD card back"
- `wifi cmd M322` / `wifi cmd M323` = ESP32 forwards network commands to GD32

The **SD card bus is shared** - only one MCU can access the card at
a time. This is why `M330` switches are so frequent in the logs (600+
entries). During job execution, GD32 owns the SD to read gcode;
during idle and file transfers, ESP32 owns it for the HTTP file
server.

### 10.2 Laser module identification

The firmware logs two laser types on boot (the S1 supports hot-swap):

```
laser type:0, power:40, brand:1, process_type:1, laser_tube:2    ← 40W Diode
laser type:1, power:2, brand:0, process_type:0, laser_tube:0     ← 2W IR
```

| Field | Meaning |
|---|---|
| `type` | Laser module type (0=diode, 1=IR) |
| `power` | Wattage |
| `brand` | 0=generic/third-party, 1=xTool branded |
| `process_type` | 0=marking, 1=cutting/engraving |
| `laser_tube` | 0=none/solid-state, 2=diode tube |

### 10.3 Thermal management (2W IR module)

The 2W IR laser has active thermal management logged:
```
2w laser exit preheat                              ← preheat phase on startup
2w laser temp less than 28.000, laser power on     ← below threshold → fire OK
2w laser temp greadter than 28.000, laser power off ← above threshold → cut power
2w laser fan off                                   ← fan cycles with temperature
```

Temperature threshold is **28°C**. The 40W diode module also has a
fan (`40w laser fan off` logged) but its thermal management is less
aggressive.

### 10.4 Fire detection system

The S1 has a **flame sensor** (likely optical, given the M2037 float
readings). Detection flow from logs:

```
fire first happened alarm!       ← flame sensor triggers
M53 F1, event:20                 ← fire event pushed
M222 S9                          ← state machine → FIRE ALARM
motion laser disable!            ← laser immediately cut
cancel!                          ← job cancelled
M2037 A0.000 B0.000 C0.375 D0.003 E0.757 T28   ← sensor readings
```

The `M2037` float values (C, E) change during a fire event compared
to normal idle (C goes from 1.0 → 0.375, E from 2.0 → 0.757). T
is likely temperature in °C (28°C after operation).

### 10.5 Per-tool lifetime counters (from logs)

The firmware writes running totals to flash every ~5 minutes:

```
config_storage_write_sys_running_info
acc_worktime:151566;               ← M2008.A (seconds)
acc_workcount:228;                 ← M2008.B (job count)
acc_sys_runtime:1291260;           ← M2008.C (standby seconds)
acc_2w_laserworktime:880;          ← per-tool: 2W IR module
acc_default_laserworktime:0;
acc_10w_laserworktime:0;
acc_20w_laserworktime:0;
acc_40w_laserworktime:3086;        ← per-tool: 40W Diode module
```

**M2008.D is the currently-installed tool's work time**, not a global
counter. When tools are swapped, M2008.D switches to the new tool's
accumulated time. This is confirmed by §5.5g (D changed from 3035 to
880 when swapping from 40W to 2W).

The counter names reveal **6 supported laser modules** (by wattage):
- default (unknown/generic)
- 2W (IR marking)
- 3W (exists in firmware but not tested)
- 10W (exists in firmware but not tested)
- 20W (exists in firmware but not tested)
- 40W (diode cutting/engraving)

### 10.6 Config storage subsystem

The firmware uses named keys to persist data:

```
S1_CONFIG_KEY wirte susecc          ← device configuration
SYS_RUNNING_INFO wirte susecc       ← lifetime counters
LASER_3W_INFO wirte susecc          ← per-tool stats
```

(Note: "wirte susecc" is a firmware typo for "write success")

These are written to flash (NVS or similar), NOT the SD card.

### 10.7 HTTP polling path for runtime stats (Coexist mode)

When the WebSocket is blocked by XCS, we can still get **fresh
lifetime counters** by reading the tail of `logs.txt`:

```bash
# The last few KB of logs.txt contain the most recent counter flush
curl -s http://<S1-IP>:8080/gcode/logs.txt | tail -50 | grep "acc_"
```

This fires every ~5 minutes and gives us:
- `acc_worktime` (= M2008.A)
- `acc_workcount` (= M2008.B)
- `acc_sys_runtime` (= M2008.C)
- Per-tool work times

**Feasibility for the integration**: In Coexist mode, poll
`GET /gcode/logs.txt` and parse the tail for the latest `acc_*`
lines, then update the lifetime sensor entities. This is a pure
HTTP read that doesn't need the WebSocket at all.

**Note**: The HTTP Range header is **not supported** - the server
ignores it and returns the full file (200, not 206). For a 3 MB
log file, this is ~1-2 seconds of download. Acceptable for a
5-minute polling interval but not for real-time. Alternative:
track `Content-Length` changes between polls - if it hasn't grown,
don't re-download.

### 10.8 Button/key events

Physical button presses are logged with combined-key codes:

```
evt:0, comb_key:1, click_cnt:1    ← first key down
evt:0, comb_key:2, click_cnt:1    ← key released
evt:0, comb_key:3, click_cnt:1    ← second key down
evt:0, comb_key:5, click_cnt:1    ← combination
evt:0, comb_key:6, click_cnt:1    ← release
```

The `comb_key` field is a bitmask of simultaneously-held buttons.
The physical safety button press that starts a job shows as the
`comb_key:1 → 2` sequence, which is what follows `M222 S24` in
the logs.
