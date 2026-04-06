from fitparse import FitFile
from lxml import etree
from datetime import datetime, timedelta
from collections import deque
import csv

NS = { "tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" }

# Telemetry files
FIT_FILE = "22257950053_ACTIVITY.fit"
TCX_FILE = "activity_22257950053.tcx"
OUTPUT_FILE = "activity_22257950053.csv"

# Processing settings
TIME_TOLERANCE = 2
WINDOW_METERS = 50
ALT_SMOOTH_POINTS = 10
DECREASE_WINDOW = 5
INCREASE_WINDOW = 5
TIMESTAMP_STEP_MS = 500


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
        with open(OUTPUT_FILE, "w", newline="") as f:
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

if __name__ == "__main__":
    f = FIT2CSV()
    f.process()
    print("CSV created:", OUTPUT_FILE)