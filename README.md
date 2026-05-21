# Telemetry dashboard

A Python utility tool to process FIT/TCX data to CSV, generate PNG images from it and merge them with a video file using FFmpeg. `main.py` implements a menu-driven tool that:

- Converts FIT + TCX telemetry into a uniformly-sampled CSV (default 500ms rows).
- Renders PNG overlay frames per CSV row for video compositing.
- Merges the generated frames with a source video using `ffmpeg` (supports NVENC and libx265 settings).

This README documents the final implementation logic and the environment-driven configuration used by `main.py`.

## FIT -> CSV conversion

- Merges temperature data from a FIT file with trackpoint data from a TCX file.
- Produces a CSV sampled every `TIMESTAMP_STEP_MS` (default 500 ms).
- Interpolates position, altitude, distance, heart rate, cadence and speed.
- Handles gaps with special logic (decrease/increase ramps around pauses).
- Computes a sliding-window gradient (percent) clamped to [-25, 25].

## Overlay frame generation
- Renders per-row PNG frames showing time (localized), elevation, distance, cadence, heart rate and a speedometer.
- Customizable resolution via `OVERLAY_WIDTH` / `OVERLAY_HEIGHT` and fonts via `FONT_PATH`.
- Widget colors and styles are configurable through a JSON `WIDGETS_CONFIG` environment variable.

## Video merge
- Uses `ffmpeg` to overlay frames onto an input video stream.
- Supports `hevc_nvenc` and `libx265` encoder-specific options (bitrate, presets, pix_fmt, params).


### Configuration

The program uses `python-dotenv` and reads environment variables. See `sample.env` for defaults. Important environment variables:

- Telemetry files: `FIT_FILE`, `TCX_FILE`, `CSV_FILE`
- Overlay frames settings: `OUTPUT_FOLDER`, `OVERLAY_WIDTH`, `OVERLAY_HEIGHT`, `FONT_PATH`, `OVERLAY_DEBUG`, `WIDGETS_CONFIG`
- Data processing: `TIME_TOLERANCE`, `WINDOW_METERS`, `DECREASE_WINDOW`, `INCREASE_WINDOW`, `TIMESTAMP_STEP_MS`
- FFmpeg / PNGs + video merge: `INPUT_VIDEO_PATH`, `INPUT_VIDEO_FPS`, `OUTPUT_VIDEO_PATH`, `START_TIME`, `END_TIME`, `FFMPEG_ENCODER` and many encoder-specific options (see `sample.env`).

> Notes:
> - `TIMESTAMP_STEP_MS` controls the CSV sampling interval. The script derives `FFMPEG_FRAMERATE` as `int((1 / TIMESTAMP_STEP_MS) * 1000)` for frame timing when merging.
> - `OVERLAY_DEBUG` when `true`, causes the overlay generator to break after the first frame (useful for quick verification).

CSV output format (columns written by `main.py`)

- `time` — ISO 8601 timestamp with milliseconds, suffixed with `Z` (UTC)
- `lat` / `lon` — coordinates (float)
- `elevation` — altitude in meters (rounded to 2 decimals in the CSV output)
- `distance` — total distance in kilometers (rounded to 2 decimals)
- `heart_rate` — BPM
- `cadence` — RPM
- `speed_kmh` — speed in km/h (rounded to 2 decimals)
- `temperature` — ambient temperature (°C) from FIT nearest timestamp within `TIME_TOLERANCE`
- `gradient_percent` — computed gradient percentage (rounded to 2 decimals)
- `missing_row` — flag indicating whether the row was interpolated (1) or from real data (0)

#### Environment variables

All configuration lives in environment variables (see [sample.env](sample.env)). Short descriptions:

