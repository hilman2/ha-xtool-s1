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
| **8080** | TCP / HTTP | command gateway + tiny info reads (`/system`, `/cmd`) |
| **8081** | TCP / WebSocket | live state push frames + command stream |
| **20000** | UDP | JSON discovery beacon |
| any other tested | — | closed (80, 443, 8000, 8082, 9100, 20001, …) |

The S1 has **no authentication** on any of these. The XCS app uses
the WebSocket exclusively for both reads and writes — but the HTTP
gateway is a viable side-channel that the app ignores.

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
`name` is empty by default — the XCS app appears to set it via the
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
| GET | `/system?action=get_dev_name` | device name | always empty body — no working setter |
| GET | `/cmd?cmd=<MCODE>` | single-command queue | echoes the cmd back, command goes to WS layer |
| POST | `/cmd` | multi-line command queue | body is `\n`-separated M-codes; replies `{"result":"ok"}` |

### Things that DON'T exist on the S1

- `/list` / `/files` / `/sd` (no file management)
- ~~`/upload` (no job upload)~~ **DISPROVED 2026-04-09** — see §3.1
- `/cnc/data?action=pause/resume/stop` (D1-style — 404)
- `/peripherystatus`, `/getmachinetype`, `/getlaserpowerinfo` (D1-style — 404)
- `/progress`, `/getmachineconfig`
- WebSocket upgrade on port 8080
- Any `/api/...` or `/v1/...` REST paths
- A `Server:` HTTP header (the device hides its impl)
- Other HTTP verbs on `/system` (POST/PUT/DELETE → 405)
- Any `/system?action=…` setter (`set_dev_name`, `set_*` etc.) — all
  either 404 or hang without changing state

### `/cmd` is pure passthrough

GET `/cmd?cmd=HELLO` returns the literal string `HELLO`. There's
zero validation — the gateway just dispatches the string to the
internal M-code handler.

### 3.1 `POST /upload` — job-file upload

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
- **`taskId`** is a client-generated UUID — the same value will later
  appear in the WS push as `M810 "<uuid>"` once the job is started.
- **`filename`** appears to always be `"tmp.gcode"` — single-slot,
  no file manager.
- **Content-Type is `text/plain`**, NOT multipart/form-data. The
  body is the raw Gcode file as a string.
- **No authentication.** Same as everything else on this device.
- The upload **does not start the job** — it only stages it. The user
  still has to press Start in the app, then the safety button on the
  device (see §6.0d). After upload, sending a yet-unidentified
  `start` command (or just clicking Start in XCS, which we know
  drives `M323 OK`) is what kicks off `M222 S13`.

**Implication**: HA can upload arbitrary jobs to the S1 by
constructing a Gcode body and POSTing it. The hardware safety lock
still applies — no remote-start is possible — but features like
"frame preview from HA", "park-and-home" buttons, or
"upload-this-saved-job" become feasible.

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
M109 S1                              # NEW — RepRap M109 = wait-for-temp;
                                     # here probably "set mode 1"
M223 X498 Y330                       # NEW — workspace bounds in mm.
                                     # (S1 advertised 498×320; the gcode
                                     # uses 498×330 — possibly extended)
M7 S1                                # already-known field, used here as
                                     # a write command
M96 S0                               # NEW — config flag, unknown
G90
G0Z0                                 # park Z
G92 U0                               # set U axis offset to 0
M32 X3200                            # NEW — speed cap? (3200 mm/min)
G0X319.15Y242.47                     # move to start position
G0 F9600
M9064 B3                             # NEW — air-assist control? B3=on
G198 P78 "M9064 B3"                  # G198 wraps a quoted command
                                     # with a "priority" (P78). Looks
                                     # like a structured execute-with-ack
                                     # mechanism.
M9039 C2                             # 🔥 AP2 air-cleaner WRITE — C2 = speed 2
                                     # (we previously only saw M9039 as
                                     # a push frame)
