import json

from PIL import Image, ImageDraw, ImageFont
from fitparse import FitFile
from lxml import etree
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import deque
from dotenv import load_dotenv
import csv
import os
import math
import subprocess
import shlex

load_dotenv()

NS = { "tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" }

# Telemetry files
FIT_FILE = os.getenv("FIT_FILE")
TCX_FILE = os.getenv("TCX_FILE")
CSV_FILE = os.getenv("CSV_FILE")

# Video merge settings
INPUT_VIDEO_PATH = os.getenv("INPUT_VIDEO_PATH")
INPUT_VIDEO_FPS = os.getenv("INPUT_VIDEO_FPS")
OUTPUT_VIDEO_PATH = os.getenv("OUTPUT_VIDEO_PATH")
START_TIME = os.getenv("START_TIME")
END_TIME = os.getenv("END_TIME")

# FFMPEG settings
FFMPEG_ENCODER=os.getenv("FFMPEG_ENCODER")
FFMPEG_BITRATE=os.getenv("FFMPEG_BITRATE")
FFMPEG_MAXBITRATE=os.getenv("FFMPEG_MAXBITRATE")
FFMPEG_BUFSIZE=os.getenv("FFMPEG_BUFSIZE")
FFMPEG_PRESET=os.getenv("FFMPEG_PRESET")
FFMPEG_TUNE=os.getenv("FFMPEG_TUNE")
FFMPEG_PROFILE=os.getenv("FFMPEG_PROFILE")
FFMPEG_RC=os.getenv("FFMPEG_RC")
FFMPEG_RC_LOOKAHEAD=os.getenv("FFMPEG_RC_LOOKAHEAD")
FFMPEG_QP=os.getenv("FFMPEG_QP")
FFMPEG_CQ=os.getenv("FFMPEG_CQ")
FFMPEG_QMIN=os.getenv("FFMPEG_QMIN")
FFMPEG_QMAX=os.getenv("FFMPEG_QMAX")
FFMPEG_CRF=os.getenv("FFMPEG_CRF")
FFMPEG_PIX_FMT=os.getenv("FFMPEG_PIX_FMT")
FFMPEG_LIBX265_PARAMS=os.getenv("FFMPEG_LIBX265_PARAMS")

# Processing settings
TIME_TOLERANCE = int(os.getenv("TIME_TOLERANCE", 2))
WINDOW_METERS = int(os.getenv("WINDOW_METERS", 20))
DECREASE_WINDOW = int(os.getenv("DECREASE_WINDOW", 5))
INCREASE_WINDOW = int(os.getenv("INCREASE_WINDOW", 5))
TIMESTAMP_STEP_MS = int(os.getenv("TIMESTAMP_STEP_MS", 500))
FFMPEG_FRAMERATE = int((1 / TIMESTAMP_STEP_MS) * 1000)

# Overlay frames settings
OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER")
OVERLAY_WIDTH = int(os.getenv("OVERLAY_WIDTH"))
OVERLAY_HEIGHT = int(os.getenv("OVERLAY_HEIGHT"))
FONT_PATH = os.getenv("FONT_PATH")
ACTIVE_THEME = os.getenv("ACTIVE_THEME", "base")

