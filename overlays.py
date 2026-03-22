# exiftool V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260308111009_0003_D.MP4
# ffmpeg -i V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260308111009_0003_D.MP4 -framerate 1 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v hevc_nvenc -rc vbr -cq 19 -preset slow -c:a copy output_nvenc.mp4
# ffmpeg -i V:\media\video\dji\internal_storage\DCIM\DJI_001\DJI_20260308111009_0003_D.MP4 -framerate 1 -i frames/frame_%05d.png -filter_complex "[0:v][1:v] overlay=0:0:eof_action=pass" -c:v libx265 -crf 18 -preset slow -c:a copy output.mp4

from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from zoneinfo import ZoneInfo
import csv, os, math

# --- SETTINGS ---
csv_file = "activity_22101275645_final.csv"
output_folder = "frames"
width, height = 1920, 1080

# TIP: For a true sports look, use a bold, italicized font like Impact, Roboto Condensed, or Ubuntu Italic.
font_path = "C:\\Windows\\Fonts\\ITCKRIST.TTF"  # Changed to Arial Bold as a safe default
font_speed = ImageFont.truetype(font_path, 85)   # For main metric values
font_time = ImageFont.truetype(font_path, 40)  # For timestamp
font_metrics = ImageFont.truetype(font_path, 50)  # For metrics
font_labels = ImageFont.truetype(font_path, 25)  # For labels and units

max_speed = 73
os.makedirs(output_folder, exist_ok=True)

# Panel Background Color: (Red, Green, Blue, Alpha) -> 0 is fully transparent, 255 is solid
panel_bg = (0, 0, 0, 0) # Semi-transparent black background for better readability of text/icons

user_tz_preference = ZoneInfo("Europe/Athens")

# --- ICON DRAWING FUNCTIONS ---
# These draw simple vector icons without needing external image files
def draw_clock_icon(draw, x, y):
    draw.ellipse((x, y, x+24, y+24), outline=(200, 200, 200), width=3)
    draw.line((x+12, y+12, x+12, y+5), fill=(255, 255, 255), width=3)
    draw.line((x+12, y+12, x+18, y+12), fill=(255, 255, 255), width=3)

def draw_mountain_icon(draw, x, y):
    draw.polygon([(x+12, y), (x, y+24), (x+24, y+24)], fill=(200, 200, 200))
    draw.polygon([(x+12, y), (x+6, y+12), (x+12, y+15), (x+18, y+12)], fill=(255, 255, 255))

def draw_road_icon(draw, x, y):
    draw.polygon([(x+8, y), (x+16, y), (x+24, y+24), (x, y+24)], fill=(200, 200, 200))
    draw.line((x+12, y+4, x+12, y+10), fill=(255, 255, 255), width=2)
    draw.line((x+12, y+14, x+12, y+20), fill=(255, 255, 255), width=2)

def draw_pedal_icon(draw, x, y, pedal_icon=True):
    if pedal_icon:
        draw.line((x-4, y+2, x+4, y+2), fill=(255, 255, 255), width=3)
        draw.line((x+12, y+12, x+2, y+2), fill=(255, 255, 255), width=3)
        draw.ellipse((x+4, y+4, x+20, y+20), outline=(200, 200, 200), width=3)
        draw.line((x+20, y+20, x+2, y+2), fill=(255, 255, 255), width=3)
        draw.line((x+20, y+20, x+26, y+20), fill=(255, 255, 255), width=3)
        draw.ellipse((x+10, y+10, x+14, y+14), outline=(200, 200, 200), width=3)
    else:
        draw.ellipse((x+4, y+4, x+20, y+20), outline=(200, 200, 200), width=3)
        draw.line((x+12, y+12, x+2, y+2), fill=(255, 255, 255), width=3)
        draw.ellipse((x, y, x+4, y+4), fill=(255, 255, 255))

def draw_heart_icon(draw, x, y):
    # Simplified diamond/heart
    draw.polygon([(x+12, y+24), (x, y+8), (x+6, y), (x+12, y+6), (x+18, y), (x+24, y+8)], fill=(255, 50, 50))