G198 P76 "M9039 V50"                 # AP2 with V50 (level/voltage?)
G198 P76 "M9043 H1 I1 J1 K194 L1 M1" # NEW — looks like AP2 filter
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
                                     # — comes from a fresh probe
                                     # measurement done for this job, NOT
                                     # the same M313 reading from §5.5.
                                     # Just shows that cut-height values
                                     # land in the body as plain G0Z..
G0X119.362Y105.187
G1X309.656Y105.187 S10 F1680         # 🔥 actual cut line:
                                     #   S10  = laser power, scale 0-1000
                                     #          (S10 / 1000 = 1% — matches
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

XCS is an **Electron app** — that explains some of the WebSocket
quirks (Electron's ws implementation isn't always 100% standard).

---

## 4. WebSocket (port 8081) — base protocol

- Plain WebSocket, no authentication, no Origin check
- Multiple parallel client connections are accepted
- Server sends both push frames (state changes) and command replies
- Most frames are TEXT; a few (notably `M9039`) are BINARY with a
  small non-printable header/footer wrapping a printable M-code body
- The XCS app **kicks other clients** by some mechanism we haven't
  isolated yet — possibly a specific M-code, possibly an HTTP-side
  command. Reconnecting with a new socket is allowed.

---

## 5. M-code reference

### Status legend

- ✅ verified — observed in our test traffic, semantics confirmed
- 🟡 plausible — observed but semantics are educated guesses
- ❓ unknown — observed in capture but no clue what it means

### 5.1 Reads we already use in the integration

| M-code | Source | Direction | Meaning | Status |
|---|---|---|---|---|
| `M2003` | M2003 JSON | request → JSON push | full status snapshot, see §5.6 | ✅ |
| `M303` | push / ping | request → push | position refresh `X<f> Y<f>` | ✅ |
| `M222` | push | push | work-state code (`S<n>`), see §5.7 | ✅ |
| `M810` | push | push | current job filename, `"NULL"` if idle | ✅ |
| `M340` | push | push | alarm code, `A0` = no alarm | ✅ |
| `M313` | push | push | last Z-probe reading: `Z<f>` mm — measures from probe tip, ~2 mm offset to laser | ✅ |
| `M99`  | M2003 field | snapshot field | main firmware version | ✅ |
| `M310` | M2003 field | snapshot field | serial number | ✅ |
| `M27`  | M2003 field | snapshot field | head position `X<f> Y<f> Z<f> U<f>` — Z is 0 at idle / parked, climbs to the cut height during a job (e.g. 27.338 mm) | ✅ |
| `M105` | M2003 field | snapshot field | temperatures `X<f>Y<f>Z<f>` (no spaces!) — values are 0.00 at idle | ✅ |
| `M13`  | M2003 + write | both | **fill-light brightness 0-100** (`A<n> B<n>`, A=B always); writeable via `M13 A<n> B<n>` | ✅ |
| `M100` | M2003 field | snapshot field | model name string (`"xTool S1"`) | ✅ |
| `M1199` | M2003 field | snapshot field | sub-firmware 1 (mainboard?) | ✅ |
| `M2099` | M2003 field | snapshot field | sub-firmware 2 (laser module?) — equals `/system?action=version` | ✅ |
| `M1098` | M2003 field | snapshot field | 10-slot tool firmware array | ✅ |
| `M54`  | M2003 field | snapshot field | tool type code (`T1`) | ✅ |
| `M9039` | push (binary) | push | AP2 air cleaner status (deferred to v2) | ✅ |

### 5.2 Newly captured in M2003 (untouched by integration so far)

These appear in the full M2003 JSON snapshot but the integration
doesn't surface them yet:

| M-code | Example value | Guess |
|---|---|---|
| `M97` | `"S0"` | always `S0` in our captures — secondary state? ❓ |
| `M345` | `"S1"` | door-lock state? ❓ |
| `M321` | `"S0"` | ❓ |
| `M120` | `"A1.2"` | ❓ |
| `M116` | `"X0Y40B1P1L2"` (40W Diode) / `"X1Y2B0P0L0"` (2W IR) | **🔥 TOOL CAPABILITY BITMAP** — `Y` field = wattage in W (verified). Other flags: `X`/`B`/`P`/`L` = capability bits (probably air-assist support, probe support, laser class). See §5.5g | ✅ |
| `M98`  | `"X0.32 Y25.88"` | workpiece origin / calibration offset? 🟡 |

