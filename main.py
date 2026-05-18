# exiftool -CreateDate /mnt/v/media/video/dji/internal_storage/DCIM/DJI_001/DJI_20260322110703_0004_D.MP4
# ffmpeg -i /mnt/v/media/video/dji/internal_storage/DCIM/DJI_001/DJI_20260322110703_0004_D.MP4 -framerate 2 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v hevc_nvenc -rc vbr -cq 19 -preset slow -c:a copy output_nvenc.mp4
# ffmpeg -i /mnt/v/media/video/dji/internal_storage/DCIM/DJI_001/DJI_20260322110703_0004_D.MP4 -framerate 1 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v libx265 -crf 18 -preset slow -c:a copy output.mp4

#exiftool -CreateDate DJI_20260409152956_0001_D.MP4 - Create Date: 2026:04:09 12:29:57
#exiftool -CreateDate DJI_20260409161406_0002_D.MP4 - Create Date: 2026:04:09 13:14:08
#exiftool -CreateDate DJI_20260409162615_0004_D.MP4 - Create Date: 2026:04:09 13:26:16
#exiftool -CreateDate DJI_20260409165359_0005_D.MP4 - Create Date: 2026:04:09 13:54:00

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
FFMPEG_RC=os.getenv("FFMPEG_RC")
FFMPEG_TUNE=os.getenv("FFMPEG_TUNE")
FFMPEG_MULTIPASS=os.getenv("FFMPEG_MULTIPASS")
FFMPEG_CQ=os.getenv("FFMPEG_CQ")

# Processing settings
TIME_TOLERANCE = 2
WINDOW_METERS = 50
DECREASE_WINDOW = 5
INCREASE_WINDOW = 5
TIMESTAMP_STEP_MS = 500
FFMPEG_FRAMERATE = int((1 / TIMESTAMP_STEP_MS) * 1000)

# Overlay frames settings
OUTPUT_FOLDER = "frames"
OVERLAY_WIDTH = 1920
OVERLAY_HEIGHT = 1080
FONT_PATH = os.getenv("FONT_PATH")

# Widget settings
WIDGET_ICON_VALUE_COLOR = (255, 255, 255)
WIDGET_ICON_LABEL_COLOR = (220, 220, 220)
WIDGET_ICON_UNIT_COLOR = (220, 220, 220)

CLOCK_ICON_COLOR_OUTLINE = (0, 0, 0)
CLOCK_ICON_COLOR_LINES = (255, 255, 255)

ELEVATION_ICON_COLOR_PEAK = (0, 0, 0)
ELEVATION_ICON_COLOR_MOUNTAIN = (255, 255, 255)

DISTANCE_ICON_COLOR_PAVEMENT = (0, 0, 0)
DISTANCE_ICON_COLOR_LINES = (255, 255, 255)

CADENCE_ICON_COLOR_CRANK = (0, 0, 0)
CADENCE_ICON_COLOR_PEDALS = (255, 255, 255)

HEARTRATE_ICON_COLOR = (255, 50, 50)

