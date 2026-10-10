# Saya

A heat-exposure advisor for outdoor workers, running on a sensor on a roof in Islamabad.

**Saya** (سایہ) is Urdu for shade. The name is a proposal, not a decision.

Air temperature is measured where people actually stand, not at an airport station kilometres away.
A camera shows whether there is shade, shelter or water within reach. **When the camera cannot see,
the system says so and refuses to advise, rather than guessing.**

## Architecture

```
  ESP32-CAM + DHT22          reading and frame, posted over WiFi
          |
          v
  server/ingest.py           append-only archive, physically impossible values refused
          |
          +--> server/heat.py     NOAA heat index, deterministic, no model involved
          |
          +--> MiniCPM-V-4_5      perception: shade, surface, sky, shelter, water, can it see
          |
          +--> Nemotron           judgment: what a person standing here should do,
                                  or a refusal to advise, and why
```

**Why perception is not on an NVIDIA model.** Nemotron Omni is not served on Nebius Token Factory,
and all four served Nemotrons reject images with `This model does not support image input`. NVIDIA
serves no vision-capable model on the platform. Measured on 2026-09-23, reproducible with
`../probes/omni_image_probe.py`. Nemotron does the reasoning, the routing and the abstention.

## Three things this deliberately refuses to do

**Ask a model to do arithmetic that has a formula.** Apparent temperature from temperature and
humidity is a published closed form, so `heat.py` computes it. A model would return a plausible number
instead of a correct one.

**Report a figure outside the range its formula covers.** The NOAA regression diverges above its
chart: 45 C at 80% RH returns 115 C, which is meaningless. But refusing outright was worse, because
38 C at 65% humidity is an ordinary monsoon reading that also sits above the chart, and silence there
would arrive exactly when the danger peaks. So the danger band is reported with confidence, the
apparent temperature is marked extrapolated, and the advice never quotes a figure it cannot stand
behind.

**Advise on a scene it cannot see.** A blocked, dark or smeared frame produces a refusal and a reason.
Verified live against both a dark frame and an obscured one.

## Run it

Python 3, and **one dependency**:

```
pip install -r requirements.txt     # Pillow, and nothing else
```

Pillow measures the mean brightness of a camera frame, which drives the darkness floor that makes
the abstention work. Without it everything still runs and every test still passes, but the floor is
disabled and each decision says so. See Known limitations.

```
python3 server/ingest.py --port 8080
```

Post a reading the way the device does:

```
curl -X POST "http://localhost:8080/api/reading?device=roof-01&t=34.2&h=41.0" \
     -H "Content-Type: image/jpeg" --data-binary @frame.jpg
```

`/` for stored readings, `/api/readings` for JSON, `/healthz` for a check.

## Tests

```
python3 tests/run_all.py            # offline, no key, no hardware, no network
python3 tests/fresh_clone_check.sh  # what a judge does: clone it, run it
python3 tests/test_models_live.py   # live models, needs NEBIUS_API_KEY and Pillow
```

The offline suite is the one to run after cloning. **It needs nothing installed.** The live suite is
the only thing needing an API key. `Pillow` is required by the darkness floor and by the synthetic
frame generator, and it is the project's single dependency.

## Firmware

`firmware/saya_node/`. Arduino IDE, board "AI Thinker ESP32-CAM", partition scheme "Huge APP".
Needs the Adafruit DHT sensor library and Adafruit Unified Sensor.

Copy your network and host details into `config.h`. The DHT22 data line goes to **GPIO 13**: the
camera takes most pins, GPIO 12 is a boot strapping pin that a sensor pull-up can stop the board
booting on, GPIO 16 is tied to PSRAM, and GPIO 1 and 3 are the serial console.

The camera sensor is **detected, not assumed**, because the ordered board's listing named OV3660 in
the title and OV2640 in the description.

## Data

- `data/readings.jsonl`, append-only, one JSON object per reading
- `data/images/<id>.jpg`, the frame that reading came from

Never rewritten, so a power cut costs at most the line being written. A truncated final line does not
destroy the file. Ids sort chronologically and are unique under rapid capture, and writing a frame
over an existing one raises rather than silently destroying it. All of this exists so the headline
device-versus-station comparison can be recomputed from the raw data by anyone who doubts it.

## Known limitations

Stated here because they are real, not because a checklist asked for them.

- **The vision model claims it can see when it cannot.** Shown real night frames at a mean brightness
  of 13 to 18 out of 255, `openbmb/MiniCPM-V-4_5` returned `can_see: true` with **high** confidence
  and described "direct sun" and a "concrete" surface, on 72 of 72 night frames. The abstention, the
  centrepiece of this project, was silently not firing. **Brightness is now measured in code before
  any model call**, and below 25 of 255 the system abstains without asking. The model is still used
  above that floor, where it is reliable.
- **That fix needs Pillow.** Without it the floor cannot run and the system falls back to asking the
  model, which is the behaviour just described. It does not crash and it does not hide: the scene
  records `darkness_floor_unavailable` and confidence drops to low.
- **The NOAA coefficients in `heat.py` have not been checked against an NOAA page.** The property
  tests catch a typo or a sign error but cannot prove the constants.
- **A Nemotron decision takes 7 to 49 seconds**, highly variable on identical input. Fine for a roof
  reporting every five minutes, too slow to wait for on camera.
- **The Urdu has not been read by a native speaker.** Script is validated automatically and every
  translation is round-tripped through a second model, but grammar is not machine-checkable. The
  refusal string and the register setting are the two things that ship verbatim.
- **Six days, two sensors, one roof.** A rooftop is not a standard weather station site, and part of
  the gap against the airport is the roof itself. The claim is not that the airport is wrong. It is
  that an advisory computed 29 km away does not describe this place.

## The data is in this repository

| File | What |
|---|---|
| `data/readings.jsonl` | all **3,392 readings** from both sensors, 27 September to 2 October 2026 |
| `data/images/` | **284 frames**, one per hour per device |
| `data/station_opis_metar.csv` | the official station's half-hourly observations for the same period |
| `data/archive_report.json` | every frame re-judged: the evidence for the 46% abstention rate |
| `data/DATA-LOCK.json` | SHA-256 of all of the above |
| `docs/video-transcript.txt`, `docs/video-subtitles.srt` | the demo video's narration |

**The station file had to be recovered.** `aviationweather.gov` serves only about 400 recent
observations and no longer reaches this period, so without a committed copy nobody could recompute
the comparison. It comes from the Iowa State University Mesonet ASOS archive, and recomputing from it
reproduces the original analysis exactly, every hour to 0.00 C.

**Every number in the writeup is recomputable from these files:**

```
python3 tools/process_archive.py --data data --with-images-only
```