### 5.3 Newly captured via XCS init burst (2026-04-09)

The XCS app issues a 17-code burst right after the WebSocket handshake.
We saw the responses in a Wireshark `Follow → TCP Stream`.

| M-code | Sample response | Direction | Guess | Status |
|---|---|---|---|---|
| `M2002` | `"192.168.32.55"` | reply | **device IP as string** | ✅ |
| `M318` | `N1` | reply | notification count? network mode? | ❓ |
| `M366` | `X507.00 Y0.00` | reply | **workspace bounds** (S1 has ~498×320 mm bed) | 🟡 |
| `M1109` | `A0.324 B25.882 C2.322 D23.562 E58.000` | reply | **NOT the exhaust fan** — 2026-04-09 disproved: fans were OFF during this reading. Possibly mainboard temps + a fixed config value. Real meaning still unknown | ❓ |
| `M1099` | `T10` | reply | tool slot? total runtime hours? | ❓ |
| `M1113` | `X21.201 Y13.563` | reply | material origin offset? | ❓ |
| `M15` | `A1 S0` | reply | power state? A=power-on, S=standby? | 🟡 |
| `M25` | `X0,Y1,Z0,T0,B0` | reply | **5-flag I/O bitmap** — limit-switches / endstops / lid sensor / button? Strong candidate for safety binary_sensors | 🟡 |
| `M21` | `S1` | reply | SD card mounted? (RepRap convention) | ❓ |
| `M7`  | `S0 N0 D10` | reply | job progress? S=state, N=line, D=duration? | 🟡 |
| `M2008` | `A151484 B219 C1263671 D3004` | reply | **lifetime statistics counters** — verified against XCS app screenshot (see §5.4) | ✅ |
| `M9009` | `S-1` | reply | accessory status — `S-1` = no AP2 attached | 🟡 |

### 5.4 `M2008` — lifetime counters ✅ VERIFIED

Verified by comparing the M2008 reply against the XCS app's
"Statistik" screen on hilman2's device on 2026-04-09:

```
M2008 A151484 B219 C1263671 D3004
       │       │      │        │
       │       │      │        └─ unknown — successful jobs? lines processed?
       │       │      └─────────  Standby-Zeit  →  1263671 / 3600 = 351.02 h ✅ exact match
       │       └────────────────  Betriebszeiten →  219                      ✅ exact match
       └────────────────────────  Arbeitszeit    →  151484 / 3600 = 42.08 h  ✅ exact match
```

Perfect 1:1 mapping. We can build sensors from this with full confidence.

### 5.5 Captured during a Z-probe sequence (2026-04-09)

The capture shows the full lifecycle of a "Measure" action in the
XCS app. The trigger M-code (what the app SENDS to start the
measurement) is not yet isolated — we only have the responses:

| M-code | Sample value | Role in the sequence | Status |
|---|---|---|---|
| `M108` | `ok` | acknowledgement / ready signal | 🟡 |
| `M222 S18` | (state push) | **NEW STATE: preparing for measurement** — appears just before S10 | 🟡 |
| `M362 S1` | (push) | measurement-phase indicator | ❓ |
| `M312 S1` / `S0` | (push) | measurement-mode flag, on at start, off at end | ❓ |
| `M311 S0` / `S2` / `R0` / `R2` | (push) | probe sub-phase — S0 approach, S2 measured, R fields = result phase | ❓ |
| `M22 S0` | (push) | unknown; appears multiple times during the sequence | ❓ |
| `M110 X1 Y1 Z1` | (push) | **3-axis referenced flag** — fired after the measurement; X/Y/Z each 0/1 | 🟡 |
| `M313 Z46.996` | (push) | **the Z-probe result** — measured 47 mm vs 49 mm app display (~2 mm offset because the probe tip hangs ~2 mm below the laser nozzle, confirmed by hilman2) | ✅ |

