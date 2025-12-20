import os

from dagster import RunRequest, SensorEvaluationContext, sensor

from orchestration.jobs import enhance_job


@sensor(job_name=enhance_job)
def new_file_sensor(context: SensorEvaluationContext):
    raw_path = "data/raw"
    last_mtime = float(context.cursor) if context.cursor else 0

    # Get all eligible files
    files_to_process = []
    for filename in os.listdir(raw_path):
        filepath = os.path.join(raw_path, filename)
        if os.path.isfile(filepath):
            file_mtime = os.path.getmtime(filepath)
            if file_mtime > last_mtime:
                files_to_process.append((file_mtime, filename))

    # FIFO
    files_to_process.sort(key=lambda x: x[0])

    # Only process the first 5 files due to local resource constraints
    # If there are 100 files, the sensor will just run again in 30 seconds
    # to get the next 5
    batch_size = 5
    current_batch = files_to_process[:batch_size]

    max_mtime_in_batch = last_mtime

    for mtime, filename in current_batch:
        partition_date = filename.replace(".csv", "")
        yield RunRequest(run_key=partition_date, partition_key=partition_date)

        # Track the latest time successfully yielded
        max_mtime_in_batch = max(max_mtime_in_batch, mtime)

    # advance the cursor as far as actually processed
    context.update_cursor(str(max_mtime_in_batch))