class FIT2CSV:
    fit: FitFile
    fit_points: list[tuple[datetime, float]]  # (time, temp)
    
    def __init__(self):
        self.fit = FitFile(FIT_FILE)
        self.fit_points = []  # (time, temp)
        pass

    def process(self):
        self._load_fit()

        # Load TCX
        tree = etree.parse(TCX_FILE)
        root = tree.getroot()

        # Gradient calculation
        points = deque()

        raw_points = []

        for tp in root.xpath(".//tcx:Trackpoint", namespaces=NS):
            tc_time = self._get_time(tp)
            lat, lon = self._get_coordinates(tp)
            alt = self._get_altitude(tp)
            dist = self._get_distance(tp)
            gradient = self._calculate_gradient(points, dist, alt)
            hr = self._get_heart_rate(tp)
            cadence = self._get_cadence(tp)
            speed, speed_kmh = self._get_speed(tp)
            temp = self._find_nearest_temp(tc_time)

            # Keep current row to compare with next one for gap filling
            raw_points.append({
                "time": tc_time,
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "dist": dist,
                "hr": hr,
                "cadence": cadence,
                "speed": speed,
                "speed_kmh": speed_kmh,
                "temp": temp,
                "gradient": gradient
            })

        # CSV setup
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time",
                "lat",
                "lon",
                "elevation",
                "distance",
                "heart_rate",
                "cadence",
                "speed_kmh",
                "temperature",
                "gradient_percent",
                "missing_row"
            ])

            start_time = raw_points[0]["time"]
            end_time = raw_points[-1]["time"]
            current_time = start_time
            i = 0

            # Step precisely every 500ms
            while current_time <= end_time:
                # Advance our pointer so p1 and p2 strictly bracket current_time
                while i < len(raw_points) - 1 and raw_points[i + 1]["time"] <= current_time:
                    i += 1

                previous_point = raw_points[i]
                next_point = raw_points[i + 1] if i + 1 < len(raw_points) else previous_point

                if previous_point["time"] == current_time or previous_point == next_point:
                    # Exact time match with a real point
                    self._write_row(writer, previous_point, is_missing=0)
                else:
                    # Interpolate between p1 and p2
                    gap = (next_point["time"] - previous_point["time"]).total_seconds()
                    offset = (current_time - previous_point["time"]).total_seconds()

                    interp_row = self._interpolate_points(current_time, previous_point, next_point, offset, gap)
                    self._write_row(writer, interp_row, is_missing=1)

                # Move to the next 500ms step
                current_time += timedelta(milliseconds=TIMESTAMP_STEP_MS)

    #region Helpers

    def _interpolate_points(self, current_time, previous_point, next_point, offset, gap):
        if gap <= 0: return previous_point
        progress = offset / gap

        # Speed and Cadence Normalization / Gap Logic
        if gap <= 1.0:
            # Standard smooth interpolation for normal 1-sec gaps
            speed = previous_point["speed_kmh"] + (next_point["speed_kmh"] - previous_point["speed_kmh"]) * progress
            cad = previous_point["cadence"] + (next_point["cadence"] - previous_point["cadence"]) * progress
        else:
            # Large gaps (pauses/losses)
            if offset <= DECREASE_WINDOW:
                speed = previous_point["speed_kmh"] * (1 - offset / DECREASE_WINDOW)
                cad = previous_point["cadence"] * (1 - offset / DECREASE_WINDOW)
            elif offset >= gap - INCREASE_WINDOW:
                prog = (offset - (gap - INCREASE_WINDOW)) / INCREASE_WINDOW
                speed = next_point["speed_kmh"] * prog
                cad = next_point["cadence"] * prog
            else:
                speed = 0
                cad = 0

        # Ensure we don't drop below 0
        speed = max(0, speed)
        cad = max(0, cad)
        
        # Linear interpolation for standard metrics mapping
        lat = previous_point["lat"] + (next_point["lat"] - previous_point["lat"]) * progress
        lon = previous_point["lon"] + (next_point["lon"] - previous_point["lon"]) * progress
        alt = previous_point["alt"] + (next_point["alt"] - previous_point["alt"]) * progress
        dist = previous_point["dist"] + (next_point["dist"] - previous_point["dist"]) * progress
        hr = previous_point["hr"] + (next_point["hr"] - previous_point["hr"]) * progress

        return {
            "time": current_time,
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "dist": dist,
            "hr": hr,
            "cadence": cad,
            "speed_kmh": speed,
            "temp": previous_point["temp"],
            "gradient": previous_point["gradient"]
        }

    def _load_fit(self):
        for record in self.fit.get_messages("record"):
            data = {d.name: d.value for d in record}

            if "timestamp" in data:
                ts = data["timestamp"]
                temp = data.get("temperature")

                if temp is not None:
                    self.fit_points.append((ts, temp))

    def _parse_time(self, ts):
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.000Z")

    def _find_nearest_temp(self, tc_time):
        closest_temp = None
        smallest_diff = float("inf")

        for fit_time, temp in self.fit_points:
            diff = abs((fit_time - tc_time).total_seconds())

            if diff < smallest_diff and diff <= TIME_TOLERANCE:
                smallest_diff = diff
                closest_temp = temp

        return closest_temp

    def _get_time(self, tp):
        time_elem = tp.find("tcx:Time", namespaces=NS)
        return self._parse_time(time_elem.text) if time_elem is not None else None

    def _get_coordinates(self, tp):
        lat = lon = 0
        pos_elem = tp.find("tcx:Position", namespaces=NS)
        if pos_elem is not None:
            lat_elem = pos_elem.find("tcx:LatitudeDegrees", namespaces=NS)
            lon_elem = pos_elem.find("tcx:LongitudeDegrees", namespaces=NS)
            lat = float(lat_elem.text) if lat_elem is not None else 0
            lon = float(lon_elem.text) if lon_elem is not None else 0
        return lat, lon

    def _get_altitude(self, tp):
        alt_elem = tp.find("tcx:AltitudeMeters", namespaces=NS)
        return float(alt_elem.text) if alt_elem is not None else 0

    def _get_distance(self, tp):
        dist_elem = tp.find("tcx:DistanceMeters", namespaces=NS)
        return float(dist_elem.text) if dist_elem is not None else 0

    def _calculate_gradient(self, points, dist, alt):
        points.append((dist, alt))
        while len(points) > 1 and (dist - points[0][0]) > WINDOW_METERS:
            points.popleft()

        if len(points) > 1:
            dist_old, alt_old = points[0]
            delta_dist = dist - dist_old
            delta_alt = alt - alt_old

            if delta_dist < 5:  # skip spikes
                gradient = 0
            else:
                gradient = (delta_alt / delta_dist) * 100
        else:
            gradient = 0

        return max(min(gradient, 25), -25)

    def _get_heart_rate(self, tp):
        hr_elem = tp.find("tcx:HeartRateBpm/tcx:Value", namespaces=NS)
        return int(hr_elem.text) if hr_elem is not None else 0

    def _get_cadence(self, tp):
        cad_elem = tp.find("tcx:Cadence", namespaces=NS)
        return int(cad_elem.text) if cad_elem is not None else 0

    def _get_speed(self, tp):
        speed_elem = tp.find(".//ns3:Speed", namespaces={"ns3": "*"})
        speed = float(speed_elem.text) if speed_elem is not None else 0
        speed_kmh = speed * 3.6 if speed is not None else 0
        return speed, speed_kmh

    def _write_row(self, writer, data, is_missing):
        writer.writerow([
            data["time"].isoformat(timespec='milliseconds') + "Z",
            data["lat"],
            data["lon"],
            round(data["alt"], 2),
            round(data["dist"]/1000, 2),
            round(data["hr"]),
            round(data["cadence"]),
            round(data["speed_kmh"], 2),
            data["temp"],
            round(data["gradient"], 2),
            is_missing
        ])

    #endregion