### 5.5b Captured during a Frame-Preview run (2026-04-09)

A second capture during a "Frame Preview" / "Rahmenfahrt" action.
The frame preview is **single-shot** — there is no pause or stop,
the head traces the workpiece outline once and returns. Still no
isolated app→laser trigger yet.

| M-code | Sample value | Role in the sequence | Status |
|---|---|---|---|
| `M53` | `A0` | (push, just before movement starts) | ❓ |
| `M206` | `ok` | (push) acknowledgement (RepRap M206 = home offset; here probably an ack) | 🟡 |
| `M321` | `S0` | (push) — same field as in M2003, here as a separate push | ❓ |
| `M322` | `R0` | (push, before frame run) | ❓ |
| `M323` | `OK` | (push) **multi-stage Start acknowledgement** — see safety note below | ✅ |
| `M330` | `S0` | (push, before frame run) | ❓ |
| `M2008` | `A151484 B219 C1266188 D3004` | (push) | ✅ **C grew by 2517 seconds (= 42 min standby) since the previous capture — confirms C is the standby-time counter in seconds** |

**`M25` flip observed!** Between the idle and frame-prep capture, the
M25 reading went from `X0,Y1,Z0,T0,B0` to `X1,Y1,Z0,T0,B0`. The first
flag (`X`) flipped from 0 to 1. This is the strongest evidence so far
that **M25 is a 5-flag I/O bitmap** for limit-switches / safety states.
Worth a follow-up: open the lid in idle and re-read M25, then close
the lid and re-read — whichever flag flips is the lid sensor.

### 5.5c New work-state codes from frame-preview

| Code | Observed in | Best-guess meaning | Status |
|---|---|---|---|
| `S11` | M2003 snapshot during frame run | **Frame run / job active** — appears for most of the duration, also after the head returns to its original position | 🟡 |
| `S12` | brief push during the actual head movement | **Executing motion** — appears between S11 and the start of the M303 position stream | 🟡 |

### 5.5d Captured during a real job start + pause + resume (2026-04-09)

A third capture: hilman2 loaded a real job file, started it, paused
it, resumed it, and let it finish. The capture is gold — it gives us
a job-parameters frame, a job-submission ack, the **paused state**,
and the full S-code lifecycle of a job.

#### Job-start frame burst (in order)

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M362 S1` | (push, ×4) | pre-job indicator | ❓ |
| `M321 S0` | (push) | state field also seen in M2003 | ❓ |
| **`M2240`** | `A0.500000 B0.800000 C50 D50 M300.0 P0.6 I0` | **NOT per-job power/speed** — verified 2026-04-09: identical bytes for a 1%/10 mm/s job AND a 100%/100 mm/s job. Most likely material defaults / global cut profile. The real per-job power/speed are in the binary upload (see §6.0c). | 🟡 |
| `M322 R0` | (push) | ❓ |
| **`M810 "<uuid>"`** | `"4b0ee700-7a94-435e-9147-ad635769f6e6"` | **the job file is now a UUID instead of `"NULL"`** — confirms M810 carries the active job filename | ✅ |
| `M330 S0` | (push) | ❓ |
| **`M2810 ok`** | (push) | **job submission ack** — fires after the file name is set, just before S13 | 🟡 |
| `M323 OK` | (push) | acknowledgement | 🟡 |
| `M222 S13` | (push) | **Starting** — begins the active state machine | ✅ |
| `M53 A0` | (push) | unknown phase indicator | ❓ |
| `M810 "<uuid>"` (re-pushed) | (push) | filename re-confirmation | |
| `M222 S14` | (push) | **Running** — head starts moving | ✅ |
| `M15 A1 S1` | (push) | power state — `S` flipped from `0` to `1` when running starts | 🟡 |

#### Job-pause and resume sequence

Once `M222 S14` (running) is up and the head is moving, the user
clicks Pause in the XCS app. Observed:

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M22 S1` | (push) | **`M22` becomes the pause-indicator** — was `S0` during running, flips to `S1` on pause | 🟡 |
| **`M222 S15`** | (push) | **NEW STATE: PAUSED** — fires immediately after `M22 S1`, head returns to its origin position and stays | 🟡 |
| `M15 A1 S0` | (push) | power-state `S` flips back to `0` while paused | 🟡 |
| `M2003{…}` | (push, several seconds later) | confirms `"M222":"S15"` and `"M27":"X286.510 Y151.060 Z27.678…"` (Z != 0 — head is at the cut depth) | ✅ |