- **FIT_FILE**: Path to the input FIT telemetry file (temperature records).
- **TCX_FILE**: Path to the input TCX track file (trackpoints, GPS, speed).
- **CSV_FILE**: Path where the converted CSV will be written.
- **INPUT_VIDEO_PATH**: Path to the source video to be composited with overlays.
- **INPUT_VIDEO_FPS**: Frame rate of the input video (used in filter timing).
- **OUTPUT_VIDEO_PATH**: Path for the merged output video.
- **START_TIME**: Optional `-ss` seek start (HH:MM:SS) for the ffmpeg merge step.
- **END_TIME**: Optional `-to` end time for ffmpeg merge.
- **FFMPEG_ENCODER**: Encoder to use for output video (`hevc_nvenc` or `libx265`).
- **FFMPEG_BITRATE**: Target bitrate for GPU encoders (e.g. `30M`).
- **FFMPEG_MAXBITRATE**: Maximum bitrate for GPU encoders.
- **FFMPEG_BUFSIZE**: Buffer size for GPU encoders.
- **FFMPEG_PRESET**: Encoder preset (e.g. `slow`, `medium`).
- **FFMPEG_TUNE**: Encoder tuning (e.g. `hq`).
- **FFMPEG_PROFILE**: Encoder profile (e.g. `main10`).
- **FFMPEG_RC**: Rate-control mode for NVENC (e.g. `vbr` or `constqp`).
- **FFMPEG_RC_LOOKAHEAD**: NVENC lookahead frames (integer).
- **FFMPEG_QP**: Constant QP value for constant-QP modes.
- **FFMPEG_CQ**: CQ quality value for variable-bitrate modes.
- **FFMPEG_QMIN** / **FFMPEG_QMAX**: QP bounds for NVENC.
- **FFMPEG_CRF**: CRF value for `libx265` encoding.
- **FFMPEG_PIX_FMT**: Pixel format passed to ffmpeg (e.g. `p010le`).
- **FFMPEG_LIBX265_PARAMS**: Extra `x265-params` string for libx265.
- **TIME_TOLERANCE**: Maximum seconds tolerance to match FIT temperature to a TCX timestamp.
- **WINDOW_METERS**: Sliding-window distance (meters) used for gradient calculation.
- **DECREASE_WINDOW**: Seconds to ramp down speed/cadence at the start of a long gap.
- **INCREASE_WINDOW**: Seconds to ramp up speed/cadence at the end of a long gap.
- **TIMESTAMP_STEP_MS**: Milliseconds between CSV rows (default `500`). Controls interpolation sampling and derived `FFMPEG_FRAMERATE`.
- **OUTPUT_FOLDER**: Directory to write generated overlay frames (PNG sequence).
- **OVERLAY_WIDTH** / **OVERLAY_HEIGHT**: Overlay frame pixel dimensions.
- **FONT_PATH**: Absolute path to a TTF font used for rendering overlays.
- **OVERLAY_DEBUG**: When `True`, overlay generation stops after the first frame (quick verification).
- **WIDGETS_CONFIG**: JSON string with widget color configuration for each widget (see `sample.env` for example). Must be valid JSON; keys map to color tuples used by the overlay renderer.

#### Implementation details and behavior

- FIT parsing: the script uses `fitparse` to iterate `record` messages and collect timestamped temperature samples. Those are stored as `(datetime, temp)` pairs for nearest-neighbor lookup when building CSV rows.
- TCX parsing: the script uses `lxml.etree` to parse TCX trackpoints and extract Time, Position, AltitudeMeters, DistanceMeters, HeartRateBpm, Cadence and Speed.
- Interpolation and gap handling:
  - For gaps ≤ 1 second: the script linearly interpolates speed, cadence and other metrics between adjacent trackpoints.
  - For gaps > 1 second: it applies a three-phase strategy — ramp down (lasting `DECREASE_WINDOW` seconds), plateau (zero speed/cadence), ramp up (lasting `INCREASE_WINDOW` seconds) — to better represent pauses or signal loss.
  - Position, altitude, distance and heart rate are interpolated linearly based on progress between surrounding trackpoints.
- Gradient: computed using a deque sliding window over `WINDOW_METERS`; when less than a minimum distance the gradient is calculated with a smoothed fallback. The result is clamped to ±25%.

#### Overlay rendering

- Uses Pillow (`PIL.Image`, `ImageDraw`, `ImageFont`) to compose PNG frames.
- Widgets are drawn from a `WIDGETS_CONFIG` dictionary (loaded from JSON in environment). Colors are converted from lists to tuples when loaded.
- Timestamp localization: overlay displays the local time by converting the UTC timestamp to a configured `ZoneInfo` timezone (default `Europe/Athens` in the code). The CSV timestamps remain UTC.

#### Video merging

- `merge_video()` in `main.py` builds an `ffmpeg` command that:
  - Reads the frames as an image sequence with `-framerate` = `FFMPEG_FRAMERATE` and input `frames/frame_%05d.png`.
  - Optionally seeks (`-ss`) and sets an end time (`-to`) when `START_TIME` / `END_TIME` are set.
  - Composes the overlay onto the input video using `-filter_complex` and sets encoder options depending on `FFMPEG_ENCODER`.
%- Supported encoders in the implementation: `hevc_nvenc` and `libx265`. Other values will print an unsupported message and exit.

### Usage

1. Copy `sample.env` to `.env` and edit paths and settings to your environment.
2. Install dependencies (example using pip):
```bash
uv sync
```

3. Run `main.py` directly:

```bash
uv run main.py
```

The interactive menu matches the implementation and offers:

```
0. Exit
1. Convert FIT to CSV
2. Generate Overlay Frames
3. Merge Video with Overlay Frames
```

### Troubleshooting tips

- If `ffmpeg` is not found, install it or ensure it's in `PATH`.
- If fonts are missing, set `FONT_PATH` to a valid TTF on your system.
- If temperatures are missing in the CSV, confirm that the FIT file contains `record` messages with `timestamp` and `temperature` fields and that `TIME_TOLERANCE` is reasonable.

### Files to inspect

- [main.py](main.py) — implementation and entry point
- [sample.env](sample.env) — example environment variables and defaults

## License

This repository is distributed under the terms of the  AGPL-3.0 license, see [LICENSE](https://github.com/nlabiris/telemetry_dashboard/blob/master/LICENSE).
