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

        # CSV setup
        with open(OUTPUT_FILE, "w", newline="") as f:
            writer = csv.writer(f)

            # Header
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

            prev_time = None
            prev_row = None  # store last known values

            # Process Trackpoints
            for tp in root.xpath(".//tcx:Trackpoint", namespaces=NS):
                # Time
                tc_time = self._get_time(tp)

                # Position
                lat, lon = self._get_coordinates(tp)

                # Elevation and distance
                
                alt = self._get_altitude(tp)
                dist = self._get_distance(tp)

                # Gradient window
                gradient = self._calculate_gradient(points, dist, alt)

                # Heart rate
                hr = self._get_heart_rate(tp)

                # Cadence
                cadence = self._get_cadence(tp)

                # Speed (from extensions)
                speed, speed_kmh = self._get_speed(tp)

                # Temperature from FIT
                temp = self._find_nearest_temp(tc_time)

                if prev_row and prev_row["speed_kmh"] > 0 and speed_kmh == 0:
                    speed_kmh = max(prev_row["speed_kmh"] - 1, 0)  # Reduce speed for gap fill

                if prev_row and prev_row["cadence"] > 0 and cadence == 0:
                    cadence = max(prev_row["cadence"] - 1, 0)  # Reduce cadence for gap fill

                # Keep current row to compare with next one for gap filling
                row_data = {
                    "lat": lat,
                    "lon": lon,
                    "alt": alt,
                    "dist": dist,
                    "hr": hr,
                    "cadence": cadence,
                    "speed": speed,
                    "speed_kmh": speed_kmh,
                    "temp": temp,
                    "gradient": round(gradient, 2)
                }

                # Fill missing seconds
                if prev_time is not None:
                    self._fill_missing_seconds(writer, prev_time, prev_row, tc_time, row_data)

                # Write row to CSV
                self._write_row(writer, tc_time, lat, lon, alt, dist, hr, cadence, speed_kmh, temp, gradient) 

                prev_time = tc_time
                prev_row = row_data

    #region Helpers

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

    def _fill_missing_seconds(self, writer, prev_time, prev_row, current_time, current_row):
        gap = int((current_time - prev_time).total_seconds())
        previous_speed = prev_row["speed_kmh"]
        current_speed = current_row["speed_kmh"]
        previous_cadence = prev_row["cadence"]
        current_cadence = current_row["cadence"]
        for i in range(1, gap):
            missing_time = prev_time + timedelta(seconds=i)

            if i <= DECREASE_WINDOW:
                # Decrease from previous_speed to 0
                interpolated_speed = previous_speed * (1 - i / DECREASE_WINDOW)
                interpolated_cadence = previous_cadence * (1 - i / DECREASE_WINDOW)
            elif i >= gap - INCREASE_WINDOW:
                # Increase from 0 to current_speed
                progress = (i - (gap - INCREASE_WINDOW)) / INCREASE_WINDOW
                interpolated_speed = current_speed * progress
                interpolated_cadence = current_cadence * progress
            else:
                # Stay at 0
                interpolated_speed = 0
                interpolated_cadence = 0

            writer.writerow([
                missing_time.isoformat(),
                prev_row["lat"],
                prev_row["lon"],
                prev_row["alt"],
                prev_row["dist"],
                prev_row["hr"],
                round(interpolated_cadence),
                interpolated_speed,
                prev_row["temp"],
                prev_row["gradient"],
                1 # mark missing row
            ])

    def _write_row(self, writer, time, lat, lon, alt, dist, hr, cadence, speed_kmh, temp, gradient):
        writer.writerow([
            time.isoformat(),
            lat,
            lon,
            alt,
            dist,
            hr,
            cadence,
            speed_kmh,
            temp,
            round(gradient, 2)
        ])

    #endregion

if __name__ == "__main__":
    f = FIT2CSV()
    f.process()
    print("CSV created:", OUTPUT_FILE)