The user then clicks Resume:

| M-code | Sample value | Role | Status |
|---|---|---|---|
| `M53 A0` | (push) | unknown — also fires on resume | ❓ |
| `M222 S14` | (push) | back to **Running** | ✅ |
| `M22 S2` | (push) | **`M22 S2`** — third value of M22, possibly "resuming" or "running after pause" | ❓ |
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
the user clicks Pause) is still not isolated — the dump above mixes
both directions and is dominated by server pushes. To find it, we'd
need to repeat this capture but with the Wireshark `Follow → TCP
Stream` direction filter set to **App → Laser only**, and look for an
otherwise-unknown short M-code right before `M22 S1` / `M222 S15`.

### 5.5g Tool change diff (2026-04-09)

hilman2 swapped a 40 W diode head for a 2 W infrared module. Five
fields change deterministically — these are the **tool-identification
fingerprint**.

| Field | 40 W Diode | 2 W IR | Interpretation |
|---|---|---|---|
| **`M1199`** | `V40.32.009.2122.01 B1` | `V40.32.008.2122.01 B3` | Tool firmware version — **eindeutiger Fingerprint** für Lookup-Table |
| **`M116`** | `X0Y40B1P1L2` | `X1Y2B0P0L0` | Capability bitmap: **`Y` = wattage in W** (40 vs 2 — verified). Other flags = capability bits |
| **`M98`** | `X0.32 Y25.88` | `X-0.88 Y20.20` | Tool mounting offset (X, Y) in mm — every tool has a slightly different mechanical offset |
| **`M1109`** | `A0.324 B25.882 C2.322 D23.562 E58.000` | `A-0.879 B20.202 C0.484 D18.999 E58.000` | Higher-precision tool offset (A,B = same as M98 in 3 decimals; C,D = secondary offset; E58.000 = constant Hardware-Maß) |
| **`M2008.D`** | 3035 | 880 | **Per-tool working-time counter in seconds** — switches with the installed tool, persists per tool independently |

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
- `M2008.D` → tool runtime in seconds (per tool) — convertible to hours
- `M98.X/Y` → tool mounting offset
- `M116` raw → full capability string for advanced users

**Fields that did NOT change** at the tool swap (so they are NOT
tool-specific despite their position in the snapshot):
- `M54` (still `T1`) — was previously assumed to be the tool-type, **disproved**
- `M1098` array — still has `V40.208.002.3D28.01 B1` in slot 2 — probably accessory-slot, not tool
- `M2099` (still `V40.32.013.2224.01 B1`) — sub-firmware of the laser-module mainboard, not the tool head
- `M99` (main firmware) — obviously stays
- `M120`, `M321`, `M345`, `M97`, `M100`, `M310` — device identity / config, all stable

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

- **`M-code` (no arguments)** = **READ request** — the server answers
  with a push frame containing the current value, in the form
  `M-code <value>`.
- **`M-code <args>`** = **WRITE / ACTION** — the server executes the
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
without needing M2003 at all** — just send the read request for the
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

- **Pause / Resume triggers** — hilman2 only triggered Stop in this
  session. Pause/Resume not yet isolated; expected to be `M22 S1` /
  `M22 S2` based on earlier observations, but unverified.
- **The Z-probe trigger** — same situation; not in this capture.

### 5.5e Captured during a real job start + STOP (2026-04-09)

A fourth capture: same job-start sequence as 5.5d, but this time the
user clicks **Stop** while the head is on the way to the first cut
position. The shutdown path is **fundamentally different** from the
pause path — there is no dedicated "stopped" state, the laser simply
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