class Overlay:
    debug: bool = False
    local_tz: ZoneInfo
    utc_tz: ZoneInfo
    max_speed: float
    font_speed: ImageFont.FreeTypeFont
    font_time: ImageFont.FreeTypeFont
    font_value: ImageFont.FreeTypeFont
    font_label: ImageFont.FreeTypeFont
    font_unit: ImageFont.FreeTypeFont
    panel_bg: tuple[int, int, int, int]
    WIDGETS_CONFIG: dict[str, tuple[int, int, int]]
    THEMES_CONFIG: dict[str, dict[str, str]]

    def __init__(self, debug=False):
        self.debug = debug
        self.local_tz = ZoneInfo("Europe/Athens")
        self.utc_tz = ZoneInfo("UTC")

        # Load widget configuration
        self.WIDGETS_CONFIG = self._load_widget_config()
        self.THEMES_CONFIG = self._load_themes_config()

        if ACTIVE_THEME == "base" or ACTIVE_THEME == "modern":
            self.font_speed = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG[ACTIVE_THEME]["FONT_SIZE_SPEED"]))
            self.font_time = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG[ACTIVE_THEME]["FONT_SIZE_TIME"]))
            self.font_value = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG[ACTIVE_THEME]["FONT_SIZE_WIDGET_VALUE"]))
            self.font_label = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG[ACTIVE_THEME]["FONT_SIZE_WIDGET_LABEL"]))
            self.font_unit = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG[ACTIVE_THEME]["FONT_SIZE_WIDGET_UNIT"]))
        else:
            print(f"Unknown theme: {ACTIVE_THEME}. Defaulting to 'base'.")
            self.font_speed = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG["base"]["FONT_SIZE_SPEED"]))
            self.font_time = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG["base"]["FONT_SIZE_TIME"]))
            self.font_value = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG["base"]["FONT_SIZE_WIDGET_VALUE"]))
            self.font_label = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG["base"]["FONT_SIZE_WIDGET_LABEL"]))
            self.font_unit = ImageFont.truetype(FONT_PATH, int(self.THEMES_CONFIG["base"]["FONT_SIZE_WIDGET_UNIT"]))

        # Panel Background Color: (Red, Green, Blue, Alpha) -> 0 is fully transparent, 255 is solid
        if ACTIVE_THEME == "base":
            self.panel_bg = (0, 0, 0, 0)

    def process(self):
        self._create_overlay_directory()
        data = self._load_csv()
        print("Generating frames...")
        for i, row in enumerate(data):
            draw, img = self._setup_overlay()

            if ACTIVE_THEME == "base":
                self._draw_base_time(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["TIME_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["TIME_WIDGET_Y"]), self._draw_clock_icon, row['time'])
                self._draw_metric(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_Y"]), self._draw_mountain_icon, "Elevation", f"{float(row['elevation']):.0f}", "m")
                self._draw_metric(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_Y"]), self._draw_road_icon, "Total Distance", f"{float(row['distance']):.2f}", "km")
                self._draw_metric(draw, OVERLAY_WIDTH - int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_Y"]), self._draw_pedal_icon, "Cadence", f"{row['cadence']}", "rpm")
                self._draw_metric(draw, OVERLAY_WIDTH - int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_Y"]), self._draw_heart_icon, "Heart Rate", f"{row['heart_rate']}", "bpm")
                self._draw_speedometer(draw, center=(OVERLAY_WIDTH - int(self.THEMES_CONFIG[ACTIVE_THEME]["SPEEDOMETER_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["SPEEDOMETER_WIDGET_Y"])), radius=140, speed=float(row['speed_kmh']))

                # --- DRAW BACKGROUND PANELS ---
                # Top-Left Panel (Timestamp)
                # draw.rounded_rectangle([30, 20, 480, 100], radius=15, fill=panel_bg)
                
                # Middle-Left Panel (Elevation & Distance)
                # draw.rounded_rectangle([30, 220, 380, 580], radius=20, fill=panel_bg)
                
                # Middle-Right Panel (Cadence & Heart Rate)
                # draw.rounded_rectangle([width-430, 220, width-50, 580], radius=20, fill=panel_bg)
                
                # Bottom-Right Panel (Speedometer)
                # draw.rounded_rectangle([width-430, height-430, width-70, height-70], radius=25, fill=panel_bg)
            elif ACTIVE_THEME == "modern":
                # 1. TOP LEFT: Timestamp
                self._draw_modern_time(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["TIME_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["TIME_WIDGET_Y"]), row['time'])

                # 2. MIDDLE LEFT: Stacked modular data cards (Elevation & Distance)
                self._draw_modern_card(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_Y"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_PANEL_WIDTH"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["ELEVATION_WIDGET_PANEL_HEIGHT"]), self._draw_mountain_icon, "ELEVATION", f"{float(row['elevation']):.0f}", "m", is_critical=(float(row['elevation']) > 1000))
                self._draw_modern_card(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_Y"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_PANEL_WIDTH"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["DISTANCE_WIDGET_PANEL_HEIGHT"]), self._draw_road_icon, "DISTANCE", f"{float(row['distance']):.2f}", "km")

                # 3. BOTTOM LEFT / FLOATING CORNER: Performance metrics
                self._draw_modern_card(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_Y"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_PANEL_WIDTH"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["CADENCE_WIDGET_PANEL_HEIGHT"]), self._draw_pedal_icon, "CADENCE", f"{row['cadence']}", "rpm", is_critical=(int(row['cadence']) > 120))
                self._draw_modern_card(draw, int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_X"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_Y"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_PANEL_WIDTH"]), int(self.THEMES_CONFIG[ACTIVE_THEME]["HEARTRATE_WIDGET_PANEL_HEIGHT"]), self._draw_heart_icon, "HEART RATE", f"{row['heart_rate']}", "bpm", is_critical=(int(row['heart_rate']) > 160))

                # 4. BOTTOM RIGHT: High-End Circular HUD Dial Speedometer
                # self._draw_hud_speedometer(draw, img, center=(OVERLAY_WIDTH - 220, OVERLAY_HEIGHT - 220), radius=160, speed=float(row['speed_kmh']))
                self._draw_speedometer(draw, center=(OVERLAY_WIDTH-int(self.THEMES_CONFIG[ACTIVE_THEME]["SPEEDOMETER_WIDGET_X"]), OVERLAY_HEIGHT - int(self.THEMES_CONFIG[ACTIVE_THEME]["SPEEDOMETER_WIDGET_Y"])), radius=140, speed=float(row['speed_kmh']))
            else:
                print(f"Unknown theme: {ACTIVE_THEME}. Skipping frame generation.")
                break

            self._save_overlay(img, i)
            self._report_progress(i)

            # For debugging purposes, break after the first frame to verify output before processing the entire dataset
            if self.debug:
                break

        return 1 if self.debug else len(data)

    #region Helpers

    def _load_widget_config(self):
        # 1. Fetch the raw JSON string from your environment
        raw_json = os.getenv("WIDGETS_CONFIG")

        if raw_json:
            # 2. Parse the string into a dictionary of lists
            raw_colors = json.loads(raw_json)

            # 3. Convert the lists into tuples so your graphics library gets exactly what it expects
            colors = {key: tuple(value) for key, value in raw_colors.items()}
        else:
            colors = {}
            print("Warning: WIDGETS_CONFIG not found in environment!")

        return colors
    
    def _load_themes_config(self):
        # 1. Fetch the raw JSON string from your environment
        raw_json = os.getenv("THEMES_CONFIG")

        if raw_json:
            # 2. Parse the string into a dictionary of dictionaries
            themes = json.loads(raw_json)
        else:
            themes = {}
            print("Warning: THEMES_CONFIG not found in environment!")

        return themes

    def _load_csv(self):
        data = []
        with open(CSV_FILE, newline='') as f:
            reader = csv.DictReader(f)
            self.max_speed = 0
            for row in reader:
                speed = float(row['speed_kmh'])
                if speed > self.max_speed:
                    self.max_speed = speed
                data.append(row)
        return data
    
    def _create_overlay_directory(self):
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    def _setup_overlay(self):
        img = Image.new("RGBA", (OVERLAY_WIDTH, OVERLAY_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        return draw, img
    
    def _save_overlay(self, img, index):
        img.save(f"{OUTPUT_FOLDER}/frame_{index:05d}.png")

    def _report_progress(self, index):
        if self.debug:
            print(f"Processed 1 frame...")
            return

        if index % 100 == 0:
            print(f"Processed {index} frames...")

    def _draw_clock_icon(self, draw, x, y):
        draw.ellipse((x, y, x+24, y+24), outline=self.WIDGETS_CONFIG["CLOCK_ICON_COLOR_OUTLINE"], width=3)
        draw.line((x+12, y+12, x+12, y+5), fill=self.WIDGETS_CONFIG["CLOCK_ICON_COLOR_LINES"], width=3)
        draw.line((x+12, y+12, x+18, y+12), fill=self.WIDGETS_CONFIG["CLOCK_ICON_COLOR_LINES"], width=3)

    def _draw_mountain_icon(self, draw, x, y):
        draw.polygon([(x+12, y), (x, y+24), (x+24, y+24)], fill=self.WIDGETS_CONFIG["ELEVATION_ICON_COLOR_PEAK"])
        draw.polygon([(x+12, y), (x+6, y+12), (x+12, y+15), (x+18, y+12)], fill=self.WIDGETS_CONFIG["ELEVATION_ICON_COLOR_MOUNTAIN"])

    def _draw_road_icon(self, draw, x, y):
        draw.polygon([(x+8, y), (x+16, y), (x+24, y+24), (x, y+24)], fill=self.WIDGETS_CONFIG["DISTANCE_ICON_COLOR_PAVEMENT"])
        draw.line((x+12, y+4, x+12, y+10), fill=self.WIDGETS_CONFIG["DISTANCE_ICON_COLOR_LINES"], width=2)
        draw.line((x+12, y+14, x+12, y+20), fill=self.WIDGETS_CONFIG["DISTANCE_ICON_COLOR_LINES"], width=2)

    def _draw_pedal_icon(self, draw, x, y, pedal_icon=True):
        if pedal_icon:
            draw.line((x-4, y+2, x+4, y+2), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)
            draw.ellipse((x+4, y+4, x+20, y+20), outline=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_CRANK"], width=3)
            draw.line((x+20, y+20, x+2, y+2), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)
            draw.line((x+20, y+20, x+26, y+20), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)
            draw.ellipse((x+10, y+10, x+14, y+14), outline=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_CRANK"], width=3)
        else:
            draw.ellipse((x+4, y+4, x+20, y+20), outline=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_CRANK"], width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)
            draw.ellipse((x, y, x+4, y+4), fill=self.WIDGETS_CONFIG["CADENCE_ICON_COLOR_PEDALS"], width=3)

    def _draw_heart_icon(self, draw, x, y):
        # Simplified diamond/heart
        draw.polygon([(x+12, y+24), (x, y+8), (x+6, y), (x+12, y+6), (x+18, y), (x+24, y+8)], fill=self.WIDGETS_CONFIG["HEARTRATE_ICON_COLOR"])

    def _draw_speedometer(self, draw, center, radius, speed):
        x, y = center
        start_angle = 135 # Bottom left
        end_angle = 405 # Bottom right
        gauge_width = 20

        # 1. Background Arc (Dark grey track)
        draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, end_angle, fill=self.WIDGETS_CONFIG["SPEEDOMETER_ICON_ARC_COLOR"], width=gauge_width)

        # 2. Colored Speed Arc
        speed_pct = min(max(speed / self.max_speed, 0), 1.0) # Clamp between 0 and 1
        current_angle = start_angle + (speed_pct * (end_angle - start_angle))
        
        # Gradient logic (Green -> Yellow -> Red)
        if speed_pct < 0.5:
            color = self.WIDGETS_CONFIG["SPEEDOMETER_ICON_SPEED_COLOR_GREEN"]  # Green
        elif speed_pct < 0.8:
            color = self.WIDGETS_CONFIG["SPEEDOMETER_ICON_SPEED_COLOR_YELLOW"] # Yellow
        else:
            color = self.WIDGETS_CONFIG["SPEEDOMETER_ICON_SPEED_COLOR_RED"]  # Red

        if current_angle > start_angle:
            draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, current_angle, fill=color, width=gauge_width)

        # 3. Outer ticks for style
        for tick_angle in range(start_angle, end_angle + 1, 27):
            rad = math.radians(tick_angle)
            in_x = x + (radius - 5) * math.cos(rad)
            in_y = y + (radius - 5) * math.sin(rad)
            out_x = x + (radius + 10) * math.cos(rad)
            out_y = y + (radius + 10) * math.sin(rad)
            draw.line((in_x, in_y, out_x, out_y), fill=self.WIDGETS_CONFIG["SPEEDOMETER_ICON_ARC_TICKS_COLOR"], width=3)

        # 4. Center Speed Text
        speed_str = f"{speed:.1f}"
        speed_w = draw.textlength(speed_str, font=self.font_speed)
        draw.text((x - speed_w/2, y - 60), speed_str, font=self.font_speed, fill=self.WIDGETS_CONFIG["SPEEDOMETER_ICON_VALUE_COLOR"])
        
        unit_str = "km/h"
        unit_w = draw.textlength(unit_str, font=self.font_unit)
        draw.text((x - unit_w/2, y + 45), unit_str, font=self.font_unit, fill=self.WIDGETS_CONFIG["SPEEDOMETER_ICON_UNIT_COLOR"])

    def _draw_dji_speedometer(self, draw, center, radius, speed):
        cx, cy = center
        # The gauge in the image starts around bottom-left and ends bottom-right
        start_angle = 140
        end_angle = 400
        total_angle = end_angle - start_angle
        
        # --- 1. Draw Outer Bezels ---
        # Thick light-grey outer track
        draw.arc([cx - radius, cy - radius, cx + radius, cy + radius], 
                 start_angle, end_angle, fill=(200, 200, 200, 180), width=18)
        
        # Thin white line on the very outside edge
        draw.arc([cx - radius - 10, cy - radius - 10, cx + radius + 10, cy + radius + 10], 
                 start_angle, end_angle, fill=(230, 230, 230, 200), width=3)

        # --- 2. Draw Segmented Colored Inner Track ---
        inner_radius = radius - 18
        track_width = 14
        gap_degrees = 4  # The visual gap between the colored segments
        
        # Define the segments based on your image: (start_pct, end_pct, RGBA_color)
        segments = [
            (0.00, 0.25, (130, 200, 60, 255)),   # Green
            (0.25, 0.55, (245, 190, 20, 255)),   # Yellow
            (0.55, 0.85, (235, 110, 20, 255)),   # Orange
            (0.85, 1.00, (210, 40, 40, 255))     # Red
        ]
        
        for start_pct, end_pct, color in segments:
            seg_start = start_angle + (start_pct * total_angle)
            # Apply the gap to the end of the segment, unless it's the very last segment
            seg_end = start_angle + (end_pct * total_angle) - (gap_degrees if end_pct < 1.0 else 0)
            
            if seg_end > seg_start:
                draw.arc([cx - inner_radius, cy - inner_radius, cx + inner_radius, cy + inner_radius],
                         seg_start, seg_end, fill=color, width=track_width)

        # --- 3. Draw the Pointer (Inward-pointing Wedge) ---
        # Clamp speed to max to avoid pointer going off the gauge
        speed_pct = min(max(speed / max(self.max_speed, 1.0), 0.0), 1.0)
        current_angle = start_angle + (speed_pct * total_angle)
        rad = math.radians(current_angle)

        # The pointer in the image has its wide base on the outside and point on the inside
        tip_radius = radius - 35
        base_radius = radius + 25
        base_width = math.radians(6) # How wide the back of the pointer is
        
        # Calculate polygon vertices
        pointer_tip_x = cx + tip_radius * math.cos(rad)
        pointer_tip_y = cy + tip_radius * math.sin(rad)
        
        base_x1 = cx + base_radius * math.cos(rad - base_width)
        base_y1 = cy + base_radius * math.sin(rad - base_width)
        base_x2 = cx + base_radius * math.cos(rad + base_width)
        base_y2 = cy + base_radius * math.sin(rad + base_width)
        
        draw.polygon([(pointer_tip_x, pointer_tip_y), (base_x1, base_y1), (base_x2, base_y2)], 
                     fill=(255, 255, 255, 255))

        # --- 4. Draw Core Information Center Callouts ---
        # Note: The image shows whole numbers. You can change it to f"{speed:.1f}" if you prefer decimals.
        speed_str = f"{int(speed)}" 
        speed_w = draw.textlength(speed_str, font=self.font_speed)
        
        # Center the main speed text
        draw.text((cx - speed_w / 2, cy - 40), speed_str, font=self.font_speed, fill=(255, 255, 255, 255))
        
        # Draw the unit right at the bottom right tail of the arc, mimicking the image
        unit_str = "km/h" # Substituting 'mph' for your dataset's km/h
        draw.text((cx + radius - 60, cy + radius - 10), unit_str, font=self.font_unit, fill=(255, 255, 255, 255))

    def _draw_hud_speedometer(self, draw, img, center, radius, speed):
        """
        Renders a futuristic HUD style circular glass gauge.
        Features a dual ring track, a smooth sweeping tail gradient bar, and central numeric projection.
        """
        cx, cy = center
        # Configuration angles mapping clockwise sweeps
        start_angle, end_angle = 150, 390
        total_angle_span = end_angle - start_angle
        
        # Speed ratio calculations 
        speed_pct = min(max(speed / max(self.max_speed, 1.0), 0.0), 1.0)
        current_angle = start_angle + (speed_pct * total_angle_span)
        
        # 1. Draw outer sleek ambient shadow bounding background ring
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=self.WIDGETS_CONFIG["PANEL_BG"], width=32)
        
        # 2. Draw interior translucent passive telemetry tracking arc
        draw.arc([cx - radius + 8, cy - radius + 8, cx + radius - 8, cy + radius - 8], 
                 start_angle, end_angle, fill=self.WIDGETS_CONFIG["GAUGE_TRACK"], width=12)

        # 3. Advanced Multi-step Speed Sweeping Gradient Trace Line
        # Splitting progress into minor steps replicates custom smooth anti-aliased pipelines
        steps = int(max(1, speed_pct * 45))
        base_color = self.WIDGETS_CONFIG["ACCENT_CYAN"]
        
        for idx in range(steps + 1):
            step_progress = idx / 45
            angle_step_start = start_angle + (step_progress * total_angle_span)
            # Ensure the arc steps do not exceed calculations
            angle_step_end = min(angle_step_start + (total_angle_span / 45), current_angle)
            
            # Dynamic fading tail alpha calculus
            alpha = int(55 + (idx / steps) * 200) if steps > 0 else 255
            step_color = (base_color[0], base_color[1], base_color[2], alpha)
            
            if angle_step_end > angle_step_start:
                draw.arc([cx - radius + 8, cy - radius + 8, cx + radius - 8, cy + radius - 8], 
                         angle_step_start, angle_step_end, fill=step_color, width=12)

        # 4. Draw Radial HUD Digital Ticks Around Rim Geometry
        for tick_angle in range(start_angle, end_angle + 1, 15):
            rad = math.radians(tick_angle)
            # Switch accent thresholds on high speed intervals
            is_active = (tick_angle <= current_angle)
            tick_color = self.WIDGETS_CONFIG["ACCENT_CYAN"] if is_active else self.WIDGETS_CONFIG["GAUGE_TRACK"]
            
            r_in = radius - 24 if is_active else radius - 20
            r_out = radius - 12
            
            in_x, in_y = cx + r_in * math.cos(rad), cy + r_in * math.sin(rad)
            out_x, out_y = cx + r_out * math.cos(rad), cy + r_out * math.sin(rad)
            draw.line((in_x, in_y, out_x, out_y), fill=tick_color, width=3 if is_active else 2)

        # 5. Core Information Center Callouts
        speed_str = f"{speed:.1f}"
        speed_w = draw.textlength(speed_str, font=self.font_speed)
        draw.text((cx - speed_w / 2, cy - 65), speed_str, font=self.font_speed, fill=self.WIDGETS_CONFIG["TEXT_MAIN"])
        
        unit_str = "KM/H"
        unit_w = draw.textlength(unit_str, font=self.font_unit)
        draw.text((cx - unit_w / 2, cy + 30), unit_str, font=self.font_unit, fill=self.WIDGETS_CONFIG["TEXT_MUTED"])

    def _draw_metric(self, draw, x, y, icon_func, label, value, unit):
        # Draw Icon & Label
        icon_func(draw, x, y)
        draw.text((x + 35, y), label, font=self.font_label, fill=self.WIDGETS_CONFIG["WIDGET_ICON_LABEL_COLOR"])
        
        # Draw Value
        draw.text((x, y + 35), value, font=self.font_value, fill=self.WIDGETS_CONFIG["WIDGET_ICON_VALUE_COLOR"])
        val_w = draw.textlength(value, font=self.font_value)
        
        # Draw Unit right next to the value
        draw.text((x + val_w + 10, y + 55), unit, font=self.font_unit, fill=self.WIDGETS_CONFIG["WIDGET_ICON_UNIT_COLOR"])

    def _draw_modern_card(self, draw, x, y, w, h, icon_func, label, value, unit, is_critical=False):
        accent_color = self.WIDGETS_CONFIG["ACCENT_MAGENTA"] if is_critical else self.WIDGETS_CONFIG["ACCENT_CYAN"]
        
        # Base container structure
        draw.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=self.WIDGETS_CONFIG["PANEL_BG"])
        draw.rounded_rectangle([x + 6, y + 6, x + w - 6, y + h - 6], radius=12, fill=self.WIDGETS_CONFIG["CARD_BG"])
        
        # Design anchor strip accent on the left border edges
        draw.rounded_rectangle([x + 10, y + 20, x + 16, y + h - 20], radius=3, fill=accent_color)
        
        # Labels and contextual data
        icon_func(draw, x + 40, y + 20)
        draw.text((x + 75, y + 15), label, font=self.font_label, fill=self.WIDGETS_CONFIG["TEXT_MUTED"])
        
        # Values with dynamic padding alignment offset rules
        draw.text((x + 40, y + 45), value, font=self.font_value, fill=self.WIDGETS_CONFIG["TEXT_MAIN"])
        val_w = draw.textlength(value, font=self.font_value)
        draw.text((x + 45 + val_w, y + 64), unit, font=self.font_unit, fill=accent_color)

    def _draw_base_time(self, draw, x, y, icon_func, value):
        # Draw Icon & Label
        icon_func(draw, x, y)
        
        if self.THEMES_CONFIG.get(ACTIVE_THEME, {}).get("CLOCK_WIDGET_THEME") == "iso":
            utctime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=self.utc_tz)
            localtime = utctime.astimezone(self.local_tz)
            draw.text((x + 50, y - 10), f"{localtime.strftime('%Y-%m-%d %H:%M:%S')}", font=self.font_time, fill=self.WIDGETS_CONFIG["WIDGET_ICON_VALUE_COLOR"])
        elif self.THEMES_CONFIG.get(ACTIVE_THEME, {}).get("CLOCK_WIDGET_THEME") == "datetime":
            utctime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=self.utc_tz)
            localtime = utctime.astimezone(self.local_tz)
            time_str = localtime.strftime('%H:%M:%S')
            date_str = localtime.strftime('%b %d, %Y').upper()

            draw.text((x + 50, y - 10), time_str, font=self.font_time, fill=self.WIDGETS_CONFIG["WIDGET_ICON_VALUE_COLOR"])
            draw.text((x + 50, y + 25), date_str, font=self.font_label, fill=self.WIDGETS_CONFIG["WIDGET_ICON_VALUE_COLOR"])

    def _draw_modern_time(self, draw, x, y, iso_timestamp):
        utctime = datetime.strptime(iso_timestamp, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=self.utc_tz)
        localtime = utctime.astimezone(self.local_tz)
        time_str = localtime.strftime('%H:%M:%S')
        date_str = localtime.strftime('%b %d, %Y').upper()

        if self.THEMES_CONFIG.get(ACTIVE_THEME, {}).get("CLOCK_WIDGET_THEME") == "minimal":
            # Render clean unified bounding pill
            w_time = draw.textlength(time_str, font=self.font_time)
            w_date = draw.textlength(date_str, font=self.font_label)
            total_w = max(w_time, w_date) + 60
            
            draw.rounded_rectangle([x, y, x + total_w, y + 90], radius=12, fill=self.WIDGETS_CONFIG["PANEL_BG"])
            self._draw_clock_icon(draw, x + 15, y + 25)
            
            draw.text((x + 50, y + 8), time_str, font=self.font_time, fill=self.WIDGETS_CONFIG["TEXT_MAIN"])
            draw.text((x + 50, y + 45), date_str, font=self.font_label, fill=self.WIDGETS_CONFIG["ACCENT_CYAN"])
        elif self.THEMES_CONFIG.get(ACTIVE_THEME, {}).get("CLOCK_WIDGET_THEME") == "panel":
            # Render clean unified bounding pill
            draw.textlength(time_str, font=self.font_time)
            draw.textlength(date_str, font=self.font_label)

            # Base container structure
            draw.rounded_rectangle([x, y, x + 290, y + 140], radius=16, fill=self.WIDGETS_CONFIG["PANEL_BG"])
            draw.rounded_rectangle([x + 6, y + 6, x + 290 - 6, y + 140 - 6], radius=12, fill=self.WIDGETS_CONFIG["CARD_BG"])
            
            # Design anchor strip accent on the left border edges
            draw.rounded_rectangle([x + 10, y + 20, x + 16, y + 140 - 20], radius=3, fill=self.WIDGETS_CONFIG["ACCENT_CYAN"])
            
            # Labels and contextual data
            self._draw_clock_icon(draw, x + 40, y + 20)
            draw.text((x + 75, y + 15), "TIMESTAMP", font=self.font_label, fill=self.WIDGETS_CONFIG["TEXT_MUTED"])
            
            draw.text((x + 45, y + 50), time_str, font=self.font_time, fill=self.WIDGETS_CONFIG["TEXT_MAIN"])
            draw.text((x + 45, y + 90), date_str, font=self.font_label, fill=self.WIDGETS_CONFIG["ACCENT_CYAN"])

    #endregion


def display_menu():
    """Display the main menu and return user's choice."""
    print("\n" + "="*50)
    print("ACTIVITY DATA PROCESSOR")
    print("="*50)
    print("0. Exit")
    print("1. Convert FIT to CSV")
    print("2. Generate Overlay Frames")
    print("3. Merge Video with Overlay Frames")
    print("="*50)
    choice = input("Select an option (0-3): ").strip()
    return choice


def merge_video():
    """Merge input video with overlay frames using ffmpeg."""

    cmd = ["ffmpeg", "-hide_banner"]

    # Input frames as a video stream with specified framerate
    cmd.extend([
        "-framerate", str(FFMPEG_FRAMERATE),
        "-i", "frames/frame_%05d.png"
    ])
    
    # Add start time if set
    if START_TIME:
        cmd.extend(["-ss", START_TIME])
    
    # Add end time if set
    if END_TIME:
        cmd.extend(["-to", END_TIME])
    
    # Main input video and overlay filter
    cmd.extend([
        "-i", INPUT_VIDEO_PATH,
        "-filter_complex", f"[0:v]fps={INPUT_VIDEO_FPS}[overlay_stream]; [1:v][overlay_stream] overlay=0:0:eof_action=pass:format=yuv420p10",
    ])

    # Video encoding settings
    cmd.extend([
        "-c:v", FFMPEG_ENCODER
    ])

    if FFMPEG_ENCODER == "hevc_nvenc":
        cmd.extend([
            "-b:v", FFMPEG_BITRATE,
            "-maxrate:v", FFMPEG_MAXBITRATE,
            "-bufsize:v", FFMPEG_BUFSIZE,
            "-preset", FFMPEG_PRESET,
            "-tune", FFMPEG_TUNE,
            "-profile:v", FFMPEG_PROFILE,
            "-rc", FFMPEG_RC,
            "-rc-lookahead", FFMPEG_RC_LOOKAHEAD,
            "-cq", FFMPEG_CQ,
            "-qmin", FFMPEG_QMIN,
            "-qmax", FFMPEG_QMAX,
            "-pix_fmt", FFMPEG_PIX_FMT,
            "-c:a", "copy",
            "-tag:v", "hvc1", # to make compatible with Apple "industry standard" H.265
            "-movflags", "+frag_keyframe+empty_moov",
            OUTPUT_VIDEO_PATH
        ])
    elif FFMPEG_ENCODER == "libx265":
        cmd.extend([
            "-crf", FFMPEG_CRF,
            "-preset", FFMPEG_PRESET,
            "-pix_fmt", FFMPEG_PIX_FMT,
            "-x265-params", FFMPEG_LIBX265_PARAMS,
            "-c:a", "copy",
            "-tag:v", "hvc1", # to make compatible with Apple "industry standard" H.265
            "-movflags", "+frag_keyframe+empty_moov",
            OUTPUT_VIDEO_PATH
        ])
    else:
        print(f"Unsupported encoder: {FFMPEG_ENCODER}")
        return

    try:
        print(f"\nExecuting ffmpeg...")
        print(f"Command: {shlex.join(cmd)}\n")
        subprocess.run(cmd, check=True)
        print(f"\nVideo merge complete: {OUTPUT_VIDEO_PATH}")
    except subprocess.CalledProcessError as e:
        print(f"Error during video merge: {e}")
    except FileNotFoundError:
        print("Error: ffmpeg not found. Please ensure ffmpeg is installed.")
    except KeyboardInterrupt:
        print("\n\nVideo merge cancelled by user.")


def main():
    """Main entry point with menu-driven interface."""
    try:
        while True:
            choice = display_menu()
            if choice == "1":
                print("\n>>> Running FIT to CSV conversion...")
                print(f"\n>>> Converting file: {FIT_FILE}")
                try:
                    converter = FIT2CSV()
                    converter.process()
                    print(f"CSV created: {CSV_FILE}")
                except Exception as e:
                    print(f"Error during conversion: {e}")
            elif choice == "2":
                print("\n>>> Generating overlay frames...")
                try:
                    overlay_debug = os.getenv("OVERLAY_DEBUG") == "True"
                    overlay = Overlay(debug=overlay_debug)
                    overlay_count = overlay.process()
                    print(f"Done! Generated {overlay_count} frames in '{OUTPUT_FOLDER}'")
                except Exception as e:
                    print(f"Error during overlay generation: {e}")
            elif choice == "3":
                print("\n>>> Merging video with overlay frames...")
                try:
                    merge_video()
                except Exception as e:
                    print(f"Error during video merge: {e}")
            elif choice == "0":
                print("\nExiting... Goodbye!")
                break
            
            else:
                print("\nInvalid option. Please select 0-3.")
    except KeyboardInterrupt:
        print("\n\nProgram interrupted by user. Exiting...")


if __name__ == "__main__":
    main()