SPEEDOMETER_ICON_ARC_COLOR = (60, 60, 60, 255)
SPEEDOMETER_ICON_ARC_TICKS_COLOR = (150, 150, 150)
SPEEDOMETER_ICON_SPEED_COLOR_GREEN = (50, 255, 50)
SPEEDOMETER_ICON_SPEED_COLOR_YELLOW = (255, 200, 50)
SPEEDOMETER_ICON_SPEED_COLOR_RED = (255, 50, 50)
SPEEDOMETER_ICON_VALUE_COLOR = (255, 255, 255)
SPEEDOMETER_ICON_UNIT_COLOR = (200, 200, 200)


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
    font_metrics: ImageFont.FreeTypeFont
    font_labels: ImageFont.FreeTypeFont
    panel_bg: tuple[int, int, int, int]

    def __init__(self, debug=False):
        self.debug = debug
        self.local_tz = ZoneInfo("Europe/Athens")
        self.utc_tz = ZoneInfo("UTC")
        font_path = FONT_PATH
        self.font_speed = ImageFont.truetype(font_path, 85)
        self.font_time = ImageFont.truetype(font_path, 40)
        self.font_metrics = ImageFont.truetype(font_path, 50)
        self.font_labels = ImageFont.truetype(font_path, 25)

        # Panel Background Color: (Red, Green, Blue, Alpha) -> 0 is fully transparent, 255 is solid
        self.panel_bg = (0, 0, 0, 0)

    def process(self):
        self._create_overlay_directory()
        data = self._load_csv()
        print("Generating frames...")
        for i, row in enumerate(data):
            draw, img = self._setup_overlay()

            # --- DRAW BACKGROUND PANELS ---
            # Top-Left Panel (Timestamp)
            # draw.rounded_rectangle([30, 20, 480, 100], radius=15, fill=panel_bg)
            
            # Middle-Left Panel (Elevation & Distance)
            # draw.rounded_rectangle([30, 220, 380, 580], radius=20, fill=panel_bg)
            
            # Middle-Right Panel (Cadence & Heart Rate)
            # draw.rounded_rectangle([width-430, 220, width-50, 580], radius=20, fill=panel_bg)
            
            # Bottom-Right Panel (Speedometer)
            # draw.rounded_rectangle([width-430, height-430, width-70, height-70], radius=25, fill=panel_bg)

            self._draw_time_metric(draw, 30, 20, self._draw_clock_icon, row['time'])
            self._draw_metric(draw, 30, 100, self._draw_mountain_icon, "Elevation", f"{float(row['elevation']):.0f}", "m")
            self._draw_metric(draw, 30, 220, self._draw_road_icon, "Total Distance", f"{float(row['distance']):.2f}", "km")
            self._draw_metric(draw, OVERLAY_WIDTH-200, 50, self._draw_pedal_icon, "Cadence", f"{row['cadence']}", "rpm")
            self._draw_metric(draw, OVERLAY_WIDTH-200, 180, self._draw_heart_icon, "Heart Rate", f"{row['heart_rate']}", "bpm")
            self._draw_speedometer(draw, center=(OVERLAY_WIDTH-170, OVERLAY_HEIGHT-150), radius=140, speed=float(row['speed_kmh']))
            self._save_overlay(img, i)
            self._report_progress(i)

            # For debugging purposes, break after the first frame to verify output before processing the entire dataset
            if self.debug:
                break

        return 1 if self.debug else len(data)

    #region Helpers

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
        draw.ellipse((x, y, x+24, y+24), outline=CLOCK_ICON_COLOR_OUTLINE, width=3)
        draw.line((x+12, y+12, x+12, y+5), fill=CLOCK_ICON_COLOR_LINES, width=3)
        draw.line((x+12, y+12, x+18, y+12), fill=CLOCK_ICON_COLOR_LINES, width=3)

    def _draw_mountain_icon(self, draw, x, y):
        draw.polygon([(x+12, y), (x, y+24), (x+24, y+24)], fill=ELEVATION_ICON_COLOR_PEAK)
        draw.polygon([(x+12, y), (x+6, y+12), (x+12, y+15), (x+18, y+12)], fill=ELEVATION_ICON_COLOR_MOUNTAIN)

    def _draw_road_icon(self, draw, x, y):
        draw.polygon([(x+8, y), (x+16, y), (x+24, y+24), (x, y+24)], fill=DISTANCE_ICON_COLOR_PAVEMENT)
        draw.line((x+12, y+4, x+12, y+10), fill=DISTANCE_ICON_COLOR_LINES, width=2)
        draw.line((x+12, y+14, x+12, y+20), fill=DISTANCE_ICON_COLOR_LINES, width=2)

    def _draw_pedal_icon(self, draw, x, y, pedal_icon=True):
        if pedal_icon:
            draw.line((x-4, y+2, x+4, y+2), fill=CADENCE_ICON_COLOR_PEDALS, width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=CADENCE_ICON_COLOR_PEDALS, width=3)
            draw.ellipse((x+4, y+4, x+20, y+20), outline=CADENCE_ICON_COLOR_CRANK, width=3)
            draw.line((x+20, y+20, x+2, y+2), fill=CADENCE_ICON_COLOR_PEDALS, width=3)
            draw.line((x+20, y+20, x+26, y+20), fill=CADENCE_ICON_COLOR_PEDALS, width=3)
            draw.ellipse((x+10, y+10, x+14, y+14), outline=CADENCE_ICON_COLOR_CRANK, width=3)
        else:
            draw.ellipse((x+4, y+4, x+20, y+20), outline=CADENCE_ICON_COLOR_CRANK, width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=CADENCE_ICON_COLOR_PEDALS, width=3)
            draw.ellipse((x, y, x+4, y+4), fill=CADENCE_ICON_COLOR_PEDALS)

    def _draw_heart_icon(self, draw, x, y):
        # Simplified diamond/heart
        draw.polygon([(x+12, y+24), (x, y+8), (x+6, y), (x+12, y+6), (x+18, y), (x+24, y+8)], fill=HEARTRATE_ICON_COLOR)

    def _draw_speedometer(self, draw, center, radius, speed):
        x, y = center
        start_angle = 135  # Bottom left
        end_angle = 405    # Bottom right (360 + 45)
        gauge_width = 20

        # 1. Background Arc (Dark grey track)
        draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, end_angle, fill=SPEEDOMETER_ICON_ARC_COLOR, width=gauge_width)

        # 2. Colored Speed Arc
        speed_pct = min(max(speed / self.max_speed, 0), 1.0) # Clamp between 0 and 1
        current_angle = start_angle + (speed_pct * (end_angle - start_angle))
        
        # Gradient logic (Green -> Yellow -> Red)
        if speed_pct < 0.5:
            color = SPEEDOMETER_ICON_SPEED_COLOR_GREEN  # Green
        elif speed_pct < 0.8:
            color = SPEEDOMETER_ICON_SPEED_COLOR_YELLOW # Yellow
        else:
            color = SPEEDOMETER_ICON_SPEED_COLOR_RED  # Red

        if current_angle > start_angle:
            draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, current_angle, fill=color, width=gauge_width)

        # 3. Outer ticks for style
        for tick_angle in range(start_angle, end_angle + 1, 27):
            rad = math.radians(tick_angle)
            in_x = x + (radius - 5) * math.cos(rad)
            in_y = y + (radius - 5) * math.sin(rad)
            out_x = x + (radius + 10) * math.cos(rad)
            out_y = y + (radius + 10) * math.sin(rad)
            draw.line((in_x, in_y, out_x, out_y), fill=SPEEDOMETER_ICON_ARC_TICKS_COLOR, width=3)

        # 4. Center Speed Text
        speed_str = f"{speed:.1f}"
        speed_w = draw.textlength(speed_str, font=self.font_speed)
        draw.text((x - speed_w/2, y - 40), speed_str, font=self.font_speed, fill=SPEEDOMETER_ICON_VALUE_COLOR)
        
        unit_str = "km/h"
        unit_w = draw.textlength(unit_str, font=self.font_labels)
        draw.text((x - unit_w/2, y + 45), unit_str, font=self.font_labels, fill=SPEEDOMETER_ICON_UNIT_COLOR)

    def _draw_metric(self, draw, x, y, icon_func, label, value, unit):
        # Draw Icon & Label
        icon_func(draw, x, y)
        draw.text((x + 35, y), label, font=self.font_labels, fill=WIDGET_ICON_LABEL_COLOR)
        
        # Draw Value
        draw.text((x, y + 30), value, font=self.font_metrics, fill=WIDGET_ICON_VALUE_COLOR)
        val_w = draw.textlength(value, font=self.font_metrics)
        
        # Draw Unit right next to the value
        draw.text((x + val_w + 10, y + 60), unit, font=self.font_labels, fill=WIDGET_ICON_UNIT_COLOR)

    def _draw_time_metric(self, draw, x, y, icon_func, value):
        # Draw Icon & Label
        icon_func(draw, x, y)
        
        utctime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=self.utc_tz)
        localtime = utctime.astimezone(self.local_tz)

        draw.text((x + 50, y - 15), f"{localtime.strftime('%Y-%m-%d %H:%M:%S')}", font=self.font_time, fill=WIDGET_ICON_VALUE_COLOR)

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

    cmd = ["ffmpeg"]
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
    
    cmd.extend([
        "-i", INPUT_VIDEO_PATH,
        "-filter_complex", f"[0:v]fps={INPUT_VIDEO_FPS}[overlay_stream]; [1:v][overlay_stream] overlay=0:0:eof_action=pass:format=yuv420p10",
        "-c:v", FFMPEG_ENCODER,
        "-rc", FFMPEG_RC, # "-rc", "constqp",
        "-tune", FFMPEG_TUNE,
        "-multipass", FFMPEG_MULTIPASS,
        "-cq", FFMPEG_CQ, # "-qp", "21",
        "-b:v", "0", # NVIDIA internally forces a hidden, low default bitrate (often 2 Mbps). To "unlock" the encoder so that your -cq value can actually ask for more data during dense frames, you must explicitly set -b:v 0.
        "-pix_fmt", "p010le",
        "-c:a", "copy",
        "-map", "0:a?",
        "-map", "0:d?",
        "-map", "0:t?",
        "-movflags", "+frag_keyframe+empty_moov",
        OUTPUT_VIDEO_PATH
    ])

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