#### Stop vs Pause vs Finish — clear differentiation

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

#### `M108` semantics — best guess

`M108 ok` was previously seen in the probe sequence (§5.5) and now
fires once on Stop. Hypothesis: it's a generic **"command accepted"
ack** that the firmware emits when the app sends a state-changing
command. In the probe sequence the trigger was the measurement
command; here it's the stop command. We don't yet know what the app
actually sends — only that the laser acks it with `M108 ok`.

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

### 5.6 `M2003` snapshot — full reference

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
from the idle snapshot — they only appear in M2003 when the laser is
in an alarm state or has a job loaded. The integration handles this
gracefully (missing field == leave state untouched).

### 5.7 `M222` work-state codes

| Code | Meaning | Status |
|---|---|---|
| `S1`  | Ready (brief transition state) | ✅ |
| `S3`  | Idle | ✅ |
| `S10` | Measuring (auto-height calibration) | ✅ |
| `S11` | **Frame run / job active** — NEW 2026-04-09, observed during frame preview, persists after head returns to its origin position | 🟡 |
| `S12` | **Motion executing** — NEW 2026-04-09, very brief, between S11 and the actual M303 movement stream | 🟡 |
| `S13` | Starting (job queued, preparing to run) | ✅ |
| `S14` | Running (job actively executing) | ✅ |
| `S15` | **Paused** — NEW 2026-04-09, observed when user clicks Pause during a real job, head returns to origin position and stays | 🟡 |
| `S18` | **Preparing / transitioning** — NEW 2026-04-09, appears both before S10 in probe sequence and after S11 when a job ends | 🟡 |
| `S19` | Finishing (job wrapping up) | ✅ |

Other S-codes likely exist (Pause, Stop, Error, Sleep) but haven't
been observed in our captures yet. There is also no Pause/Stop for a
frame-preview run — it's a single-shot operation that the user
cannot interrupt.

---

## 6. Workflows we've decoded

### 6.0 Frame preview (single-shot, no pause/stop)

```
[idle, M222=S3]
  ↓ (App sends trigger — not yet isolated)
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
   lock is not bypassable from the network — and that is correct
   and intentional. Don't try to fight it.
2. We **can** ship a "job armed, waiting for button" sensor: when
   the first `M323 OK` arrives but the second one hasn't, we know
   the laser is sitting at the safety lock. That's a useful HA
   automation trigger — e.g. "send a phone notification 'job armed
   on the S1, press the start button to begin'" when you've
   prepared a job from another room.
3. Pause / Resume / Stop are different — those work mid-run and
   don't trigger the same safety lock. So we can safely build
   `button.pause / resume / stop` once we know the trigger M-codes.

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
   what is almost certainly a Gcode file (we don't parse them — they
   show up as garbage in `Follow → TCP Stream`). The block size scales
   with cut complexity — a 1%/10 mm/s test job is shorter than a
   100%/100 mm/s test of the same path.
2. The head moves from the parking position (`X0.0 Y99.8`) to the
   **Job-Start position** (e.g. `X178.06 Y133.39` for a job with that
   workpiece origin).
3. The init burst pushes its usual 17 M-codes — but `M810` is still
   `"NULL"` and `M2240` is unchanged from idle.
4. **`M27.Z` is still 0** — the Z axis is parked.
5. The head sits at the start position waiting for the user to click
   Start.

This means we can detect "job loaded but not running" by observing
that the head has moved from the parking position to a non-trivial
location while the work state is still `S3` (Idle) and `M810` is
still `"NULL"`.

The actual per-job power/speed values are **inside the binary upload
block** — we don't have a way to read them from any M-code. If we
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
M22 S1                 ← M22 STAYS at S1 — abnormal-termination marker

[head ends up at the safe position, stable M303 stream]
```

Note the difference vs §6.0a (natural finish):
- **No `M222 S19`** — there is no Finishing state on Stop, the laser
  jumps from S14 → S18 → S1 → S3 directly
