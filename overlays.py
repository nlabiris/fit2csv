# exiftool V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260322110703_0004_D.MP4
# ffmpeg -i V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260322110703_0004_D.MP4 -framerate 1 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v hevc_nvenc -rc vbr -cq 19 -preset slow -c:a copy output_nvenc.mp4
# ffmpeg -i V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260322110703_0004_D.MP4 -framerate 1 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v libx265 -crf 18 -preset slow -c:a copy output.mp4

from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from zoneinfo import ZoneInfo
import csv, os, math

# Overlay frames settings
CSV_FILE = "activity_22257950053_final.csv"
OUTPUT_FOLDER = "frames"
WIDTH = 1920
HEIGHT = 1080

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
        font_path = "C:\\Windows\\Fonts\\ITCKRIST.TTF"  # Changed to Arial Bold as a safe default
        self.font_speed = ImageFont.truetype(font_path, 85)   # For main metric values
        self.font_time = ImageFont.truetype(font_path, 40)  # For timestamp
        self.font_metrics = ImageFont.truetype(font_path, 50)  # For metrics
        self.font_labels = ImageFont.truetype(font_path, 25)  # For labels and units

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
            self._draw_metric(draw, 30, 220, self._draw_road_icon, "Total Distance", f"{float(row['distance'])/1000:.2f}", "km")
            self._draw_metric(draw, WIDTH-200, 50, self._draw_pedal_icon, "Cadence", f"{row['cadence']}", "rpm")
            self._draw_metric(draw, WIDTH-200, 180, self._draw_heart_icon, "Heart Rate", f"{row['heart_rate']}", "bpm")
            self._draw_speedometer(draw, center=(WIDTH-170, HEIGHT-150), radius=140, speed=float(row['speed_kmh']))
            self._save_overlay(img, i)
            self._report_progress(i)

            # For: testing, break after the first frame to verify output before processing the entire dataset
            if self.debug and i % 1 == 0:
                break

        return len(data)

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
        img = Image.new("RGBA", (WIDTH, HEIGHT), (0,0,0,0))
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

    def _draw_clock_icon(self,draw, x, y):
        draw.ellipse((x, y, x+24, y+24), outline=(100, 100, 100), width=3)
        draw.line((x+12, y+12, x+12, y+5), fill=(255, 255, 255), width=3)
        draw.line((x+12, y+12, x+18, y+12), fill=(255, 255, 255), width=3)

    def _draw_mountain_icon(self, draw, x, y):
        draw.polygon([(x+12, y), (x, y+24), (x+24, y+24)], fill=(100, 100, 100))
        draw.polygon([(x+12, y), (x+6, y+12), (x+12, y+15), (x+18, y+12)], fill=(255, 255, 255))

    def _draw_road_icon(self, draw, x, y):
        draw.polygon([(x+8, y), (x+16, y), (x+24, y+24), (x, y+24)], fill=(100, 100, 100))
        draw.line((x+12, y+4, x+12, y+10), fill=(255, 255, 255), width=2)
        draw.line((x+12, y+14, x+12, y+20), fill=(255, 255, 255), width=2)

    def _draw_pedal_icon(self, draw, x, y, pedal_icon=True):
        if pedal_icon:
            draw.line((x-4, y+2, x+4, y+2), fill=(255, 255, 255), width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=(255, 255, 255), width=3)
            draw.ellipse((x+4, y+4, x+20, y+20), outline=(100, 100, 100), width=3)
            draw.line((x+20, y+20, x+2, y+2), fill=(255, 255, 255), width=3)
            draw.line((x+20, y+20, x+26, y+20), fill=(255, 255, 255), width=3)
            draw.ellipse((x+10, y+10, x+14, y+14), outline=(100, 100, 100), width=3)
        else:
            draw.ellipse((x+4, y+4, x+20, y+20), outline=(100, 100, 100), width=3)
            draw.line((x+12, y+12, x+2, y+2), fill=(255, 255, 255), width=3)
            draw.ellipse((x, y, x+4, y+4), fill=(255, 255, 255))

    def _draw_heart_icon(self, draw, x, y):
        # Simplified diamond/heart
        draw.polygon([(x+12, y+24), (x, y+8), (x+6, y), (x+12, y+6), (x+18, y), (x+24, y+8)], fill=(255, 50, 50))

    def _draw_speedometer(self, draw, center, radius, speed):
        x, y = center
        start_angle = 135  # Bottom left
        end_angle = 405    # Bottom right (360 + 45)
        gauge_width = 20

        # 1. Background Arc (Dark grey track)
        draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, end_angle, fill=(60, 60, 60, 255), width=gauge_width)

        # 2. Colored Speed Arc
        speed_pct = min(max(speed / self.max_speed, 0), 1.0) # Clamp between 0 and 1
        current_angle = start_angle + (speed_pct * (end_angle - start_angle))
        
        # Gradient logic (Green -> Yellow -> Red)
        if speed_pct < 0.5:
            color = (50, 255, 50)  # Green
        elif speed_pct < 0.8:
            color = (255, 200, 50) # Yellow
        else:
            color = (255, 50, 50)  # Red

        if current_angle > start_angle:
            draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, current_angle, fill=color, width=gauge_width)

        # 3. Outer ticks for style
        for tick_angle in range(start_angle, end_angle + 1, 27):
            rad = math.radians(tick_angle)
            in_x = x + (radius - 5) * math.cos(rad)
            in_y = y + (radius - 5) * math.sin(rad)
            out_x = x + (radius + 10) * math.cos(rad)
            out_y = y + (radius + 10) * math.sin(rad)
            draw.line((in_x, in_y, out_x, out_y), fill=(150, 150, 150), width=3)

        # 4. Center Speed Text
        speed_str = f"{speed:.1f}"
        speed_w = draw.textlength(speed_str, font=self.font_speed)
        draw.text((x - speed_w/2, y - 40), speed_str, font=self.font_speed, fill=(255,255,255))
        
        unit_str = "km/h"
        unit_w = draw.textlength(unit_str, font=self.font_labels)
        draw.text((x - unit_w/2, y + 45), unit_str, font=self.font_labels, fill=(200,200,200))

    def _draw_metric(self, draw, x, y, icon_func, label, value, unit):
        # Draw Icon & Label
        icon_func(draw, x, y)
        draw.text((x + 35, y), label, font=self.font_labels, fill=(200, 200, 200))
        
        # Draw Value
        draw.text((x, y + 30), value, font=self.font_metrics, fill=(255, 255, 255))
        val_w = draw.textlength(value, font=self.font_metrics)
        
        # Draw Unit right next to the value
        draw.text((x + val_w + 10, y + 60), unit, font=self.font_labels, fill=(200, 200, 200))

    def _draw_time_metric(self, draw, x, y, icon_func, value):
        # Draw Icon & Label
        icon_func(draw, x, y)
        
        utctime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=self.utc_tz)
        localtime = utctime.astimezone(self.local_tz)

        draw.text((x + 50, y - 15), f"{localtime.strftime('%Y-%m-%d %H:%M:%S')}", font=self.font_time, fill=(255,255,255))

    #endregion

if __name__ == "__main__":
    f = Overlay(True)
    overlay_count = f.process()
    if f.debug:
        print("Debug mode: Only 1 frame generated for testing.")
    else:
        print(f"Done! Generated {overlay_count} frames in '{OUTPUT_FOLDER}'")

