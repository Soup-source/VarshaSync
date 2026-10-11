import json
import pandas as pd
import requests

# Parameters: Vellore / Chennai region coordinates
LATITUDE = 13.0827
LONGITUDE = 80.2707
START_DATE = "2023-11-01"
END_DATE = "2023-12-05"

url = "https://archive-api.open-meteo.com/v1/archive"
params = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "start_date": START_DATE,
    "end_date": END_DATE,
    "daily": ["precipitation_sum", "precipitation_hours"],
    "timezone": "Asia/Kolkata",
}

print("Fetching authentic meteorological observations from Open-Meteo...")
response = requests.get(url, params=params)

if response.status_code == 200:
  data = response.json()
  daily = data["daily"]

  # Save as CSV for Member 1 & 3
  df = pd.DataFrame({
      "date": daily["time"],
      "rainfall_mm": daily["precipitation_sum"],
      "rain_hours": daily["precipitation_hours"],
  })
  df.to_csv("rainfall_data.csv", index=False)

  # Save as JSON
  with open("rainfall_live.json", "w") as f:
    json.dump(daily, f, indent=2)

  print("Success! Created rainfall_data.csv and rainfall_live.json")
else:
  print(f"Error: {response.status_code}")