- **`M22` stays at `S1`** instead of going back to `S0` — this is
  probably the marker for "the last job was aborted"

### 6.1 Z-probe / auto-focus

```
[idle, M222=S3]
  ↓ (App sends trigger — not yet isolated)
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
the XCS app's slider drives them in lockstep — they're two physical
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

1. **Pause / Resume trigger M-codes** (App→Laser direction) — Stop is
   now known to be `M108` (verified §5.5h). Pause and Resume still
   need an isolated capture with the WebSocket decoder enabled.
   Strong hypothesis: `M22 S1` (pause) and `M22 S2` (resume) — easy
   to test with one more Wireshark session that pauses+resumes
   instead of stopping.
2. **The Z-probe trigger** — same situation, what M-code does the app
   send to *start* a measurement?
3. ~~**`M2240` field meanings** — power %, speed, passes, dot mode?~~
   **DISPROVED 2026-04-09**: M2240 is bit-identical between a
   1%/10 mm/s job and a 100%/100 mm/s job. The real cut parameters
   are in the binary Gcode upload (see §6.0c). To get them we'd
   have to parse the upload — out of scope for v1.
4. **`M1109` — what is it really?** — the fan hypothesis is
   disproved (2026-04-09: fans were off, E58.000 was still present).
   Possibilities to test: read M1109 in different machine states
   (cold start, after running for 30 min, lid open, lid closed,
   probe extended) and see which fields move.
5. **`M25` flag bitmap** — open/close the lid and re-read M25; one of
   the 5 flags should flip. Whichever flag changes is `lid_open`. We
   already saw the X flag flip between two captures (0→1).
6. **`M9009`** — connect an AP2 air cleaner, re-read; `S-1` should
   change. That gives us the "is AP2 attached" sensor.
7. **The mechanism the XCS app uses to kick other WebSocket clients**
   — answer 2026-04-09 night: the XCS Desktop app sends `M303` (a
   read request) **once per second**, and the server pushes the
   resulting `M303 X<f> Y<f>` reply **to every connected client**.
   The combination of constant fan-out and our foreign connection
   seems to overwhelm a fairness/buffer limit in the firmware,
   dropping our connection. See §5.5h for the captured polling
   pattern.

   → Architectural consequence: the integration must treat the
   WebSocket as **best-effort** and never let WS unavailability
   make HTTP-side functionality (like the fill-light entity) go
   unavailable. See §9 below.
8. **Job upload protocol** — does the app POST a giant gcode file to
   `/cmd`, or is there a transport we haven't found? `M2810 ok` may be
   an ack for that upload happening just before.
9. **`/cmd?cmd=set_dev_name&name=...`** — confirmed not working via the
   public action list. Maybe a different endpoint exists.

---

## 9. Coexistence with the XCS desktop app — required architecture

**Problem** (verified 2026-04-09): The XCS desktop app polls `M303`
every ~1 second over its WebSocket. While the desktop app is open the
foreign WebSocket from this integration gets kicked **almost
continuously** — every reconnect lives only a few seconds before
being kicked again.

**Wrong fix**: Try to reconnect faster. → just thrashes harder.

**Right fix**: Decouple the integration's availability from the
WebSocket entirely. Use HTTP as the primary heartbeat and the
WebSocket as a best-effort live-state stream.

### Three operational modes

| Mode | Trigger | Behaviour |
|---|---|---|
| **Normal** | WS connected, no recent kicks | WS push frames update state in real time. HTTP for writes only. |
| **Coexist** | ≥ 3 kicks within 30 s | Stop hammering the WS. Switch to HTTP heartbeat (`/system?action=mac` every 10 s). State entities show their **last known cached values** with a `stale` attribute. The light entity stays fully usable because writes go via HTTP `/cmd`. Try a single WS reconnect every 60 s — if it survives 30 s, drop back to Normal. |
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
  counters): available iff HTTP heartbeat is OK — these don't need
  the WebSocket at all because they only refresh on a timer anyway.
- **`binary_sensor.connection`**: this one **does** mirror the WS
  state directly — it's the diagnostic that tells the user "WS is
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
