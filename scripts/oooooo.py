from datetime import datetime, timezone, timedelta

# Convert milliseconds to seconds
timestamp_ms = 1750982400000
timestamp_s = timestamp_ms / 1000

# UTC time
dt_utc = datetime.fromtimestamp(timestamp_s, tz=timezone.utc)
print("UTC:", dt_utc)

# Vietnam time (UTC+7)
dt_vn = dt_utc.astimezone(timezone(timedelta(hours=7)))
print("Vietnam:", dt_vn)