# --- FANCY SPEEDOMETER ---
def draw_speedometer(draw, center, radius, speed):
    x, y = center
    start_angle = 135  # Bottom left
    end_angle = 405    # Bottom right (360 + 45)
    gauge_width = 20

    # 1. Background Arc (Dark grey track)
    draw.arc([x-radius, y-radius, x+radius, y+radius], start_angle, end_angle, fill=(60, 60, 60, 255), width=gauge_width)

    # 2. Colored Speed Arc
    speed_pct = min(max(speed / max_speed, 0), 1.0) # Clamp between 0 and 1
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
    speed_w = draw.textlength(speed_str, font=font_speed)
    draw.text((x - speed_w/2, y - 40), speed_str, font=font_speed, fill=(255,255,255))
    
    unit_str = "km/h"
    unit_w = draw.textlength(unit_str, font=font_labels)
    draw.text((x - unit_w/2, y + 45), unit_str, font=font_labels, fill=(200,200,200))

# --- HELPER: DRAW METRIC BLOCK ---
def draw_metric(draw, x, y, icon_func, label, value, unit):
    # Draw Icon & Label
    icon_func(draw, x, y)
    draw.text((x + 35, y), label, font=font_labels, fill=(200, 200, 200))
    
    # Draw Value
    draw.text((x, y + 30), value, font=font_metrics, fill=(255, 255, 255))
    val_w = draw.textlength(value, font=font_metrics)
    
    # Draw Unit right next to the value
    draw.text((x + val_w + 10, y + 60), unit, font=font_labels, fill=(200, 200, 200))

def draw_time_metric(draw, x, y, icon_func, value):
    # Draw Icon & Label
    icon_func(draw, x, y)
    
    utctime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=ZoneInfo("UTC"))
    localtime = utctime.astimezone(user_tz_preference)

    draw.text((x + 50, y - 15), f"{localtime.strftime('%Y-%m-%d %H:%M:%S')}", font=font_time, fill=(255,255,255))

# --- READ CSV ---
data = []
with open(csv_file, newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        data.append(row)

# --- GENERATE FRAMES ---
print("Generating frames...")
for i, row in enumerate(data):
    img = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    # --- DRAW BACKGROUND PANELS ---
    # Top-Left Panel (Timestamp)
    # draw.rounded_rectangle([30, 20, 480, 100], radius=15, fill=panel_bg)
    
    # Middle-Left Panel (Elevation & Distance)
    # draw.rounded_rectangle([30, 220, 380, 580], radius=20, fill=panel_bg)
    
    # Middle-Right Panel (Cadence & Heart Rate)
    # draw.rounded_rectangle([width-430, 220, width-50, 580], radius=20, fill=panel_bg)
    
    # Bottom-Right Panel (Speedometer)
    # draw.rounded_rectangle([width-430, height-430, width-70, height-70], radius=25, fill=panel_bg)

    # --- DRAW FOREGROUND METRICS ---
    # 1. Top-Left: Timestamp
    draw_time_metric(draw, 30, 20, draw_clock_icon, row['time'])

    # 2. Middle-Left: Elevation & Distance
    draw_metric(draw, 30, 100, draw_mountain_icon, "Elevation", f"{float(row['elevation']):.0f}", "m")
    draw_metric(draw, 30, 220, draw_road_icon, "Total Distance", f"{float(row['distance'])/1000:.2f}", "km")

    # 3. Middle-Right: Cadence & Heart Rate
    draw_metric(draw, width-200, 50, draw_pedal_icon, "Cadence", f"{row['cadence']}", "rpm")
    draw_metric(draw, width-200, 180, draw_heart_icon, "Heart Rate", f"{row['heart_rate']}", "bpm")

    # 4. Bottom-Right: Speedometer
    # Adjust position slightly up from the absolute corner to match your image
    draw_speedometer(draw, center=(width-170, height-150), radius=140, speed=float(row['speed_kmh']))

    # Save frame
    img.save(f"{output_folder}/frame_{i:05d}.png")

    # Quick progress indicator
    if i % 100 == 0:
        print(f"Processed {i} frames...")

    # For: testing, break after the first frame to verify output before processing the entire dataset
    # if i % 1 == 0:
    #     break

print(f"Done! Generated {len(data)} frames in '{output_folder}'")